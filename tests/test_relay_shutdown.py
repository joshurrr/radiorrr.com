"""The relay must recover even when API jobs occupy the default executor."""

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock


SOURCE = Path(os.environ.get(
    "RRR_ROUTER_SOURCE", Path(__file__).resolve().parents[1] / "backend/main.py"
))


class RelayShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_unhealthy_relay_shutdown_with_busy_executor(self):
        release = threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown)
        self.addCleanup(release.set)
        asyncio.get_running_loop().set_default_executor(pool)
        pool.submit(release.wait)

        names = {"stop_relay_async", "stop_audio_relay_async", "terminate_relay_process_async"}
        tree = ast.parse(SOURCE.read_bytes())
        functions = [node for node in tree.body if getattr(node, "name", "") in names]
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        playlist = directory / "candidate.m3u8"
        playlist.touch()

        def fixture_process():
            process = Mock()
            process.poll.return_value = None
            process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
            return process

        audio, relay = fixture_process(), fixture_process()
        task = Mock()
        task.done.return_value = False
        ns = dict(
            asyncio=asyncio, subprocess=subprocess, audio_process=audio, audio_task=task,
            relay_process=relay, relay_username="former-dj", relay_platform="TikTok",
            RELAY_DIR=directory, RELAY_PLAYLIST=playlist, retired_relay_outputs=[],
        )
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), ns)
        try:
            await asyncio.wait_for(ns["stop_relay_async"](), timeout=0.5)
        finally:
            release.set()

        audio.terminate.assert_called_once()
        relay.terminate.assert_called_once()
        task.cancel.assert_called_once()
        self.assertIsNone(ns["relay_process"])
        self.assertIsNone(ns["audio_process"])
        self.assertFalse(playlist.exists())

    async def test_force_kills_encoder_that_ignores_terminate(self):
        tree = ast.parse(SOURCE.read_bytes())
        function = next(node for node in tree.body
                        if getattr(node, "name", "") == "terminate_relay_process_async")
        ns = dict(asyncio=asyncio, subprocess=subprocess)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), ns)
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = lambda: setattr(process.poll, "return_value", -9)
        await ns["terminate_relay_process_async"](process, timeout=0.01)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
