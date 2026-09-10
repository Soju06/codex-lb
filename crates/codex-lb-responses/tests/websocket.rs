use codex_lb_responses::stream::interpret_websocket;
use serde::Deserialize;

#[derive(Deserialize)]
struct Case {
    name: String,
    text: String,
    interpreted: bool,
    // Surrogate types remain opaque and cannot decode into a Rust string.
    event_type: Box<serde_json::value::RawValue>,
    compact: String,
}

#[test]
fn shared_websocket_fixtures_preserve_raw_objects() {
    let cases: Vec<Case> =
        serde_json::from_str(include_str!("fixtures/websocket-v1.json")).unwrap();
    for case in cases {
        let result = interpret_websocket(&case.text);
        assert_eq!(result.is_some(), case.interpreted, "{}", case.name);
        if let Some(event) = result {
            assert_eq!(event.payload.get(), case.compact, "{}", case.name);
            let expected: Option<String> = serde_json::from_str(case.event_type.get()).unwrap();
            assert_eq!(event.event_type, expected, "{}", case.name);
        }
    }
}

#[test]
fn large_websocket_object_retains_opaque_delivery() {
    let text = format!(
        r#"{{"type":"response.output_text.delta","delta":"{}"}}"#,
        "x".repeat(1024 * 1024)
    );
    assert!(interpret_websocket(&text).is_none());
}

#[derive(Deserialize)]
struct RoutingCase {
    name: String,
    text: String,
    interpreted: bool,
    // Opaque cases may contain an unpaired surrogate ID.
    payload_response_id: Box<serde_json::value::RawValue>,
    sequence_token: Option<String>,
}

#[test]
fn shared_websocket_routing_fixtures_preserve_id_and_integer_semantics() {
    let cases: Vec<RoutingCase> =
        serde_json::from_str(include_str!("fixtures/websocket-routing-v1.json")).unwrap();
    for case in cases {
        let result = interpret_websocket(&case.text);
        assert_eq!(result.is_some(), case.interpreted, "{}", case.name);
        if let Some(event) = result {
            let expected: Option<String> =
                serde_json::from_str(case.payload_response_id.get()).unwrap();
            assert_eq!(event.payload_response_id, expected, "{}", case.name);
            assert_eq!(
                event.sequence_number.as_ref().map(|value| value.get()),
                case.sequence_token.as_deref(),
                "{}",
                case.name
            );
        }
    }
}
