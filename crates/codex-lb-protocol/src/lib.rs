//! Versioned local IPC contract between the Python control plane and Rust egress worker.
//!
//! This crate intentionally has no async runtime or networking dependencies. Wire
//! compatibility can therefore be tested independently from either implementation.

use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: u16 = 1;
pub const CAPABILITIES: &[&str] = &[
    "failure_provenance_v1",
    "http",
    "http2_profile_v1",
    "websocket",
    "websocket_send_ack",
];

#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum NativeCommand {
    ClientHello {
        min_protocol_version: u16,
        max_protocol_version: u16,
    },
    Request(NativeRequest),
    WebsocketConnect(NativeWebSocketRequest),
    WebsocketSendText {
        request_id: String,
        command_id: String,
        text: String,
    },
    WebsocketSendBinary {
        request_id: String,
        command_id: String,
        data: String,
    },
    WebsocketClose {
        request_id: String,
        command_id: String,
        code: u16,
        reason: String,
    },
    Cancel {
        request_id: String,
    },
}

#[derive(Deserialize, Serialize)]
pub struct NativeRequest {
    pub request_id: String,
    pub method: String,
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub body: Option<String>,
    pub timeout_ms: u64,
    pub connect_timeout_ms: Option<u64>,
    pub proxy_url: Option<String>,
}

#[derive(Deserialize, Serialize)]
pub struct NativeWebSocketRequest {
    pub request_id: String,
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub connect_timeout_ms: u64,
    pub max_message_bytes: usize,
    pub ping_interval_ms: Option<u64>,
    pub ping_timeout_ms: Option<u64>,
    pub proxy_url: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum NativeEvent {
    ServerHello {
        protocol_version: u16,
        capabilities: Vec<String>,
    },
    Head {
        request_id: String,
        status: u16,
        http_version: String,
        headers: Vec<(String, String)>,
    },
    Chunk {
        request_id: String,
        data: String,
    },
    End {
        request_id: String,
    },
    WebsocketOpen {
        request_id: String,
        status: u16,
        headers: Vec<(String, String)>,
    },
    WebsocketText {
        request_id: String,
        text: String,
    },
    WebsocketBinary {
        request_id: String,
        data: String,
    },
    WebsocketSent {
        request_id: String,
        command_id: String,
    },
    WebsocketClose {
        request_id: String,
        code: Option<u16>,
        reason: Option<String>,
    },
    WebsocketError {
        request_id: String,
        command_id: Option<String>,
        message: String,
        failure_phase: String,
        retryable_same_contract: bool,
        is_tls_verification_failure: bool,
        status: Option<u16>,
        headers: Vec<(String, String)>,
        body: Option<String>,
    },
    Cancelled {
        request_id: String,
    },
    Error {
        request_id: String,
        message: String,
        failure_phase: String,
        retryable_same_contract: bool,
        is_tls_verification_failure: bool,
    },
}

#[cfg(test)]
mod tests {
    use super::{CAPABILITIES, NativeCommand, NativeEvent, PROTOCOL_VERSION};

    #[test]
    fn hello_contract_is_stable() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/handshake-v1.json"))
                .expect("parse shared handshake fixture");
        let command = NativeCommand::ClientHello {
            min_protocol_version: PROTOCOL_VERSION,
            max_protocol_version: PROTOCOL_VERSION,
        };
        assert_eq!(
            serde_json::to_value(command).expect("serialize client hello"),
            fixture["client_hello"],
        );

        let event = NativeEvent::ServerHello {
            protocol_version: PROTOCOL_VERSION,
            capabilities: CAPABILITIES
                .iter()
                .map(|value| (*value).to_owned())
                .collect(),
        };
        assert_eq!(
            serde_json::to_value(event).expect("serialize server hello"),
            fixture["server_hello"],
        );
    }
}
