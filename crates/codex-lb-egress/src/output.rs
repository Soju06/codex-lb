//! Ordered IPC writes whose accepted bytes survive cancellation of a producer.

use std::io;
use std::sync::Arc;

use codex_lb_protocol::NativeEvent;
use tokio::io::{AsyncWrite, AsyncWriteExt, BufWriter, Stdout};
use tokio::sync::Mutex;

pub(crate) type Output<W = BufWriter<Stdout>> = Arc<Mutex<FrameWriter<W>>>;

pub(crate) fn stdout() -> Output {
    Arc::new(Mutex::new(FrameWriter::new(BufWriter::new(
        tokio::io::stdout(),
    ))))
}

pub(crate) struct FrameWriter<W> {
    writer: W,
    pending: Vec<u8>,
    written: usize,
}

impl<W: AsyncWrite + Unpin> FrameWriter<W> {
    fn new(writer: W) -> Self {
        Self {
            writer,
            pending: Vec::new(),
            written: 0,
        }
    }

    async fn write(&mut self, bytes: Vec<u8>) -> io::Result<()> {
        self.flush().await?;
        // Ownership transfers before the first write can suspend. If this
        // producer is cancelled, the next producer drains these bytes first.
        self.pending = bytes;
        self.flush().await
    }

    pub(crate) async fn flush(&mut self) -> io::Result<()> {
        if self.pending.is_empty() {
            return Ok(());
        }
        while self.written < self.pending.len() {
            // write() is cancellation-safe; record each accepted prefix before
            // the next await. write_all() would lose that progress on cancel.
            let written = self.writer.write(&self.pending[self.written..]).await?;
            if written == 0 {
                return Err(io::ErrorKind::WriteZero.into());
            }
            self.written += written;
        }
        self.writer.flush().await?;
        self.pending = Vec::new();
        self.written = 0;
        Ok(())
    }
}

pub(crate) async fn emit(output: &Output, event: &NativeEvent) -> io::Result<()> {
    let mut bytes = serde_json::to_vec(event)?;
    bytes.push(b'\n');
    emit_bytes(output, bytes).await
}

async fn emit_bytes<W: AsyncWrite + Unpin>(output: &Output<W>, bytes: Vec<u8>) -> io::Result<()> {
    output.lock().await.write(bytes).await
}

const BATCH_BYTES: usize = 64 * 1024;
const BATCH_RECORDS: usize = 32;

/// Coalesce ready JSON lines only; the caller flushes before reading upstream.
#[derive(Default)]
pub(crate) struct EventBatch {
    bytes: Vec<u8>,
    records: usize,
}

impl EventBatch {
    pub(crate) async fn push<W: AsyncWrite + Unpin>(
        &mut self,
        output: &Output<W>,
        event: &NativeEvent,
    ) -> io::Result<()> {
        let mut record = serde_json::to_vec(event)?;
        record.push(b'\n');
        if self.records != 0 && self.bytes.len() + record.len() > BATCH_BYTES {
            self.flush(output).await?;
        }
        if record.len() > BATCH_BYTES {
            // Existing text/metadata bounds permit a single escaped JSON line
            // larger than the batch budget; never accumulate peers alongside it.
            return emit_bytes(output, record).await;
        }
        self.bytes.extend_from_slice(&record);
        self.records += 1;
        if self.records == BATCH_RECORDS {
            self.flush(output).await?;
        }
        Ok(())
    }

    pub(crate) async fn flush<W: AsyncWrite + Unpin>(
        &mut self,
        output: &Output<W>,
    ) -> io::Result<()> {
        if self.records == 0 {
            return Ok(());
        }
        self.records = 0;
        emit_bytes(output, std::mem::take(&mut self.bytes)).await
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use tokio::io::{AsyncReadExt, BufWriter, duplex};

    use super::*;

    fn record(text: String) -> NativeEvent {
        NativeEvent::Sse {
            request_id: "request".to_owned(),
            text,
            more: false,
        }
    }

    fn encoded(event: &NativeEvent) -> Vec<u8> {
        let mut bytes = serde_json::to_vec(event).unwrap();
        bytes.push(b'\n');
        bytes
    }

    #[tokio::test]
    async fn cancelled_partial_write_finishes_before_sibling_record() {
        // Exercise both a direct large write and buffered output suspended in flush.
        for capacity in [4, 64 * 1024] {
            assert_cancelled_write_finishes(capacity).await;
        }
    }

    async fn assert_cancelled_write_finishes(capacity: usize) {
        let (writer, mut reader) = duplex(8);
        let output = Arc::new(Mutex::new(FrameWriter::new(BufWriter::with_capacity(
            capacity, writer,
        ))));
        let first = encoded(&record("large\n".repeat(500)));
        let next = encoded(&NativeEvent::Cancelled {
            request_id: "sibling".to_owned(),
        });
        let expected = [first.as_slice(), next.as_slice()].concat();
        let task_output = output.clone();
        let producer = tokio::spawn(async move { emit_bytes(&task_output, first).await });
        let mut prefix = [0; 8];
        tokio::time::timeout(Duration::from_secs(2), reader.read_exact(&mut prefix))
            .await
            .unwrap()
            .unwrap();
        producer.abort();
        assert!(producer.await.unwrap_err().is_cancelled());
        let mut remainder = vec![0; expected.len() - prefix.len()];
        tokio::time::timeout(Duration::from_secs(2), async {
            let (write, read) =
                tokio::join!(emit_bytes(&output, next), reader.read_exact(&mut remainder));
            write.unwrap();
            read.unwrap();
        })
        .await
        .unwrap();
        assert_eq!([prefix.as_slice(), &remainder].concat(), expected);
        assert!(output.lock().await.pending.is_empty());
    }

    #[tokio::test]
    async fn ready_batches_enforce_encoded_byte_and_record_bounds() {
        let output = Arc::new(Mutex::new(FrameWriter::new(Vec::new())));
        let mut batch = EventBatch::default();
        let small = record("data: {}\n\n".to_owned());
        let mut expected = Vec::new();
        for _ in 0..BATCH_RECORDS - 1 {
            batch.push(&output, &small).await.unwrap();
            expected.extend(encoded(&small));
        }
        assert!(output.lock().await.writer.is_empty());
        batch.push(&output, &small).await.unwrap();
        expected.extend(encoded(&small));
        assert_eq!(output.lock().await.writer, expected);
        assert_eq!(batch.records, 0);

        // A maximum-sized text fragment can expand sixfold when JSON-escaped.
        // Flush the ready prefix before emitting that record alone.
        batch.push(&output, &small).await.unwrap();
        expected.extend(encoded(&small));
        let escaped = record("\0".repeat(16 * 1024));
        assert!(encoded(&escaped).len() > BATCH_BYTES);
        batch.push(&output, &escaped).await.unwrap();
        expected.extend(encoded(&escaped));
        assert_eq!(output.lock().await.writer, expected);
        assert_eq!(batch.records, 0);

        // Ordinary records must flush before their cumulative encoded size
        // would exceed the budget, even before the record-count threshold.
        let medium = record("\0".repeat(6000));
        batch.push(&output, &medium).await.unwrap();
        batch.push(&output, &medium).await.unwrap();
        expected.extend(encoded(&medium));
        assert_eq!(output.lock().await.writer, expected);
        assert_eq!(batch.records, 1);
        batch.flush(&output).await.unwrap();
        expected.extend(encoded(&medium));
        assert_eq!(output.lock().await.writer, expected);
    }
}
