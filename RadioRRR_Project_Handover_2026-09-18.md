# Radio RRR — Project Handover / Technical State

**Project:** Radio RRR / RadioRouter  
**Website:** radiorrr.com  
**Handover date:** 18 September 2026  
**Purpose:** Technical handover covering the current architecture, functionality, known issues, conventions, deployment, schedule/genre matching, and frontend behaviour.

---

## 1. Project overview

Radio RRR is a live-DJ discovery and routing platform.

The public site at **radiorrr.com** provides:

- Live DJ discovery
- Live DJ video playback
- Live audio-only playback
- A main live radio stream
- Live DJ cards
- Favourites
- Current programming/schedule display
- Program-to-DJ genre matching
- Live detected genre display
- DJ submission functionality
- A scheduler/admin workflow

The backend is called **RadioRouter** and runs in Docker on an Unraid NAS.

A separate **rrr-genre-detector** Docker container analyses the live audio stream and supplies live genre detection/matching information.

The overall goal is to automatically identify which currently-live DJ best matches the current Radio RRR program, while still allowing the site to display and browse all currently live DJs.

---

# 2. Core architecture

## 2.1 Frontend

The frontend is primarily:

- `index.html`
- `style.css`
- `script.js`
- static assets/images

The site is a static frontend hosted for `radiorrr.com`.

The frontend:

1. Loads the current schedule.
2. Loads live DJ information from RadioRouter APIs.
3. Displays the main selected/current live DJ.
4. Displays other live DJs.
5. Plays HLS video/audio.
6. Provides an MP3/audio-only stream.
7. Displays detected genres.
8. Displays program-match percentages.
9. Handles favourites.
10. Provides navigation and DJ submission UI.

The frontend has been edited frequently, so **the exact latest uploaded working file must always be inspected before making changes**. Do not reconstruct `index.html` from memory.

---

## 2.2 Backend

Backend Docker container:

**Container:** `radiorouter`

Image commonly used:

`radiorouter:latest`

Backend technology:

- Python
- FastAPI
- SQLite
- FFmpeg
- TikTokLive / TikTok live discovery

Database:

`/data/radiorouter.db`

The backend exposes APIs including:

- `/api/live`
- `/api/live-stream`
- `/api/genre-match`

There are additional scheduler/admin APIs in the current backend.

The backend monitors live TikTok DJs and can relay live audio/video using FFmpeg/HLS.

---

## 2.3 Genre detector

Docker container:

**`rrr-genre-detector`**

Model directory:

`/mnt/user/rrr-genredetect/models`

Technology used includes:

- Python
- Essentia
- TensorFlow
- FFmpeg/audio processing

The detector was changed to use:

`RADIO_ROUTER_URL`

with:

`/api/live-stream`

as its source.

The detector processes the RadioRouter live audio and generates live detected genres.

Genre detection has historically updated at approximately **30-second intervals**.

---

# 3. NAS / Docker environment

The project runs on a TerraMaster F4-424 Pro NAS with:

- 32 GB RAM
- Unraid
- Docker
- Home Assistant also running as a VM

Known project containers:

- `radiorouter`
- `rrr-genre-detector`
- `cloudflared` container, previously shown as `xenodochial_roentgen`

The RadioRouter backend stores its SQLite database in:

`/data/radiorouter.db`

The genre detector models are stored under:

`/mnt/user/rrr-genredetect/models`

The public site is exposed through the project's existing hosting/cloud setup. Cloudflare Tunnel has also been present in the NAS environment.

---

# 4. Database

Known SQLite tables include:

- `favourite_djs`
- `live_djs`
- `schedule`
- `schedule_genres`
- `ai_genre_detection`

The `schedule_genres` table currently contains a large genre set. A recent inspection showed approximately **144 genres**.

Examples encountered include:

- Chill
- Downtempo
- Ambient
- Deep House
- Chill Electro
- Electro
- Electro House
- House
- Tech House
- Trance
- Progressive
- Tech
- Techno
- Dark Electro
- Bassline
- 80s

Do not assume the full current genre list from this document; inspect the live database when making backend/scheduler changes.

---

# 5. DJ lifecycle / states

The project needs to distinguish between several DJ states.

Conceptually these are:

1. Approved / active
2. Currently live
3. Temporarily ineligible because their detected content does not match the current programming rules
4. Manually disabled
5. Banned

This distinction matters because a DJ being unsuitable for the current program should not necessarily mean that the DJ is permanently disabled.

There are admin commands/scripts used on the NAS for DJ management, including:

- `adddj`
- `djadd`
- `djdis`
- `djdel`

Exact current command implementation should be inspected on the NAS before relying on assumptions.

---

# 6. Schedule system

The schedule is stored/read from SQLite and is also intended to be editable through the scheduler/admin interface.

Recent weekday schedule blocks were explicitly changed to:

### 16:00 – 20:00
**Dinner Warm ups**

### 20:00 – 00:00
**Prime Time**

Other established programming concepts include:

### 04:00 – 08:00
**Sunrise Sessions**

A previously used afternoon block called:

**Afternoon Beats**

replaced an older name:

**Arvo Lunch Beats**

The overnight 00:00–04:00 programming was later changed/named:

**Early Mornings**

The exact current overnight target genres have changed during development, so inspect the database/current scheduler rather than assuming historical settings are current.

The schedule includes target genres and genre weighting.

Example diagnostic output previously showed a program with:

- Chill
- Downtempo
- Ambient
- Deep House

with each genre carrying approximately 25% weighting.

Another recent log showed:

`[Schedule] Active program: Dinner Warm ups | House · Tech House · Trance · Progressive`

The scheduler logs:

`[Schedule] Loaded schedule from SQLite`

and then identifies the active program.

---

# 7. Genre matching

The core routing idea is:

**Current schedule → target genres → compare against live DJ detected genres → rank DJs → select the best suitable live DJ.**

A diagnostic endpoint exists:

`/api/genre-match`

It has been used to inspect:

- Current program
- Target genres
- Genre weights
- Candidate DJs
- Match scores
- Ranking

Example stage logging has included:

`[Stage 3] Candidate @imdjfifty50 score=22.5 wins=1/3`

The precise scoring algorithm should be treated as implementation-specific and inspected in `main.py` before modifying.

The desired frontend behaviour is that **other live DJ cards are ordered from best match to worst match for the current schedule**.

This lets the operator immediately see which currently-live DJ would be selected next.

---

# 8. Frontend DJ card requirements

There are two conceptual classes of cards:

## Main/current DJ card

The primary card shows the currently selected/main live DJ.

It has included:

- DJ profile image
- Live status
- Program match %
- Current program
- Genre information
- Video/audio controls

## Other live DJ cards

The other live DJ cards should:

- Be ordered by their match against the current program
- Show the same general **Program Match %** visual treatment used by the main card
- Show **live detected genre bubbles**
- Not show the old detector-line/percentage presentation

This was deliberately changed to reduce visual complexity.

The live detected genre bubbles should represent the genres currently detected from the DJ's stream.

Do not revert these cards to the old detector-line/percentage UI unless explicitly requested.

---

# 9. Current schedule UI requirement

A recent frontend design change was requested so that:

**Current schedule name + genre bubbles**

appear inside **one single shared border/container**.

The desired visual structure is effectively:

```text
┌──────────────────────────────────────────────┐
│ Dinner Warm ups                              │
│                                              │
│ [HOUSE] [TECH HOUSE] [TRANCE] [PROGRESSIVE] │
└──────────────────────────────────────────────┘
```

rather than separate bordered areas around the schedule name and genres.

The exact current CSS should be inspected before modifying because several iterations have changed spacing, borders, card sizing and mobile/desktop behaviour.

---

# 10. Live audio/video architecture

This is one of the most fragile parts of the project.

The site uses HLS for live playback and also provides an audio-only stream.

The key requirements are:

- The main live video stream should remain the primary/main stream.
- The audio-only MP3 stream should correspond to the same main live stream.
- Clicking another DJ should not permanently break the main stream.
- Switching between DJs should not leave multiple competing playback pipelines running.
- Refreshing the page should allow the user to unmute and continue listening.
- Audio must not unexpectedly mute after approximately 10 seconds.
- Playback must not freeze after approximately one minute.
- Audio must continue when the browser tab loses focus / the user switches to another browser tab.

There have been multiple regressions where these bugs returned after otherwise unrelated frontend changes.

Therefore, **do not casually rewrite the HLS/player lifecycle code**.

When changing unrelated UI/CSS, preserve the existing player logic exactly unless the playback bug itself is the requested target.

---

# 11. Known HLS/audio bugs and history

Several specific bugs have appeared repeatedly:

## Bug A — audio mutes after unmute

Typical behaviour:

1. Refresh page.
2. Click unmute.
3. Audio plays for roughly 10 seconds.
4. Audio then mutes itself.

This has previously been fixed and later reintroduced by restoring/merging older frontend code.

## Bug B — playback freezes

After approximately a minute, the video/audio can freeze.

This has also previously been fixed and later reintroduced.

## Bug C — changing browser tabs mutes audio

A recent regression caused audio to mute when the user switched to another browser tab.

This is particularly important because the Radio RRR use case is listening while doing other work.

The player should not rely on page visibility changes to intentionally mute the stream.

If browser autoplay/media policy is involved, distinguish actual browser policy from JavaScript code that explicitly pauses/mutes the player.

## Bug D — clicking another DJ can interfere with main playback

Historically:

- Main DJ stream works.
- Clicking another DJ starts another stream.
- In some versions this caused freezing or stream conflicts.

The intended architecture is to keep the main stream authoritative and avoid multiple unnecessary HLS pipelines.

---

# 12. HLS implementation guidance

When debugging HLS:

1. Identify exactly which `<video>` / `<audio>` element is the main player.
2. Identify where HLS.js is created.
3. Identify every `play()`, `pause()`, `muted =`, `volume =`, `src =`, `load()`, and HLS `destroy()` call.
4. Identify all `visibilitychange`, `blur`, `focus`, `pagehide`, and `pageshow` handlers.
5. Identify autoplay recovery logic.
6. Identify any timer that can call pause/mute.
7. Identify any code that recreates HLS after a stall.
8. Make the smallest possible change.
9. Test:
   - same tab
   - switching tabs
   - refresh
   - unmute
   - 1+ minute playback
   - switching DJ
   - returning to main DJ
   - audio-only mode
   - mobile browser if relevant

Do not assume the bug is server-side just because playback freezes.

---

# 13. Frontend branding / navigation

The site has gone through several branding/navigation changes.

Current/established copy includes:

**Top line:**

`LIVE DJs · BROADCASTING 24/7`

Other headline/slogan copy used:

`LIVE DJ VIDEOS — NO PLAYLISTS`

and:

`DISCOVER NEW MUSIC — NO PLAYLIST REQUIRED`

Navigation changes included:

- Removed `Socials`
- Added `Twitch`
- Moved `Add a DJ` into `Tools`
- Changed `Live DJs` menu label to `Live Video`
- Added `Live Audio`

The Live Audio section provides the MP3/audio-only experience and visualiser.

---

# 14. DJ card visual design

The DJ cards have been iterated heavily.

Established direction:

- DJ profile photo used as the card background
- Transparent/translucent UI treatment
- Avoid unnecessary double borders
- Mobile and desktop layouts differ
- Desktop has been configured for a dense multi-card layout, including a target of roughly 7 cards across in one version
- Mobile uses vertically arranged cards

Card sizing and spacing have been tuned multiple times.

A previous issue occurred where cards became too large.

Another issue caused excessive requests/page disappearance and was subsequently fixed.

Therefore, avoid broad CSS rewrites.

---

# 15. Program Match display

The main DJ card has a **Program Match %** indicator.

The desired behaviour is to reuse the same visual treatment on the other live DJ cards.

The percentage should correspond to the DJ's match against the current active schedule/program.

This is distinct from raw AI detector percentages.

Important distinction:

- **Program Match %** = how well the DJ matches the current Radio RRR program.
- **Live detected genres** = the genres detected from that DJ's current stream.

The other cards should show the latter as genre bubbles, while retaining the program match percentage visual.

---

# 16. Anti-genre / exclusion rules

The project has a concept of an **anti-genre list**.

Purpose:

Prevent programming slots from selecting DJs whose live content conflicts with the desired program.

One known requirement was to avoid:

- Hardcore
- Talking / spoken content

during certain slots.

Anti-genres should be evaluated as part of the eligibility/routing process rather than simply being treated as positive target genres.

The exact current anti-genre implementation and configuration should be inspected before changing it.

---

# 17. Genre list evolution

Genres have been expanded over time.

Specific requested additions/changes included:

- `bassline`
- More frequent `Trance`
- `80s` added to Thursday and Friday afternoons

There are also multiple variations in the database such as:

- Chill Electro
- Electro
- Electro House

Do not assume string matching is case-insensitive, normalized, or synonym-aware without checking the current implementation.

---

# 18. Scheduler administration

A scheduler admin package/zip was added to the project.

The backend was updated at:

`/mnt/user/radiorouter/app/main.py`

A Docker image was then built:

`radiorouter:latest`

and the container restarted.

Recent logs confirmed:

`[Schedule] Loaded schedule from SQLite`

followed by:

`[Schedule] Active program: Dinner Warm ups | House · Tech House · Trance · Progressive`

The scheduler is intended to make program/schedule editing easier and to ensure the genre matcher reads the same schedule data.

---

# 19. Recent working-state history

There have been several rounds of restoring older working `index.html` files and then applying individual changes.

Examples of uploaded versions include filenames similar to:

- `index(20260917-105911).html`
- `index(20260917-112150).html`
- `index(20260917-132248).html`
- `index(20260917-133940).html`

These timestamps represent iterations that were used during debugging.

**Important:** these are historical references, not guaranteed current source-of-truth files.

When an uploaded project file is supplied, inspect the exact file rather than rebuilding it from memory.

---

# 20. Project file modification rules

The project has an explicit source-of-truth rule:

> When modifying uploaded project files, inspect and modify the exact uploaded file rather than reconstructing it from memory or partial previews.

File-search previews can be truncated.

Never conclude that a file is complete simply because the preview looks complete.

Before changing code:

1. Locate the exact uploaded file.
2. Inspect the relevant full content.
3. Identify the exact function/CSS/HTML block.
4. Make only the required change.
5. Verify the resulting code.
6. Give the user the exact replacement block.

---

# 21. Critical handover rule for code responses

The project has a strict user-facing modification rule:

> **Do NOT output the full file. Do NOT use inline auto-apply tools to modify my files. Locate the specific bug, output ONLY the modified function/CSS block as a self-contained snippet, and tell me the exact line number range to replace manually.**

Therefore, future code-fix responses should:

- NOT dump the entire `index.html`
- NOT dump the entire `style.css`
- NOT dump the entire `script.js`
- NOT automatically modify the user's files
- Identify the affected function/block
- Provide only the replacement block
- State the exact line range to replace manually

If exact line numbers cannot be established from the current file, inspect the file until they can.

---

# 22. Versioned asset cache-busting rule

Radio RRR uses versioned asset URLs for:

- `style.css`
- `script.js`

Example:

`style.css?v=20260917.1`

The purpose is to prevent browsers/CDNs from serving stale assets.

**Whenever CSS or JavaScript is changed, increment the version number in `index.html`.**

Do not tell the user to:

- clear cache
- Ctrl+F5
- disable browser cache
- use DevTools Disable Cache

Those are not the project solution.

The asset version must be incremented.

Example:

```html
style.css?v=20260917.1
```

becomes:

```html
style.css?v=20260918.1
```

or another appropriate increment based on the existing version.

Do not blindly overwrite the version scheme; inspect the current `index.html`.

---

# 23. Do not reintroduce fixed bugs

This project has suffered from regressions caused by restoring old versions.

Before applying a change, preserve these known-good behaviours:

### Playback
- Main HLS stream works
- Unmute stays unmuted
- Playback survives tab switching
- Playback does not randomly freeze
- Audio-only stream follows the main stream
- DJ switching does not destroy the main playback architecture

### DJ cards
- Other live DJs show genre bubbles
- Genre bubbles represent live detected genres
- Program Match % is visible on other DJ cards
- Cards are ordered by program match

### Schedule
- Current schedule name and genres can share a single border
- Current program is loaded from scheduler/database
- Genre matcher uses current schedule

### Visual
- DJ photo is card background
- Transparent styling
- No accidental double borders
- Desktop/mobile layouts remain intact

---

# 24. Current intended routing experience

The intended end-user experience is:

1. User opens Radio RRR.
2. Current program is shown.
3. Current program's target genres are shown.
4. RadioRouter discovers live DJs.
5. Genre detector analyses their live audio.
6. Each live DJ receives a program match score.
7. DJs are ordered from highest to lowest match.
8. The main stream routes to the selected/best eligible DJ.
9. Other DJs remain available for browsing.
10. The live detected genres are visible on each DJ card.
11. The user can switch between Live Video and Live Audio.
12. The main radio stream remains stable while the site is used.

---

# 25. Example data flow

Conceptually:

```text
TikTok LIVE DJs
       │
       ▼
RadioRouter discovery
       │
       ▼
Live DJ state
       │
       ├──────────────► /api/live
       │
       ▼
FFmpeg / HLS relay
       │
       ├──────────────► /api/live-stream
       │
       ▼
Genre detector
(Essentia + TensorFlow)
       │
       ▼
Detected live genres
       │
       ▼
Schedule / target genres
       │
       ▼
Genre matcher
       │
       ▼
Program Match score
       │
       ▼
Rank live DJs
       │
       ├──────────────► Main selected DJ
       │
       └──────────────► Other live DJ cards
```

---

# 26. Known operational/debugging commands and concepts

Backend Docker image:

`radiorouter:latest`

Likely workflow after backend code changes:

1. Modify `/mnt/user/radiorouter/app/main.py`
2. Build the `radiorouter:latest` image
3. Restart the `radiorouter` container
4. Inspect logs
5. Test API endpoints
6. Test frontend

The exact Docker build command should be checked against the current NAS setup rather than assumed.

Useful log strings include:

- `[Schedule]`
- `[Stage 3]`
- `Candidate`
- `score=`
- `wins=`

A useful diagnostic endpoint is:

`/api/genre-match`

---

# 27. Backend debugging priorities

If routing is wrong:

1. Check current active program.
2. Check target genres and weights.
3. Check detected genres for each live DJ.
4. Check eligibility/anti-genre rules.
5. Check candidate scores.
6. Check ranking.
7. Check final selection.
8. Only then inspect frontend ordering/display.

Do not fix a backend ranking issue with frontend sorting if the API already exposes a correctly ranked list.

Conversely, if the backend ranking is correct but the cards are visually unordered, fix the frontend data rendering rather than changing the matcher.

---

# 28. Frontend debugging priorities

If a card is wrong:

1. Check raw `/api/live` data.
2. Check whether match score is supplied.
3. Check detected genre field.
4. Check whether frontend is sorting the data.
5. Check card rendering.
6. Check CSS.

If schedule display is wrong:

1. Check schedule API/database data.
2. Check active-program selection.
3. Check frontend schedule rendering.
4. Then modify CSS only if the data is correct.

---

# 29. Avoid broad refactors

This project has become fragile because multiple unrelated changes have occasionally been combined.

Preferred approach:

**One bug → one targeted change → test → move to next change.**

Avoid:

- replacing the whole player
- rewriting all card rendering
- replacing all CSS
- merging old `index.html` versions wholesale
- restoring large historical blocks without comparing them against the latest working version

Small diffs are strongly preferred.

---

# 30. Current high-priority known issues

As of the handover, the major recurring areas requiring caution are:

### A. Browser-tab playback
Audio/video must continue when the user changes browser tabs.

### B. HLS stability
Avoid the ~10-second mute regression and ~1-minute freezing regression.

### C. DJ ordering
Other live DJs should be sorted by best match to the active schedule.

### D. Genre bubbles
Other DJ cards should show live detected genre bubbles rather than detector lines/percentages.

### E. Schedule container
Current schedule title + target genre bubbles should appear inside one shared border.

### F. Regression prevention
Changes to unrelated UI must not overwrite working player code.

---

# 31. Testing checklist

After frontend changes:

## Desktop
- [ ] Open site
- [ ] Main DJ loads
- [ ] Click unmute
- [ ] Confirm audio remains active for >10 minutes
- [ ] Switch browser tabs
- [ ] Confirm audio continues
- [ ] Return to site
- [ ] Confirm player remains healthy
- [ ] Switch Live Video / Live Audio
- [ ] Click another live DJ
- [ ] Return to main DJ
- [ ] Confirm no stream conflict
- [ ] Confirm other DJ cards are sorted by match
- [ ] Confirm program match percentage is visible
- [ ] Confirm live genre bubbles are visible
- [ ] Confirm current schedule + genre bubbles share one border

## Refresh
- [ ] Refresh page
- [ ] Unmute
- [ ] Confirm no ~10-second mute
- [ ] Confirm no delayed mute
- [ ] Confirm no freeze after ~1 minute

## Mobile
- [ ] Check card size
- [ ] Check card stacking
- [ ] Check schedule container
- [ ] Check genre bubble wrapping
- [ ] Check playback controls

## Cache
- [ ] If CSS changed, increment CSS version in `index.html`
- [ ] If JS changed, increment JS version in `index.html`

---

# 32. Security / reliability considerations

The backend is internet-facing.

Be cautious with:

- arbitrary stream URLs
- user-submitted DJs
- FFmpeg command construction
- shell command injection
- TikTokLive failures
- malformed API input
- unbounded HLS processes
- zombie FFmpeg processes
- SQLite locking
- excessive API polling

When changing FFmpeg handling, verify that user-controlled data cannot become shell syntax.

When changing live stream lifecycle, ensure old processes are cleaned up and a failed stream cannot cause an uncontrolled restart loop.

---

# 33. Performance considerations

The project has experienced periods of:

- too many frontend requests
- page disappearance/loading issues
- multiple stream creation
- freezing

Avoid unnecessarily increasing polling frequency.

Genre detection historically updates around every 30 seconds, so frontend polling should not need to be dramatically more aggressive unless there is a specific reason.

If real-time UI is required, use the backend's existing state rather than creating duplicate detection requests.

---

# 34. Radio stream directory / distribution

The MP3 radio stream has become stable enough that the project considered submitting it to radio station directories.

TuneIn was attempted but the verification process reportedly failed when the supplied verification code was not accepted.

This is not part of the core RadioRouter architecture, but is a potential future distribution task.

---

# 35. Future development ideas

Potential future improvements discussed or implied by the project:

- Better automated DJ eligibility management
- More reliable anti-genre filtering
- Improved genre synonym/normalisation
- More sophisticated weighted matching
- Better confidence scoring
- Match-history analytics
- DJ performance history
- Program-specific exclusion rules
- Automatic fallback when no DJ sufficiently matches
- Better stream failover
- Stream health monitoring
- Administrative dashboard
- Match/routing logs
- Radio directory submissions
- More robust mobile playback
- Automated regression testing for player lifecycle

---

# 36. Recommended future architecture discipline

For future work, treat these as separate layers:

### Layer 1 — Discovery
Who is live?

### Layer 2 — Relay
Can their stream be captured/relayed?

### Layer 3 — Detection
What is currently being played?

### Layer 4 — Programming
What does the current schedule want?

### Layer 5 — Eligibility
Is the DJ allowed to play this slot?

### Layer 6 — Matching
How well does the DJ match?

### Layer 7 — Routing
Which DJ becomes the main stream?

### Layer 8 — Presentation
How does the frontend display the result?

Keeping these layers separate will make debugging considerably easier.

---

# 37. Source-of-truth hierarchy

When investigating a problem, prefer:

1. Exact current uploaded source file
2. Current NAS/container source
3. Current SQLite database
4. Current API response
5. Current Docker logs
6. Historical uploaded versions
7. Memory of previous implementation

Do not use historical memory as the primary source if the actual file/database can be inspected.

---

# 38. Important distinction: historical vs current

This handover intentionally contains historical implementation details because they explain why regressions have happened.

Not every item listed is guaranteed to be the exact current implementation.

Before changing:

- `index.html`
- `style.css`
- `script.js`
- `main.py`
- database schema
- Docker configuration

inspect the current file/configuration.

Treat this document as a **project map**, not as a replacement for the source code.

---

# 39. Minimum information a new developer/AI should establish first

Before making a meaningful code change, establish:

### Frontend
- Current `index.html`
- Current `style.css`
- Current `script.js`
- Current asset versions
- Current player implementation
- Current card rendering
- Current API polling

### Backend
- Current `main.py`
- Current Dockerfile
- Current container configuration
- Current API responses
- Current scheduler implementation

### Database
- Current schema
- Current schedule
- Current schedule genres
- Current live DJ state
- Current detection records

### Detector
- Current `detector.py`
- Current environment variables
- Current model files
- Current detection interval
- Current API interaction

---

# 40. Bottom line

Radio RRR is a live DJ routing platform whose key differentiator is:

**real live DJs + real-time genre detection + schedule-aware automatic routing.**

The most important technical principle for continuing the project is **stability over refactoring**.

The project has repeatedly reached a working state and then regressed because unrelated changes restored older player/card code.

The safe development pattern is:

```text
Inspect exact current source
        ↓
Identify exact bug
        ↓
Change only affected function/block
        ↓
Preserve player/routing logic
        ↓
Increment CSS/JS asset version if applicable
        ↓
Test the specific regression cases
        ↓
Only then make the next change
```

The main functionality to protect is:

```text
LIVE DJs
   ↓
RadioRouter
   ↓
Live stream relay
   ↓
Genre detector
   ↓
Current program
   ↓
Match/rank DJs
   ↓
Select best eligible DJ
   ↓
Stable main stream
```

This is the core of Radio RRR.
