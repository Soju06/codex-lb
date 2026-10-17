use codex_lb_responses::stream::interpret;
use serde_json::Value;

#[test]
fn shared_stream_fixtures() {
    let cases: Vec<Value> = serde_json::from_str(include_str!("fixtures/stream-v1.json")).unwrap();
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let event = interpret(case["block"].as_str().unwrap());
        assert_eq!(
            event.python_normalization,
            case["python_normalization"].as_bool().unwrap(),
            "{name}: handoff"
        );
        if !event.python_normalization {
            assert_eq!(
                event.text,
                case["native_text"].as_str().unwrap(),
                "{name}: text"
            );
            assert_eq!(
                event.event_type.as_deref(),
                case["native_type"].as_str(),
                "{name}: type"
            );
        }
    }
}
