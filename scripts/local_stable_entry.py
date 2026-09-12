"""Loopback connection router. Never starts, stops, or migrates a backend."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from collections import Counter
from pathlib import Path

import aiohttp


class Entry:
    def __init__(self, state: Path, validator: list[str], timeout: float = 60):
        self.state = state
        self.port = self.valid_port(json.loads(state.read_text())["port"])
        self.validator = validator
        self.timeout = timeout
        self.counts: Counter[int] = Counter()
        self.lock = asyncio.Lock()
        self.listen_port: int | None = None

    @staticmethod
    def valid_port(port: int) -> int:
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid backend port")
        return port

    async def validate(self, port: int) -> None:
        url = f"http://127.0.0.1:{port}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
            async with client.get(url + "/health", allow_redirects=False) as response:
                if response.status != 200 or (await response.json()).get("status") != "ok":
                    raise ValueError("Candidate health failed")
            async with client.get(url + "/backend-api/codex/models", allow_redirects=False) as response:
                if response.status != 200 or not (await response.json()).get("models"):
                    raise ValueError("Candidate catalog failed")
        # Fixed operator-owned argv; control requests cannot inject commands.
        process = await asyncio.create_subprocess_exec(
            *self.validator,
            env={**os.environ, "CANDIDATE_URL": url},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(self.timeout):
                if await process.wait() != 0:
                    raise ValueError("Candidate acceptance failed")
        finally:
            # Kill descendants too, including a probe leaving children behind.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def switch(self, port: int) -> None:
        port = self.valid_port(port)
        if port == self.listen_port:
            raise ValueError("Entrance cannot point to itself")
        async with self.lock:
            await self.validate(port)
            temporary = self.state.with_suffix(".pending")
            with temporary.open("w") as stream:
                os.chmod(temporary, 0o600)
                json.dump({"port": port}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.state)
            self.port = port

    async def relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        port = self.port
        self.counts[port] += 1
        upstream = None
        try:
            remote, upstream = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 5)

            async def pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
                while chunk := await source.read(65536):
                    target.write(chunk)
                    await target.drain()
                if target.can_write_eof():
                    target.write_eof()

            async with asyncio.TaskGroup() as group:
                group.create_task(pipe(reader, upstream))
                group.create_task(pipe(remote, writer))
        except (OSError, TimeoutError, ExceptionGroup):
            pass  # Close this connection; never replay potentially committed bytes.
        finally:
            self.counts[port] -= 1
            for connection in (writer, upstream):
                if connection is not None:
                    connection.close()
                    try:
                        await connection.wait_closed()
                    except OSError:
                        pass

    async def control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(self.timeout + 30):
                request = json.loads(await reader.readline())
                if request["action"] == "switch":
                    await self.switch(request["port"])
                elif request["action"] != "status":
                    raise ValueError("Unknown action")
                result = {"ok": True, "port": self.port, "connections": dict(self.counts)}
        except (ValueError, KeyError, OSError, TimeoutError, aiohttp.ClientError):
            result = {"ok": False, "error": "Validation or control failed; destination unchanged"}
        writer.write(json.dumps(result).encode() + b"\n")
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--listen", type=int, default=2457)
    parser.add_argument("--validator", nargs="+", required=True)
    args = parser.parse_args()
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.directory.stat().st_uid != os.getuid() or args.directory.stat().st_mode & 0o077:
        raise ValueError("Control directory must be owned by current user and mode 0700")
    # Exclusive process lock prevents two entrances owning the same state/socket.
    import fcntl

    with (args.directory / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        entry = Entry(args.directory / "state.json", args.validator)
        if entry.port == args.listen:
            raise ValueError("Entrance cannot point to itself")
        entry.listen_port = args.listen
        socket = args.directory / "control.sock"
        socket.unlink(missing_ok=True)
        tcp = await asyncio.start_server(entry.relay, "127.0.0.1", args.listen)
        control = await asyncio.start_unix_server(entry.control, path=socket)
        os.chmod(socket, 0o600)
        async with tcp, control:
            await asyncio.gather(tcp.serve_forever(), control.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
