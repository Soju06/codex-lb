const SSE_SEPARATOR_OVERLAP: usize = 3;

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct SseEventTooLarge {
    pub(crate) size_bytes: usize,
    pub(crate) limit_bytes: usize,
}

pub(crate) struct SseFramer {
    buffer: Vec<u8>,
    start: usize,
    scanned: usize,
    swallow_lf: bool,
    max_event_bytes: usize,
}

impl SseFramer {
    pub(crate) fn new(max_event_bytes: usize) -> Self {
        Self {
            buffer: Vec::new(),
            start: 0,
            scanned: 0,
            swallow_lf: false,
            max_event_bytes,
        }
    }

    pub(crate) fn push(&mut self, mut chunk: &[u8]) {
        if chunk.is_empty() {
            return;
        }
        self.compact_consumed_prefix();
        if self.swallow_lf {
            self.swallow_lf = false;
            if chunk.first() == Some(&b'\n') {
                chunk = &chunk[1..];
            }
        }
        self.buffer.extend_from_slice(chunk);
    }

    pub(crate) fn next_event(&mut self) -> Result<Option<String>, SseEventTooLarge> {
        loop {
            let active = &self.buffer[self.start..];
            let search_from = self.scanned.saturating_sub(SSE_SEPARATOR_OVERLAP);
            let Some((separator_start, separator_len)) = find_sse_separator(active, search_from)
            else {
                self.scanned = active.len();
                self.check_size(active.len())?;
                return Ok(None);
            };
            let event_end = separator_start + separator_len;
            self.check_size(event_end)?;
            let raw_event = &active[..event_end];
            let is_whitespace = raw_event
                .iter()
                .all(|byte| is_python_bytes_whitespace(*byte));
            let text = (!is_whitespace).then(|| String::from_utf8_lossy(raw_event).into_owned());
            self.swallow_lf = raw_event.last() == Some(&b'\r') && event_end == active.len();
            self.start += event_end;
            self.scanned = 0;
            if text.is_some() {
                return Ok(text);
            }
        }
    }

    pub(crate) fn finish(self) -> Result<Option<String>, SseEventTooLarge> {
        let active = &self.buffer[self.start..];
        if active.is_empty() {
            return Ok(None);
        }
        self.check_size(active.len())?;
        Ok(Some(String::from_utf8_lossy(active).into_owned()))
    }

    fn check_size(&self, size_bytes: usize) -> Result<(), SseEventTooLarge> {
        if size_bytes > self.max_event_bytes {
            return Err(SseEventTooLarge {
                size_bytes,
                limit_bytes: self.max_event_bytes,
            });
        }
        Ok(())
    }

    fn compact_consumed_prefix(&mut self) {
        if self.start == 0 {
            return;
        }
        if self.start == self.buffer.len() {
            self.buffer.clear();
        } else {
            self.buffer.copy_within(self.start.., 0);
            self.buffer.truncate(self.buffer.len() - self.start);
        }
        self.start = 0;
    }
}

fn is_python_bytes_whitespace(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

fn line_ending_len(buffer: &[u8], index: usize) -> Option<usize> {
    match buffer.get(index) {
        Some(b'\r') if buffer.get(index + 1) == Some(&b'\n') => Some(2),
        Some(b'\r' | b'\n') => Some(1),
        _ => None,
    }
}

fn find_sse_separator(buffer: &[u8], start: usize) -> Option<(usize, usize)> {
    let mut index = start;
    while index < buffer.len() {
        let Some(first) = line_ending_len(buffer, index) else {
            index += 1;
            continue;
        };
        if let Some(second) = line_ending_len(buffer, index + first) {
            return Some((index, first + second));
        }
        index += first;
    }
    None
}

pub(crate) fn text_fragments(text: &str, max_fragment_bytes: usize) -> TextFragments<'_> {
    assert!(
        max_fragment_bytes > 0,
        "SSE text fragments must be nonempty"
    );
    TextFragments {
        remaining: text,
        max_fragment_bytes,
    }
}

pub(crate) struct TextFragments<'a> {
    remaining: &'a str,
    max_fragment_bytes: usize,
}

impl<'a> Iterator for TextFragments<'a> {
    type Item = (&'a str, bool);

    fn next(&mut self) -> Option<Self::Item> {
        if self.remaining.is_empty() {
            return None;
        }
        if self.remaining.len() <= self.max_fragment_bytes {
            let final_fragment = std::mem::take(&mut self.remaining);
            return Some((final_fragment, false));
        }
        let mut split = self.max_fragment_bytes;
        while !self.remaining.is_char_boundary(split) {
            split -= 1;
        }
        let (fragment, remaining) = self.remaining.split_at(split);
        self.remaining = remaining;
        Some((fragment, true))
    }
}

#[cfg(test)]
mod tests {
    use base64::Engine as _;
    use serde_json::Value;

    use super::{SseEventTooLarge, SseFramer, text_fragments};

    #[test]
    fn shared_sse_fixture_matches_framer() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../codex-lb-protocol/tests/fixtures/sse-v1.json"
        ))
        .expect("parse shared SSE fixture");
        for case in fixture["cases"].as_array().expect("fixture cases") {
            let name = case["name"].as_str().expect("case name");
            let limit = case["max_event_bytes"].as_u64().expect("event limit") as usize;
            let mut framer = SseFramer::new(limit);
            let mut events = Vec::new();
            let mut failure = None;
            for encoded in case["chunks_base64"].as_array().expect("case chunks") {
                let chunk = base64::engine::general_purpose::STANDARD
                    .decode(encoded.as_str().expect("base64 chunk"))
                    .expect("decode fixture chunk");
                framer.push(&chunk);
                loop {
                    match framer.next_event() {
                        Ok(Some(event)) => events.push(event),
                        Ok(None) => break,
                        Err(error) => {
                            failure = Some(error);
                            break;
                        }
                    }
                }
                if failure.is_some() {
                    break;
                }
            }
            if failure.is_none() {
                match framer.finish() {
                    Ok(Some(event)) => events.push(event),
                    Ok(None) => {}
                    Err(error) => failure = Some(error),
                }
            }

            let expected_events = case["events"]
                .as_array()
                .expect("expected events")
                .iter()
                .map(|event| event.as_str().expect("event text").to_owned())
                .collect::<Vec<_>>();
            assert_eq!(events, expected_events, "event mismatch for {name}");
            let expected_failure = case.get("failure").map(|failure| SseEventTooLarge {
                size_bytes: failure["size_bytes"].as_u64().expect("failure size") as usize,
                limit_bytes: failure["limit_bytes"].as_u64().expect("failure limit") as usize,
            });
            assert_eq!(failure, expected_failure, "failure mismatch for {name}");
        }
    }

    #[test]
    fn fragments_do_not_split_utf8_code_points() {
        let text = format!("{}é{}", "a".repeat(16_383), "b".repeat(16_384));
        let fragments = text_fragments(&text, 16 * 1024).collect::<Vec<_>>();

        assert_eq!(fragments.len(), 3);
        assert!(fragments[0].1);
        assert!(fragments[1].1);
        assert!(!fragments[2].1);
        assert!(
            fragments
                .iter()
                .all(|(fragment, _)| fragment.len() <= 16 * 1024)
        );
        assert_eq!(
            fragments
                .iter()
                .map(|(fragment, _)| *fragment)
                .collect::<String>(),
            text
        );
    }

    #[test]
    fn large_event_scans_incrementally_across_transport_reads() {
        let mut framer = SseFramer::new(4 * 1024 * 1024);
        let body = [b"data: ".as_slice(), &vec![b'x'; 2 * 1024 * 1024], b"\n\n"].concat();
        for chunk in body.chunks(16 * 1024) {
            framer.push(chunk);
            if let Some(event) = framer.next_event().expect("within size limit") {
                assert_eq!(event.len(), body.len());
            }
        }
    }
}
