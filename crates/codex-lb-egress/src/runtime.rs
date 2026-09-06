use std::collections::HashMap;
use std::sync::Arc;

use base64::Engine as _;
use codex_lb_protocol::{CAPABILITIES, NativeCommand, NativeEvent, PROTOCOL_VERSION};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, BufWriter};
use tokio::sync::{Mutex, mpsc, oneshot};
use tokio::task::{AbortHandle, JoinSet};
use tokio_tungstenite::tungstenite::Message;

use crate::http::{ClientKey, ClientPool, classify_error, execute_request};
use crate::websocket::{
    WebSocketCommand, emit_websocket_error, emit_websocket_setup_error, execute_websocket,
};

pub(crate) type Output = Arc<Mutex<BufWriter<tokio::io::Stdout>>>;
type ActiveRequests = Arc<Mutex<HashMap<String, ActiveRequest>>>;
pub type RequestError = Box<dyn std::error::Error + Send + Sync>;

enum ActiveRequest {
    Http(oneshot::Sender<()>),
    WebSocket {
        commands: mpsc::Sender<WebSocketCommand>,
        abort: AbortHandle,
    },
}

pub async fn run_stdio() -> Result<(), RequestError> {
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .map_err(|_| "failed to install aws-lc-rs crypto provider")?;

    let output = Arc::new(Mutex::new(BufWriter::new(tokio::io::stdout())));
    let active: ActiveRequests = Arc::new(Mutex::new(HashMap::new()));
    let mut clients = ClientPool::default();
    let mut tasks = JoinSet::new();
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    let mut handshake_complete = false;

    loop {
        tokio::select! {
            line = lines.next_line() => {
                let Some(line) = line? else {
                    break;
                };
                let command: NativeCommand = serde_json::from_str(&line)?;
                if !handshake_complete {
                    match command {
                        NativeCommand::ClientHello {
                            min_protocol_version,
                            max_protocol_version,
                        } if min_protocol_version <= PROTOCOL_VERSION
                            && PROTOCOL_VERSION <= max_protocol_version =>
                        {
                            emit(
                                &output,
                                &NativeEvent::ServerHello {
                                    protocol_version: PROTOCOL_VERSION,
                                    capabilities: CAPABILITIES
                                        .iter()
                                        .map(|value| (*value).to_owned())
                                        .collect(),
                                },
                            )
                            .await?;
                            handshake_complete = true;
                        }
                        _ => return Err("native helper protocol handshake failed".into()),
                    }
                    continue;
                }
                match command {
                    NativeCommand::ClientHello { .. } => {
                        return Err("native helper received a duplicate protocol handshake".into());
                    }
                    NativeCommand::Request(request) => {
                        if request.request_id.is_empty() || active.lock().await.contains_key(&request.request_id) {
                            emit_error(
                                &output,
                                &request.request_id,
                                "native helper rejected the request",
                                "setup",
                                false,
                                false,
                            ).await?;
                            continue;
                        }
                        if request.timeout_ms == 0 || request.connect_timeout_ms == Some(0) {
                            emit_error(
                                &output,
                                &request.request_id,
                                "native helper rejected the request",
                                "setup",
                                false,
                                false,
                            ).await?;
                            continue;
                        }
                        let key = ClientKey {
                            proxy_url: request.proxy_url.clone(),
                            connect_timeout_ms: request.connect_timeout_ms,
                            decode_response: request
                                .headers
                                .iter()
                                .any(|(name, _)| name.eq_ignore_ascii_case("accept-encoding")),
                        };
                        let client = match clients.get(&key) {
                            Ok(client) => client,
                            Err(error) => {
                                let (message, phase, retryable, tls_verification) = classify_error(&error);
                                emit_error(
                                    &output,
                                    &request.request_id,
                                    message,
                                    phase,
                                    retryable,
                                    tls_verification,
                                ).await?;
                                continue;
                            }
                        };
                        let request_id = request.request_id.clone();
                        let (cancel_tx, cancel_rx) = oneshot::channel();
                        active
                            .lock()
                            .await
                            .insert(request_id.clone(), ActiveRequest::Http(cancel_tx));
                        let task_output = output.clone();
                        let task_active = active.clone();
                        tasks.spawn(async move {
                            tokio::select! {
                                result = execute_request(request, client, &task_output) => {
                                    if let Err(error) = result {
                                        let (message, phase, retryable, tls_verification) =
                                            classify_error(error.as_ref());
                                        let _ = emit_error(
                                            &task_output,
                                            &request_id,
                                            message,
                                            phase,
                                            retryable,
                                            tls_verification,
                                        ).await;
                                    }
                                }
                                _ = cancel_rx => {
                                    let _ = emit(
                                        &task_output,
                                        &NativeEvent::Cancelled { request_id: request_id.clone() },
                                    ).await;
                                }
                            }
                            task_active.lock().await.remove(&request_id);
                        });
                    }
                    NativeCommand::WebsocketConnect(request) => {
                        if request.request_id.is_empty()
                            || request.connect_timeout_ms == 0
                            || request.max_message_bytes == 0
                            || request.ping_interval_ms == Some(0)
                            || request.ping_timeout_ms == Some(0)
                            || active.lock().await.contains_key(&request.request_id)
                        {
                            emit_websocket_setup_error(
                                &output,
                                &request.request_id,
                                None,
                                "native helper rejected the websocket request",
                            )
                            .await?;
                            continue;
                        }
                        let request_id = request.request_id.clone();
                        let (command_tx, command_rx) = mpsc::channel(32);
                        let (start_tx, start_rx) = oneshot::channel();
                        let task_output = output.clone();
                        let task_active = active.clone();
                        let task_request_id = request_id.clone();
                        let abort = tasks.spawn(async move {
                            let _ = start_rx.await;
                            if let Err(error) =
                                execute_websocket(request, command_rx, &task_output).await
                            {
                                let _ = emit_websocket_error(
                                    &task_output,
                                    &task_request_id,
                                    None,
                                    &error,
                                )
                                .await;
                            }
                            task_active.lock().await.remove(&task_request_id);
                        });
                        active.lock().await.insert(
                            request_id.clone(),
                            ActiveRequest::WebSocket {
                                commands: command_tx,
                                abort,
                            },
                        );
                        let _ = start_tx.send(());
                    }
                    NativeCommand::WebsocketSendText {
                        request_id,
                        command_id,
                        text,
                    } => {
                        dispatch_websocket_command(
                            &active,
                            &output,
                            request_id,
                            command_id.clone(),
                            WebSocketCommand::Send {
                                command_id,
                                message: Message::Text(text.into()),
                            },
                        )
                        .await?;
                    }
                    NativeCommand::WebsocketSendBinary {
                        request_id,
                        command_id,
                        data,
                    } => {
                        let decoded = match base64::engine::general_purpose::STANDARD.decode(data) {
                            Ok(decoded) => decoded,
                            Err(_) => {
                                emit_websocket_setup_error(
                                    &output,
                                    &request_id,
                                    Some(command_id),
                                    "native helper rejected websocket binary data",
                                )
                                .await?;
                                continue;
                            }
                        };
                        dispatch_websocket_command(
                            &active,
                            &output,
                            request_id,
                            command_id.clone(),
                            WebSocketCommand::Send {
                                command_id,
                                message: Message::Binary(decoded.into()),
                            },
                        )
                        .await?;
                    }
                    NativeCommand::WebsocketClose {
                        request_id,
                        command_id,
                        code,
                        reason,
                    } => {
                        dispatch_websocket_command(
                            &active,
                            &output,
                            request_id,
                            command_id.clone(),
                            WebSocketCommand::Close {
                                command_id,
                                code,
                                reason,
                            },
                        )
                        .await?;
                    }
                    NativeCommand::Cancel { request_id } => {
                        let cancellation = active.lock().await.remove(&request_id);
                        match cancellation {
                            Some(ActiveRequest::Http(cancellation)) => {
                                let _ = cancellation.send(());
                            }
                            Some(ActiveRequest::WebSocket { abort, .. }) => {
                                abort.abort();
                                emit(&output, &NativeEvent::Cancelled { request_id }).await?;
                            }
                            None => {
                                emit(&output, &NativeEvent::Cancelled { request_id }).await?;
                            }
                        }
                    }
                }
            }
            Some(_result) = tasks.join_next(), if !tasks.is_empty() => {}
        }
    }

    let cancellations = {
        let mut active = active.lock().await;
        active
            .drain()
            .map(|(_, cancellation)| cancellation)
            .collect::<Vec<_>>()
    };
    for cancellation in cancellations {
        match cancellation {
            ActiveRequest::Http(cancellation) => {
                let _ = cancellation.send(());
            }
            ActiveRequest::WebSocket { abort, .. } => {
                abort.abort();
            }
        }
    }
    while tasks.join_next().await.is_some() {}
    output.lock().await.flush().await?;
    Ok(())
}

async fn dispatch_websocket_command(
    active: &ActiveRequests,
    output: &Output,
    request_id: String,
    command_id: String,
    command: WebSocketCommand,
) -> Result<(), std::io::Error> {
    let sender = {
        let active = active.lock().await;
        match active.get(&request_id) {
            Some(ActiveRequest::WebSocket { commands, .. }) => Some(commands.clone()),
            _ => None,
        }
    };
    let Some(sender) = sender else {
        return emit_websocket_setup_error(
            output,
            &request_id,
            Some(command_id),
            "native websocket is not active",
        )
        .await;
    };
    if let Err(error) = sender.try_send(command) {
        let message = match error {
            mpsc::error::TrySendError::Full(_) => "native websocket command channel is full",
            mpsc::error::TrySendError::Closed(_) => "native websocket command channel closed",
        };
        emit_websocket_setup_error(output, &request_id, Some(command_id), message).await?;
    }
    Ok(())
}

async fn emit_error(
    output: &Output,
    request_id: &str,
    message: &str,
    failure_phase: &str,
    retryable_same_contract: bool,
    is_tls_verification_failure: bool,
) -> Result<(), std::io::Error> {
    emit(
        output,
        &NativeEvent::Error {
            request_id: request_id.to_owned(),
            message: message.to_owned(),
            failure_phase: failure_phase.to_owned(),
            retryable_same_contract,
            is_tls_verification_failure,
        },
    )
    .await
}

pub(crate) async fn emit(output: &Output, event: &NativeEvent) -> Result<(), std::io::Error> {
    let mut output = output.lock().await;
    output.write_all(&serde_json::to_vec(event)?).await?;
    output.write_all(b"\n").await?;
    output.flush().await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::sync::Once;

    use futures_util::SinkExt;
    use futures_util::StreamExt;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_hdr_async_with_config;
    use tokio_tungstenite::tungstenite::Message;
    use tokio_tungstenite::tungstenite::http::HeaderValue;

    use crate::http::{
        CODEX_H2_INITIAL_CONNECTION_WINDOW_SIZE, CODEX_H2_INITIAL_STREAM_WINDOW_SIZE,
        CODEX_H2_MAX_FRAME_SIZE, CODEX_H2_MAX_HEADER_LIST_SIZE, ClientKey, ClientPool,
        error_chain_has_invalid_certificate,
    };
    use codex_lb_protocol::NativeWebSocketRequest;

    use crate::websocket::{connect_native_websocket, native_websocket_config};

    static INSTALL_PROVIDER: Once = Once::new();

    fn install_provider() {
        INSTALL_PROVIDER.call_once(|| {
            rustls::crypto::aws_lc_rs::default_provider()
                .install_default()
                .expect("install test crypto provider");
        });
    }

    #[test]
    fn compatible_requests_share_one_client_pool_entry() {
        install_provider();
        let mut pool = ClientPool::default();
        let key = ClientKey {
            proxy_url: None,
            connect_timeout_ms: Some(10_000),
            decode_response: true,
        };

        pool.get(&key).expect("first client");
        pool.get(&key).expect("reused client");

        assert_eq!(pool.clients.len(), 1);
    }

    #[test]
    fn response_decode_policy_partitions_client_pool_entries() {
        install_provider();
        let mut pool = ClientPool::default();
        let decoding = ClientKey {
            proxy_url: None,
            connect_timeout_ms: Some(10_000),
            decode_response: true,
        };
        let decoding_disabled = ClientKey {
            decode_response: false,
            ..decoding.clone()
        };

        pool.get(&decoding).expect("decoding client");
        pool.get(&decoding_disabled)
            .expect("decoding-disabled client");

        assert_eq!(pool.clients.len(), 2);
    }

    #[test]
    fn connector_policy_partitions_client_pool_entries() {
        install_provider();
        let mut pool = ClientPool::default();
        let direct = ClientKey {
            proxy_url: None,
            connect_timeout_ms: Some(10_000),
            decode_response: true,
        };
        let proxied = ClientKey {
            proxy_url: Some("http://127.0.0.1:18080".to_owned()),
            connect_timeout_ms: Some(10_000),
            decode_response: true,
        };

        pool.get(&direct).expect("direct client");
        pool.get(&proxied).expect("proxied client");

        assert_eq!(pool.clients.len(), 2);
    }

    #[test]
    fn codex_http2_startup_profile_uses_measured_fixed_windows() {
        assert_eq!(CODEX_H2_INITIAL_STREAM_WINDOW_SIZE, 2_097_152);
        assert_eq!(CODEX_H2_INITIAL_CONNECTION_WINDOW_SIZE, 5_242_880);
        assert_eq!(CODEX_H2_MAX_FRAME_SIZE, 16_384);
        assert_eq!(CODEX_H2_MAX_HEADER_LIST_SIZE, 16_384);
    }

    #[test]
    fn tls_certificate_failure_is_typed_without_message_matching() {
        let error = rustls::Error::InvalidCertificate(rustls::CertificateError::UnknownIssuer);

        assert!(error_chain_has_invalid_certificate(&error));
    }

    #[tokio::test]
    #[allow(clippy::result_large_err)] // callback error type is fixed by tungstenite
    async fn codex_websocket_fork_negotiates_compression_and_relays_frames() {
        install_provider();
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind websocket server");
        let address = listener.local_addr().expect("server address");
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.expect("accept websocket client");
            let mut websocket = accept_hdr_async_with_config(
                stream,
                |request: &tokio_tungstenite::tungstenite::handshake::server::Request,
                 mut response: tokio_tungstenite::tungstenite::handshake::server::Response| {
                    assert!(
                        request
                            .headers()
                            .get("sec-websocket-extensions")
                            .and_then(|value| value.to_str().ok())
                            .is_some_and(|value| value.contains("permessage-deflate"))
                    );
                    assert_eq!(
                        request
                            .headers()
                            .get("sec-websocket-protocol")
                            .and_then(|value| value.to_str().ok()),
                        Some("openai")
                    );
                    response.headers_mut().insert(
                        "sec-websocket-protocol",
                        HeaderValue::from_static("openai"),
                    );
                    Ok(response)
                },
                Some(native_websocket_config(1024)),
            )
            .await
            .expect("accept websocket handshake");
            websocket
                .send(Message::Text("server-frame".into()))
                .await
                .expect("send server frame");
            let message = websocket
                .next()
                .await
                .expect("receive client frame")
                .expect("valid client frame");
            assert_eq!(message, Message::Binary(vec![0, 255].into()));
        });

        let request = NativeWebSocketRequest {
            request_id: "ws-test".to_owned(),
            url: format!("ws://{address}/v1/responses"),
            headers: vec![("sec-websocket-protocol".to_owned(), "openai".to_owned())],
            connect_timeout_ms: 2_000,
            max_message_bytes: 1024,
            ping_interval_ms: Some(20_000),
            ping_timeout_ms: Some(120_000),
            proxy_url: None,
        };
        let (mut websocket, response) = connect_native_websocket(&request)
            .await
            .expect("connect native websocket");

        assert_eq!(response.status().as_u16(), 101);
        assert_eq!(
            response
                .headers()
                .get("sec-websocket-protocol")
                .and_then(|value| value.to_str().ok()),
            Some("openai")
        );
        assert!(
            response
                .headers()
                .get("sec-websocket-extensions")
                .and_then(|value| value.to_str().ok())
                .is_some_and(|value| value.contains("permessage-deflate"))
        );
        assert_eq!(
            websocket
                .next()
                .await
                .expect("receive server frame")
                .expect("valid server frame"),
            Message::Text("server-frame".into())
        );
        websocket
            .send(Message::Binary(vec![0, 255].into()))
            .await
            .expect("send client frame");
        server.await.expect("websocket server task");
    }
}
