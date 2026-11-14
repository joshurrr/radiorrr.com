"""Run TikTok liveness probes in a single disposable child process."""

import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import time

import httpx


HTTP_TIMEOUT = 10
PROBE_TIMEOUT = 20
PROCESS_TIMEOUT = 25
_pending = None


class TikTokProbeError(Exception):
    def __init__(self, error_class, message, error_repr, timeout, phase, elapsed):
        super().__init__(message)
        self.diagnostic = (
            f"{error_class} {error_repr} timeout={timeout} "
            f"phase={phase} elapsed={elapsed:.2f}s"
        )


async def _probe(username):
    from TikTokLive import TikTokLiveClient

    client = TikTokLiveClient(
        unique_id=f"@{username}",
        web_kwargs={"httpx_kwargs": {"timeout": HTTP_TIMEOUT}},
    )
    try:
        return await asyncio.wait_for(client.is_live(), timeout=PROBE_TIMEOUT)
    finally:
        await client.web.close()


def _child_main(username):
    try:
        with redirect_stdout(sys.stderr):
            result = {"live": asyncio.run(_probe(username))}
    except Exception as error:
        result = {"error": [
            type(error).__name__, str(error), repr(error),
            isinstance(error, (TimeoutError, httpx.TimeoutException)),
        ]}
    print(json.dumps(result))


async def _run_child(username):
    started = time.monotonic()
    phase = "starting"
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).resolve()), username,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        phase = "executing"
        output = asyncio.create_task(process.communicate())
        try:
            stdout, _ = await asyncio.wait_for(
                asyncio.shield(output), timeout=PROCESS_TIMEOUT
            )
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            # Keep the slot until the child has exited and its pipe is drained.
            await output

        if process.returncode:
            raise RuntimeError(f"TikTok probe exited with status {process.returncode}")
        result = json.loads(stdout)
        if isinstance(result.get("live"), bool):
            return result["live"]
        error_class, message, error_repr, timeout = result["error"]
    except Exception as error:
        error_class, message, error_repr = type(error).__name__, str(error), repr(error)
        timeout = isinstance(error, TimeoutError)

    raise TikTokProbeError(
        error_class, message, error_repr, timeout, phase, time.monotonic() - started
    )


def _observe_result(task):
    if not task.cancelled():
        task.exception()


async def request_tiktok_is_live(username):
    global _pending
    # Both callers use the application loop. Reserve the slot before yielding.
    if _pending is not None and not _pending.done():
        raise TikTokProbeError(
            "RuntimeError", "Previous TikTok probe is still active",
            "RuntimeError('Previous TikTok probe is still active')",
            False, "busy", 0.0,
        )
    _pending = asyncio.create_task(_run_child(username))
    _pending.add_done_callback(_observe_result)
    # Cancelling an HTTP caller must not release the slot ahead of child cleanup.
    return await asyncio.shield(_pending)


if __name__ == "__main__":
    _child_main(sys.argv[1])
