use std::process::Stdio;
use std::time::Duration;

use futures_util::StreamExt;
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::net::TcpListener;
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::oneshot;
use tokio_tungstenite::accept_async_with_config;
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tungstenite::extensions::ExtensionsConfig;
use tungstenite::extensions::compression::deflate::DeflateConfig;

type HelperLines = Lines<BufReader<ChildStdout>>;

async fn start_helper() -> (Child, ChildStdin, HelperLines) {
    let mut helper = Command::new(env!("CARGO_BIN_EXE_codex-lb-native-egress"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .expect("spawn native helper");
    let mut stdin = helper.stdin.take().expect("helper stdin");
    let stdout = helper.stdout.take().expect("helper stdout");
    let mut lines = BufReader::new(stdout).lines();
    write_command(
        &mut stdin,
        &json!({
            "type": "client_hello",
            "min_protocol_version": 1,
            "max_protocol_version": 1
        }),
    )
    .await;
    let ready = read_event(&mut lines, "handshake timeout").await;
    assert_eq!(ready["type"], "server_hello");
    assert_eq!(ready["protocol_version"], 1);
    (helper, stdin, lines)
}

async fn write_command(stdin: &mut ChildStdin, command: &Value) {
    stdin
        .write_all(format!("{command}\n").as_bytes())
        .await
        .expect("send native helper command");
}

async fn read_event(lines: &mut HelperLines, timeout_message: &str) -> Value {
    serde_json::from_str(
        &tokio::time::timeout(Duration::from_secs(2), lines.next_line())
            .await
            .expect(timeout_message)
            .expect("read native helper event")
            .expect("native helper event line"),
    )
    .expect("decode native helper event")
}

#[tokio::test]
async fn missing_pong_emits_liveness_timeout() {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind websocket server");
    let address = listener.local_addr().expect("server address");
    let (release_server, wait_for_liveness_failure) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.expect("accept websocket client");
        let mut extensions = ExtensionsConfig::default();
        extensions.permessage_deflate = Some(DeflateConfig::default());
        let mut config = WebSocketConfig::default();
        config.extensions = extensions;
        let _websocket = accept_async_with_config(stream, Some(config))
            .await
            .expect("accept websocket handshake");
        // Do not poll the server stream: tungstenite therefore cannot observe
        // or automatically answer the helper's ping.
        wait_for_liveness_failure
            .await
            .expect("liveness assertion must release websocket server");
    });

    let (mut helper, mut stdin, mut lines) = start_helper().await;

    let connect = json!({
        "type": "websocket_connect",
        "request_id": "liveness-test",
        "url": format!("ws://{address}/v1/responses"),
        "headers": [],
        "connect_timeout_ms": 2_000,
        "max_message_bytes": 1_024,
        "ping_interval_ms": 20,
        "ping_timeout_ms": 40,
        "proxy_url": null
    });
    write_command(&mut stdin, &connect).await;

    let open = read_event(&mut lines, "open event timeout").await;
    assert_eq!(open["type"], "websocket_open");

    let failure = read_event(&mut lines, "liveness event timeout").await;
    assert_eq!(failure["type"], "websocket_error");
    assert_eq!(failure["failure_phase"], "liveness_timeout");
    assert_eq!(failure["retryable_same_contract"], false);

    release_server
        .send(())
        .expect("release websocket server after liveness failure");
    drop(stdin);
    tokio::time::timeout(Duration::from_secs(2), helper.wait())
        .await
        .expect("helper exit timeout")
        .expect("wait for helper");
    server.await.expect("websocket server task");
}

#[tokio::test]
async fn explicit_cancel_aborts_websocket_and_emits_one_cancelled_event() {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind websocket server");
    let address = listener.local_addr().expect("server address");
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.expect("accept websocket client");
        let mut extensions = ExtensionsConfig::default();
        extensions.permessage_deflate = Some(DeflateConfig::default());
        let mut config = WebSocketConfig::default();
        config.extensions = extensions;
        let mut websocket = accept_async_with_config(stream, Some(config))
            .await
            .expect("accept websocket handshake");
        let closure = tokio::time::timeout(Duration::from_secs(2), websocket.next())
            .await
            .expect("cancelled helper must tear down websocket promptly");
        assert!(
            closure.is_none() || closure.is_some_and(|result| result.is_err()),
            "aborted websocket must close instead of delivering another frame"
        );
    });

    let (mut helper, mut stdin, mut lines) = start_helper().await;

    let connect = json!({
        "type": "websocket_connect",
        "request_id": "cancel-test",
        "url": format!("ws://{address}/v1/responses"),
        "headers": [],
        "connect_timeout_ms": 2_000,
        "max_message_bytes": 1_024,
        "ping_interval_ms": 20_000,
        "ping_timeout_ms": 120_000,
        "proxy_url": null
    });
    write_command(&mut stdin, &connect).await;
    let open = read_event(&mut lines, "open event timeout").await;
    assert_eq!(open["type"], "websocket_open");

    let cancel = json!({"type": "cancel", "request_id": "cancel-test"});
    write_command(&mut stdin, &cancel).await;
    let cancelled = read_event(&mut lines, "cancel event timeout").await;
    assert_eq!(cancelled["type"], "cancelled");
    assert_eq!(cancelled["request_id"], "cancel-test");

    server.await.expect("websocket server task");
    drop(stdin);
    tokio::time::timeout(Duration::from_secs(2), helper.wait())
        .await
        .expect("helper exit timeout")
        .expect("wait for helper");
    let mut remaining_events = Vec::new();
    while let Some(event) = lines
        .next_line()
        .await
        .expect("drain native helper events after exit")
    {
        remaining_events.push(event);
    }
    assert!(
        remaining_events.is_empty(),
        "explicit cancellation must emit exactly one terminal event: {remaining_events:?}"
    );
}
