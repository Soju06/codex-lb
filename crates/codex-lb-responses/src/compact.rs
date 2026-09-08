//! Collect a compact response from already framed SSE events.

use std::cmp::Ordering;
use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::value::{RawValue, to_raw_value};

type Object = BTreeMap<String, Box<RawValue>>;

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CompactResult {
    Completed { response: Box<RawValue> },
    TerminalError { event: Box<RawValue> },
    Invalid { message: &'static str },
}

/// One collector belongs to exactly one response. Stop feeding it after a result.
#[derive(Default)]
pub struct CompactCollector {
    indexed: BTreeMap<OutputIndex, Box<RawValue>>,
    unindexed: Vec<Box<RawValue>>,
    saw_payload: bool,
}

impl CompactCollector {
    pub fn push(&mut self, block: &str) -> Option<CompactResult> {
        let data = block
            .split(['\r', '\n'])
            .filter_map(|line| {
                let (field, value) = line.split_once(':').unwrap_or((line, ""));
                (field == "data").then(|| value.strip_prefix(' ').unwrap_or(value))
            })
            .collect::<Vec<_>>()
            .join("\n");
        let mut payload: Object = serde_json::from_str(&data).ok()?;
        self.saw_payload = true;
        let event_type: String = serde_json::from_str(payload.get("type")?.get()).ok()?;
        match event_type.as_str() {
            "response.output_item.added" | "response.output_item.done" => {
                let item = payload
                    .remove("item")
                    .filter(|item| item.get().starts_with('{'))?;
                if let Some(index) = payload
                    .get("output_index")
                    .and_then(|index| OutputIndex::parse(index.get()))
                {
                    self.indexed.insert(index, item);
                } else if event_type == "response.output_item.done" {
                    self.unindexed.push(item);
                }
                None
            }
            "response.completed" => {
                let Some(response) = payload
                    .remove("response")
                    .filter(|response| response.get().starts_with('{'))
                else {
                    return Some(CompactResult::Invalid {
                        message: "response.completed event missing response object",
                    });
                };
                // Only interpret the output array; all other JSON remains opaque.
                let mut fields: Object =
                    serde_json::from_str(response.get()).expect("validated JSON object");
                let has_output = fields.get("output").is_some_and(|output| {
                    serde_json::from_str::<Vec<Box<RawValue>>>(output.get())
                        .is_ok_and(|items| !items.is_empty())
                });
                if has_output || (self.indexed.is_empty() && self.unindexed.is_empty()) {
                    return Some(CompactResult::Completed { response });
                }
                let items: Vec<_> = std::mem::take(&mut self.indexed)
                    .into_values()
                    .chain(std::mem::take(&mut self.unindexed))
                    .collect();
                fields.insert(
                    "output".to_owned(),
                    to_raw_value(&items).expect("serialize raw items"),
                );
                Some(CompactResult::Completed {
                    response: to_raw_value(&fields).expect("serialize raw response"),
                })
            }
            "response.failed" | "response.incomplete" | "error" => {
                Some(CompactResult::TerminalError {
                    event: RawValue::from_string(data).expect("validated JSON payload"),
                })
            }
            _ => None,
        }
    }

    pub fn finish(self) -> CompactResult {
        CompactResult::Invalid {
            message: if self.saw_payload {
                "upstream SSE ended before response.completed"
            } else {
                "empty upstream SSE response"
            },
        }
    }
}

/// Python integer ordering, including bool indices, without narrowing large ints.
#[derive(Eq, PartialEq)]
struct OutputIndex(String);

impl OutputIndex {
    fn parse(raw: &str) -> Option<Self> {
        let raw = match raw {
            "true" => "1",
            "false" | "-0" => "0",
            value => value,
        };
        let magnitude = raw.strip_prefix('-').unwrap_or(raw);
        (!magnitude.is_empty() && magnitude.bytes().all(|byte| byte.is_ascii_digit()))
            .then(|| Self(raw.to_owned()))
    }
}

impl Ord for OutputIndex {
    fn cmp(&self, other: &Self) -> Ordering {
        let left_negative = self.0.starts_with('-');
        let right_negative = other.0.starts_with('-');
        match (left_negative, right_negative) {
            (true, false) => Ordering::Less,
            (false, true) => Ordering::Greater,
            _ => {
                let order = self
                    .0
                    .len()
                    .cmp(&other.0.len())
                    .then_with(|| self.0.cmp(&other.0));
                if left_negative {
                    order.reverse()
                } else {
                    order
                }
            }
        }
    }
}

impl PartialOrd for OutputIndex {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
