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

#[test]
fn python_integer_decode_limits_keep_the_entire_object_opaque() {
    for sign in ["", "-"] {
        let accepted = format!(r#"{{"sequence_number":{sign}{}}}"#, "9".repeat(640));
        let event = interpret_websocket(&accepted).unwrap();
        assert_eq!(event.payload.get(), accepted);
        assert_eq!(
            event.sequence_number.unwrap().get(),
            format!("{sign}{}", "9".repeat(640))
        );
        for digits in [641, 5000] {
            let token = format!("{sign}{}", "9".repeat(digits));
            for text in [
                format!(r#"{{"sequence_number":{token}}}"#),
                format!(r#"{{"response":{{"extra":[{token}]}},"sequence_number":1}}"#),
                format!(r#"{{"sequence_number":{token},"sequence_number":1}}"#),
            ] {
                assert!(interpret_websocket(&text).is_none());
            }
            for value in [
                format!(r#""{token}""#),
                format!("{token}.0"),
                format!("{token}e-5000"),
            ] {
                let text = format!(r#"{{"sequence_number":{value}}}"#);
                let event = interpret_websocket(&text).unwrap();
                assert_eq!(event.payload.get(), text);
                assert!(event.sequence_number.is_none());
            }
        }
    }
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
