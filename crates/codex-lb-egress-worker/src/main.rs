#[tokio::main]
async fn main() {
    if codex_lb_egress::run_stdio().await.is_err() {
        std::process::exit(1);
    }
}
