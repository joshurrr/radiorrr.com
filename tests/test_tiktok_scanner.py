"""Exercise the real scanner functions without importing the application."""

import ast
import asyncio
import contextlib
from datetime import datetime, timezone
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import tiktok_liveness



class TikTokScannerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "backend" / "main.py"
        tree = ast.parse(source.read_bytes())
        names = {
            "check_tiktok_dj", "tiktok_monitor",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ]
        folder = self.enterContext(tempfile.TemporaryDirectory())
        database = Path(folder) / "test.sqlite"

        def connect():
            conn = sqlite3.connect(database)
            conn.row_factory = sqlite3.Row
            return conn

        with contextlib.closing(connect()) as conn:
            conn.execute("""
                CREATE TABLE live_djs (
                    username TEXT, name TEXT, platform TEXT, url TEXT,
                    genre TEXT, viewers INTEGER, started_at TEXT, updated_at TEXT,
                    UNIQUE(platform, username)
                )
            """)
        self.connect = connect
        self.log = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.probe = AsyncMock()
        self.ns = dict(
            asyncio=asyncio, time=time,
            datetime=datetime, timezone=timezone, get_db=connect,
            request_tiktok_is_live=self.probe,
            TIKTOK_LIVE_ERROR_BACKOFF_SECONDS=300,
            TIKTOK_LIVE_ERROR_BACKOFF_THRESHOLD=5,
            TIKTOK_LIVE_CHECK_DELAY=2, TIKTOK_MAINTENANCE_INTERVAL=60,
            invalid_tiktok_account_counts={}, offline_miss_counts={},
            profile_picture_refresh_attempted={},
            relay_username=None, relay_platform=None, relay_process=None,
            OFFLINE_CONFIRMATIONS_REQUIRED=2,
            ensure_favourite_profile_picture=AsyncMock(),
            _record_tiktok_live_check=Mock(), _defer_tiktok_transient_error=Mock(),
            _is_tiktok_invalid_account_error=Mock(return_value=False),
            _refresh_tiktok_dormant_states=Mock(),
            _maintain_tiktok_live_state=Mock(),
        )
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.ns)

    async def check(self):
        return await self.ns["check_tiktok_dj"]("dj", "DJ", "https://example.test/live")

    def live_count(self):
        with contextlib.closing(self.connect()) as conn:
            return conn.execute("SELECT count(*) FROM live_djs").fetchone()[0]

    async def test_live_and_confirmed_offline(self):
        self.probe.return_value = True
        self.assertTrue(await self.check())
        self.assertEqual(self.live_count(), 1)
        self.probe.return_value = False
        self.assertTrue(await self.check())
        self.assertEqual(self.live_count(), 1)
        self.assertTrue(await self.check())
        self.assertEqual(self.live_count(), 0)

    async def test_timeout_preserves_live_and_next_probe_recovers(self):
        self.probe.return_value = True
        self.assertTrue(await self.check())

        self.probe.side_effect = tiktok_liveness.TikTokProbeError(
            "TimeoutError", "", "TimeoutError()", True, "executing", 25,
        )
        self.assertFalse(await self.check())
        self.assertEqual(self.live_count(), 1)
        self.assertEqual(self.ns["offline_miss_counts"], {})
        self.assertEqual(self.ns["_record_tiktok_live_check"].call_count, 1)
        self.assertIn("TimeoutError() timeout=True phase=executing elapsed=", self.log.getvalue())
        self.probe.side_effect = None
        self.assertTrue(await self.check())

    async def test_five_errors_back_off_without_removing_live_dj(self):
        self.probe.return_value = True
        self.assertTrue(await self.check())
        self.probe.side_effect = tiktok_liveness.TikTokProbeError(
            "ReadTimeout", "test timeout", "ReadTimeout('test timeout')",
            True, "executing", 10,
        )
        self.ns["_build_tiktok_scan_queue"] = lambda: [
            {"username": "dj", "name": "DJ", "live_url": "https://example.test/live"}
        ]

        async def stop_at_backoff(seconds):
            if seconds == 300:
                raise asyncio.CancelledError
            if seconds == 10:
                self.fail(self.log.getvalue())

        fake_asyncio = SimpleNamespace(**vars(asyncio))
        fake_asyncio.sleep = AsyncMock(side_effect=stop_at_backoff)
        self.ns["asyncio"] = fake_asyncio
        with self.assertRaises(asyncio.CancelledError):
            await self.ns["tiktok_monitor"]()
        self.assertEqual(self.probe.await_count, 6)
        self.assertEqual(self.ns["_defer_tiktok_transient_error"].call_count, 5)
        self.assertEqual(self.live_count(), 1)
        self.assertEqual(self.ns["offline_miss_counts"], {})
        self.assertIn("ReadTimeout('test timeout') timeout=True", self.log.getvalue())
        self.assertIn("backing off for 300s after 5 consecutive check errors", self.log.getvalue())


class ProbeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_timeout_and_client_cleanup(self):
        client = Mock()
        client.web.close = AsyncMock()
        client.is_live = AsyncMock(side_effect=httpx.ReadTimeout("read stalled"))
        factory = Mock(return_value=client)
        with patch.dict(sys.modules, {"TikTokLive": SimpleNamespace(TikTokLiveClient=factory)}):
            with self.assertRaises(httpx.ReadTimeout):
                await tiktok_liveness._probe("dj")
            client.web.close.assert_awaited_once()
            factory.assert_called_once_with(
                unique_id="@dj", web_kwargs={"httpx_kwargs": {"timeout": 10}},
            )
            client.is_live.side_effect = None
            for live in (True, False):
                client.is_live.return_value = live
                self.assertIs(await tiktok_liveness._probe("dj"), live)
            async def stall():
                await asyncio.sleep(60)

            client.is_live.side_effect = stall
            with patch.object(tiktok_liveness, "PROBE_TIMEOUT", 0.02):
                with self.assertRaises(TimeoutError):
                    await tiktok_liveness._probe("dj")
            self.assertEqual(client.web.close.await_count, 4)

    async def test_hard_block_is_killed_before_recovery(self):
        spawn = asyncio.create_subprocess_exec
        started = asyncio.Event()
        children = []
        script = """
import json
import sys
import time
if sys.argv[1] == 'blocked':
    while True:
        time.sleep(60)
elif sys.argv[1] == 'network':
    print(json.dumps({'error': ['ReadTimeout', 'stalled', "ReadTimeout('stalled')", True]}))
else:
    print(json.dumps({'live': sys.argv[1] == 'live'}))
"""

        async def spawn_fixture(executable, path, username, **options):
            process = await spawn(executable, "-c", script, username, **options)
            children.append(process)
            started.set()
            return process

        with (
            patch.object(asyncio, "create_subprocess_exec", side_effect=spawn_fixture),
            patch.object(tiktok_liveness, "PROCESS_TIMEOUT", 1),
        ):
            blocked = asyncio.create_task(tiktok_liveness.request_tiktok_is_live("blocked"))
            await started.wait()
            for _ in range(20):
                with self.assertRaisesRegex(tiktok_liveness.TikTokProbeError, "still active") as busy:
                    await tiktok_liveness.request_tiktok_is_live("live")
                self.assertIn("phase=busy", busy.exception.diagnostic)
            self.assertEqual(len(children), 1)
            with self.assertRaises(tiktok_liveness.TikTokProbeError) as timed_out:
                await blocked
            self.assertIn("timeout=True phase=executing", timed_out.exception.diagnostic)
            self.assertIsNotNone(children[0].returncode)

            with self.assertRaises(tiktok_liveness.TikTokProbeError) as network:
                await tiktok_liveness.request_tiktok_is_live("network")
            self.assertIn("ReadTimeout('stalled') timeout=True", network.exception.diagnostic)
            self.assertTrue(await tiktok_liveness.request_tiktok_is_live("live"))
            self.assertFalse(await tiktok_liveness.request_tiktok_is_live("offline"))
            self.assertEqual(len(children), 4)


if __name__ == "__main__":
    unittest.main()
