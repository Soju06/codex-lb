from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from scripts.local_release_tests import child
from scripts.local_release_tests import test_release as run_release_tests
from scripts.local_verification import (
    ROUTE,
    Report,
    VerificationError,
    exact_text,
    http_completion,
    idle_reason,
    probe,
)


@asynccontextmanager
async def server(handler, health=None):
    app = web.Application()

    async def healthy(request):
        return web.json_response({"status": "ok"})

    app.router.add_get("/health/ready", health or healthy)
    app.router.add_route("*", ROUTE, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


def marker_from(value):
    match = re.search(r"VERIFY_[a-f0-9]+", json.dumps(value))
    assert match is not None
    return match.group()


def completed(text="ok", output=None):
    return {
        "type": "response.completed",
        "response": {
            "status": "completed",
            "output": output
            if output is not None
            else [
                {"type": "message", "content": [{"type": "output_text", "text": text}]},
            ],
        },
    }


def sse(event):
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@pytest.mark.asyncio
async def test_readiness_gates_inference_and_cross_replica_replay(tmp_path):
    calls, capsules = [], {}
    not_ready = 2

    async def health(request):
        nonlocal not_ready
        not_ready -= 1
        return web.json_response({"status": "ok"}, status=503 if not_ready >= 0 else 200)

    def handler(side):
        def reply(body):
            inputs = body["input"]
            calls.append((side, body))
            if inputs[0].get("type") == "compaction":
                marker = capsules[inputs[0]["encrypted_content"]]
                assert marker not in json.dumps(body)
            else:
                marker = marker_from(inputs)
            if inputs[-1].get("type") == "compaction_trigger":
                capsule = "trae-compact-v1:opaque-" + side
                capsules[capsule] = marker
                return completed(output=[{"type": "compaction", "encrypted_content": capsule}])
            return completed(marker)

        async def handle(request):
            if request.method == "GET":
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                body = await ws.receive_json()
                await ws.send_json({"type": "codex.keepalive"})
                await ws.send_json(reply(body))
                async for _ in ws:
                    pass
                return ws
            return web.Response(text=sse(reply(await request.json())), content_type="text/event-stream")

        return handle

    async with server(handler("a"), health) as a, server(handler("b")) as b:
        report = Report(tmp_path / "report.json", 3, 0.2)
        await probe(report, a, "test-model", compaction=True, replay_url=b)
    assert [side for side, _ in calls] == ["a", "a", "a", "b", "b"]
    data = json.loads((tmp_path / "report.json").read_text())
    assert len(data["stages"]) == 7 and all(s["status"] == "passed" for s in data["stages"])
    assert data["stages"][-2]["details"]["crossReplica"] is True
    assert (
        "opaque-" not in (tmp_path / "report.json").read_text()
        and "VERIFY_" not in (tmp_path / "report.json").read_text()
    )


@pytest.mark.asyncio
async def test_readiness_timeout_sends_no_inference(tmp_path):
    calls = []

    async def handler(request):
        calls.append(request.path)
        return web.Response(status=500)

    async def health(request):
        return web.Response(status=503)

    async with server(handler, health) as url:
        report = Report(tmp_path / "report.json", 0.05, 0.01)
        with pytest.raises(TimeoutError):
            await probe(report, url, "test")
    assert not calls
    assert [(s.name, s.status, s.error) for s in report.stages] == [("ready", "failed", "TimeoutError")]


@pytest.mark.asyncio
async def test_failure_retains_partial_report_without_raw_error(tmp_path):
    async def handler(request):
        if request.method == "GET":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.receive_json()
            await ws.send_json({"type": "error", "error": {"message": "secret credential content"}})
            async for _ in ws:
                pass
            return ws
        body = await request.json()
        marker = marker_from(body)
        return web.Response(text=sse(completed(marker)), content_type="text/event-stream")

    async with server(handler) as url:
        report = Report(tmp_path / "report.json", 2)
        with pytest.raises(VerificationError):
            await probe(report, url, "test")
    assert [s.status for s in report.stages] == ["passed", "passed", "failed"]
    assert "secret" not in (tmp_path / "report.json").read_text()


@pytest.mark.asyncio
async def test_fragmented_crlf_multiline_and_unicode_sse():
    message = completed("你好\u2028世界")
    raw = (
        ": heartbeat\r\n\r\ndata: "
        + json.dumps(message, ensure_ascii=False, indent=1).replace("\n", "\r\ndata: ")
        + "\r\n\r\n"
    ).encode()

    async def handler(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for byte in raw:
            await response.write(bytes([byte]))
            await asyncio.sleep(0)
        return response

    async with server(handler) as url, aiohttp.ClientSession() as client:
        result = await http_completion(client, url, {})
        assert exact_text(result, "你好\u2028世界")["exactMarkerRecovered"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "data: [DONE]\n\n",
        "data: nope\n\n",
        sse({"type": "response.incomplete"}),
        sse({"type": "response.failed"}),
        sse({"type": "response.completed", "response": {"status": "failed", "output": []}}),
        sse(completed()).rstrip(),
        "data: " + "x" * 1_048_577,
    ],
)
async def test_invalid_or_unterminated_stream_fails(raw):
    async def handler(request):
        return web.Response(text=raw, content_type="text/event-stream")

    async with server(handler) as url, aiohttp.ClientSession() as client:
        with pytest.raises(VerificationError):
            await http_completion(client, url, {})


@pytest.mark.asyncio
async def test_progress_and_timeout_clean_up_operation(tmp_path, capsys):
    cleaned = asyncio.Event()

    async def operation():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    report = Report(tmp_path / "report.json", 0.06, 0.01)
    with pytest.raises(TimeoutError):
        await report.run("waiting", operation)
    assert cleaned.is_set()
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(e["status"] == "running" and e["elapsed_seconds"] > 0 for e in events)
    assert events[-1]["status"] == "failed"
    with pytest.raises(FileExistsError):
        Report(report.path, 1)


@pytest.mark.parametrize(
    "checks,connections,active,expected",
    [
        ({"draining": "false", "bridge_drain_active": "false", "in_flight": "0"}, 0, 2462, None),
        ({"draining": "false", "bridge_drain_active": "false", "in_flight": "0"}, 1, 2462, "Bridge"),
        ({"draining": "false", "in_flight": "0"}, 0, 2462, "Bridge"),
        ({"draining": "true", "in_flight": "1"}, 0, 2462, "Active"),
        ({"draining": "true", "in_flight": "0", "http_bridge_restart_blocking": "false"}, 2, 2462, None),
        ({"draining": "false", "in_flight": "0", "http_bridge_restart_blocking": "false"}, 2, 2462, "Connected"),
        ({"draining": "true", "in_flight": "0", "http_bridge_activity_error": "Error"}, 0, 2462, "Invalid"),
        ({"draining": "true", "in_flight": "0"}, 0, 2461, "Candidate"),
    ],
)
def test_idle_telemetry_is_conservative(checks, connections, active, expected):
    reason = idle_reason(checks, {"ok": True, "port": active, "connections": {"2461": connections}}, 2461)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and reason.startswith(expected)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()


@pytest.mark.asyncio
async def test_release_uses_explicit_fixture_and_candidate_without_mutation(tmp_path, monkeypatch):
    repo, release = tmp_path / "repo", tmp_path / "release"
    for root in [repo, release]:
        (root / "app").mkdir(parents=True)
        (root / "tests").mkdir()
    (release / "app/__init__.py").write_text('VERSION = "candidate"\n')
    (repo / "app/__init__.py").write_text('VERSION = "wrong-checkout"\n')
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (repo / "tests/conftest.py").write_text('import pytest\n@pytest.fixture\ndef baseline(): return "old-fixture"\n')
    (repo / "tests/test_candidate.py").write_text("""import app,os
from pathlib import Path
def test_candidate(baseline):
    assert baseline == 'old-fixture'
    assert app.VERSION == 'candidate'
    assert 'codex-lb-release-tests-' in os.environ['CODEX_LB_TEST_DATABASE_URL']
    assert os.environ.get('CODEX_LB_PRODUCTION_SENTINEL') is None
""")
    git(repo, "init")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Harness Test", "-c", "user.email=harness@example.invalid", "commit", "-m", "fixture")
    (repo / "tests/conftest.py").write_text("import nonexistent_new_repository_module\n")
    (release / "tests/conftest.py").write_text('raise RuntimeError("Do not use release test fixtures")\n')
    before = {str(p): p.read_bytes() for root in [repo, release] for p in root.rglob("*.py")}
    monkeypatch.setenv("CODEX_LB_TEST_DATABASE_URL", "postgresql://must-never-be-used")
    monkeypatch.setenv("CODEX_LB_PRODUCTION_SENTINEL", "present")
    report = Report(tmp_path / "report.json", 10)
    await run_release_tests(report, release, repo, "HEAD", ["tests/test_candidate.py"])
    assert report.stages[-1].details["applicationFile"] == str(release / "app/__init__.py")
    assert before == {path: Path(path).read_bytes() for path in before}
    with pytest.raises(VerificationError, match="Fixture reference"):
        await run_release_tests(
            Report(tmp_path / "bad.json", 10), release, repo, "nonexistent", ["tests/test_candidate.py"]
        )
    assert (repo / "tests/conftest.py").read_text() == "import nonexistent_new_repository_module\n"


@pytest.mark.asyncio
async def test_child_timeout_terminates_owned_process(tmp_path):
    pid = tmp_path / "pid"
    code = (
        "import os,time;from pathlib import Path;Path("
        + repr(str(pid))
        + ").write_text(str(os.getpid()));time.sleep(100)"
    )
    report = Report(tmp_path / "report.json", 0.3)
    with pytest.raises(TimeoutError):
        await report.run(
            "child",
            lambda: child([sys.executable, "-c", code], cwd=tmp_path, env=dict(os.environ), log=tmp_path / "log"),
        )
    assert pid.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


@pytest.mark.asyncio
async def test_legacy_validator_environment_contract(monkeypatch, tmp_path):
    import scripts.local_entry_acceptance as legacy

    calls = []

    async def fake(report, url, model, *, plan):
        calls.append((report.timeout, url, model, plan))

    monkeypatch.setenv("CANDIDATE_URL", "http://127.0.0.1:2461")
    monkeypatch.setenv("LOCAL_ENTRY_DATABASE_PLAN", str(tmp_path / "plan.json"))
    monkeypatch.setattr(legacy, "probe", fake)
    await legacy.main()
    assert calls == [(50, "http://127.0.0.1:2461", "trae/GPT-5.6-Luna-max", tmp_path / "plan.json")]


@pytest.mark.asyncio
async def test_database_gate_failure_prevents_all_network_probes(tmp_path):
    calls = []

    async def handler(request):
        calls.append(request.path)
        return web.Response(status=500)

    plan = tmp_path / "plan.json"
    plan.write_text('{"invalid": "private-credential-content"}')
    async with server(handler, handler) as url:
        report = Report(tmp_path / "report.json", 5)
        with pytest.raises(VerificationError, match="Database compatibility"):
            await probe(report, url, "test", plan=plan)
    assert not calls
    assert report.stages[-1].name == "database" and report.stages[-1].status == "failed"
    assert "private-credential" not in (tmp_path / "report.json").read_text()


@pytest.mark.asyncio
async def test_report_only_finalizes_after_all_checks_and_preserves_inputs(tmp_path):
    report = Report(tmp_path / "report.json", 1)
    with pytest.raises(VerificationError):
        report.finish("passed")

    async def operation():
        return {"result": "ok"}

    await report.run("probe", operation, lambda value: value, initial={"model": "test-model"})
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "running"
    report.finish("passed")
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["status"] == "passed"
    assert saved["stages"][0]["details"] == {"model": "test-model", "result": "ok"}


@pytest.mark.asyncio
async def test_legacy_direct_script_works_from_another_directory(tmp_path):
    import scripts.local_entry_acceptance as legacy

    calls = []

    async def handler(request):
        if request.method == "GET":
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            body = await socket.receive_json()
            calls.append("websocket")
            await socket.send_json(completed(marker_from(body)))
            async for _ in socket:
                pass
            return socket
        body = await request.json()
        calls.append("http")
        return web.Response(text=sse(completed(marker_from(body))), content_type="text/event-stream")

    async with server(handler) as url:
        env = {key: value for key, value in os.environ.items() if key != "LOCAL_ENTRY_DATABASE_PLAN"}
        env["CANDIDATE_URL"] = url
        async with asyncio.timeout(5):
            result = await child(
                [sys.executable, "-I", str(Path(legacy.__file__).resolve())],
                cwd=tmp_path,
                env=env,
                log=tmp_path / "legacy.log",
            )
    assert result == 0
    assert calls == ["http", "websocket"]
