use std::time::Duration;

use reqwest::header::{CONTENT_ENCODING, CONTENT_LENGTH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::time::timeout;

const ENCODED_SENTINEL: [u8; 40] = [
    31, 139, 8, 0, 0, 0, 0, 0, 2, 255, 203, 75, 44, 201, 44, 75, 213, 77, 175, 202, 44, 208, 45,
    78, 205, 43, 201, 204, 75, 205, 1, 0, 124, 79, 131, 92, 20, 0, 0, 0,
];
const SENTINEL: &[u8] = b"native-gzip-sentinel";

#[tokio::test]
async fn gzip_response_relay() {
    // Given: a local origin returns a gzip entity to a client that advertises gzip.
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .expect("install test crypto provider");
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind gzip origin");
    let address = listener.local_addr().expect("gzip origin address");
    let server = async move {
        let (mut stream, _) = listener.accept().await.expect("accept native client");
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
        let request = String::from_utf8(request).expect("ASCII request headers");
        let request = request.to_ascii_lowercase();
        assert!(
            request.contains("accept-encoding: gzip"),
            "reqwest must advertise the decoder compiled into native egress"
        );
        assert!(!request.contains("accept-encoding: br"));
        assert!(!request.contains("accept-encoding: zstd"));

        stream
            .write_all(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Encoding: gzip\r\nContent-Length: 40\r\nConnection: close\r\n\r\n",
            )
            .await
            .expect("write response headers");
        stream
            .write_all(&ENCODED_SENTINEL)
            .await
            .expect("write gzip entity");
        stream.shutdown().await.expect("close gzip origin");
    };
    let client = async {
        reqwest::Client::builder()
            .build()
            .expect("build native HTTP client")
            .get(format!("http://{address}/response"))
            .send()
            .await
            .expect("request gzip response")
    };

    // When: the response crosses reqwest's streaming response boundary.
    let (_, response) = timeout(Duration::from_secs(5), async {
        tokio::join!(server, client)
    })
    .await
    .expect("gzip relay completed before timeout");
    let has_content_encoding = response.headers().contains_key(CONTENT_ENCODING);
    let has_content_length = response.headers().contains_key(CONTENT_LENGTH);
    let body = response.bytes().await.expect("read relayed body");

    // Then: body bytes and headers both describe the decoded representation.
    assert_eq!(body.as_ref(), SENTINEL);
    assert!(!has_content_encoding);
    assert!(!has_content_length);
}
