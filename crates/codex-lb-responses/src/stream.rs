//! Interpret framed HTTP Responses events without owning request policy.

use std::borrow::Cow;
use std::collections::BTreeMap;
use std::fmt::Write;

use serde::Deserialize;
use serde_json::Value;
use serde_json::value::RawValue;

const ALIASES: [(&str, &str); 3] = [
    ("response.text.delta", "response.output_text.delta"),
    ("response.audio.delta", "response.output_audio.delta"),
    (
        "response.audio_transcript.delta",
        "response.output_audio_transcript.delta",
    ),
];

/// Interpreted text plus a handoff for Python's context-dependent normalization.
#[derive(Debug)]
pub struct StreamEvent<'a> {
    pub text: Cow<'a, str>,
    pub event_type: Option<String>,
    pub python_normalization: bool,
}

impl StreamEvent<'_> {
    /// These response terminals end both SDK and native HTTP streams.
    /// Bare errors still require the caller's normalization policy.
    pub fn completes_http_stream(&self) -> bool {
        matches!(
            self.event_type.as_deref(),
            Some("response.completed" | "response.failed" | "response.incomplete")
        )
    }
}

#[derive(Deserialize)]
struct Payload<'a> {
    #[serde(default, borrow, rename = "type")]
    kind: Option<&'a RawValue>,
    #[serde(default, borrow)]
    error: Option<&'a RawValue>,
}

/// Preserve Python's alias and canonical-frame classification, including its
/// fast path. Error conversion still needs Python's request context.
pub fn interpret(block: &str) -> StreamEvent<'_> {
    let Ok(text) = normalize_aliases(block) else {
        return StreamEvent {
            text: Cow::Borrowed(block),
            event_type: None,
            python_normalization: true,
        };
    };
    // Mirror Python's literal-key fast path, including escaped error keys.
    // Noncanonical frames still decode keys before selecting the error handoff.
    if let Some(kind) = canonical_type(&text)
        && kind != "error"
        && alias(kind).is_none()
        && !text.contains("\"error\"")
    {
        let event_type = Some(kind.to_owned());
        return StreamEvent {
            text,
            event_type,
            python_normalization: false,
        };
    }
    let data = data_text(&text);
    let payload = parse_payload(&data);
    let event_type = payload
        .as_ref()
        .and_then(|p| p.kind)
        .and_then(|kind| serde_json::from_str::<String>(kind.get()).ok());
    let python_normalization = payload.is_none()
        || (event_type.is_none()
            && payload
                .as_ref()
                .and_then(|p| p.kind)
                .is_some_and(|kind| kind.get().starts_with('"')))
        || event_type.as_deref() == Some("error")
        || payload
            .as_ref()
            .and_then(|p| p.error)
            .is_some_and(|error| error.get().starts_with('{'))
        || event_type
            .as_deref()
            .is_some_and(|kind| alias(kind).is_some());
    StreamEvent {
        text,
        event_type,
        python_normalization,
    }
}

/// The raw object is embedded in IPC, so Python's IPC decoder supplies the
/// policy payload without another JSON parse or any numeric conversion here.
pub struct WebSocketEvent {
    pub payload: Box<RawValue>,
    pub event_type: Option<String>,
    /// Payload-only precedence; validated lifecycle IDs remain caller policy.
    pub payload_response_id: Option<String>,
    pub sequence_number: Option<Box<RawValue>>,
}

/// Match Python's WebSocket classification: a string type wins, otherwise an
/// object error classifies as "error". WebSocket relay preserves aliases and
/// original text; HTTP SSE alias rewriting remains a separate boundary.
pub fn interpret_websocket(text: &str) -> Option<WebSocketEvent> {
    // IPC lines are capped at 24 MiB. Metadata duplicates the payload and may
    // expand text/type escaping; larger frames retain the existing opaque path.
    const MAX_INTERPRETED_BYTES: usize = 1024 * 1024;
    if text.len() > MAX_INTERPRETED_BYTES || !text.trim_start().starts_with('{') {
        return None;
    }
    // A map preserves Python's last-key precedence, including escaped keys.
    // Raw values preserve large ints, floats, and escaped surrogate values.
    let fields: BTreeMap<String, &RawValue> = serde_json::from_str(text).ok()?;
    let event_type = match fields.get("type") {
        Some(kind) if kind.get().starts_with('"') => {
            Some(serde_json::from_str::<String>(kind.get()).ok()?)
        }
        _ if fields
            .get("error")
            .is_some_and(|error| error.get().starts_with('{')) =>
        {
            Some("error".to_owned())
        }
        _ => None,
    };
    Some(WebSocketEvent {
        payload_response_id: payload_response_id(&fields).ok()?,
        sequence_number: fields.get("sequence_number").and_then(|value| {
            let token = value.get();
            let digits = token.strip_prefix('-').unwrap_or(token);
            (!digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit()))
                .then(|| (*value).to_owned())
        }),
        payload: RawValue::from_string(compact_json_whitespace(text)?).ok()?,
        event_type,
    })
}

fn payload_response_id(fields: &BTreeMap<String, &RawValue>) -> Result<Option<String>, ()> {
    fn stripped_id(value: Option<&&RawValue>) -> Result<Option<String>, ()> {
        let Some(value) = value.filter(|value| value.get().starts_with('"')) else {
            return Ok(None);
        };
        let value: String = serde_json::from_str(value.get()).map_err(|_| ())?;
        // Python str.strip also treats these four information separators as whitespace.
        let value = value
            .trim_matches(|ch: char| ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch));
        Ok((!value.is_empty()).then(|| value.to_owned()))
    }
    if let Some(id) = stripped_id(fields.get("response_id"))? {
        return Ok(Some(id));
    }
    let Some(response) = fields
        .get("response")
        .filter(|value| value.get().starts_with('{'))
    else {
        return Ok(None);
    };
    let response: BTreeMap<String, &RawValue> =
        serde_json::from_str(response.get()).map_err(|_| ())?;
    stripped_id(response.get("id"))
}

// RawValue emits bytes verbatim. Strip only JSON whitespace outside strings so
// a pretty-printed object cannot split the newline-delimited IPC record. Numeric
// tokens, duplicate keys, string escapes and all string content stay untouched.
// Keep integers beyond Python's minimum configurable digit limit opaque: any
// such token (including nested or overwritten values) could fail the shared IPC
// decoder before Python can attribute the failure to an individual exchange.
fn compact_json_whitespace(text: &str) -> Option<String> {
    const MAX_PYTHON_INTEGER_DIGITS: usize = 640;
    let mut result = String::with_capacity(text.len());
    let mut in_string = false;
    let mut escaped = false;
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if in_string {
            result.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
        } else if ch == '"' {
            in_string = true;
            result.push(ch);
        } else if ch == '-' || ch.is_ascii_digit() {
            let start = result.len();
            result.push(ch);
            while let Some(next) =
                chars.next_if(|next| matches!(next, '0'..='9' | '.' | 'e' | 'E' | '+' | '-'))
            {
                result.push(next);
            }
            let token = &result[start..];
            if !token.contains(['.', 'e', 'E'])
                && token.strip_prefix('-').unwrap_or(token).len() > MAX_PYTHON_INTEGER_DIGITS
            {
                return None;
            }
        } else if !matches!(ch, ' ' | '\t' | '\r' | '\n') {
            result.push(ch);
        }
    }
    Some(result)
}

fn alias(kind: &str) -> Option<&'static str> {
    ALIASES
        .iter()
        .find_map(|(old, new)| (*old == kind).then_some(*new))
}

fn parse_payload(data: &str) -> Option<Payload<'_>> {
    // Serde also deserializes structs from arrays; Python accepts objects only.
    data.trim_start()
        .starts_with('{')
        .then(|| serde_json::from_str(data).ok())
        .flatten()
}

fn lines(block: &str) -> Vec<&str> {
    // CRLF is one boundary. Other Unicode separators are ordinary content.
    let mut result = Vec::new();
    let bytes = block.as_bytes();
    let mut start = 0;
    let mut i = 0;
    while i < bytes.len() {
        if matches!(bytes[i], b'\r' | b'\n') {
            result.push(&block[start..i]);
            if bytes[i] == b'\r' && bytes.get(i + 1) == Some(&b'\n') {
                i += 1;
            }
            start = i + 1;
        }
        i += 1;
    }
    result.push(&block[start..]);
    result
}

fn data_text(block: &str) -> String {
    lines(block)
        .into_iter()
        .filter_map(|line| {
            let (field, value) = line.split_once(':').unwrap_or((line, ""));
            (field == "data").then(|| value.strip_prefix(' ').unwrap_or(value))
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn canonical_type(block: &str) -> Option<&str> {
    let body = block.strip_prefix("event: ")?;
    let (kind, data) = body.split_once("\ndata: {")?;
    let data = data.strip_suffix("\n\n")?;
    (!kind.is_empty() && !kind.contains(['\r', '\n']) && !data.contains(['\r', '\n']))
        .then_some(kind)
}

fn normalize_aliases(block: &str) -> Result<Cow<'_, str>, ()> {
    if !ALIASES.iter().any(|(old, _)| block.contains(old)) {
        return Ok(Cow::Borrowed(block));
    }
    let (separator, terminator) = if block.ends_with("\r\n\r\n") {
        ("\r\n", "\r\n\r\n")
    } else if block.ends_with("\n\n") {
        ("\n", "\n\n")
    } else if block.ends_with("\r\r") {
        ("\r", "\r\r")
    } else {
        (if block.contains("\r\n") { "\r\n" } else { "\n" }, "")
    };
    let parts = lines(&block[..block.len() - terminator.len()]);
    let multiline = parts
        .iter()
        .filter(|line| line.starts_with("data:"))
        .count()
        > 1;
    let replacement = if multiline {
        let data = data_text(block);
        // Preserve Python's valid-JSON-object gate for all multiline rewrites.
        let Some(payload) = parse_payload(&data) else {
            return Err(());
        };
        rewrite_payload(&data, &payload)?
    } else {
        None
    };
    let mut emitted = false;
    let mut changed = false;
    let mut normalized = Vec::new();
    for line in parts {
        if multiline && line.starts_with("data:") {
            if let Some(ref replacement) = replacement {
                if !emitted {
                    normalized.push(format!("data: {replacement}"));
                    emitted = true;
                    changed = true;
                }
            } else {
                normalized.push(line.to_owned());
            }
            continue;
        }
        if !multiline && let Some(data) = line.strip_prefix("data:") {
            if let Some(payload) = parse_payload(data.trim()) {
                if let Some(value) = rewrite_payload(data.trim(), &payload)? {
                    normalized.push(format!("data: {value}"));
                    changed = true;
                    continue;
                }
            } else {
                // Python accepts some JSON strings/numbers that serde does not.
                return Err(());
            }
        }
        if let Some(kind) = line.strip_prefix("event:")
            && let Some(kind) = alias(kind.strip_prefix(' ').unwrap_or(kind))
        {
            normalized.push(format!("event: {kind}"));
            changed = true;
        } else {
            normalized.push(line.to_owned());
        }
    }
    Ok(if changed {
        Cow::Owned(normalized.join(separator) + terminator)
    } else {
        Cow::Borrowed(block)
    })
}

fn rewrite_payload(data: &str, payload: &Payload<'_>) -> Result<Option<String>, ()> {
    let Some(kind) = payload
        .kind
        .and_then(|kind| serde_json::from_str::<String>(kind.get()).ok())
    else {
        return Ok(None);
    };
    let Some(normalized) = alias(&kind) else {
        return Ok(None);
    };
    let mut value: Value = serde_json::from_str(data).map_err(|_| ())?;
    if !exact_json_domain(&value) {
        return Err(());
    }
    value["type"] = Value::String(normalized.to_owned());
    let text = serde_json::to_string(&value).map_err(|_| ())?;
    // Match json.dumps(ensure_ascii=True, separators=(',', ':')).
    let mut ascii = String::with_capacity(text.len());
    for ch in text.chars() {
        if ch.is_ascii() && ch != '\x7f' {
            ascii.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut [0; 2]) {
                write!(ascii, "\\u{unit:04x}").expect("write to String");
            }
        }
    }
    Ok(Some(ascii))
}

fn exact_json_domain(value: &Value) -> bool {
    match value {
        Value::Number(number) => number.is_i64() || number.is_u64(),
        Value::Array(items) => items.iter().all(exact_json_domain),
        Value::Object(fields) => fields.values().all(exact_json_domain),
        _ => true,
    }
}
