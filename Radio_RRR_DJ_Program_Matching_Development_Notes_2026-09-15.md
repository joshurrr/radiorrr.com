# Radio RRR — DJ Program Matching System
## Development Notes — 15 September 2026

This document records the matching and automatic DJ-selection work completed during tonight's Radio RRR development session.

---

## 1. The goal

The objective is for Radio RRR to automatically choose the best currently-live TikTok DJ for the **main/default station relay**, based on what Radio RRR wants to hear at that time.

The system is intended to reproduce the type of decision previously being made manually:

> Who is live right now, what are they actually playing, how well does that fit the current Radio RRR programme, and is this DJ worth keeping on air?

The system should **not** behave like a simple popularity ranking or constantly jump between DJs.

The desired behaviour is:

```text
RRR Programme
     ↓
Current genre targets
     ↓
Currently-live DJs
     ↓
Current AI genre detection
     +
Learned DJ genre profile
     ↓
Program Match score
     ↓
Stability + continuity checks
     ↓
Stream validation
     ↓
Main Radio RRR relay
```

---

# 2. Two different types of DJ selection

Radio RRR has two separate concepts.

## A. Main/default station DJ

This is the DJ selected automatically by Radio RRR.

The selection is shared by the station and supplies the main:

```text
radio.mp3
```

and the default live video.

Stage 3 controls this selection.

## B. Personal listener DJ selection

When a visitor clicks another live DJ, that should only affect that visitor's video experience.

It must **not** globally switch the Radio RRR station.

The frontend therefore uses:

```text
/api/live-stream?dj=USERNAME
```

for personal DJ relay selection.

The old global:

```text
/api/live/switch?dj=USERNAME
```

should not be used for normal visitor clicks.

This separation is important:

```text
Station schedule
      ↓
Main/default DJ
      ↓
Shared station relay

Visitor clicks DJ
      ↓
Personal DJ relay
      ↓
That visitor only
```

---

# 3. The three major components

The matching architecture was deliberately separated into three jobs.

## AI Genre Detection

Answers:

> What is this DJ playing right now?

It provides current observations such as:

- Tech Trance
- Trance
- Progressive Trance
- House
- Techno
- etc.

It may also provide confidence percentages.

Example:

```text
Tech Trance       61%
Trance            48%
Progressive Trance 12%
Electro House      8%
Psy-Trance         7%
```

AI detection is **current evidence**, not a permanent description of the DJ.

---

## Learned DJ Profile

Answers:

> What does this DJ tend to play historically?

The system builds a learned profile from previous AI observations.

The profile is based on historical observations and currently uses a rolling period of approximately 30 days.

The profile becomes more trustworthy as more observations are collected.

### Profile maturity

Current maturity scaling:

| Samples | Maturity |
|---:|---:|
| 0–2 | 0% |
| 3–4 | 25% |
| 5–9 | 50% |
| 10–19 | 75% |
| 20+ | 100% |

This prevents a DJ with one or two observations from having an overly powerful historical profile.

---

## Genre Matcher

Answers:

> How suitable is this DJ for the current Radio RRR programme?

The matcher compares:

- current AI genres
- learned historical genres
- static DJ genres when no learned profile exists

against the current RRR programme targets.

The matcher is implemented in:

```text
radio_rrr_genre_matcher.py
```

---

# 4. Genre matching

The matcher understands exact genres and selected parent/child relationships.

Examples:

```text
Tech Trance → Trance
Tech Trance → Techno
Tech House → House
Tech House → Techno
Progressive House → House
Deep House → House
Progressive Trance → Trance
Hard Trance → Trance
Melodic Techno → Techno
Hard Techno → Techno
```

These relationships are weighted rather than treated as identical genres.

For example, Tech Trance is considered a stronger match for Trance than for Techno.

This prevents the system from requiring exact genre names before recognising that two genres are musically related.

---

# 5. Programme targets

The programme determines what Radio RRR wants at that time.

The current weekday schedule wired into Stage 3 is:

### 00:00–04:00 — Early Mornings

```text
Tech Trance 35
Techno      30
Dark Electro 20
Trance      15
```

### 04:00–08:00 — Morning Drive

```text
Chill       35
Downtempo   25
Ambient     20
Deep House  20
```

### 08:00–12:00 — Day Drive

```text
80s          30
Synthwave    25
Chill Electro 25
Nu-Disco     20
```

### 12:00–16:00 — Afternoon

```text
House        35
Deep House   25
Progressive  20
Funky House  20
```

### 16:00–20:00 — Prime Time

```text
House       35
Tech House  25
Trance      20
Progressive 20
```

### 20:00–24:00 — Night Drive

The agreed Night Drive weighting is:

```text
Tech Trance 40
House       30
Techno      20
Trance      10
```

The schedule uses Australia/Brisbane local time.

---

# 6. Stage 2 — learned profiles

Stage 2 added historical genre intelligence to the matcher.

The basic scoring model was:

```text
70% current AI
20% learned profile
10% viewer tie-break
```

The learned profile is matured according to the number of historical samples.

Stage 2 was diagnostic only. It did **not** control the main relay.

The `/api/genre-match` endpoint was used to inspect the ranking.

Example results showed that a DJ could have:

```text
AI score
Profile score
Viewer bonus
Final score
```

and a breakdown against each programme target.

---

# 7. Stage 2.1 — profile maturity

Stage 2.1 added maturity scaling to the learned profile.

This was important because a DJ with only a handful of observations should not have the same historical authority as a DJ with dozens of observations.

Examples from testing:

```text
16 samples → 75%
17 samples → 75%
9 samples  → 50%
4 samples  → 25%
0 samples  → 0%
```

This was successfully deployed and tested.

---

# 8. Stage 3 — automatic relay selection

Stage 3 changed the role of the matcher.

It became capable of influencing the **main/default relay**.

The basic process is:

```text
1. Get currently-live DJs
2. Enrich them with AI data
3. Add learned profiles
4. Determine current programme targets
5. Score/rank the live DJs
6. Select the strongest candidate
7. Apply continuity/stability rules
8. Validate the candidate's TikTok stream
9. Start/replace the main relay
```

The existing stream validation remains important.

The system does not simply trust the score and switch.

---

# 9. Stream validation

Before a new DJ becomes the main relay, Radio RRR must successfully obtain their TikTok stream and start the FFmpeg relay.

The HLS output is then checked.

The relay is considered ready only after valid HLS output is available.

This protects the station from selecting a DJ whose metadata looks good but whose actual stream cannot be relayed.

The existing relay health checks also monitor:

- playlist availability
- recent HLS segment activity
- FFmpeg process health

---

# 10. Stability protection

Stage 3 does not immediately switch DJs because of one scoring change.

The same candidate must win:

```text
3 consecutive decisions
```

before it can replace the current DJ.

The monitor runs approximately every:

```text
10 seconds
```

Therefore the system normally needs approximately 30 seconds of consistent preference before switching.

This helps prevent rapid DJ bouncing.

---

# 11. Continuity protection

During testing, an important design decision was made:

> A DJ who is already playing and sounds good should not be removed simply because another DJ has a slightly higher mathematical score.

The current relay therefore receives strong protection.

The current Stage 3 replacement margin is:

```text
6 points
```

A healthy current DJ is kept unless another candidate is materially better.

The philosophy is:

```text
Current DJ healthy
        ↓
Keep them
        ↓
Is another DJ clearly better?
        ↓
No → stay
Yes → require stability + validation
```

This is intentionally conservative.

Radio RRR is a radio station, not a constantly changing leaderboard.

---

# 12. AI freshness

An early Stage 3 implementation required AI detection to be less than 15 minutes old.

Testing showed this was too restrictive.

A DJ with a good learned profile could suddenly become ineligible simply because their most recent AI observation was old.

That was changed.

AI freshness is now treated as a **confidence/weighting factor**, not a hard eligibility gate.

Current concept:

```text
Fresh AI
   ↓
Full AI influence

Older AI
   ↓
Reduced AI influence

No current AI
   ↓
Learned profile can still contribute
```

The current freshness bands are approximately:

```text
0–15 minutes   → full AI influence
15–60 minutes  → gradual reduction
60+ minutes    → AI contribution decays to zero
```

The historical learned profile remains useful.

---

# 13. Learned profile when AI is stale

When current AI becomes stale, the learned profile receives a larger relative share of the scoring influence.

This prevents a DJ with a strong established history from effectively becoming invisible merely because a new AI sample hasn't arrived.

The principle is:

```text
Fresh AI:
current music matters most

Stale AI:
historical behaviour matters more

No AI + mature profile:
historical profile can still nominate the DJ

No AI + no profile:
do not automatically promote the unknown DJ
```

---

# 14. Current DJ does not need a minimum score

Another important change was made after testing.

Originally, a candidate needed a minimum score to be considered.

That caused a problem.

For example, `guenthervictor` had strong historical evidence but his score temporarily fell because the current AI observation had aged.

The result was effectively:

> "This DJ has a good history, but his current score isn't high enough, so treat him as unsuitable."

That is not desirable for a radio station.

The logic was therefore changed so that:

**A healthy current DJ does not need to meet a minimum score to remain on air.**

The score is mainly used to decide whether a **replacement is better**.

This is a major conceptual distinction.

---

# 15. Safety fallback

If there is no active healthy relay, Radio RRR still needs audio/video.

Stage 3 therefore has a fallback mechanism.

If automatic genre selection cannot provide a usable candidate, the system tests live DJs until it finds a working stream.

This is what happened during the deployment tests.

For example:

```text
[Stage 3] Candidate @guenthervictor ...
[Relay] Safety fallback testing @guenthervictor
[Relay] HLS relay ready
[Audio] MP3 relay started
[Relay] Safety fallback validated
```

The important principle is:

> A radio station should prefer an imperfect live DJ over silence when no better validated option is available.

---

# 16. The Program Match score on the website

The website now displays a small score on live DJ cards.

The label was deliberately changed from:

```text
MATCH
```

to:

```text
PROGRAM MATCH
```

This is a better description because the number is **not a rating of the DJ**.

It means:

> How well does this DJ match what Radio RRR is currently looking for?

This distinction is important for DJ-facing presentation.

A DJ seeing:

```text
PROGRAM MATCH 23
```

should not interpret it as:

> "Radio RRR thinks I'm a 23/100 DJ."

It means:

> "Your current music is a relatively weak match for the current RRR programme."

The main/featured DJ also displays the Program Match score.

---

# 17. Real-world testing tonight

The system was tested live with DJs including:

```text
guenthervictor
one_six_zero
michael_night_
```

An early diagnostic ranking showed `guenthervictor` strongly matched the Night Drive targets.

Later, after AI freshness effects were introduced, the score changed substantially.

This testing demonstrated why AI freshness should influence confidence rather than act as a hard gate.

It also showed that human listening judgement remains valuable.

In particular, the current DJ was subjectively preferred by the station operator.

That led to the stronger continuity rule:

> Do not replace a DJ who is working and sounding good unless another candidate is clearly better.

---

# 18. What the system does NOT yet understand

The matcher is currently a genre suitability system.

It does not yet fully understand subjective musical quality.

For example, a human listener can hear:

- good mixing
- poor mixing
- boring selection
- excellent progression
- an inappropriate track
- an unusually good set

while the current system mainly sees genre evidence.

Therefore the Program Match score should be viewed as:

```text
programme compatibility
```

rather than:

```text
DJ quality rating
```

---

# 19. Future anti-genre / negative signals

A future improvement discussed tonight is the use of **negative genre signals**.

For example:

```text
Opera
Classical
Country
Talk
News
Religious
```

could be treated as undesirable for certain RRR programmes.

The preferred future model is not simply:

```text
positive genre = good
negative genre = bad
```

but something more intelligent.

### Hard veto

Persistent, highly-confident detection of a clearly incompatible category could make a DJ temporarily ineligible.

Example:

```text
Classical 91%
```

for a sustained period.

### Soft penalty

Something merely outside the target could reduce the score without immediately removing the DJ.

### Grace period

Short transitions should be ignored.

Examples:

```text
5-second orchestral intro → ignore
DJ talking between tracks → ignore
short genre transition → ignore
```

### Persistence

A negative genre should preferably persist across multiple observations before triggering a replacement.

This prevents the system from reacting to a momentary false detection.

---

# 20. Future concept: musical common sense

The long-term goal is for the selector to behave more like a human radio programmer.

Conceptually:

```text
Positive match
      +
Historical fit
      +
Current music
      +
Programme target
      +
Continuity
      +
Stream quality
      -
Persistent incompatible music
      ↓
Final programming decision
```

The system should not behave like:

```text
"Opera detected for 2 seconds.
REMOVE DJ."
```

It should behave more like:

```text
"Current DJ has been a good RRR match.
There was a brief unusual transition.
Stay with them."
```

---

# 21. Future feedback loop

The operator's real-world feedback can eventually become another useful signal.

Examples:

```text
"Keep this DJ"
"This DJ was perfect"
"Too far outside the RRR sound"
"Should have stayed with the previous DJ"
"Great Night Drive DJ"
```

Over time this could help Radio RRR learn that **genre matching is necessary but not sufficient**.

The ultimate model could become:

```text
AI genre detection
        +
Historical DJ profile
        +
Programme matching
        +
Negative/exclusion signals
        +
Human/operator feedback
        +
Continuity
        +
Stream reliability
        ↓
Radio RRR programming decision
```

---

# 22. Current Stage 3 philosophy

The current system should be thought of as:

### Prefer
A DJ who:

- fits the current programme
- has useful current AI evidence
- has a good learned profile
- has a reliable stream
- has been consistently suitable

### Avoid
A DJ who:

- has no useful genre evidence
- is persistently outside the programme
- has a broken stream
- repeatedly fails relay validation

### Protect
A DJ who:

- is already on air
- has a healthy relay
- sounds appropriate
- is not clearly beaten by another candidate

---

# 23. Important architectural rule

The following distinction should remain intact:

```text
AI Detector
"What is this DJ playing?"

Program Guide
"What does RRR want right now?"

Genre Matcher
"How well does this DJ fit RRR right now?"

Relay Selector
"Should RRR actually switch?"

Listener Override
"What DJ does this individual listener want to watch?"
```

Keeping these responsibilities separate makes the system easier to improve without breaking the streaming architecture.

---

# 24. Current status

At the end of tonight's work:

- Stage 2 learned profiles are working.
- Stage 2.1 maturity scaling is working.
- Stage 3 automatic candidate selection is deployed.
- Current programme targets are being passed into the matcher.
- AI freshness is weighted rather than being a hard gate.
- Learned profiles remain useful.
- Current relay continuity protection is active.
- Candidate stability requires 3 consecutive wins.
- Replacement requires a substantial score advantage.
- TikTok stream validation remains mandatory.
- Safety fallback remains active.
- Personal listener DJ selection remains separate from the global relay.
- Program Match scores are displayed on the website.
- The website terminology is now **PROGRAM MATCH**.

The system is now at the point where real-world listening feedback should drive the next tuning decisions rather than making more theoretical changes.

---

## 25. Next logical improvements

Recommended order:

1. **Observe Stage 3 in real use**
2. Record which DJs the system selects versus human preference
3. Tune scoring based on those observations
4. Add persistent negative/exclusion genre signals
5. Add smarter handling of short genre transitions
6. Improve schedule weighting based on actual RRR programming results
7. Potentially add operator feedback as a learning signal
8. Only then consider more aggressive automatic switching

The guiding principle should remain:

> **Keep the station sounding good first. Optimise second.**
