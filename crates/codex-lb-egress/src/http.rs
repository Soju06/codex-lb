use std::collections::HashMap;
use std::time::Duration;

use base64::Engine as _;
use codex_lb_protocol::{NativeEvent, NativeRequest};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};

use crate::runtime::{Output, RequestError, emit};

pub(crate) const CODEX_H2_INITIAL_STREAM_WINDOW_SIZE: u32 = 2 * 1024 * 1024;
pub(crate) const CODEX_H2_INITIAL_CONNECTION_WINDOW_SIZE: u32 = 5 * 1024 * 1024;
pub(crate) const CODEX_H2_MAX_FRAME_SIZE: u32 = 16 * 1024;
pub(crate) const CODEX_H2_MAX_HEADER_LIST_SIZE: u32 = 16 * 1024;

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
) -> Result<(), RequestError> {
    let method = reqwest::Method::from_bytes(request.method.as_bytes())?;
    let headers = forwarded_headers(request.headers)?;
    let mut builder = client
        .request(method, request.url)
        .headers(headers)
        .timeout(Duration::from_millis(request.timeout_ms));
    if let Some(encoded_body) = request.body {
        builder = builder.body(base64::engine::general_purpose::STANDARD.decode(encoded_body)?);
    }

    let mut response = builder.send().await?;
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
            status: response.status().as_u16(),
            http_version: format!("{:?}", response.version()),
            headers: response_headers,
        },
    )
    .await?;

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
    emit(
        output,
        &NativeEvent::End {
            request_id: request.request_id,
        },
    )
    .await?;
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
