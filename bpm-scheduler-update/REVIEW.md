# BPM scheduler update

This is a complete updated copy of the current NAS backend at
`\\192.168.7.10\radiorouter\app\main.py`. The running service and the older
website-repository main.py have not been modified. No deployment/restart was performed.

## Review findings

The schedule table already contains nullable bpm_min/bpm_max columns, but
_db_schedule and _db_schedule_profiles did not expose them. Active programs are
resolved by get_active_schedule_block using the existing Brisbane schedule logic.
Recent BPM is embedded in ai_genre_detection.genres_json and extracted by the
existing matcher, which returns bpm and bpm_confidence. The detector timestamp
reaches Stage 3 as ai_genre_detected_at.

Contrary to the proposal's assumption, the current matcher already blends a
genre-derived BPM score into ai_score. Explicit schedule bounds now use the
existing genre_ai_score instead of that blend, followed by the new penalty.
Programs without bounds retain the original ai_score and ranking exactly,
including that pre-existing inferred BPM behavior.

## Changed sections in main.py

- Import math for finite-number validation.
- _db_schedule and _db_schedule_profiles: pass through nullable BPM bounds;
  tolerate databases without the optional columns, without a migration.
- build_genre_match_candidates: attach active bounds to enriched candidates.
- New _positive_bpm_value and stage3_bpm_details: validate values and compute diagnostics.
- stage3_adjusted_ranking: apply explicit schedule BPM scoring consistently to
  candidates, current relay scoring, and diagnostics.
- genre_match: add current_program, including configured BPM bounds.

## Formula

Distance d is the distance outside the configured range (zero inside).
Adjustment = -min(60, 0.1 * d^2) * confidence * freshness, rounded to 2 decimals.
No positive bonus is awarded. Final scores retain the existing zero floor and
hard exclusion behavior. A single bound is treated as an open-ended range.
Reversed valid bounds are normalized for scoring; invalid bounds are ignored.
Unknown/invalid BPM, zero confidence, missing timestamps and fully stale readings
receive no penalty. Diagnostics expose bpm_status and a null bpm_distance when
no reliable comparison is available. Raw measured BPM remains visible if stale.

For 105-124 BPM at confidence=1 and freshness=1:

| BPM | Distance | Adjustment |
| --- | --- | --- |
| 118 | 0 | 0 |
| 124 | 0 | 0 |
| 127 | 3 | -0.9 |
| 135 | 11 | -12.1 |
| 150 | 26 | -60 |
| Unknown | null | 0 |

Freshness reuses the existing full weight through 15 minutes and linear decay
to zero at 60 minutes. Low confidence reduces the penalty proportionally.
Existing inferred bpm_score/bpm_target/effective_bpm fields are retained for API
compatibility; bpm_min/bpm_max/bpm_distance/bpm_adjustment describe Stage 3's
explicit schedule adjustment. No half/double-time conversion is added.

## Verification

- Python syntax compilation passed.
- Six isolated-function test cases passed, covering examples, single bounds,
  invalid values, stale/low-confidence readings, anti-genre/speech exclusions,
  unknown-BPM selection, ranking outcomes, SQLite column compatibility, and
  /api/genre-match responses.
- 200 randomized five-candidate comparisons against the original implementation
  produced identical ordering and scores when schedule bounds were null.
- AST comparison confirmed only the five existing functions listed above changed;
  switching decisions, confirmation rounds, relay monitor, HLS/audio and TikTok
  functions remain unchanged.
- Reviewed the full diff in main.patch.

Run reproducible focused tests with `python test_bpm_scoring.py` from this folder.
Tests extract actual functions without importing the application or starting its
background jobs. They do not exercise the running FastAPI service or live audio.

The existing grouped-day active schedule resolution is preserved. The 60-point
cap and 0.1 coefficient are initial tuning choices, not production-calibrated
measurements. Deployment requires the existing NAS matcher that exposes
bpm, bpm_confidence and genre_ai_score. Database rows were only read.
