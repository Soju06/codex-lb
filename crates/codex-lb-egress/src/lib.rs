//! Native outbound transport implementation for codex-lb.
//!
//! The public surface is intentionally narrow: process lifecycle belongs to the
//! worker crate, and wire types belong to `codex-lb-protocol`. Future migrated
//! application crates can depend on transport APIs here without depending on
//! the stdio worker binary.

mod http;
mod runtime;
mod websocket;

pub use runtime::{RequestError, run_stdio};
