# Relay hold correction

The router's existing 15-minute dwell guard exempted `below_quality_floor` and
`escape_poor_current`. A changing audio classification could therefore replace a
healthy DJ after three 10-second decisions, even shortly after promotion.

The hold now runs inside `stage3_switch_decision`, before every score-driven
takeover. The monitor passes its last successful promotion timestamp and current
decision time. A failed relay or explicit anti-genre/speech exclusion still bypasses
the hold; operator Featured priority is unchanged. After 15 minutes, the existing
score floor, escape rule, improvement margin and confirmation checks still apply.

Shared `app/main.py` was backed up and updated. Validation passed with
`python tests/relay-hold.py`, covering one-minute score dips, the 15-minute
boundary, failure and content exclusions, margin protection and monitor wiring.

Activation is pending: SSH refused connections and computer/browser automation
could not initialize. In the NAS terminal run:

```bash
bash /mnt/user/radiorouter/tools/activate-relay-hold.sh
```

This builds the image, copies the source into the existing `radiorouter` container,
restarts only that container, and checks status. It preserves container environment
(including heartbeat credentials), ports and mounts. Playback will briefly pause.
Runtime deployment and a full 15-minute listening observation remain unverified.
