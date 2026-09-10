use std::collections::HashMap;
use std::time::Duration;

use base64::Engine as _;
use codex_lb_protocol::{NativeEvent, NativeRequest, NativeSseOptions};
use codex_lb_responses::compact::{CompactCollector, CompactResult};
use codex_lb_responses::stream::interpret;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};

use crate::output::EventBatch;
use crate::runtime::{Output, RequestError, emit};
use crate::sse::{SseEventTooLarge, SseFramer, text_fragments};

pub(crate) const CODEX_H2_INITIAL_STREAM_WINDOW_SIZE: u32 = 2 * 1024 * 1024;
pub(crate) const CODEX_H2_INITIAL_CONNECTION_WINDOW_SIZE: u32 = 5 * 1024 * 1024;
pub(crate) const CODEX_H2_MAX_FRAME_SIZE: u32 = 16 * 1024;
pub(crate) const CODEX_H2_MAX_HEADER_LIST_SIZE: u32 = 16 * 1024;
const SSE_READ_CHUNK_SIZE: usize = 16 * 1024;
const SSE_IPC_TEXT_FRAGMENT_SIZE: usize = 16 * 1024;

#[derive(Debug)]
struct StreamIdleTimeout;

impl std::fmt::Display for StreamIdleTimeout {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("native upstream SSE body read timed out")
    }
}

impl std::error::Error for StreamIdleTimeout {}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub(crate) struct ClientKey {
    pub(crate) proxy_url: Option<String>,
    pub(crate) connect_timeout_ms: Option<u64>,
    pub(crate) decode_response: bool,
}

#[derive(Default)]
pub(crate) struct ClientPool {
    pub(crate) clients: HashMap<ClientKey, reqwest::Client>,
}

impl ClientPool {
    pub(crate) fn get(&mut self, key: &ClientKey) -> Result<reqwest::Client, reqwest::Error> {
        if let Some(client) = self.clients.get(key) {
            return Ok(client.clone());
        }
        let mut builder = reqwest::Client::builder()
            .use_rustls_tls()
            .http2_initial_stream_window_size(CODEX_H2_INITIAL_STREAM_WINDOW_SIZE)
            .http2_initial_connection_window_size(CODEX_H2_INITIAL_CONNECTION_WINDOW_SIZE)
            .http2_max_frame_size(CODEX_H2_MAX_FRAME_SIZE)
            .http2_max_header_list_size(CODEX_H2_MAX_HEADER_LIST_SIZE)
            .pool_idle_timeout(Duration::from_secs(120))
            .pool_max_idle_per_host(8);
        if !key.decode_response {
            builder = builder.no_brotli().no_deflate().no_gzip().no_zstd();
        }
        if let Some(connect_timeout_ms) = key.connect_timeout_ms {
            builder = builder.connect_timeout(Duration::from_millis(connect_timeout_ms));
        }
        if let Some(proxy_url) = key.proxy_url.as_deref() {
            builder = builder.proxy(reqwest::Proxy::all(proxy_url)?);
        }
        let client = builder.build()?;
        self.clients.insert(key.clone(), client.clone());
        Ok(client)
    }
}

pub(crate) async fn execute_request(
    request: NativeRequest,
    client: reqwest::Client,
    output: &Output,
) -> Result<NativeEvent, RequestError> {
    let sse = request.sse;
    let method = reqwest::Method::from_bytes(request.method.as_bytes())?;
    let headers = forwarded_headers(request.headers)?;
    let mut builder = client.request(method, request.url).headers(headers);
    if let Some(timeout_ms) = request.timeout_ms {
        builder = builder.timeout(Duration::from_millis(timeout_ms));
    }
    if let Some(encoded_body) = request.body {
        builder = builder.body(base64::engine::general_purpose::STANDARD.decode(encoded_body)?);
    }

    let mut response = builder.send().await?;
    let status = response.status().as_u16();
    let response_headers = response
        .headers()
        .iter()
        .map(|(name, value)| {
            (
                name.as_str().to_owned(),
                String::from_utf8_lossy(value.as_bytes()).into_owned(),
            )
        })
        .collect();
    emit(
        output,
        &NativeEvent::Head {
            request_id: request.request_id.clone(),
            status,
            http_version: format!("{:?}", response.version()),
            headers: response_headers,
        },
    )
    .await?;

    if let Some(options) = sse.filter(|options| {
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .map(|value| String::from_utf8_lossy(value.as_bytes()))
            .unwrap_or_default();
        status < 400
            && (!options.content_type_aware
                || content_type.is_empty()
                || content_type.split(';').next().is_some_and(|media_type| {
                    media_type.trim().eq_ignore_ascii_case("text/event-stream")
                }))
    }) {
        if let Some(error) =
            execute_sse_body(&mut response, &request.request_id, options, output).await?
        {
            return Ok(NativeEvent::SseEventTooLarge {
                request_id: request.request_id,
                size_bytes: error.size_bytes,
                limit_bytes: error.limit_bytes,
            });
        }
    } else {
        while let Some(chunk) = response.chunk().await? {
            emit(
                output,
                &NativeEvent::Chunk {
                    request_id: request.request_id.clone(),
                    data: base64::engine::general_purpose::STANDARD.encode(chunk),
                },
            )
            .await?;
        }
    }
    Ok(NativeEvent::End {
        request_id: request.request_id,
    })
}

async fn execute_sse_body(
    response: &mut reqwest::Response,
    request_id: &str,
    options: NativeSseOptions,
    output: &Output,
) -> Result<Option<SseEventTooLarge>, RequestError> {
    let idle_timeout = Duration::from_millis(options.idle_timeout_ms);
    let mut framer = SseFramer::new(options.max_event_bytes);
    let mut collector = options.collect_compact.then(CompactCollector::default);
    let mut batch = EventBatch::default();
    loop {
        let chunk = tokio::time::timeout(idle_timeout, response.chunk())
            .await
            .map_err(|_| StreamIdleTimeout)??;
        let Some(chunk) = chunk else {
            break;
        };
        for read in chunk.chunks(SSE_READ_CHUNK_SIZE) {
            framer.push(read);
            loop {
                match framer.next_event() {
                    Ok(Some(text)) => {
                        if consume_sse(
                            output,
                            request_id,
                            &text,
                            &mut collector,
                            options.interpret_responses,
                            &mut batch,
                        )
                        .await?
                        {
                            batch.flush(output).await?;
                            return Ok(None);
                        }
                    }
                    Ok(None) => break,
                    Err(error) => {
                        batch.flush(output).await?;
                        return Ok(Some(error));
                    }
                }
            }
            batch.flush(output).await?;
        }
    }
    match framer.finish() {
        Ok(Some(text)) => {
            if consume_sse(
                output,
                request_id,
                &text,
                &mut collector,
                options.interpret_responses,
                &mut batch,
            )
            .await?
            {
                batch.flush(output).await?;
                return Ok(None);
            }
        }
        Ok(None) => {}
        Err(error) => return Ok(Some(error)),
    }
    batch.flush(output).await?;
    if let Some(collector) = collector {
        emit_compact(output, request_id, collector.finish()).await?;
    }
    Ok(None)
}

async fn consume_sse(
    output: &Output,
    request_id: &str,
    text: &str,
    collector: &mut Option<CompactCollector>,
    interpret_responses: bool,
    batch: &mut EventBatch,
) -> Result<bool, std::io::Error> {
    if let Some(collector) = collector {
        if let Some(result) = collector.push(text) {
            emit_compact(output, request_id, result).await?;
            return Ok(true);
        }
    } else if interpret_responses {
        let mut event = interpret(text);
        let stream_complete = event.completes_http_stream();
        // Type metadata is not fragmented; keep it within the text budget too.
        if event
            .event_type
            .as_ref()
            .is_some_and(|kind| kind.len() > SSE_IPC_TEXT_FRAGMENT_SIZE)
        {
            event.event_type = None;
            event.python_normalization = true;
        }
        for (fragment, more) in text_fragments(&event.text, SSE_IPC_TEXT_FRAGMENT_SIZE) {
            batch
                .push(
                    output,
                    &NativeEvent::ResponsesEvent {
                        request_id: request_id.to_owned(),
                        text: fragment.to_owned(),
                        more,
                        event_type: if more { None } else { event.event_type.clone() },
                        python_normalization: !more && event.python_normalization,
                        stream_complete: !more && stream_complete,
                    },
                )
                .await?;
        }
        return Ok(stream_complete);
    } else {
        emit_sse(output, request_id, text, batch).await?;
    }
    Ok(false)
}

async fn emit_compact(
    output: &Output,
    request_id: &str,
    result: CompactResult,
) -> Result<(), std::io::Error> {
    let text = serde_json::to_string(&result)?;
    for (fragment, more) in text_fragments(&text, SSE_IPC_TEXT_FRAGMENT_SIZE) {
        emit(
            output,
            &NativeEvent::Compact {
                request_id: request_id.to_owned(),
                text: fragment.to_owned(),
                more,
            },
        )
        .await?;
    }
    Ok(())
}

async fn emit_sse(
    output: &Output,
    request_id: &str,
    text: &str,
    batch: &mut EventBatch,
) -> Result<(), std::io::Error> {
    for (fragment, more) in text_fragments(text, SSE_IPC_TEXT_FRAGMENT_SIZE) {
        batch
            .push(
                output,
                &NativeEvent::Sse {
                    request_id: request_id.to_owned(),
                    text: fragment.to_owned(),
                    more,
                },
            )
            .await?;
    }
    Ok(())
}

fn forwarded_headers(request_headers: Vec<(String, String)>) -> Result<HeaderMap, RequestError> {
    let mut headers = HeaderMap::new();
    for (name, value) in request_headers {
        let name = HeaderName::from_bytes(name.as_bytes())?;
        headers.append(name, HeaderValue::from_str(&value)?);
    }
    Ok(headers)
}

pub(crate) fn classify_error(
    error: &(dyn std::error::Error + 'static),
) -> (&'static str, &'static str, bool, bool) {
    if error.downcast_ref::<StreamIdleTimeout>().is_some() {
        return (
            "native upstream SSE body read timed out",
            "stream_idle_timeout",
            false,
            false,
        );
    }
    let Some(request_error) = error.downcast_ref::<reqwest::Error>() else {
        return ("native helper rejected the request", "setup", false, false);
    };
    let tls_verification = error_chain_has_invalid_certificate(request_error);
    if request_error.is_connect() {
        return (
            "native upstream connection failed",
            "connect",
            !tls_verification,
            tls_verification,
        );
    }
    if request_error.is_timeout() {
        return (
            "native upstream request timed out",
            "timeout",
            false,
            tls_verification,
        );
    }
    if request_error.is_body() || request_error.is_decode() {
        return (
            "native upstream response body failed",
            "body_read",
            false,
            tls_verification,
        );
    }
    (
        "native upstream request failed",
        "request",
        false,
        tls_verification,
    )
}

pub(crate) fn error_chain_has_invalid_certificate(
    error: &(dyn std::error::Error + 'static),
) -> bool {
    let mut current = Some(error);
    while let Some(source) = current {
        if source
            .downcast_ref::<rustls::Error>()
            .is_some_and(|error| matches!(error, rustls::Error::InvalidCertificate(_)))
        {
            return true;
        }
        current = source.source();
    }
    false
}

#[cfg(test)]
mod tests {
    use reqwest::header::{ACCEPT, ACCEPT_ENCODING};

    use super::forwarded_headers;

    #[test]
    fn forwarded_headers_preserve_inbound_accept_encoding() {
        let headers = forwarded_headers(vec![
            ("accept".to_owned(), "application/json".to_owned()),
            ("accept-encoding".to_owned(), "br, zstd, gzip".to_owned()),
        ])
        .expect("valid forwarded headers");

        assert_eq!(
            headers.get(ACCEPT).and_then(|value| value.to_str().ok()),
            Some("application/json")
        );
        assert_eq!(
            headers
                .get(ACCEPT_ENCODING)
                .and_then(|value| value.to_str().ok()),
            Some("br, zstd, gzip")
        );
    }
}
