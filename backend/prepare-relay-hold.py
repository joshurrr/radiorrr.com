"""Prepare a focused router patch; --apply backs up and updates shared source."""
import ast
import difflib
import sys
from datetime import datetime
from pathlib import Path

SOURCE = Path(r"\\192.168.7.10\radiorouter\app\main.py")
HERE = Path(__file__).resolve().parent


def patched_source(source):
    tree = ast.parse(source)
    decision = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "stage3_switch_decision")
    lines = source.splitlines(keepends=True)
    old = "".join(lines[decision.lineno - 1:decision.end_lineno])
    new = old.replace(
        '    current_platform="TikTok",\n',
        '    current_platform="TikTok",\n    relay_selected_at=None,\n    now=None,\n',
        1,
    )
    marker = '    if current_score < STAGE3_CURRENT_MIN_SCORE:\n'
    hold = '''    # Score dips and emergency score escapes must respect the same hold as
    # ordinary ranking improvements. Only failure or explicit exclusions bypass it.
    if relay_selected_at is not None:
        decision_time = now or datetime.now(timezone.utc)
        dwell_elapsed = (decision_time - relay_selected_at).total_seconds()
        if dwell_elapsed < STAGE3_MIN_DWELL_SECONDS:
            return False, "minimum_dwell"
'''
    assert old.count(marker) == 1
    new = new.replace(marker, hold + marker, 1)
    assert new != old and "relay_selected_at=None" in new
    source = source.replace(old, new, 1)
    start = source.index('                        # Hold a newly promoted healthy DJ for 15 minutes before')
    end = source.index('                        # If the current relay is healthy but temporarily absent', start)
    source = source[:start] + source[end:]
    old_call = '''                            relay_is_healthy,
                            relay_platform or "TikTok",
                        )'''
    new_call = '''                            relay_is_healthy,
                            relay_platform or "TikTok",
                            relay_selected_at=stage3_relay_selected_at,
                            now=decision_time,
                        )'''
    assert source.count(old_call) == 1
    source = source.replace(old_call, new_call, 1)
    ast.parse(source)
    return source


def main():
    original = SOURCE.read_text(encoding="utf-8")
    candidate = patched_source(original)
    patch = "".join(difflib.unified_diff(original.splitlines(True), candidate.splitlines(True), fromfile="app/main.py", tofile="app/main.py"))
    (HERE / "relay-hold.patch").write_text(patch, encoding="utf-8")
    if "--apply" in sys.argv:
        assert SOURCE.read_text(encoding="utf-8") == original, "Router source changed during preparation"
        backup = SOURCE.with_name("main.py.before-relay-hold-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        backup.write_text(original, encoding="utf-8")
        SOURCE.write_text(candidate, encoding="utf-8")
        assert SOURCE.read_text(encoding="utf-8") == candidate
        print("Shared source updated; backup:", backup)
    else:
        print(patch)


if __name__ == "__main__":
    main()
