use std::process::Stdio;
use std::time::Duration;

use base64::Engine as _;
use codex_lb_protocol::{NativeCommand, NativeEvent, NativeRequest, PROTOCOL_VERSION};
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
            timeout_ms: 2_000,
            connect_timeout_ms: Some(2_000),
            proxy_url: None,
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
            timeout_ms: 2_000,
            connect_timeout_ms: Some(2_000),
            proxy_url: None,
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
