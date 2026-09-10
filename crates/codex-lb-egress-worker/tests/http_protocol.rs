use std::process::Stdio;
use std::time::Duration;

use base64::Engine as _;
use codex_lb_protocol::{
    NativeCommand, NativeEvent, NativeRequest, NativeSseOptions, PROTOCOL_VERSION,
};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::net::{TcpListener, TcpStream};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};

type HelperLines = Lines<BufReader<ChildStdout>>;

const REQUEST_ID: &str = "gzip-relay";
const ENCODED_SENTINEL: [u8; 40] = [
    31, 139, 8, 0, 0, 0, 0, 0, 2, 255, 203, 75, 44, 201, 44, 75, 213, 77, 175, 202, 44, 208, 45,
    78, 205, 43, 201, 204, 75, 205, 1, 0, 124, 79, 131, 92, 20, 0, 0, 0,
];
const SENTINEL: &[u8] = b"native-gzip-sentinel";

async fn start_helper() -> (Child, ChildStdin, HelperLines) {
    let mut helper = Command::new(env!("CARGO_BIN_EXE_codex-lb-native-egress"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .expect("spawn native helper");
    let mut stdin = helper.stdin.take().expect("helper stdin");
    let stdout = helper.stdout.take().expect("helper stdout");
    let mut lines = BufReader::new(stdout).lines();

    write_command(
        &mut stdin,
        &NativeCommand::ClientHello {
            min_protocol_version: PROTOCOL_VERSION,
            max_protocol_version: PROTOCOL_VERSION,
        },
    )
    .await;

    match read_event(&mut lines, "handshake timeout").await {
        NativeEvent::ServerHello {
            protocol_version, ..
        } => assert_eq!(protocol_version, PROTOCOL_VERSION),
        _ => panic!("native helper must emit server_hello first"),
    }

    (helper, stdin, lines)
}

async fn write_command(stdin: &mut ChildStdin, command: &NativeCommand) {
    let mut line = serde_json::to_vec(command).expect("encode native helper command");
    line.push(b'\n');
    stdin
        .write_all(&line)
        .await
        .expect("send native helper command");
}

async fn read_event(lines: &mut HelperLines, timeout_message: &str) -> NativeEvent {
    let line = tokio::time::timeout(Duration::from_secs(5), lines.next_line())
        .await
        .expect(timeout_message)
        .expect("read native helper event")
        .expect("native helper event line");
    serde_json::from_str(&line).expect("decode native helper event")
}

async fn read_request_headers(stream: &mut TcpStream) -> String {
    let mut request = Vec::with_capacity(1024);
    while !request.ends_with(b"\r\n\r\n") {
        let read = stream
            .read_buf(&mut request)
            .await
            .expect("read request headers");
        assert_ne!(read, 0, "request ended before headers completed");
        assert!(
            request.len() <= 8 * 1024,
            "request headers exceeded test bound"
        );
    }

    String::from_utf8(request).expect("ASCII request headers")
}

fn sse_request(
    request_id: &str,
    url: String,
    idle_timeout_ms: u64,
    max_event_bytes: usize,
) -> NativeCommand {
    NativeCommand::Request(NativeRequest {
        request_id: request_id.to_owned(),
        method: "GET".to_owned(),
        url,
        headers: vec![("accept".to_owned(), "text/event-stream".to_owned())],
        body: None,
        timeout_ms: Some(2_000),
        connect_timeout_ms: Some(2_000),
        proxy_url: None,
        sse: Some(NativeSseOptions {
            idle_timeout_ms,
            max_event_bytes,
            content_type_aware: false,
            collect_compact: false,
            interpret_responses: false,
        }),
    })
}

async fn stop_helper(mut helper: Child, stdin: ChildStdin, mut lines: HelperLines) {
    drop(stdin);
    let exit = tokio::time::timeout(Duration::from_secs(2), helper.wait())
        .await
        .expect("helper exit timeout")
        .expect("wait for helper");
    assert!(exit.success(), "native helper must exit cleanly");
    let extra = lines.next_line().await.expect("drain helper output");
    assert!(
        extra.is_none(),
        "duplicate terminal after stdin EOF: {extra:?}"
    );
}

fn header_values(request: &str, expected_name: &str) -> Vec<String> {
    request
        .lines()
        .filter_map(|line| line.split_once(':'))
        .filter(|(name, _)| name.eq_ignore_ascii_case(expected_name))
        .map(|(_, value)| value.trim().to_owned())
        .collect()
}

#[tokio::test]
async fn gzip_response_relay_crosses_native_helper_boundary() {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind gzip origin");
    let address = listener.local_addr().expect("gzip origin address");
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept native helper");
        let request = read_request_headers(&mut stream).await;
        let accept_encodings = header_values(&request, "accept-encoding");

        stream
            .write_all(
                b"HTTP/1.1 200 OK\r\n\
                  Content-Type: text/plain\r\n\
                  Content-Encoding: gzip\r\n\
                  Content-Length: 40\r\n\
                  Connection: close\r\n\
                  \r\n",
            )
            .await
            .expect("write response headers");
        stream
            .write_all(&ENCODED_SENTINEL)
            .await
            .expect("write gzip entity");
        stream.shutdown().await.expect("close gzip origin");

        accept_encodings
    });

    let (mut helper, mut stdin, mut lines) = start_helper().await;
    write_command(
        &mut stdin,
        &NativeCommand::Request(NativeRequest {
            request_id: REQUEST_ID.to_owned(),
            method: "GET".to_owned(),
            url: format!("http://{address}/response"),
            headers: vec![("accept-encoding".to_owned(), "br, zstd, gzip".to_owned())],
            body: None,
            timeout_ms: Some(2_000),
            connect_timeout_ms: Some(2_000),
            proxy_url: None,
            sse: None,
        }),
    )
    .await;

    let mut events = Vec::new();
    loop {
        let event = read_event(&mut lines, "gzip relay event timeout").await;
        let terminal = matches!(&event, NativeEvent::End { .. } | NativeEvent::Error { .. });
        events.push(event);
        if terminal {
            break;
        }
    }

    let accept_encodings = tokio::time::timeout(Duration::from_secs(2), server)
        .await
        .expect("gzip origin task timeout")
        .expect("gzip origin task");
    drop(stdin);
    let exit = tokio::time::timeout(Duration::from_secs(2), helper.wait())
        .await
        .expect("helper exit timeout")
        .expect("wait for helper");
    assert!(exit.success(), "native helper must exit cleanly");

    let mut head: Option<(u16, Vec<(String, String)>)> = None;
    let mut body = Vec::new();
    let mut saw_end = false;
    for event in events {
        match event {
            NativeEvent::Head {
                request_id,
                status,
                headers,
                ..
            } => {
                assert_eq!(request_id, REQUEST_ID);
                assert!(head.is_none(), "native helper must emit one head event");
                head = Some((status, headers));
            }
            NativeEvent::Chunk { request_id, data } => {
                assert_eq!(request_id, REQUEST_ID);
                assert!(head.is_some(), "chunk must follow head");
                body.extend(
                    base64::engine::general_purpose::STANDARD
                        .decode(data)
                        .expect("decode native helper chunk"),
                );
            }
            NativeEvent::End { request_id } => {
                assert_eq!(request_id, REQUEST_ID);
                assert!(head.is_some(), "end must follow head");
                assert!(!saw_end, "native helper must emit one end event");
                saw_end = true;
            }
            NativeEvent::Error { message, .. } => {
                panic!("native helper request failed: {message}");
            }
            _ => panic!("unexpected native helper event"),
        }
    }

    let (status, headers) = head.expect("native helper head event");
    assert!(saw_end, "native helper end event");
    assert_eq!(status, 200);
    assert_eq!(accept_encodings, vec!["br, zstd, gzip".to_owned()]);
    assert_eq!(body, SENTINEL);
    assert!(
        !headers
            .iter()
            .any(|(name, _)| name.eq_ignore_ascii_case("content-encoding"))
    );
    assert!(
        !headers
            .iter()
            .any(|(name, _)| name.eq_ignore_ascii_case("content-length"))
    );
}

#[tokio::test]
async fn request_without_accept_encoding_reaches_origin_without_accept_encoding() {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind origin");
    let address = listener.local_addr().expect("origin address");
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept native helper");
        let request = read_request_headers(&mut stream).await;
        stream
            .write_all(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
            .await
            .expect("write response");
        stream.shutdown().await.expect("close origin");

        header_values(&request, "accept-encoding")
    });

    let (mut helper, mut stdin, mut lines) = start_helper().await;
    write_command(
        &mut stdin,
        &NativeCommand::Request(NativeRequest {
            request_id: "no-accept-encoding".to_owned(),
            method: "GET".to_owned(),
            url: format!("http://{address}/response"),
            headers: vec![("accept".to_owned(), "application/json".to_owned())],
            body: None,
            timeout_ms: Some(2_000),
            connect_timeout_ms: Some(2_000),
            proxy_url: None,
            sse: None,
        }),
    )
    .await;

    loop {
        match read_event(&mut lines, "request event timeout").await {
            NativeEvent::End { request_id } => {
                assert_eq!(request_id, "no-accept-encoding");
                break;
            }
            NativeEvent::Error { message, .. } => {
                panic!("native helper request failed: {message}");
            }
            _ => {}
        }
    }

    let accept_encodings = tokio::time::timeout(Duration::from_secs(2), server)
        .await
        .expect("origin task timeout")
        .expect("origin task");
    assert!(
        accept_encodings.is_empty(),
        "native helper must not synthesize Accept-Encoding"
    );

    drop(stdin);
    let exit = tokio::time::timeout(Duration::from_secs(2), helper.wait())
        .await
        .expect("helper exit timeout")
        .expect("wait for helper");
    assert!(exit.success(), "native helper must exit cleanly");
}

#[tokio::test]
async fn successful_sse_response_emits_utf8_safe_fragments_and_end() {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind origin");
    let address = listener.local_addr().expect("origin address");
    let body = format!("data: {}\n\n", "é".repeat(20_000)).into_bytes();
    let expected_body = body.clone();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept native helper");
        read_request_headers(&mut stream).await;
        stream
            .write_all(
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .as_bytes(),
            )
            .await
            .expect("write response head");
        for chunk in body.chunks(7_000) {
            stream.write_all(chunk).await.expect("write SSE chunk");
            tokio::task::yield_now().await;
        }
        stream.shutdown().await.expect("close origin");
    });

    let (helper, mut stdin, mut lines) = start_helper().await;
    write_command(
        &mut stdin,
        &sse_request(
            "framed-sse",
            format!("http://{address}/response"),
            1_000,
            64 * 1024,
        ),
    )
    .await;

    let mut reconstructed = String::new();
    let mut fragment_count = 0;
    loop {
        match read_event(&mut lines, "SSE event timeout").await {
            NativeEvent::Head {
                request_id, status, ..
            } => {
                assert_eq!(request_id, "framed-sse");
                assert_eq!(status, 200);
            }
            NativeEvent::Sse {
                request_id,
                text,
                more,
            } => {
                assert_eq!(request_id, "framed-sse");
                assert!(text.len() <= 16 * 1024);
                assert_eq!(more, reconstructed.len() + text.len() < expected_body.len());
                reconstructed.push_str(&text);
                fragment_count += 1;
            }
            NativeEvent::End { request_id } => {
                assert_eq!(request_id, "framed-sse");
                break;
            }
            NativeEvent::Chunk { .. } => panic!("framed SSE must not emit raw chunks"),
            NativeEvent::Error { message, .. } => panic!("native helper request failed: {message}"),
            event => panic!(
                "unexpected native helper event: {}",
                serde_json::to_string(&event).unwrap()
            ),
        }
    }

    assert!(fragment_count > 1);
    assert_eq!(reconstructed.as_bytes(), expected_body);
    server.await.expect("origin task");
    stop_helper(helper, stdin, lines).await;
}

#[tokio::test]
async fn oversized_sse_event_is_typed_terminal_after_valid_prefix() {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind origin");
    let address = listener.local_addr().expect("origin address");
    let body = b"data: ok\n\ndata: 1234\n\n";
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept native helper");
        read_request_headers(&mut stream).await;
        stream
            .write_all(
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .as_bytes(),
            )
            .await
            .expect("write response head");
        stream.write_all(body).await.expect("write response body");
        stream.shutdown().await.expect("close origin");
    });

    let (helper, mut stdin, mut lines) = start_helper().await;
    write_command(
        &mut stdin,
        &sse_request(
            "oversized-sse",
            format!("http://{address}/response"),
            1_000,
            10,
        ),
    )
    .await;

    let mut events = Vec::new();
    loop {
        let event = read_event(&mut lines, "oversized SSE event timeout").await;
        let terminal = matches!(
            event,
            NativeEvent::SseEventTooLarge { .. } | NativeEvent::Error { .. }
        );
        events.push(event);
        if terminal {
            break;
        }
    }
    assert!(matches!(events[0], NativeEvent::Head { status: 200, .. }));
    assert!(matches!(
        &events[1],
        NativeEvent::Sse { text, more: false, .. } if text == "data: ok\n\n"
    ));
    assert!(matches!(
        events[2],
        NativeEvent::SseEventTooLarge {
            size_bytes: 12,
            limit_bytes: 10,
            ..
        }
    ));

    server.await.expect("origin task");
    stop_helper(helper, stdin, lines).await;
}

#[tokio::test]
async fn sse_body_idle_timeout_has_distinct_failure_phase() {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind origin");
    let address = listener.local_addr().expect("origin address");
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept native helper");
        read_request_headers(&mut stream).await;
        stream
            .write_all(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 1\r\nConnection: close\r\n\r\n",
            )
            .await
            .expect("write response head");
        tokio::time::sleep(Duration::from_millis(250)).await;
        let _ = stream.write_all(b"x").await;
    });

    let (helper, mut stdin, mut lines) = start_helper().await;
    write_command(
        &mut stdin,
        &sse_request("idle-sse", format!("http://{address}/response"), 40, 1024),
    )
    .await;
    assert!(matches!(
        read_event(&mut lines, "SSE head timeout").await,
        NativeEvent::Head { status: 200, .. }
    ));
    assert!(matches!(
        read_event(&mut lines, "SSE idle event timeout").await,
        NativeEvent::Error {
            request_id,
            failure_phase,
            retryable_same_contract: false,
            ..
        } if request_id == "idle-sse" && failure_phase == "stream_idle_timeout"
    ));

    stop_helper(helper, stdin, lines).await;
    server.await.expect("origin task");
}

#[tokio::test]
async fn http_error_with_sse_options_preserves_raw_body_chunks() {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind origin");
    let address = listener.local_addr().expect("origin address");
    let body = b"{\"error\":{\"code\":\"rate_limit_exceeded\"}}";
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept native helper");
        read_request_headers(&mut stream).await;
        stream
            .write_all(
                format!(
                    "HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .as_bytes(),
            )
            .await
            .expect("write response head");
        stream.write_all(body).await.expect("write response body");
        stream.shutdown().await.expect("close origin");
    });

    let (helper, mut stdin, mut lines) = start_helper().await;
    write_command(
        &mut stdin,
        &sse_request(
            "error-body",
            format!("http://{address}/response"),
            1_000,
            1024,
        ),
    )
    .await;
    let mut received = Vec::new();
    loop {
        match read_event(&mut lines, "HTTP error event timeout").await {
            NativeEvent::Head { status, .. } => assert_eq!(status, 429),
            NativeEvent::Chunk { data, .. } => received.extend(
                base64::engine::general_purpose::STANDARD
                    .decode(data)
                    .expect("decode raw error chunk"),
            ),
            NativeEvent::End { .. } => break,
            NativeEvent::Sse { .. } => panic!("HTTP errors must not use SSE framing"),
            NativeEvent::Error { message, .. } => panic!("native helper request failed: {message}"),
            _ => {}
        }
    }
    assert_eq!(received, body);

    server.await.expect("origin task");
    stop_helper(helper, stdin, lines).await;
}
