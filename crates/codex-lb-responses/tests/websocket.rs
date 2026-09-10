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
