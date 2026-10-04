"""Exercise the router's real decision function and monitor wiring."""
import ast
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("prepare", Path("backend/prepare-relay-hold.py"))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
source = prepare.SOURCE.read_text(encoding="utf-8")
if "relay_selected_at=None" not in source:
    source = prepare.patched_source(source)
tree = ast.parse(source)
constants = {"STAGE3_MIN_SCORE", "STAGE3_CURRENT_MIN_SCORE", "STAGE3_ESCAPE_CURRENT_MAX_SCORE", "STAGE3_ESCAPE_MIN_IMPROVEMENT", "STAGE3_SWITCH_MARGIN", "STAGE3_MIN_DWELL_SECONDS"}
nodes = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name in {"relay_identity_key", "stage3_switch_decision"})]
ns = dict(datetime=datetime, timezone=timezone)
exec(compile(ast.Module(body=nodes, type_ignores=[]), "router-decision", "exec"), ns)
decide = ns["stage3_switch_decision"]
now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def decision(current, candidate, age=60, healthy=True, excluded=None):
    item = {"username": "current", "score": current}
    if excluded:
        item[excluded] = True
    return decide({"username": "next", "score": candidate}, item, "current", healthy, relay_selected_at=now - timedelta(seconds=age), now=now)


# One minute score fluctuation, below-floor recovery and emergency escape all hold.
for current, candidate in [(40, 65), (29, 35), (10, 27)]:
    assert decision(current, candidate) == (False, "minimum_dwell")
    assert decision(current, candidate, age=899) == (False, "minimum_dwell")
    assert decision(current, candidate, age=900)[0] is True
# A real failure or explicit exclusion can still recover immediately.
assert decision(40, 65, healthy=False) == (True, "no_healthy_relay")
for excluded in ("anti_genre_excluded", "speech_excluded"):
    assert decision(40, 25, excluded=excluded) == (True, "current_relay_anti_genre")
assert decision(40, 45, age=1000) == (False, "current_relay_protected")
assert decide(None, None, "current", True) == (False, "no_eligible_candidate")
assert decide({"username": "current", "score": 40}, {"score": 40}, "current", True) == (False, "current_relay_is_best")
# The actual monitor must pass the promotion time and evaluation time to the guard.
monitor = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "relay_monitor")
calls = [n for n in ast.walk(monitor) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "stage3_switch_decision"]
assert len(calls) == 1
assert {k.arg: ast.unparse(k.value) for k in calls[0].keywords} == {"relay_selected_at": "stage3_relay_selected_at", "now": "decision_time"}
print("PASS: score dips hold for 15 minutes, boundary release, failure/exclusion recovery, ordinary margin protection and monitor timing wiring.")
