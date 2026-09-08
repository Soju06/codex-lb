use codex_lb_responses::compact::CompactCollector;

#[test]
fn shared_compact_fixtures() {
    let cases: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/compact-v1.json")).unwrap();
    for case in cases.as_array().unwrap() {
        let mut collector = CompactCollector::default();
        let result = case["blocks"]
            .as_array()
            .unwrap()
            .iter()
            .find_map(|block| collector.push(block.as_str().unwrap()))
            .unwrap_or_else(|| collector.finish());
        assert_eq!(
            serde_json::to_value(result).unwrap(),
            case["expected"],
            "{}",
            case["name"]
        );
    }
}
