"""Exercise the deployed relay monitor without starting the app or touching the NAS."""

import ast
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


SOURCE = Path(os.environ.get(
    "RRR_ROUTER_SOURCE", Path(__file__).resolve().parents[1] / "backend/main.py"
))


class EndMonitor(BaseException):
    pass


class MissingRelayHandoff(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, stage3=False, replacement=True, stale=False, featured=None):
        names = {"relay_monitor", "relay_identity_key", "stage3_switch_decision"}
        tree = ast.parse(SOURCE.read_bytes())
        functions = [node for node in tree.body if getattr(node, "name", "") in names]
        on_air = Mock()
        on_air.poll.return_value = None
        candidate = {"username": "next", "platform": "TikTok", "score": 40}
        now = datetime(2026, 11, 15, tzinfo=timezone.utc)
        ticks = 0
        running = False

        async def sleep(seconds):
            nonlocal now, ticks
            ticks += 1
            if ticks == 3:
                raise EndMonitor
            now += timedelta(seconds=300 if ticks == 1 else 10)

        async def live_djs():
            return [candidate] if replacement and ticks else []

        def healthy(playlist, _directory):
            return (playlist == "current.m3u8" and not (stale and ticks)) or running

        async def stop():
            ns.update(relay_process=None, relay_username=None, RELAY_PLAYLIST="index.m3u8")

        async def promote(stream, username, platform):
            nonlocal running
            self.assertIs(ns["relay_process"], on_air)
            self.assertEqual(ns["relay_username"], "current")
            if not stage3:
                # An unsuccessful validation must leave the current feed live.
                raise RuntimeError("candidate failed to advance")
            running = True
            ns.update(relay_username=username, RELAY_PLAYLIST="candidate.m3u8")

        def start(stream, username):
            ns["relay_process"] = on_air
            return on_air, "candidate.m3u8"

        async def ready(*args, **kwargs):
            nonlocal running
            if not stale:
                raise RuntimeError("candidate failed to advance")
            running = True
            ns["RELAY_PLAYLIST"] = "candidate.m3u8"

        ns = dict(
            print=Mock(), asyncio=SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
            datetime=SimpleNamespace(now=lambda _tz: now), timezone=timezone,
            timedelta=timedelta, relay_lock=asyncio.Lock(), relay_process=on_air,
            relay_username="current", relay_platform="TikTok", relay_failed_until={},
            RELAY_PLAYLIST="current.m3u8", RELAY_DIR="relay", STAGE3_ENABLED=stage3,
            STAGE3_FAILURE_COOLDOWN=300, STAGE3_DECISION_INTERVAL=10,
            STAGE3_REQUIRED_WINS=3, cleanup_retired_relay_outputs=Mock(),
            get_live_djs=live_djs, relay_output_is_healthy=healthy,
            stop_relay_async=AsyncMock(side_effect=stop),
            select_featured_live_dj=Mock(return_value=None),
            is_featured_identity=Mock(return_value=featured),
            build_genre_match_candidates=AsyncMock(return_value=([], [candidate], [candidate])),
            stage3_adjusted_ranking=Mock(return_value=[candidate]),
            select_stage3_candidate=Mock(return_value=(candidate, "eligible")),
            get_platform_stream=AsyncMock(return_value={"url": "fixture"}),
            validate_and_promote_relay=AsyncMock(side_effect=promote),
            start_relay=Mock(side_effect=start), wait_for_relay_ready=AsyncMock(side_effect=ready),
            start_audio_relay=Mock(),
        )
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), ns)
        with self.assertRaises(EndMonitor):
            await ns["relay_monitor"]()
        return ns

    async def test_missing_featured_dj_hands_off_after_grace(self):
        ns = await self.exercise(stage3=True, featured=1)
        self.assertEqual(ns["RELAY_PLAYLIST"], "candidate.m3u8")
        self.assertEqual(ns["relay_username"], "next")
        ns["stop_relay_async"].assert_not_awaited()
        ns["validate_and_promote_relay"].assert_awaited_once()

    async def test_no_replacement_keeps_healthy_relay(self):
        ns = await self.exercise(replacement=False)
        self.assertEqual(ns["RELAY_PLAYLIST"], "current.m3u8")
        ns["stop_relay_async"].assert_not_awaited()

    async def test_failed_fallback_keeps_relay_and_applies_cooldown(self):
        ns = await self.exercise()
        self.assertEqual(ns["RELAY_PLAYLIST"], "current.m3u8")
        ns["stop_relay_async"].assert_not_awaited()
        ns["validate_and_promote_relay"].assert_awaited_once()
        self.assertEqual(len(ns["relay_failed_until"]), 1)

    async def test_stale_output_stops_and_allows_normal_fallback(self):
        ns = await self.exercise(stale=True)
        self.assertEqual(ns["relay_username"], "next")
        ns["stop_relay_async"].assert_awaited()
        ns["start_relay"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
