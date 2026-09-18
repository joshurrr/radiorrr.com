/* Radio RRR application JavaScript — extracted from the working index.html */

document.addEventListener("DOMContentLoaded", function () {


      /* RRR TOOLS — TIKTOK ACCOUNT SCANNER */

      const scanRrrDjs =
        document.getElementById("scanRrrDjs");

      const scanOneDj =
        document.getElementById("scanOneDj");

      const clearScanner =
        document.getElementById("clearScanner");

      const scannerHandle =
        document.getElementById("scannerHandle");

      const scannerResults =
        document.getElementById("scannerResults");

      const scannerSummary =
        document.getElementById("scannerSummary");

      /* RECOMMEND A DJ — FORMSPREE */
      const recommendDjOpen =
        document.getElementById("recommendDjOpen");

      const recommendDjModal =
        document.getElementById("recommendDjModal");

      const recommendDjClose =
        document.getElementById("recommendDjClose");

      const recommendDjForm =
        document.getElementById("recommendDjForm");

      const recommendDjUsername =
        document.getElementById("recommendDjUsername");

      function openRecommendDjModal() {
        if (!recommendDjModal) return;
        recommendDjModal.classList.add("open");
        if (recommendDjOpen) recommendDjOpen.classList.add("active");
        document.body.style.overflow = "hidden";
        window.setTimeout(function () {
          if (recommendDjUsername) recommendDjUsername.focus();
        }, 50);
      }

      function closeRecommendDjModal() {
        if (!recommendDjModal) return;
        recommendDjModal.classList.remove("open");
        if (recommendDjOpen) recommendDjOpen.classList.remove("active");
        document.body.style.overflow = "";
      }

      if (recommendDjOpen) {
        recommendDjOpen.addEventListener("click", openRecommendDjModal);
      }

      if (recommendDjClose) {
        recommendDjClose.addEventListener("click", closeRecommendDjModal);
      }

      if (recommendDjModal) {
        recommendDjModal.addEventListener("click", function (event) {
          if (event.target === recommendDjModal) {
            closeRecommendDjModal();
          }
        });
      }

      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && recommendDjModal && recommendDjModal.classList.contains("open")) {
          closeRecommendDjModal();
        }
      });

      if (recommendDjForm) {
        recommendDjForm.addEventListener("submit", function (event) {
          const raw = recommendDjUsername ? recommendDjUsername.value : "";
          const normalized = normalizeTikTokHandle(raw);

          if (!normalized) {
            event.preventDefault();
            if (recommendDjUsername) {
              recommendDjUsername.setAttribute("aria-invalid", "true");
              recommendDjUsername.focus();
            }
            const fieldError =
              recommendDjForm.querySelector('[data-fs-error="username"]');
            if (fieldError) {
              fieldError.textContent = "Enter a valid TikTok username or profile URL.";
            }
            return;
          }

          if (recommendDjUsername) {
            recommendDjUsername.value = normalized;
          }
        });
      }

      function normalizeTikTokHandle(input) {
        const s = String(input || "").trim();
        if (!s) return "";

        let m = s.match(/tiktok\.com\/@([A-Za-z0-9._]+)/i);
        if (m && m[1]) return m[1];

        m = s.match(/^@?([A-Za-z0-9._]{2,24})$/);
        return m && m[1] ? m[1] : "";
      }

      function scanAccountSignals(account) {
        const handle = normalizeTikTokHandle(
          account.username ||
          account.unique_id ||
          account.handle ||
          account
        );

        let score = 8;
        const reasons = [];

        if (!handle) {
          return {
            handle: "",
            score: 30,
            verdict: "Medium",
            level: "medium",
            reasons: ["Could not extract a TikTok username."]
          };
        }

        if (/\d{5,}$/.test(handle)) {
          score += 28;
          reasons.push("Username ends with a large block of digits.");
        } else if (/\d{4}$/.test(handle)) {
          score += 18;
          reasons.push("Username ends with four digits.");
        }

        const separators = (handle.match(/[._]/g) || []).length;
        if (separators >= 3) {
          score += 10;
          reasons.push("Username contains many separators.");
        }

        if (handle.length <= 4) {
          score += 10;
          reasons.push("Very short username.");
        }

        if (/^(user|account|official|real|dj)\d+/i.test(handle)) {
          score += 12;
          reasons.push("Generic/generated-looking username pattern.");
        }

        if (/(free|promo|airdrop|crypto|giveaway|cash|adult|sex|dm|whatsapp|telegram)/i.test(handle)) {
          score += 25;
          reasons.push("Username contains a common spam/scam keyword.");
        }

        // Use API data when it is available.
        const followers = Number(
          account.followers ??
          account.follower_count ??
          account.followerCount
        );

        const following = Number(
          account.following ??
          account.following_count ??
          account.followingCount
        );

        const likes = Number(
          account.likes ??
          account.like_count ??
          account.likeCount
        );

        const videos = Number(
          account.videos ??
          account.video_count ??
          account.videoCount
        );

        if (Number.isFinite(followers) && Number.isFinite(following)) {
          if (following >= 5000 && followers <= 200) {
            score += 22;
            reasons.push("Very high following count with very few followers.");
          } else if (following >= 2000 && followers <= 50) {
            score += 25;
            reasons.push("Extreme following/follower imbalance.");
          }

          if (following > 500 && followers > 0 && followers / following < 0.02) {
            score += 12;
            reasons.push("Extremely low follower/following ratio.");
          }
        }

        if (Number.isFinite(videos)) {
          if (videos === 0) {
            score += 16;
            reasons.push("No public videos.");
          } else if (videos <= 2) {
            score += 8;
            reasons.push("Very small public content footprint.");
          }
        }

        if (Number.isFinite(likes) && likes === 0) {
          score += 6;
          reasons.push("No visible likes/activity reported.");
        }

        // A lack of API stats is deliberately NOT treated as proof of a bot.
        if (reasons.length === 0) {
          reasons.push("No strong bot-like signals found from the available data.");
        }

        score = Math.max(0, Math.min(100, score));

        let verdict = "Low";
        let level = "low";

        if (score >= 65) {
          verdict = "High";
          level = "high";
        } else if (score >= 35) {
          verdict = "Medium";
          level = "medium";
        }

        return {
          handle,
          score,
          verdict,
          level,
          reasons
        };
      }

      function renderScannerSummary(results) {
        if (!scannerSummary) return;

        const high = results.filter(r => r.level === "high").length;
        const medium = results.filter(r => r.level === "medium").length;
        const low = results.filter(r => r.level === "low").length;

        scannerSummary.innerHTML =
          '<span class="pill">Scanned: ' + results.length + '</span>' +
          '<span class="pill">🔴 High risk: ' + high + '</span>' +
          '<span class="pill">🟠 Review: ' + medium + '</span>' +
          '<span class="pill">🟢 Low risk: ' + low + '</span>';
      }

      function renderScannerResults(results) {
        if (!scannerResults) return;

        if (!results.length) {
          scannerResults.innerHTML =
            '<div class="scanner-empty">No accounts to scan.</div>';
          if (scannerSummary) scannerSummary.innerHTML = "";
          return;
        }

        results.sort((a, b) => b.score - a.score);
        renderScannerSummary(results);

        scannerResults.innerHTML = "";

        results.forEach(result => {
          const card = document.createElement("div");
          card.className = "scanner-result " + result.level;

          const reasonItems = result.reasons
            .map(reason => "<li>" + escapeHtml(reason) + "</li>")
            .join("");

          const profileUrl =
            "https://www.tiktok.com/@" +
            encodeURIComponent(result.handle);

          const removalText =
            result.level === "high"
              ? "🔴 Suggest Removal"
              : "⚠️ Review Account";

          card.innerHTML =
            '<div class="scanner-result-head">' +
              '<div class="scanner-account">@' +
                escapeHtml(result.handle) +
              '</div>' +
              '<div class="scanner-score ' + result.level + '">' +
                escapeHtml(result.score + "/100 · " + result.verdict + " RISK") +
              '</div>' +
            '</div>' +
            '<div class="scanner-verdict">' +
              (result.level === "high"
                ? "Strong enough signals to suggest manual removal review."
                : result.level === "medium"
                  ? "Some suspicious signals — inspect the profile before deciding."
                  : "No strong bot-like signals detected from the available data.") +
            '</div>' +
            '<ul class="scanner-reasons">' +
              reasonItems +
            '</ul>' +
            '<div class="scanner-actions">' +
              '<a href="' + escapeAttr(profileUrl) +
                '" target="_blank" rel="noopener">Open TikTok</a>' +
              '<button type="button" class="remove-suggest" ' +
                'data-handle="' + escapeAttr(result.handle) + '">' +
                escapeHtml(removalText) +
              '</button>' +
            '</div>';

          scannerResults.appendChild(card);
        });

        scannerResults
          .querySelectorAll(".remove-suggest")
          .forEach(button => {
            button.addEventListener("click", function () {
              const handle = this.getAttribute("data-handle") || "";
              const confirmed = window.confirm(
                "Suggest removal of @" + handle +
                " from the RRR favourite DJ list?\n\n" +
                "This does NOT remove the account automatically. " +
                "It is only a manual review reminder."
              );

              if (confirmed) {
                showToast(
                  "Removal suggested for @" + handle +
                  ". Review the TikTok profile and remove it from the RRR list manually if appropriate."
                );
              }
            });
          });
      }

      async function scanRrrFavouriteDjs() {
        if (!scannerResults) return;

        scannerResults.innerHTML =
          '<div class="scanner-empty">Loading the RRR favourite DJ list…</div>';

        try {
          const response = await fetch(
            "https://api.radiorrr.com/api/live",
            { cache: "no-store" }
          );

          if (!response.ok) {
            throw new Error("API returned " + response.status);
          }

          const data = await response.json();
          const favourites =
            Array.isArray(data.favourites) ? data.favourites : [];

          const results = favourites
            .map(dj => scanAccountSignals(dj))
            .filter(r => r.handle);

          renderScannerResults(results);

          if (!results.length) {
            scannerResults.innerHTML =
              '<div class="scanner-empty">' +
              'The API returned no favourite DJs. Add DJs to the RRR favourite list first.' +
              '</div>';
          }

        } catch (error) {
          console.error("RRR scanner error:", error);

          scannerResults.innerHTML =
            '<div class="live-error">' +
            'Could not load the RRR DJ list for scanning.<br>' +
            '<small>Check that the RRR API is online and allows browser access.</small>' +
            '</div>';
        }
      }

      function scanOneAccount() {
        const handle = normalizeTikTokHandle(
          scannerHandle ? scannerHandle.value : ""
        );

        if (!handle) {
          showToast("Enter a TikTok @handle or profile URL first.");
          return;
        }

        const result = scanAccountSignals({ username: handle });
        renderScannerResults([result]);
      }

      if (scanRrrDjs) {
        scanRrrDjs.addEventListener("click", scanRrrFavouriteDjs);
      }

      if (scanOneDj) {
        scanOneDj.addEventListener("click", scanOneAccount);
      }

      if (scannerHandle) {
        scannerHandle.addEventListener("keydown", function (e) {
          if (e.key === "Enter") scanOneAccount();
        });
      }

      if (clearScanner) {
        clearScanner.addEventListener("click", function () {
          if (scannerSummary) scannerSummary.innerHTML = "";
          if (scannerResults) {
            scannerResults.innerHTML =
              '<div class="scanner-empty">' +
              'Scanner cleared. Click <strong>Scan RRR DJ List</strong> to scan again.' +
              '</div>';
          }
        });
      }

            const navLinks =
        document.querySelectorAll(".nav-tabs a[data-target]");

      const contentPanes =
        document.querySelectorAll(".content-pane");

      const heroNowEl =
        document.getElementById("heroNowPlaying");

      const liveProgramTargetGenres =
        document.getElementById("liveProgramTargetGenres");

      const liveDjsList =
        document.getElementById("liveDjsList");

      const liveRefresh =
        document.getElementById("liveRefresh");

      const randomLiveDj =
        document.getElementById("randomLiveDj");

      const randomLiveDjName =
        document.getElementById("randomLiveDjName");
      const randomLiveDjMatch =
        document.getElementById("randomLiveDjMatch");

      const randomLiveDjGenres =
        document.getElementById("randomLiveDjGenres");

      const randomLiveDjGenresField =
        document.getElementById("randomLiveDjGenresField");

      const randomLiveDjAvatar =
        document.getElementById("randomLiveDjAvatar");

      const randomLiveDjBioField =
        document.getElementById("randomLiveDjBioField");

      const randomLiveDjBio =
        document.getElementById("randomLiveDjBio");

      const randomLiveDjMetaField =
        document.getElementById("randomLiveDjMetaField");

      const randomLiveDjMeta =
        document.getElementById("randomLiveDjMeta");

      const liveGenresDetected =
        document.getElementById("liveGenresDetected");

      const liveGenresDetectedStatus =
        document.getElementById("liveGenresDetectedStatus");

      const liveGenresDetectedList =
        document.getElementById("liveGenresDetectedList");


      const randomLiveDjFrame =
        document.getElementById("randomLiveDjFrame");

      const randomLiveDjNext =
        document.getElementById("randomLiveDjNext");

      const liveDjVideo =
        document.getElementById("liveDjVideo");

      const liveDjBackground =
        document.getElementById("liveDjBackground");

      const liveDjPlaceholder =
        document.getElementById("liveDjPlaceholder");

      const liveDjUnmute =
        document.getElementById("liveDjUnmute");

      /* LIVE GENRES DETECTED stays inside the Current DJ card at every breakpoint. */


      const copyLiveAudioUrl = document.getElementById("copyLiveAudioUrl");
      const liveAudioStreamUrl = document.getElementById("liveAudioStreamUrl");

      if (copyLiveAudioUrl && liveAudioStreamUrl) {
        copyLiveAudioUrl.addEventListener("click", async function () {
          try {
            await navigator.clipboard.writeText(liveAudioStreamUrl.value);
          } catch (e) {
            liveAudioStreamUrl.focus();
            liveAudioStreamUrl.select();
            document.execCommand("copy");
          }

          const originalText = copyLiveAudioUrl.textContent;
          copyLiveAudioUrl.textContent = "Copied!";
          setTimeout(function () {
            copyLiveAudioUrl.textContent = originalText;
          }, 1500);
        });
      }

      const audio =
        document.getElementById("liveStream");

      const statusEl =
        document.getElementById("status");

      const canvas =
        document.getElementById("viz");

      const ctx =
        canvas && canvas.getContext
          ? canvas.getContext("2d")
          : null;

      let audioCtx = null;
      let analyser = null;
      let dataArray = null;
      let analyserReady = false;
      let uiAudioCtx = null;

      if (audio) {
        audio.crossOrigin = "anonymous";
      }

      function playNavClick() {

        try {

          const AC =
            window.AudioContext ||
            window.webkitAudioContext;

          if (!AC) return;

          if (!uiAudioCtx) {
            uiAudioCtx = new AC();
          }

          const now =
            uiAudioCtx.currentTime;

          const osc =
            uiAudioCtx.createOscillator();

          const gain =
            uiAudioCtx.createGain();

          osc.type = "triangle";

          osc.frequency.setValueAtTime(
            880,
            now
          );

          gain.gain.setValueAtTime(
            0.05,
            now
          );

          gain.gain.exponentialRampToValueAtTime(
            0.001,
            now + 0.09
          );

          osc.connect(gain);

          gain.connect(
            uiAudioCtx.destination
          );

          osc.start(now);

          osc.stop(now + 0.1);

        } catch (e) {}

      }

      function switchTab(targetId) {

        contentPanes.forEach(
          pane => pane.classList.add("hidden-pane")
        );

        const targetElement =
          document.getElementById(targetId);

        if (targetElement) {
          targetElement.classList.remove("hidden-pane");
        }

        navLinks.forEach(
          link => link.classList.remove("active")
        );

        const activeLink =
          document.querySelector(
            '.nav-tabs a[data-target="' +
            targetId +
            '"]'
          );

        if (activeLink) {
          activeLink.classList.add("active");
        }

        // IMPORTANT: the station audio element is deliberately NOT touched
        // here. Changing tabs must not pause, reload, mute, or recreate it.
        // This matches the behaviour of the earlier working Radio RRR build.

        // IMPORTANT: Live DJ video is also NOT touched by menu switching.
        // Keep the live video stream running when the listener opens Schedule,
        // Tools, Live Audio, etc. The player must only be stopped by its own
        // DJ/stream lifecycle logic or an explicit user action.

      }

      navLinks.forEach(link => {

        link.addEventListener(
          "click",
          function (e) {

            // These are JavaScript tabs, not real navigation links.
            e.preventDefault();

            const targetId =
              e.currentTarget.getAttribute(
                "data-target"
              );

            if (targetId) {

              switchTab(targetId);

            }

          }
        );

      });

      switchTab("live-section");

      /* RADIO ROUTER LIVE DJ PLAYER */

      const RADIO_ROUTER_STREAM_URL = "https://api.radiorrr.com/api/live-stream";

      let activeLiveUsername = "";
      let activeLiveStreamKey = "";
      let activeLiveStreamUrl = "";
      let userRequestedAudio = false;
      let liveDjRequestId = 0;
      let liveGenreRequestId = 0;
      const LIVE_GENRE_REFRESH_SECONDS = 30;
      let liveGenreRefreshDeadline = Date.now() + (LIVE_GENRE_REFRESH_SECONDS * 1000);
      let liveGenreCountdownTimer = null;
      let liveDjRequestController = null;
      let nativePlaybackRecoveryTimer = null;
      let manualDJStreamFailureHandled = false;

      // The backend relay has its own liveness grace period. TikTok can
      // occasionally return a false "offline" result for one or two checks
      // while the existing relay stream is still healthy. Never tear down a
      // working player just because one /api/live response has no relay.
      const LIVE_RELAY_MISSING_GRACE_MS = 2 * 60 * 1000;
      let relayMissingSince = 0;
      let lastKnownRelayDJ = null;

      // Mobile browsers (especially iOS/Safari) are much less tolerant of
      // maintaining two simultaneous HLS decoders for the same live stream.
      // The real player is always the only HLS connection on mobile.
      const isMobileLivePlayer =
        window.matchMedia("(max-width: 767px)").matches;

      function getFreshUrl(url) {
        const separator = url.indexOf("?") === -1 ? "?" : "&";
        return url + separator + "_rrr=" + Date.now();
      }

      function updateLiveGenreRefreshCountdown() {
        if (!liveGenresDetectedStatus) return;

        const remaining = Math.max(
          0,
          Math.ceil((liveGenreRefreshDeadline - Date.now()) / 1000)
        );

        liveGenresDetectedStatus.textContent =
          "Refresh in " + remaining + " sec";
      }

      function resetLiveGenreRefreshCountdown() {
        liveGenreRefreshDeadline =
          Date.now() + (LIVE_GENRE_REFRESH_SECONDS * 1000);

        updateLiveGenreRefreshCountdown();

        if (!liveGenreCountdownTimer) {
          liveGenreCountdownTimer = window.setInterval(
            updateLiveGenreRefreshCountdown,
            1000
          );
        }
      }

      updateLiveGenreRefreshCountdown();

      function resumeLivePlayback() {
        if (!liveDjVideo || document.hidden) return;

        // The user's explicit audio choice is authoritative. Recovery and
        // HLS events must never silently mute the player again.
        liveDjVideo.muted = !userRequestedAudio;
        updateLiveDjMuteButton();

        liveDjVideo.play().catch(() => {});
      }

      function updateLiveDjMuteButton() {
        if (!liveDjUnmute || !liveDjVideo) return;

        const isMuted = liveDjVideo.muted;
        liveDjUnmute.textContent = isMuted ? "🔊 UNMUTE LIVE" : "🔇 MUTE";
        liveDjUnmute.setAttribute("aria-label", isMuted ? "Unmute live DJ" : "Mute live DJ");
        liveDjUnmute.setAttribute("aria-pressed", String(!isMuted));
      }

      function scheduleNativePlaybackRecovery() {
        // Do not replace/reload the media element here. hls.js owns HLS
        // recovery. Replacing the source after waiting/stalled resets media
        // state and can also lose the user's unmute gesture.
        if (liveDjVideo && !document.hidden) {
          liveDjVideo.muted = !userRequestedAudio;
          updateLiveDjMuteButton();
        }
      }

      function handleManualDJStreamFailure(data) {
        // A manually selected DJ uses the Router's per-DJ browser relay.
        // If that private relay fails fatally, abandon only this browser's
        // override and return to the station-wide default relay.
        if (!manualFeaturedDJIdentity || !data || !data.fatal) return false;
        if (manualDJStreamFailureHandled) return true;

        manualDJStreamFailureHandled = true;
        manualFeaturedDJIdentity = "";
        manualFeaturedDJMissingCount = 0;

        window.setTimeout(function () {
          manualDJStreamFailureHandled = false;
          loadLiveDJs();
        }, 250);

        return true;
      }

      function normalizeGenreForMatch(value) {
        return String(value || "")
          .replace(/^.*---/, "")
          .toLowerCase()
          .replace(/&/g, "and")
          .replace(/[^a-z0-9]+/g, " ")
          .trim()
          .replace(/\s+/g, " ");
      }

      async function updateLiveGenresDetected(aiGenre, featuredIdentity, relayIdentity) {
        if (!liveGenresDetected || !liveGenresDetectedList) return;

        const requestId = ++liveGenreRequestId;
        const selectedIdentity = String(featuredIdentity || "").trim();
        const routerIdentityValue = String(relayIdentity || "").trim();

        // Nothing is live/selected: hide the panel.
        if (!selectedIdentity) {
          liveGenresDetected.style.display = "none";
          liveGenresDetectedList.innerHTML = "";
          return;
        }

        liveGenresDetected.style.display = "block";
        liveGenresDetectedList.innerHTML = "";

        let genreData = null;

        // /api/live supplies ai_genre as a display string (for example
        // "Pop Rap"), while /api/ai-genre supplies the full detection
        // object containing the ranked genres. The LIVE GENRES DETECTED
        // panel needs the full object, so never treat a display string as
        // complete genre-data.
        const hasFullAiGenreData =
          aiGenre &&
          typeof aiGenre === "object" &&
          Array.isArray(aiGenre.genres);

        if (
          routerIdentityValue &&
          selectedIdentity === routerIdentityValue &&
          hasFullAiGenreData
        ) {
          genreData = aiGenre;
        } else {
          // Fetch the latest stored AI result when the selected/default DJ
          // only has the short ai_genre display string.
          try {
            const username = selectedIdentity.replace(/^username:/i, "").replace(/^@/, "");
            const response = await fetch(
              getFreshUrl(
                "https://api.radiorrr.com/api/ai-genre?username=" +
                encodeURIComponent(username)
              ),
              {
                cache: "no-store",
                headers: {
                  "Cache-Control": "no-cache",
                  "Pragma": "no-cache"
                }
              }
            );

            if (response.ok) {
              genreData = await response.json();
            }
          } catch (error) {
            console.warn("Could not load stored AI genre data:", error);
          }
        }

        // Ignore a result belonging to an older DJ selection.
        if (requestId !== liveGenreRequestId) return;

        if (!genreData || !Array.isArray(genreData.genres)) {
          if (liveGenresDetectedStatus) {
            liveGenresDetectedStatus.textContent = "Analysing live audio…";
          }
          return;
        }

      liveGenresDetectedStatus.textContent = "AI detected · live audio";
              const genres = genreData.genres
          .map(item => {
            let confidence = Number(item && item.confidence);

            if (confidence > 1 && confidence <= 100) {
              confidence = confidence / 100;
            }

            return {
              genre: String(item && item.genre || "").trim(),
              confidence
            };
          })
          .filter(item =>
            item.genre &&
            Number.isFinite(item.confidence) &&
            item.confidence > 0
          )
          .sort((a, b) => b.confidence - a.confidence)
          .slice(0, 5);

        if (!genres.length) {
          if (liveGenresDetectedStatus) {
            liveGenresDetectedStatus.textContent = "Analysing live audio…";
          }
          return;
        }

        const maxConfidence = Math.max(
          ...genres.map(item => item.confidence),
          0.01
        );

        genres.forEach(item => {
          const row = document.createElement("div");
          row.className = "live-genre-detected-row";

          const name = item.genre.replace(/^.*---/, "");
          const percent = Math.round(item.confidence * 100);
          const barWidth = Math.max(
            4,
            Math.min(100, (item.confidence / maxConfidence) * 100)
          );

          row.innerHTML =
            '<span class="live-genre-detected-name" title="' +
              escapeAttr(name) + '">' +
              escapeHtml(name) +
            '</span>' +
            '<span class="live-genre-detected-bar" aria-hidden="true">' +
              '<span style="width:' + barWidth.toFixed(1) + '%;"></span>' +
            '</span>' +
            '<span class="live-genre-detected-confidence">' +
              percent + '%' +
            '</span>';

          liveGenresDetectedList.appendChild(row);
        });

        // Genre data has successfully refreshed. Restart the visible
        // countdown from 30 seconds at the moment the new result is rendered.
        resetLiveGenreRefreshCountdown();
      }

      function getDJGenres(dj) {
        if (!window.RadioRRRGenreDetection) return [];

        const bio = String(
          dj && (dj.bio || dj.biography || dj.profile_bio || dj.about || "")
        );

        // The API's curated genre can be broad; profile text supplies the
        // more specific taxonomy match when one is present.
        return window.RadioRRRGenreDetection.normalizeGenres(
          [dj && dj.genre, dj && dj.genre_keywords, dj && dj.genres],
          bio
        );
      }

      function getStoredDJGenres(dj) {
        if (!window.RadioRRRGenreDetection) return [];

        return filterMusicGenres(window.RadioRRRGenreDetection.normalizeGenres(
          [dj && dj.genre, dj && dj.genre_keywords, dj && dj.genres],
          ""
        ));
      }

      const GENRE_NEON_CLASS_MAP = [
        { match: /\bdeep\s+house\b/i, className: "genre-neon-electric-purple" },
        { match: /\bhouse\b/i, className: "genre-neon-hot-pink" },
        { match: /\btechno\b/i, className: "genre-neon-electric-blue" },
        { match: /\bpsy\s*trance\b|\bpsytrance\b/i, className: "genre-neon-acid-green" },
        { match: /\btrance\b/i, className: "genre-neon-cyan" },
        { match: /\bdrum\s*(?:&|and|\+)?\s*bass\b|\bdnb\b|\bd&b\b/i, className: "genre-neon-orange" },
        { match: /\bdubstep\b/i, className: "genre-neon-blue" },
        { match: /\bhardstyle\b/i, className: "genre-neon-red" },
        { match: /\bdisco\b/i, className: "genre-neon-violet" },
        { match: /\bfunk\b/i, className: "genre-neon-yellow" },
        { match: /\bchill\b|\bdowntempo\b/i, className: "genre-neon-aqua" },
        { match: /\bbreaks?\b|\bbreakbeat\b/i, className: "genre-neon-orange" },
        { match: /\buk\s+garage\b|\bgarage\b/i, className: "genre-neon-lime" },
        { match: /\bedm\b/i, className: "genre-neon-blue-purple" },
        { match: /\bpop\b/i, className: "genre-neon-magenta" }
      ];

      function getGenreNeonClass(genre) {
        const text = String(genre || "");
        const mapped = GENRE_NEON_CLASS_MAP.find(item => item.match.test(text));
        return mapped ? mapped.className : "genre-neon-cyan";
      }

      function getGenrePillHtml(genre) {
        return '<span class="dj-genre-pill ' + getGenreNeonClass(genre) + '">' +
          escapeHtml(String(genre)) +
          '</span>';
      }

      function isMusicGenre(value) {
        const text = String(value || "").trim();
        return !!text && !/^non[\s_-]*music$/i.test(text);
      }

      function filterMusicGenres(genres) {
        return (Array.isArray(genres) ? genres : []).filter(isMusicGenre);
      }

      function getLiveAIGenres(dj) {
        const raw = Array.isArray(dj && dj.ai_genres)
          ? dj.ai_genres
          : [];

        const genres = [];
        const seen = new Set();

        raw.forEach(item => {
          const rawGenre =
            item && typeof item === "object"
              ? item.genre
              : item;

          const value = String(rawGenre || "").trim();
          if (!value) return;

          // AI labels are stored as Parent---Child.
          // The frontend displays only the child genre.
          const parts = value.split("---");
          const displayGenre =
            parts.length > 1
              ? parts.slice(1).join("---").trim()
              : value;

          if (!displayGenre || !isMusicGenre(displayGenre)) return;

          const key = displayGenre.toLowerCase();
          if (seen.has(key)) return;

          seen.add(key);
          genres.push(displayGenre);
        });

        return genres;
      }

      let learnedDJGenreRequestId = 0;
      const learnedDJGenreCache = new Map();
      const LEARNED_DJ_GENRE_CACHE_TTL = 2 * 60 * 1000;

      function parseLearnedDJGenres(profile) {
        const learned = Array.isArray(profile && profile.genres)
          ? profile.genres
          : [];

        const genres = [];

        learned.forEach(item => {
          const rawGenre =
            item && typeof item === "object"
              ? item.genre
              : item;

          const value = String(rawGenre || "").trim();
          if (!value) return;

          const displayGenre = value.split("---").pop().trim();
          if (!displayGenre || !isMusicGenre(displayGenre)) return;

          if (!genres.some(
            existing => existing.toLowerCase() === displayGenre.toLowerCase()
          )) {
            genres.push(displayGenre);
          }
        });

        return genres;
      }

      const liveDetectedGenreCache = new Map();
      const LIVE_DETECTED_GENRE_CACHE_TTL = 25 * 1000;

      function parseLiveDetectedGenres(data) {
        const raw = data && Array.isArray(data.genres)
          ? data.genres
          : [];

        const genres = raw.map(item => {
          let confidence = Number(item && item.confidence);
          if (confidence > 1 && confidence <= 100) confidence /= 100;
          return {
            genre: String(item && item.genre || "").trim(),
            confidence
          };
        }).filter(item =>
          item.genre && Number.isFinite(item.confidence) && item.confidence > 0
        ).sort((a, b) => b.confidence - a.confidence).slice(0, 5);

        const maxConfidence = Math.max(...genres.map(item => item.confidence), 0.01);
        return genres.map(item => ({
          genre: item.genre,
          confidence: item.confidence,
          relativeWidth: Math.max(4, Math.min(100, (item.confidence / maxConfidence) * 100))
        }));
      }

      async function getLiveDetectedGenres(dj) {
        const username = getDJUsername(dj);
        if (!username) return [];

        const cacheKey = username.toLowerCase();
        const cached = liveDetectedGenreCache.get(cacheKey);
        const now = Date.now();
        if (cached && (now - cached.timestamp) < LIVE_DETECTED_GENRE_CACHE_TTL) {
          return cached.genres.map(item => ({ ...item }));
        }

        // Prefer the full AI result already attached to /api/live when
        // available. Otherwise read the Scout-published result from the
        // per-DJ /api/ai-genre endpoint.
        let genres = parseLiveDetectedGenres(dj && dj.ai_genres ? { genres: dj.ai_genres } : null);

        if (!genres.length) {
          try {
            const response = await fetch(
              getFreshUrl(
                "https://api.radiorrr.com/api/ai-genre?username=" +
                encodeURIComponent(username)
              ),
              {
                cache: "no-store",
                headers: {
                  "Cache-Control": "no-cache",
                  "Pragma": "no-cache"
                }
              }
            );

            if (response.ok) {
              genres = parseLiveDetectedGenres(await response.json());
            }
          } catch (error) {
            console.warn("Could not load live Scout genre data for @" + username + ":", error);
          }
        }

        liveDetectedGenreCache.set(cacheKey, { timestamp: now, genres });
        return genres.map(item => ({ ...item }));
      }

      async function getLearnedDJGenres(dj) {
        const username = getDJUsername(dj);
        if (!username) return [];

        const cacheKey = username.toLowerCase();
        const cached = learnedDJGenreCache.get(cacheKey);
        const now = Date.now();

        if (cached && (now - cached.timestamp) < LEARNED_DJ_GENRE_CACHE_TTL) {
          return cached.genres.slice();
        }

        try {
          const response = await fetch(
            getFreshUrl(
              "https://api.radiorrr.com/api/ai-genre/profile?username=" +
              encodeURIComponent(username)
            ),
            {
              cache: "no-store",
              headers: {
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
              }
            }
          );

          if (!response.ok) {
            throw new Error("API returned " + response.status);
          }

          const genres = parseLearnedDJGenres(await response.json());
          learnedDJGenreCache.set(cacheKey, {
            timestamp: now,
            genres
          });

          return genres.slice();
        } catch (error) {
          console.warn("Could not load learned DJ genre profile:", error);

          // Keep the last known learned profile if a temporary request fails.
          return cached ? cached.genres.slice() : [];
        }
      }

      async function updateLiveDjGenres(dj) {
        if (!randomLiveDjGenres) return;

        const requestId = ++learnedDJGenreRequestId;

        // The CURRENT DJ card shows the learned/rolling DJ profile.
        // This is deliberately separate from LIVE GENRES DETECTED, which
        // continues to show the latest live audio classification.
        let genres = await getLearnedDJGenres(dj);

        // Ignore a slower response for an older DJ selection.
        if (requestId !== learnedDJGenreRequestId) return;

        // Only use the stored/profile genre as a fallback if the DJ has never
        // accumulated a learned AI profile. Do not live-analyse this card.
        if (!genres.length) {
          genres = getStoredDJGenres(dj);
        }

        randomLiveDjGenres.innerHTML = "";

        if (randomLiveDjGenresField) {
          randomLiveDjGenresField.hidden = !genres.length;
        }

        genres.slice(0, 5).forEach(genre => {
          const tag = document.createElement("span");
          tag.className = "random-live-genre " + getGenreNeonClass(genre);
          tag.textContent = genre;
          randomLiveDjGenres.appendChild(tag);
        });
      }

      function updateLiveDjProfile(dj) {
        if (!dj) {
          const currentDjCard = document.querySelector("#randomLiveDj .current-dj-card");
          if (currentDjCard) currentDjCard.style.backgroundImage = "";
          if (randomLiveDjAvatar) {
            randomLiveDjAvatar.hidden = true;
            randomLiveDjAvatar.innerHTML = "";
          }
          if (randomLiveDjBioField) randomLiveDjBioField.hidden = true;
          if (randomLiveDjMetaField) randomLiveDjMetaField.hidden = true;
          if (randomLiveDjBio) randomLiveDjBio.textContent = "";
          if (randomLiveDjMeta) randomLiveDjMeta.textContent = "";
          return;
        }

        const profilePic =
          dj.profile_pic ||
          dj.profile_picture ||
          dj.avatar ||
          dj.photo ||
          "";

        const currentDjCard = document.querySelector("#randomLiveDj .current-dj-card");
        if (currentDjCard) {
          if (profilePic) {
            const safeProfilePic = JSON.stringify(String(profilePic));
            currentDjCard.style.backgroundImage = "url(" + safeProfilePic + ")";
          } else {
            currentDjCard.style.backgroundImage = "";
          }
        }

        if (randomLiveDjAvatar) {
          randomLiveDjAvatar.innerHTML = "";
          randomLiveDjAvatar.hidden = !profilePic;

          if (profilePic) {
            const image = document.createElement("img");
            image.src = String(profilePic);
            image.alt = "";
            image.loading = "lazy";
            image.addEventListener("error", function () {
              randomLiveDjAvatar.hidden = true;
            });
            randomLiveDjAvatar.appendChild(image);
          }
        }

        const bio = String(
          dj.bio ||
          dj.biography ||
          dj.profile_bio ||
          dj.about ||
          ""
        ).trim();

        if (randomLiveDjBio) randomLiveDjBio.textContent = bio;
        if (randomLiveDjBioField) randomLiveDjBioField.hidden = !bio;

        const username = getDJUsername(dj);
        const title = dj.title || dj.room_title || dj.description || "";
        const viewers = dj.viewers ?? dj.viewer_count ?? dj.viewerCount;
        const metadata = [
          username ? "@" + username : "",
          title ? String(title).trim() : "",
          viewers !== undefined && viewers !== null && Number(viewers) > 0
            ? String(viewers) + " watching"
            : ""
        ].filter(Boolean).join(" · ");

        if (randomLiveDjMeta) randomLiveDjMeta.textContent = metadata;
        if (randomLiveDjMetaField) randomLiveDjMetaField.hidden = !metadata;
      }

      function showLiveDjPlayer(dj) {
  // Preserve the player's current mute/unmute state when manually switching DJs.
  if (liveDjVideo) {
    userRequestedAudio = !liveDjVideo.muted;
  }

        if (!liveDjVideo || !randomLiveDj) return;

        const username = getDJUsername(dj);
        const name = dj.name || dj.username || dj.display_name || "LIVE DJ";
        const liveUrl = getDJLiveUrl(dj);
        // The station default uses the shared Router relay. A listener who
        // manually selects a DJ gets that DJ's existing per-DJ manual relay
        // in this browser only. Never call /api/live/switch for a listener
        // selection because that changes the shared station relay.
        const isManualSelection =
          Boolean(manualFeaturedDJIdentity) &&
          getDJIdentity(dj) === manualFeaturedDJIdentity;
        const streamUrl = isManualSelection && username
          ? RADIO_ROUTER_STREAM_URL + "?dj=" + encodeURIComponent(username)
          : RADIO_ROUTER_STREAM_URL;

        activeLiveUsername = username;
        activeLiveStreamKey = (isManualSelection ? "manual:" : "default:") + (username || String(name));
        activeLiveStreamUrl = streamUrl;
        liveDjVideo.autoplay = true;
        liveDjVideo.playsInline = true;
        liveDjVideo.muted = !userRequestedAudio;
        updateLiveDjMuteButton();
        if (liveDjBackground) {
          liveDjBackground.autoplay = true;
          liveDjBackground.playsInline = true;
          liveDjBackground.muted = true;
        }
        randomLiveDjName.textContent = "🎧 " + String(name);
        updateMainDJMatchScore(dj);
        updateLiveDjGenres(dj);
        updateLiveDjProfile(dj);
        randomLiveDj.style.display = "block";

        // Chrome/Edge/Firefox need hls.js for HLS (.m3u8).
        // Safari can use native HLS directly.
        if (window.Hls && Hls.isSupported()) {
          if (window.radioRrrHls) {
            window.radioRrrHls.destroy();
            window.radioRrrHls = null;
          }

          if (window.radioRrrHlsBackground) {
            window.radioRrrHlsBackground.destroy();
            window.radioRrrHlsBackground = null;
          }

          const hls = new Hls({
            enableWorker: true,

            // Stability-first settings for the TikTok → FFmpeg → HLS relay.
            // Running too close to the live edge was causing bufferStalledError.
            lowLatencyMode: false,
            liveSyncDurationCount: 5,
            maxLiveSyncPlaybackRate: 1.1,
            maxBufferLength: 30,
            maxMaxBufferLength: 60,
            backBufferLength: 30,

            manifestLoadingTimeOut: 10000,
            manifestLoadingMaxRetry: 3,
            manifestLoadingRetryDelay: 1000
          });

          window.radioRrrHls = hls;
          hls.loadSource(getFreshUrl(streamUrl));
          hls.attachMedia(liveDjVideo);

          hls.on(Hls.Events.MANIFEST_PARSED, function () {
            resumeLivePlayback();
          });

          hls.on(Hls.Events.ERROR, function (event, data) {
            console.warn("Radio RRR HLS error:", data);

            if (handleManualDJStreamFailure(data)) return;

            // A temporary buffer stall is recoverable. Keep the player alive
            // and let hls.js load more media instead of treating it as a stop.
            if (data.details === Hls.ErrorDetails.BUFFER_STALLED_ERROR) {
              try {
                hls.startLoad();
              } catch (e) {}
              return;
            }

            if (data.fatal) {
              if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                hls.startLoad();
              } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                hls.recoverMediaError();
              }
            }
          });

        } else if (liveDjVideo.canPlayType("application/vnd.apple.mpegurl")) {
          liveDjVideo.src = getFreshUrl(streamUrl);
          liveDjVideo.addEventListener("loadedmetadata", resumeLivePlayback, { once: true });
          liveDjVideo.addEventListener("canplay", resumeLivePlayback, { once: true });
          resumeLivePlayback();

        } else {
          if (liveDjPlaceholder) {
            liveDjPlaceholder.querySelector("span").textContent =
              "This browser does not support HLS playback.";
          }
        }

        const player = liveDjVideo.closest(".radio-router-player");
        if (player) player.classList.add("live-active");

        if (liveDjPlaceholder) {
          liveDjPlaceholder.querySelector("span").textContent =
            "LIVE DJ stream is connecting…";
        }
      }

      function showRadioFallback() {
        activeLiveUsername = "";
        activeLiveStreamKey = "";
        activeLiveStreamUrl = "";
        relayMissingSince = 0;
        lastKnownRelayDJ = null;

        if (nativePlaybackRecoveryTimer) {
          window.clearTimeout(nativePlaybackRecoveryTimer);
          nativePlaybackRecoveryTimer = null;
        }

        if (window.radioRrrHls) {
          window.radioRrrHls.destroy();
          window.radioRrrHls = null;
        }

        if (window.radioRrrHlsBackground) {
          window.radioRrrHlsBackground.destroy();
          window.radioRrrHlsBackground = null;
        }

        if (liveDjVideo) {
          liveDjVideo.pause();
          liveDjVideo.removeAttribute("src");
          liveDjVideo.load();
        }

        if (liveDjBackground) {
          liveDjBackground.pause();
          liveDjBackground.removeAttribute("src");
          liveDjBackground.load();
        }

        if (randomLiveDj) {
          randomLiveDj.style.display = "none";
        }

        updateLiveDjProfile(null);
      }

      if (liveDjUnmute && liveDjVideo) {
        liveDjUnmute.addEventListener("click", function () {
          // Record the state the user is explicitly requesting.
          const willUnmute = liveDjVideo.muted;
          userRequestedAudio = willUnmute;
          liveDjVideo.muted = !willUnmute;
          liveDjVideo.defaultMuted = !willUnmute;
          updateLiveDjMuteButton();
          liveDjVideo.play().catch(() => {});
        });

        liveDjVideo.addEventListener("volumechange", updateLiveDjMuteButton);
        updateLiveDjMuteButton();
      }

      if (liveDjVideo) {
        liveDjVideo.addEventListener("playing", function () {
          // Re-assert the user's explicit mute/unmute choice whenever the
          // media element starts playing or recovers.
          liveDjVideo.muted = !userRequestedAudio;
          updateLiveDjMuteButton();

          const player = liveDjVideo.closest(".radio-router-player");
          if (player) player.classList.add("live-active");
        });

        liveDjVideo.addEventListener("waiting", function () {
          if (liveDjPlaceholder) {
            liveDjPlaceholder.querySelector("span").textContent =
              "LIVE DJ stream is buffering…";
          }
          scheduleNativePlaybackRecovery();
        });

        liveDjVideo.addEventListener("stalled", function () {
          scheduleNativePlaybackRecovery();
        });

        liveDjVideo.addEventListener("error", function () {
          if (liveDjPlaceholder) {
            liveDjPlaceholder.querySelector("span").textContent =
              "Live DJ stream is not available yet.";
          }
        });
      }

      /* RANDOM LIVE DJ */

      let currentLiveDJs = [];
      let currentLiveMatchScores = {};
      let currentRandomDJ = null;
      let currentFeaturedDJ = null;
      let manualFeaturedDJIdentity = "";
      let manualFeaturedDJMissingCount = 0;

      function getDJUsername(dj) {
        return String(
          dj.username ||
          dj.unique_id ||
          dj.handle ||
          ""
        ).replace(/^@/, "").trim();
      }

      function getDJIdentity(dj) {
        const username = getDJUsername(dj).toLowerCase();

        if (username) return "username:" + username;

        const name = String(
          dj && (dj.name || dj.display_name || "")
        ).trim().toLowerCase().replace(/\s+/g, " ");

        return name ? "name:" + name : "";
      }

      function updateMainDJMatchScore(dj) {
        if (!randomLiveDjMatch) return;

        const username = getDJUsername(dj);
        const key = String(username || "")
          .replace(/^@/, "")
          .trim()
          .toLowerCase();

        const score = Number(
          key ? currentLiveMatchScores[key] : NaN
        );

        if (!Number.isFinite(score)) {
          randomLiveDjMatch.textContent = "🎯 PROGRAM MATCH —";
          randomLiveDjMatch.className =
            "dj-match-score main-dj-match-score match-low";
          return;
        }

        const rounded = Math.max(
          0,
          Math.min(100, Math.round(score))
        );

        const matchClass =
          rounded >= 50
            ? "match-high"
            : rounded >= 20
              ? "match-mid"
              : "match-low";

        randomLiveDjMatch.textContent =
          "🎯 PROGRAM MATCH " + rounded + "%";
        randomLiveDjMatch.className =
          "dj-match-score main-dj-match-score " + matchClass;
      }

      function mergeDJRecords() {
        const merged = {};

        Array.from(arguments).filter(Boolean).forEach(record => {
          Object.keys(record).forEach(key => {
            const value = record[key];
            const isEmpty =
              value === undefined ||
              value === null ||
              value === "" ||
              Array.isArray(value) && value.length === 0;

            if (!isEmpty && (merged[key] === undefined || merged[key] === null || merged[key] === "")) {
              merged[key] = value;
            }
          });
        });

        return merged;
      }

      /*
       * LIVE profile hydration
       *
       * live_djs contains live-state data, while favourite_djs is the
       * canonical source for stable DJ profile metadata such as profile_pic.
       * Do not rely on the live record to carry that metadata when a DJ moves
       * from the featured position into the secondary LIVE cards.
       *
       * mergeDJRecords() is intentionally non-destructive, but that also
       * means a missing/empty profile field in the live record can make the
       * source of truth less obvious. This helper explicitly restores the
       * canonical profile fields from favourite_djs whenever available.
       */
      function hydrateLiveDJProfile(dj, favourite) {
        const merged = mergeDJRecords(dj, favourite);

        if (favourite) {
          const profileFields = [
            "profile_pic",
            "profile_picture",
            "avatar",
            "photo",
            "bio",
            "biography",
            "profile_bio",
            "about",
            "followers",
            "following",
            "verified",
            "tiktok_user_id",
            "profile_updated_at"
          ];

          profileFields.forEach(field => {
            const value = favourite[field];
            const isEmpty =
              value === undefined ||
              value === null ||
              value === "" ||
              Array.isArray(value) && value.length === 0;

            if (!isEmpty) {
              merged[field] = value;
            }
          });
        }

        return merged;
      }

      function mergeUniqueDJs(djs) {
        const unique = [];
        const indexes = new Map();

        djs.forEach(dj => {
          const identity = getDJIdentity(dj);

          if (!identity || !indexes.has(identity)) {
            if (identity) indexes.set(identity, unique.length);
            unique.push(dj);
            return;
          }

          const index = indexes.get(identity);
          unique[index] = mergeDJRecords(unique[index], dj);
        });

        return unique;
      }

      function getDJLiveUrl(dj) {
        const username = getDJUsername(dj);

        return (
          dj.live_url ||
          dj.url ||
          (username
            ? "https://www.tiktok.com/@" + username + "/live"
            : "#")
        );
      }

      function pickRandomLiveDJ(forceDifferent) {
        if (!randomLiveDj) return;

        if (!currentLiveDJs.length) {
          randomLiveDj.style.display = "none";
          currentRandomDJ = null;
          return;
        }

        let candidates = currentLiveDJs;

        if (forceDifferent && currentLiveDJs.length > 1 && currentRandomDJ) {
          const currentUsername = getDJUsername(currentRandomDJ);
          candidates = currentLiveDJs.filter(
            dj => getDJUsername(dj) !== currentUsername
          );
        }

        const selected =
          candidates[Math.floor(Math.random() * candidates.length)];

        currentRandomDJ = selected;

        const name =
          selected.name ||
          selected.username ||
          selected.display_name ||
          "DJ";

        randomLiveDjName.textContent = "🎧 " + String(name);
        randomLiveDjWatch.href = getDJLiveUrl(selected);
        randomLiveDj.style.display = "block";
      }

      if (randomLiveDjNext) {
        randomLiveDjNext.addEventListener("click", function () {
          pickRandomLiveDJ(true);
        });
      }

      /* DISCOVER DJs
         Offline favourite DJs are searchable instead of being rendered as a
         long page. Genres come from learned AI genre profiles only. */
      let djDiscoveryDJs = [];
      let djDiscoveryQuery = "";
      let djDiscoveryGenre = "";
      let djDiscoveryRenderId = 0;

      function getDiscoveryGenres(dj) {
        // The /api/live favourite records can expose genres under several
        // field names depending on how the DJ was learned. Use all of them
        // so genre search works for both older and newer catalogue records.
        const rawSources = [
          dj && dj.rrr_learned_genres,
          dj && dj.ai_genres,
          dj && dj.genres,
          dj && dj.genre,
          dj && dj.detected_genres,
          dj && dj.current_genres,
          dj && dj.rrr_genres
        ];

        const raw = [];
        rawSources.forEach(source => {
          if (Array.isArray(source)) {
            raw.push(...source);
          } else if (source && typeof source === "object") {
            // Handle simple keyed genre maps as well as { genre: ... }.
            if (source.genre) raw.push(source.genre);
            else raw.push(...Object.values(source));
          } else if (source) {
            // A comma/pipe separated string is common in older records.
            raw.push(...String(source).split(/[,|]/));
          }
        });

        // Keep the existing LIVE AI parser as a final fallback.
        if (!raw.length) {
          raw.push(...getLiveAIGenres(dj));
        }

        const out = [];
        const seen = new Set();

        raw.forEach(item => {
          const rawValue =
            item && typeof item === "object"
              ? (item.genre || item.name || item.label || "")
              : item;

          const value = String(rawValue || "").split("---").pop().trim();
          if (!value || !isMusicGenre(value)) return;

          const key = value.toLowerCase();
          if (seen.has(key)) return;
          seen.add(key);
          out.push(value);
        });

        return filterMusicGenres(out);
      }

      async function renderDjDiscovery(favourites, liveDJs) {
        if (!liveDjsList) return;

        const renderId = ++djDiscoveryRenderId;
        const favouriteList = Array.isArray(favourites) ? favourites.slice() : [];
        const liveList = Array.isArray(liveDJs) ? liveDJs : [];

        // Discover is based on the RRR catalogue, but a DJ who is live can
        // have fresher genre/profile data in /api/live than the catalogue
        // record. Merge the live record over the favourite record so the
        // search/filter view sees the same DJ data as the LIVE cards.
        const liveByIdentity = new Map();
        liveList.forEach(live => {
          const identity = getDJIdentity(live);
          if (identity) liveByIdentity.set(identity, live);
        });

        djDiscoveryDJs = favouriteList.map(favourite => {
          const live = liveByIdentity.get(getDJIdentity(favourite));
          if (!live) return favourite;

          const merged = mergeDJRecords(live, favourite);
          // The live record is authoritative for live status.
          merged.live = true;
          return merged;
        });

        // A live DJ's rolling AI genre profile is fetched by
        // getLearnedDJGenres(), which is already cached for two minutes.
        // Populate that profile here so a genre such as TRANCE is immediately
        // available to Discover even when the older favourite record has
        // genre: null. This fixes the mismatch between the LIVE card and the
        // Discover filter without adding another backend data source.
        await Promise.all(
          liveList.map(async live => {
            const identity = getDJIdentity(live);
            if (!identity) return;

            const merged = djDiscoveryDJs.find(
              dj => getDJIdentity(dj) === identity
            );
            if (!merged) return;

            const learnedGenres = await getLearnedDJGenres(live);
            if (learnedGenres.length) {
              merged.rrr_learned_genres = learnedGenres;
            }
            merged.live = true;
          })
        );

        // A newer 30-second /api/live refresh may have started while the
        // profile requests above were in flight. Never let an older render
        // overwrite the latest catalogue state.
        if (renderId !== djDiscoveryRenderId) return;

        let section = document.getElementById("rrrDjDiscovery");
        if (!section) {
          section = document.createElement("section");
          section.id = "rrrDjDiscovery";
          section.className = "dj-discovery";
          liveDjsList.appendChild(section);
        }

        section.innerHTML = "";
        section.innerHTML =
          '<div class="dj-discovery-header">' +
            '<div class="dj-discovery-title">🎧 DISCOVER DJs</div>' +
            '<div class="dj-discovery-subtitle">Search the RRR DJ catalogue by name, @username or AI-detected genre.</div>' +
          '</div>' +
          '<input class="dj-discovery-search" id="djDiscoverySearch" type="search" ' +
            'autocomplete="off" placeholder="Search DJ name, @username or genre…" aria-label="Search RRR DJs">' +
          '<div class="dj-discovery-chips" id="djDiscoveryChips"></div>' +
          '<div class="dj-discovery-results" id="djDiscoveryResults"></div>';

        const search = section.querySelector("#djDiscoverySearch");
        search.value = djDiscoveryQuery;

        search.addEventListener("input", function () {
          djDiscoveryQuery = this.value;
          renderDjDiscoveryResults();
        });

        renderDjDiscoveryChips();
        renderDjDiscoveryResults();
      }

      function renderDjDiscoveryChips() {
        const chips = document.getElementById("djDiscoveryChips");
        if (!chips) return;

        const counts = new Map();

        djDiscoveryDJs.forEach(dj => {
          getDiscoveryGenres(dj).forEach(genre => {
            const key = genre.toLowerCase();
            if (!counts.has(key)) counts.set(key, { label: genre, count: 0 });
            counts.get(key).count++;
          });
        });

        const genres = Array.from(counts.values())
          .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
          .slice(0, 14);

        chips.innerHTML =
          '<button type="button" class="dj-discovery-chip ' +
          (!djDiscoveryGenre ? "active" : "") +
          '" data-genre="">ALL</button>' +
          genres.map(item =>
            '<button type="button" class="dj-discovery-chip ' +
            (djDiscoveryGenre.toLowerCase() === item.label.toLowerCase() ? "active" : "") +
            '" data-genre="' + escapeAttr(item.label) + '">' +
            escapeHtml(item.label) + ' · ' + item.count +
            '</button>'
          ).join("");

        chips.querySelectorAll(".dj-discovery-chip").forEach(button => {
          button.addEventListener("click", function () {
            djDiscoveryGenre = this.getAttribute("data-genre") || "";
            renderDjDiscoveryChips();
            renderDjDiscoveryResults();
          });
        });
      }

      function getDiscoveryScore(dj, genres) {
        if (!dj) return 0;

        // Prefer an explicit RRR discovery/popularity score when the API
        // supplies one. Never use the bot-risk scanner score for ordering.
        const explicitScore = [
          dj.discovery_score,
          dj.discoveryScore,
          dj.popularity_score,
          dj.popularityScore,
          dj.dj_score,
          dj.djScore
        ]
          .map(value => Number(value))
          .find(value => Number.isFinite(value));

        if (Number.isFinite(explicitScore)) {
          return Math.max(0, Math.min(100, explicitScore));
        }

        const followers = Number(
          dj.followers ?? dj.follower_count ?? dj.followerCount
        );
        const likes = Number(
          dj.likes ?? dj.like_count ?? dj.likeCount
        );
        const videos = Number(
          dj.videos ?? dj.video_count ?? dj.videoCount
        );

        let score = 0;

        // Popularity/activity are deliberately logarithmic so a huge account
        // does not completely swamp smaller DJs.
        if (Number.isFinite(followers) && followers > 0) {
          score += Math.min(42, Math.log10(followers + 1) * 7);
        }

        if (Number.isFinite(likes) && likes > 0) {
          score += Math.min(28, Math.log10(likes + 1) * 4.7);
        }

        if (Number.isFinite(videos) && videos > 0) {
          score += Math.min(12, videos * 0.35);
        }

        if (Array.isArray(genres)) {
          score += Math.min(8, genres.length * 1.6);
        }

        if (dj.verified === true || dj.is_verified === true) {
          score += 5;
        }

        // A currently live DJ gets a small discovery boost, while the
        // popularity/activity signals remain the main driver of the order.
        if (dj.live === true) score += 5;

        return Math.max(0, Math.min(100, score));
      }

      function renderDjDiscoveryResults() {
        const resultsEl = document.getElementById("djDiscoveryResults");
        if (!resultsEl) return;

        const query = djDiscoveryQuery.trim().toLowerCase();
        const genreFilter = djDiscoveryGenre.trim().toLowerCase();

        const ranked = djDiscoveryDJs
          .map(dj => {
            const name = String(dj.name || dj.display_name || dj.username || "").trim();
            const username = getDJUsername(dj);
            const genres = getDiscoveryGenres(dj);
            const haystack = [
              name,
              username,
              dj.unique_id,
              dj.handle,
              dj.bio,
              dj.biography,
              dj.genre,
              dj.genres,
              dj.ai_genres,
              dj.detected_genres,
              dj.current_genres,
              dj.rrr_genres,
              genres.join(" ")
            ].map(value => String(value || "").toLowerCase()).join(" ");

            if (genreFilter && !genres.some(g => g.toLowerCase() === genreFilter)) {
              return null;
            }

            if (query && !haystack.includes(query)) return null;

            let searchScore = 0;
            if (query) {
              if (name.toLowerCase().startsWith(query)) searchScore += 30;
              if (username.toLowerCase() === query.replace(/^@/, "")) searchScore += 50;
              if (genres.some(g => g.toLowerCase().startsWith(query))) searchScore += 20;
            }

            const discoveryScore = getDiscoveryScore(dj, genres);

            return { dj, name, username, genres, searchScore, discoveryScore };
          })
          .filter(Boolean)
          .sort((a, b) => {
            // With a search term, relevance remains the first priority.
            // Otherwise the catalogue is ordered by the RRR discovery score.
            if (query && b.searchScore !== a.searchScore) {
              return b.searchScore - a.searchScore;
            }
            if (b.discoveryScore !== a.discoveryScore) {
              return b.discoveryScore - a.discoveryScore;
            }
            return a.name.localeCompare(b.name);
          });

        const limited = ranked.slice(0, 12);

        if (!limited.length) {
          resultsEl.innerHTML =
            '<div class="dj-discovery-empty">' +
            (djDiscoveryDJs.length
              ? "No DJs match that search. Try another name or genre."
              : "No DJs are currently available in the RRR catalogue.") +
            '</div>';
          return;
        }

        resultsEl.innerHTML = limited.map(item => {
          const dj = item.dj;
          const profilePic =
            dj.profile_pic || dj.profile_picture || dj.avatar || dj.photo || "";
          const profileUrl =
            dj.profile_url ||
            (item.username
              ? "https://www.tiktok.com/@" + item.username
              : "#");
          const isLive = dj.live === true;
          const status = isLive ? "🔴 LIVE NOW" : "OFFLINE";
          const genresHtml = item.genres.length
            ? item.genres.slice(0, 5).map(getGenrePillHtml).join("")
            : '';

          const photo = profilePic
            ? '<img class="dj-discovery-thumb" src="' + escapeAttr(String(profilePic)) +
              '" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\';">' +
              '<div class="dj-discovery-placeholder" style="display:none;">🎧</div>'
            : '<div class="dj-discovery-placeholder">🎧</div>';

          return '<a class="dj-discovery-card' + (isLive ? ' is-live' : '') + '" href="' + escapeAttr(String(profileUrl)) +
            '" target="_blank" rel="noopener">' +
              '<div class="dj-discovery-card-top">' +
                photo +
              '</div>' +
              '<div class="dj-discovery-overlay"></div>' +
              '<div class="dj-discovery-content">' +
                '<div class="dj-discovery-status ' + (isLive ? "live" : "offline") + '">' +
                  '<span class="dj-discovery-status-dot"></span>' + status.replace('🔴 ', '') +
                '</div>' +
                '<div class="dj-discovery-name" title="' + escapeAttr(String(item.name || "DJ")) + '">' +
                  escapeHtml(item.name || "DJ") +
                '</div>' +
                '<div class="dj-discovery-handle">@' + escapeHtml(item.username) + '</div>' +
                '<div class="dj-discovery-genres" aria-label="DJ genres">' + genresHtml + '</div>' +
                '<div class="dj-discovery-match-label"><span>PROGRAM MATCH</span><span>—</span></div>' +
                '<div class="dj-discovery-match-bar"><div class="dj-discovery-match-fill"></div></div>' +
              '</div>' +
            '</a>';
        }).join("");
      }

      /* LIVE DJs API */

      async function loadLiveDJs() {
        if (!liveDjsList) return;

        const requestId = ++liveDjRequestId;

        if (liveDjRequestController) {
          liveDjRequestController.abort();
        }

        liveDjRequestController = new AbortController();

        try {
          const response = await fetch(
            getFreshUrl("https://api.radiorrr.com/api/live"),
            {
              cache: "no-store",
              headers: {
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
              },
              signal: liveDjRequestController.signal
            }
          );

          if (!response.ok) {
            throw new Error("API returned " + response.status);
          }

          const data = await response.json();

          if (requestId !== liveDjRequestId || !data || typeof data !== "object") {
            return;
          }

          // Stage 3 exposes the same ranking used by the automatic relay
          // selector. Use it only as a display/monitoring signal here.
          // If the endpoint is temporarily unavailable, the DJ cards still
          // render normally without scores.
          let genreMatchData = null;
          try {
            const matchResponse = await fetch(
              getFreshUrl("https://api.radiorrr.com/api/genre-match"),
              {
                cache: "no-store",
                headers: {
                  "Cache-Control": "no-cache",
                  "Pragma": "no-cache"
                },
                signal: liveDjRequestController.signal
              }
            );

            if (matchResponse.ok) {
              genreMatchData = await matchResponse.json();
            }
          } catch (matchError) {
            if (matchError && matchError.name === "AbortError") {
              return;
            }
            console.warn("Radio RRR genre-match display unavailable:", matchError);
          }

          const live = Array.isArray(data.live) ? data.live : [];

          currentLiveMatchScores = {};
          if (
            genreMatchData &&
            Array.isArray(genreMatchData.ranked)
          ) {
            genreMatchData.ranked.forEach(item => {
              const key = String(item.username || "")
                .replace(/^@/, "")
                .trim()
                .toLowerCase();

              if (!key) return;

              const score = Number(item.score);
              if (Number.isFinite(score)) {
                currentLiveMatchScores[key] = score;
              }
            });
          }
          const favourites = Array.isArray(data.favourites) ? data.favourites : [];

          // data.relay is the station-wide relay selected by the backend.
          // If TikTok briefly reports the DJ as offline, the backend may
          // deliberately keep the existing relay alive. Preserve that same
          // relay in the browser instead of destroying a healthy HLS player.
          const reportedRelayDj = data.relay || null;

          if (reportedRelayDj) {
            relayMissingSince = 0;
            lastKnownRelayDJ = reportedRelayDj;
          } else if (
            !manualFeaturedDJIdentity &&
            activeLiveStreamKey.indexOf("default:") === 0 &&
            activeLiveUsername
          ) {
            if (!relayMissingSince) {
              relayMissingSince = Date.now();
              console.warn(
                "Radio RRR: relay temporarily missing from API; keeping the existing LIVE player"
              );
            }
          } else {
            relayMissingSince = 0;
          }

          const relayGraceActive =
            !reportedRelayDj &&
            !manualFeaturedDJIdentity &&
            activeLiveStreamKey.indexOf("default:") === 0 &&
            activeLiveUsername &&
            relayMissingSince > 0 &&
            (Date.now() - relayMissingSince) < LIVE_RELAY_MISSING_GRACE_MS &&
            lastKnownRelayDJ;

          if (
            !reportedRelayDj &&
            !manualFeaturedDJIdentity &&
            relayMissingSince > 0 &&
            (Date.now() - relayMissingSince) >= LIVE_RELAY_MISSING_GRACE_MS
          ) {
            console.warn(
              "Radio RRR: relay missing beyond frontend grace period; allowing fallback"
            );
          }

          const relayDj = reportedRelayDj || (
            relayGraceActive ? lastKnownRelayDJ : null
          );

          const routerUsername = relayDj ? getDJUsername(relayDj) : "";
          const routerIdentity = relayDj ? getDJIdentity(relayDj) : "";
          const routerKey = relayDj
            ? routerUsername ||
              String(
                relayDj.name ||
                relayDj.display_name ||
                "LIVE DJ"
              )
            : "";
          const aiGenre = data.ai_genre || null;

          currentLiveDJs = live;

          const liveKeys = new Set(
            live.map(dj =>
              getDJUsername(dj).toLowerCase()
            ).filter(Boolean)
          );

          Object.keys(currentLiveMatchScores).forEach(key => {
            if (!liveKeys.has(key)) {
              delete currentLiveMatchScores[key];
            }
          });

           const uniqueLiveDJs = mergeUniqueDJs(live);
           const uniqueFavouriteDJs = mergeUniqueDJs(favourites);
           const liveFeaturedOverride = manualFeaturedDJIdentity
             ? uniqueLiveDJs.find(dj => getDJIdentity(dj) === manualFeaturedDJIdentity)
             : null;

           if (manualFeaturedDJIdentity) {
             if (liveFeaturedOverride) {
               manualFeaturedDJMissingCount = 0;
             } else {
               manualFeaturedDJMissingCount += 1;
               if (manualFeaturedDJMissingCount >= 2) {
                 manualFeaturedDJIdentity = "";
                 manualFeaturedDJMissingCount = 0;
               }
             }
           }

          /*
           * IMPORTANT: the backend relay may deliberately choose a DJ that
           * is NOT live[0]. For example, TikTok can report several live DJs
           * while some are age-restricted/unavailable. The backend tests
           * them and sets data.relay to the DJ actually feeding the HLS
           * stream.
           *
           * The router therefore follows data.relay, never live[0].
           */
           if (relayDj) {
              if (manualFeaturedDJIdentity && liveFeaturedOverride) {
                if (
                  activeLiveStreamKey !==
                  "manual:" + getDJUsername(liveFeaturedOverride)
                ) {
                  showLiveDjPlayer(liveFeaturedOverride);
                }
              } else if (!manualFeaturedDJIdentity && routerUsername) {
               if (
                 activeLiveStreamKey !== "default:" + routerKey
               ) {
                showLiveDjPlayer(relayDj);
              } else {
                // Refresh the label/link only. Keep the existing HLS
                // connection and buffer untouched.
                activeLiveUsername = routerUsername;
                randomLiveDjName.textContent =
                  "🎧 " + String(
                    relayDj.name ||
                    relayDj.username ||
                    relayDj.display_name ||
                    "LIVE DJ"
                  );
                updateMainDJMatchScore(relayDj);
                updateLiveDjGenres(relayDj);
                updateLiveDjProfile(relayDj);
                randomLiveDj.style.display = "block";
              }
            } else if (!manualFeaturedDJIdentity) {
              showRadioFallback();
            }
          } else if (!manualFeaturedDJIdentity) {
            // No relay was reported and there is no active grace-protected
            // player. This is a genuine station fallback condition.
            showRadioFallback();
          }

          /*
           * data.relay is the station-wide default. A manualFeaturedDJIdentity
           * is a browser-local override and must never alter data.relay.
           */
          liveDjsList.innerHTML = "";

          const matchingLiveDj = routerIdentity
            ? uniqueLiveDJs.find(dj => getDJIdentity(dj) === routerIdentity)
            : null;
          const matchingFavouriteDj = routerIdentity
            ? uniqueFavouriteDJs.find(dj => getDJIdentity(dj) === routerIdentity)
            : null;
          const routedDj = relayDj
            ? hydrateLiveDJProfile(
                mergeDJRecords(relayDj, matchingLiveDj),
                matchingFavouriteDj
              )
            : null;
           const selectedLiveDj = manualFeaturedDJIdentity
             ? uniqueLiveDJs.find(dj => getDJIdentity(dj) === manualFeaturedDJIdentity)
             : null;
           const selectedFavouriteDj = manualFeaturedDJIdentity
             ? uniqueFavouriteDJs.find(dj => getDJIdentity(dj) === manualFeaturedDJIdentity)
             : null;
           const selectedFeaturedDj = manualFeaturedDJIdentity && selectedLiveDj
             ? hydrateLiveDJProfile(selectedLiveDj, selectedFavouriteDj)
             : routedDj;
          currentFeaturedDJ = selectedFeaturedDj;
          const featuredIdentity = selectedFeaturedDj
            ? getDJIdentity(selectedFeaturedDj)
            : "";
          const hasRelay = Boolean(selectedFeaturedDj && featuredIdentity);

          await updateLiveGenresDetected(
            aiGenre,
            featuredIdentity,
            routerIdentity
          );

          if (hasRelay) {
            updateLiveDjGenres(selectedFeaturedDj);
            updateLiveDjProfile(selectedFeaturedDj);
          }

          const otherLiveDJs = uniqueLiveDJs.filter(
            dj => getDJIdentity(dj) !== featuredIdentity
          );

          if (routedDj && featuredIdentity &&
            getDJIdentity(routedDj) !== featuredIdentity &&
            !otherLiveDJs.some(dj => getDJIdentity(dj) === getDJIdentity(routedDj))) {
            otherLiveDJs.unshift(routedDj);
          }

          if (otherLiveDJs.length) {
            const otherLiveHeader = document.createElement("div");
            otherLiveHeader.style.gridColumn = "1 / -1";
            otherLiveHeader.style.marginTop = hasRelay ? "0.8rem" : "0";
            otherLiveHeader.innerHTML =
              '<div style="font-size:1.05rem;font-weight:800;color:#fff;margin:0.2rem 0 0.1rem;text-shadow:0 0 8px rgba(255,0,102,0.65);">🔴 ' +
              "SWITCH TO ANOTHER LIVE DJ" +
              '</div>';
            liveDjsList.appendChild(otherLiveHeader);
            // Secondary LIVE cards use the Scout detector's latest live
            // audio classification. Fetch the stored AI result for each
            // candidate so every live DJ card can show its own current
            // detected genres. This does NOT start another audio relay.
            const secondaryLiveDJs = await Promise.all(
              otherLiveDJs.map(async dj => {
                const matchingFavourite = uniqueFavouriteDJs.find(
                  favourite => getDJIdentity(favourite) === getDJIdentity(dj)
                );
                const merged = hydrateLiveDJProfile(dj, matchingFavourite);
                merged.rrr_live_detected_genres = await getLiveDetectedGenres(merged);
                return merged;
              })
            );

            secondaryLiveDJs.forEach(dj => {
              appendDjCard(
                dj,
                true,
                featuredIdentity,
                true
              );
            });
          } else if (!hasRelay) {
            const emptyLive = document.createElement("div");
            emptyLive.className = "live-empty";
            emptyLive.style.gridColumn = "1 / -1";
            emptyLive.innerHTML =
              'Nobody from the RRR DJ list is live right now.<br><small>We\'ll keep checking automatically.</small>';
            liveDjsList.appendChild(emptyLive);
          }

          // Offline RRR favourites are no longer rendered as a long list here.
          // They are available through the searchable DISCOVER DJs section below.
          await renderDjDiscovery(uniqueFavouriteDJs, uniqueLiveDJs);

        } catch (error) {
          if (error && error.name === "AbortError") return;
          console.error("Live DJ API error:", error);
          if (!currentLiveDJs.length && !activeLiveUsername) {
            liveDjsList.innerHTML =
              '<div class="live-error" style="grid-column:1 / -1;">Could not load the Live DJ list right now.<br><small>The API may be temporarily unavailable, or browser access may need CORS enabled.</small></div>';
          }
        }
      }


      function appendDjCard(dj, isLive, routerIdentity, moveWatchButton) {
        const name =
          dj.name || dj.username || dj.display_name || "DJ";

        const username =
          dj.username || dj.unique_id || dj.handle || "";

        const viewers =
          dj.viewers ?? dj.viewer_count ?? dj.viewerCount;

        const title =
          dj.title || dj.room_title || dj.description || "";

        const profileUrl =
          dj.profile_url ||
          (username
            ? "https://www.tiktok.com/@" + String(username).replace(/^@/, "")
            : "#");

        const isFavourite = !isLive;
        const card = document.createElement(isFavourite ? "a" : "div");
        card.className = "dj-card" +
          (isFavourite ? " dj-favourite-card" : "");
        if (isFavourite) {
          card.href = profileUrl;
          card.target = "_blank";
          card.rel = "noopener";
        }

        const liveUrl =
          dj.live_url ||
          dj.url ||
          (username
            ? "https://www.tiktok.com/@" + String(username).replace(/^@/, "") + "/live"
            : profileUrl);

        const isRouted =
          isLive &&
          routerIdentity &&
          getDJIdentity(dj) === routerIdentity;

        const isActuallyLive = isLive || dj.live === true;

        const matchKey = String(username || "")
          .replace(/^@/, "")
          .trim()
          .toLowerCase();

        const matchScore = isActuallyLive
          ? currentLiveMatchScores[matchKey]
          : undefined;

        let matchScoreHtml = "";
        if (Number.isFinite(matchScore)) {
          const roundedMatchScore = Math.max(
            0,
            Math.min(100, Math.round(matchScore))
          );

          const matchClass =
            roundedMatchScore >= 50
              ? "match-high"
              : roundedMatchScore >= 20
                ? "match-mid"
                : "match-low";

          matchScoreHtml =
            '<div class="dj-match-score ' + matchClass + '" title="RRR genre compatibility score out of 100">' +
            '🎯 PROGRAM MATCH ' + roundedMatchScore +
            '</div>';
        }

        const viewerText =
          viewers !== undefined && viewers !== null && Number(viewers) > 0
            ? " · " + viewers + " watching"
            : "";

        const statusText = isRouted
          ? "ROUTING NOW"
          : isActuallyLive
            ? "LIVE NOW"
            : "OFFLINE";
        const actionText = isActuallyLive ? "▶ WATCH LIVE" : "VIEW TIKTOK";
        const actionUrl = isActuallyLive ? liveUrl : profileUrl;
        // Secondary LIVE DJ cards are rendered from their previously learned
        // AI genre profile, supplied by loadLiveDJs(). They never use the
        // current live audio classification. The featured DJ remains the
        // only DJ whose current audio is actively analysed.
        const genres = Array.isArray(dj.rrr_learned_genres)
          ? dj.rrr_learned_genres
          : getStoredDJGenres(dj);
        const genreHtml = genres.length
          ? genres.slice(0, 5).map(getGenrePillHtml).join("")
          : "";
        const liveStatusHtml =
          '<div class="dj-live" style="' +
          (isActuallyLive ? "" : "color:#a5b4fc;") +
          '">' +
          (isActuallyLive
            ? '<span class="dj-live-dot"></span>'
            : '<span style="width:7px;height:7px;border-radius:50%;background:#64748b;display:inline-block;"></span>') +
          escapeHtml(statusText) +
          '</div>';
        const watchHtml =
          '<a class="dj-watch" href="' + escapeAttr(String(actionUrl)) +
          '" target="_blank" rel="noopener">' + actionText + '</a>';
        const metaText = title;
        const metaHtml = metaText || viewerText
          ? '<div class="dj-meta">' +
            escapeHtml(String(metaText)) +
            escapeHtml(String(viewerText)) +
            '</div>'
          : "";

        if (isActuallyLive) {
          card.style.borderColor = "rgba(34,211,238,0.82)";
          card.style.boxShadow = "0 0 18px rgba(34,211,238,0.36)";
        } else {
          card.style.borderColor = "rgba(168,85,247,0.45)";
        }

        const profilePic =
          dj.profile_pic ||
          dj.profile_picture ||
          dj.avatar ||
          dj.photo ||
          "";

        // Live DJ cards use the DJ profile image as a subtle full-card
        // background. A dark translucent gradient keeps the text readable
        // while letting the photo add visual personality to the card.
        if (isActuallyLive && profilePic) {
          card.style.backgroundImage =
            'linear-gradient(90deg, rgba(7,10,25,0.92) 0%, rgba(7,10,25,0.76) 48%, rgba(7,10,25,0.58) 100%), url("' +
            String(profilePic).replace(/"/g, '\\"') +
            '")';
          card.style.backgroundSize = "cover";
          card.style.backgroundPosition = "center";
          card.style.backgroundRepeat = "no-repeat";
        }

        const photoHtml = profilePic
          ? '<img class="dj-thumb" src="' + escapeAttr(String(profilePic)) +
            '" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\';">' +
            '<div class="dj-thumb-placeholder" style="display:none;">🎧</div>'
          : '<div class="dj-thumb-placeholder">🎧</div>';

        if (moveWatchButton && isActuallyLive) {
          const safeScore = Number.isFinite(matchScore)
            ? Math.max(0, Math.min(100, Math.round(matchScore)))
            : null;
          const secondaryMatchClass = safeScore === null
            ? "match-low"
            : safeScore >= 50
              ? "match-high"
              : safeScore >= 20
                ? "match-mid"
                : "match-low";
          const secondaryScoreText = safeScore === null ? "—" : safeScore + "%";
          const secondaryFillWidth = safeScore === null ? 0 : safeScore;
          // Secondary LIVE cards show the Scout detector's CURRENT audio
          // classification, not the DJ's learned/profile genres. The live
          // detection is supplied by loadLiveDJs() as rrr_live_detected_genres.
          const liveDetectedGenres = Array.isArray(dj.rrr_live_detected_genres)
            ? dj.rrr_live_detected_genres
            : [];
          const secondaryDetectedGenres = liveDetectedGenres.length
            ? liveDetectedGenres.slice(0, 5).map(item => {
                const name = String(item.genre || "").replace(/^.*---/, "").trim();
                const percent = Math.round(Number(item.confidence) * 100);
                const width = Math.max(4, Math.min(100, Number(item.relativeWidth || percent)));
                return '<div class="dj-secondary-detected-row">' +
                  '<span class="dj-secondary-detected-name" title="' + escapeAttr(name) + '">' + escapeHtml(name) + '</span>' +
                  '<span class="dj-secondary-detected-bar"><span style="width:' + width.toFixed(1) + '%"></span></span>' +
                  '<span class="dj-secondary-detected-confidence">' + percent + '%</span>' +
                '</div>';
              }).join("")
            : '<div class="dj-secondary-detected-empty">Analysing live audio…</div>';
          const secondaryPhoto = profilePic
            ? '<img class="dj-secondary-photo" src="' + escapeAttr(String(profilePic)) +
              '" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\';">' +
              '<div class="dj-secondary-placeholder" style="display:none;">🎧</div>'
            : '<div class="dj-secondary-placeholder">🎧</div>';

          card.innerHTML =
            secondaryPhoto +
            '<div class="dj-secondary-overlay"></div>' +
            '<div class="dj-secondary-content">' +
              '<div class="dj-secondary-live"><span class="dj-secondary-live-dot"></span>LIVE</div>' +
              '<div class="dj-secondary-name" title="' + escapeAttr(String(name)) + '">' + escapeHtml(String(name)) + '</div>' +
              '<div class="dj-secondary-detected-title">LIVE DETECTED</div>' +
              '<div class="dj-secondary-detected-list" aria-label="Live detected genres">' + secondaryDetectedGenres + '</div>' +
              '<div class="dj-secondary-match-label"><span>Program Match</span><span class="dj-secondary-match-value">' + secondaryScoreText + '</span></div>' +
              '<div class="dj-secondary-match-bar" aria-label="Program Match ' + secondaryScoreText + '">' +
                '<div class="dj-secondary-match-fill ' + secondaryMatchClass + '" style="width:' + secondaryFillWidth + '%"></div>' +
              '</div>' +
            '</div>';
        } else {
          card.innerHTML =
            '<div class="dj-card-top">' +
              photoHtml +
              '<div>' +
                liveStatusHtml +
                '<div class="dj-name">' + escapeHtml(String(name)) + '</div>' +
                matchScoreHtml +
                '<div class="dj-genre">' + genreHtml + '</div>' +
              '</div>' +
            '</div>' +
            metaHtml +
            (isFavourite ? "" : watchHtml);
        }

        if (moveWatchButton && isActuallyLive) {
          card.classList.add("dj-live-switch-card");
          card.setAttribute("role", "button");
          card.setAttribute("tabindex", "0");
          card.setAttribute("aria-label", "Watch " + String(name) + " in this browser");

          const switchToDj = async function () {
            if (card.dataset.switching === "1") return;

            const targetUsername = getDJUsername(dj);
            if (!targetUsername) return;

            card.dataset.switching = "1";
            const originalOpacity = card.style.opacity;
            card.style.opacity = "0.65";

            try {
              // IMPORTANT: do not call /api/live/switch here. That endpoint
              // changes the shared station relay. The Router already supports
              // per-DJ manual relays via /api/live-stream?dj=USERNAME.
              // This selection therefore affects this browser only.
              manualFeaturedDJIdentity = getDJIdentity(dj);
              manualFeaturedDJMissingCount = 0;
              manualDJStreamFailureHandled = false;
              currentFeaturedDJ = dj;

              showLiveDjPlayer(dj);
              await updateLiveGenresDetected(
                null,
                manualFeaturedDJIdentity,
                ""
              );
              await loadLiveDJs();
            } catch (error) {
              console.error("Radio RRR DJ selection failed:", error);
              manualFeaturedDJIdentity = "";
              manualFeaturedDJMissingCount = 0;
              manualDJStreamFailureHandled = false;
              await loadLiveDJs();
            } finally {
              card.dataset.switching = "0";
              card.style.opacity = originalOpacity;
            }
          };

          card.addEventListener("click", function () {
            switchToDj();
          });

          card.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              switchToDj();
            }
          });
        }

        liveDjsList.appendChild(card);
      }

      function escapeHtml(value) {
        return value
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");
      }

      function escapeAttr(value) {
        return value
          .replace(/&/g, "&amp;")
          .replace(/"/g, "&quot;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
      }

      if (liveRefresh) {
        liveRefresh.addEventListener("click", loadLiveDJs);
      }

      loadLiveDJs();

      setInterval(loadLiveDJs, 30 * 1000);

      document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
          loadLiveDJs();

          // Returning to the RadioRRR tab must NOT change the user's
          // existing mute/unmute state. Only resume playback if necessary.
          if (liveDjVideo && liveDjVideo.paused) {
            liveDjVideo.play().catch(() => {});
          }
        }
      });

      window.addEventListener("pageshow", function () {
        loadLiveDJs();

        // pageshow must not re-apply a potentially stale mute state.
        if (liveDjVideo && liveDjVideo.paused) {
          liveDjVideo.play().catch(() => {});
        }
      });

      // The scheduler API is the live source of truth for the public site.
      // The existing hard-coded arrays below remain only as a temporary
      // fallback if the API is unavailable.
      let publicScheduleRows = [];
      let publicScheduleLoaded = false;

      function publicDayName(dayIndex) {
        return [
          "Sunday",
          "Monday",
          "Tuesday",
          "Wednesday",
          "Thursday",
          "Friday",
          "Saturday"
        ][dayIndex];
      }

      function publicGenreClass(genre) {
        const g = String(genre || "").toLowerCase();
        if (g.includes("80s")) return "genre-neon-yellow";
        if (g.includes("bass") || g.includes("drum")) return "genre-neon-orange";
        if (g.includes("techno")) return "genre-neon-electric-blue";
        if (g.includes("trance")) return "genre-neon-cyan";
        if (g.includes("electro")) return "genre-neon-aqua";
        if (g.includes("house")) return "genre-neon-hot-pink";
        if (g.includes("progressive") || g.includes("psy")) return "genre-neon-violet";
        if (g.includes("synth") || g.includes("retro")) return "genre-neon-blue-purple";
        if (g.includes("ambient") || g.includes("chill") || g.includes("downtempo")) return "genre-neon-cyan";
        return "genre-neon-electric-purple";
      }

      function publicProgramIcon(name, start) {
        const n = String(name || "").toLowerCase();
        if (n.includes("sunrise") || n.includes("morning")) return "🌅";
        if (n.includes("drive")) return "🚗";
        if (n.includes("afternoon") || n.includes("day party")) return "⚡";
        if (n.includes("dinner") || n.includes("prime") || n.includes("after dark")) return "🔥";
        if (n.includes("night") || Number(start) >= 20 || Number(start) < 4) return "🌙";
        return "🎧";
      }

      function normalisePublicScheduleRows(rows) {
        return (rows || []).map(function (row) {
          return {
            day: row.day,
            start: row.start,
            end: row.end,
            name: row.name,
            genres: Array.isArray(row.genres) ? row.genres : []
          };
        });
      }

      function publicScheduleSignature(rows) {
        return JSON.stringify((rows || []).map(function (r) {
          return [r.start, r.end, r.name, r.genres];
        }));
      }

      function renderPublicSchedule(rows) {
        const container = document.getElementById("publicScheduleRows");
        if (!container) return;

        const byDay = {};
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].forEach(function (day) {
          byDay[day] = normalisePublicScheduleRows(rows).filter(function (r) { return r.day === day; });
        });

        const displayRows = [];
        const weekdaySame = ["Monday", "Tuesday", "Wednesday", "Thursday"].every(function (day) {
          return publicScheduleSignature(byDay[day]) === publicScheduleSignature(byDay.Monday);
        });

        if (weekdaySame && byDay.Monday.length) {
          byDay.Monday.forEach(function (r) {
            displayRows.push({ row: r, label: "WEEKDAYS · MON–THU" });
          });
        } else {
          ["Monday", "Tuesday", "Wednesday", "Thursday"].forEach(function (day) {
            byDay[day].forEach(function (r) {
              displayRows.push({ row: r, label: day.toUpperCase() });
            });
          });
        }

        byDay.Friday.forEach(function (r) { displayRows.push({ row: r, label: "FRIDAY · WEEKEND WARM-UP" }); });
        byDay.Saturday.forEach(function (r) { displayRows.push({ row: r, label: "SATURDAY · WEEKEND" }); });
        byDay.Sunday.forEach(function (r) { displayRows.push({ row: r, label: "SUNDAY · SUNDAY SESSIONS" }); });

        if (!displayRows.length) {
          container.innerHTML = '<div class="schedule-row" role="row"><div role="cell">—</div><div role="cell">—</div><div role="cell"><strong>Schedule unavailable</strong></div><div role="cell"></div></div>';
          return;
        }

        container.innerHTML = displayRows.map(function (item) {
          const r = item.row;
          const genres = r.genres.map(function (genre) {
            const safe = String(genre).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
            return '<span class="dj-genre-pill ' + publicGenreClass(genre) + '">' + safe + '</span>';
          }).join("");
          const safeName = String(r.name || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
          return '<div class="schedule-row" role="row"><div role="cell">' + item.label + '</div><div role="cell">' + r.start + ' – ' + r.end + '</div><div role="cell"><strong>' + publicProgramIcon(r.name, r.start) + ' ' + safeName + '</strong></div><div role="cell"><div class="schedule-genres">' + genres + '</div></div></div>';
        }).join("");
      }

      async function loadPublicSchedule() {
        try {
          const response = await fetch("https://api.radiorrr.com/api/schedule?public_site=" + Date.now(), {
            cache: "no-store",
            headers: { "Accept": "application/json" }
          });
          if (!response.ok) throw new Error("Schedule API returned " + response.status);
          const data = await response.json();
          if (!Array.isArray(data.schedule) || !data.schedule.length) throw new Error("Schedule API returned no schedule");

          publicScheduleRows = normalisePublicScheduleRows(data.schedule);
          publicScheduleLoaded = true;
          renderPublicSchedule(publicScheduleRows);
          updateTitles();
        } catch (error) {
          console.warn("Radio RRR public schedule API unavailable; using fallback schedule:", error);
          if (!publicScheduleLoaded) renderPublicSchedule([]);
          updateTitles();
        }
      }

      function getCurrentScheduleBlock(hour, dayIndex) {

        if (publicScheduleRows.length) {
          const dayName = publicDayName(dayIndex);
          const rowsForDay = publicScheduleRows.filter(function (row) {
            return row.day === dayName;
          });
          const current = rowsForDay.find(function (row) {
            return hour >= Number(row.start.split(":")[0]) + Number(row.start.split(":")[1]) / 60 &&
                   hour < (row.end === "00:00" ? 24 : Number(row.end.split(":")[0]) + Number(row.end.split(":")[1]) / 60);
          });
          if (current) {
            return {
              start: Number(current.start.split(":")[0]) + Number(current.start.split(":")[1]) / 60,
              end: current.end === "00:00" ? 24 : Number(current.end.split(":")[0]) + Number(current.end.split(":")[1]) / 60,
              name: current.name,
              genres: current.genres.join(" · ")
            };
          }
        }

        const weekdayBlocks = [
          {
            start: 0,
            end: 4,
            name: "Late Nights Insomnia",
            genres: "Chill · Deep House · Melodic · Progressive"
          },
          {
            start: 4,
            end: 8,
            name: "Sunrise Sessions",
            genres: "Chill · Downtempo · Ambient · Deep House"
          },
          {
            start: 8,
            end: 12,
            name: "Day Drive",
            genres: "80s · Synthwave · Chill Electro · Nu-Disco"
          },
          {
            start: 12,
            end: 16,
            name: "Afternoon Beats",
            genres: "House · Deep House · Progressive · Funky House"
          },
          {
            start: 16,
            end: 20,
            name: "Dinner Warm ups",
            genres: "House · Tech House · Trance · Progressive"
          },
          {
            start: 20,
            end: 24,
            name: "Prime Time",
            genres: "Electro · Techno · Trance · Progressive"
          }
        ];

        const fridaySaturdayBlocks = [
          {
            start: 0,
            end: 4,
            name: "After Dark",
            genres: "Techno · Hard Techno · Trance · Psy-Trance"
          },
          {
            start: 4,
            end: 8,
            name: "Late Mornings",
            genres: "Techno · Trance · Progressive · Psy-Trance"
          },
          {
            start: 8,
            end: 12,
            name: "Morning",
            genres: "Chill · House · Progressive · Melodic"
          },
          {
            start: 12,
            end: 16,
            name: "Day Party",
            genres: "House · Tech House · Progressive · Electro"
          },
          {
            start: 16,
            end: 20,
            name: "Prime Time",
            genres: "Tech House · Techno · Trance · Progressive"
          },
          {
            start: 20,
            end: 24,
            name: "Party Night",
            genres: "Techno · Trance · BASSLINE · Drum & Bass"
          }
        ];

        const sundayBlocks = [
          {
            start: 0,
            end: 4,
            name: "Late Night",
            genres: "Techno · Trance · Progressive · Psy-Trance"
          },
          {
            start: 4,
            end: 8,
            name: "After Hours",
            genres: "Deep House · Progressive · Melodic · Chill"
          },
          {
            start: 8,
            end: 12,
            name: "Sunday Morning",
            genres: "Chill · Downtempo · Deep House · Ambient"
          },
          {
            start: 12,
            end: 16,
            name: "Sunday Session",
            genres: "House · Deep House · Progressive · Organic House"
          },
          {
            start: 16,
            end: 20,
            name: "Sunday Sunset",
            genres: "Melodic · Progressive · Deep House · Chill"
          },
          {
            start: 20,
            end: 24,
            name: "Sunday Night",
            genres: "Chill · Deep House · Progressive · Trance"
          }
        ];

        let blocks = weekdayBlocks;

        if (dayIndex === 5 || dayIndex === 6) {
          blocks = fridaySaturdayBlocks;
        } else if (dayIndex === 0) {
          blocks = sundayBlocks;
        }

        return (
          blocks.find(function (block) {
            return hour >= block.start && hour < block.end;
          }) || blocks[0]
        );

      }


      function updateActiveScheduleRow(now) {

        const rows = document.querySelectorAll(
          '.schedule-table .schedule-row:not(.schedule-header)'
        );

        rows.forEach(function (row) {
          row.classList.remove('schedule-active');
        });

        if (!rows.length) return;

        const dayIndex = now.getDay();
        const hour = now.getHours() + (now.getMinutes() / 60);

        let dayType = 'weekday';
        if (dayIndex === 5) dayType = 'friday';
        else if (dayIndex === 6) dayType = 'saturday';
        else if (dayIndex === 0) dayType = 'sunday';

        rows.forEach(function (row) {
          const cells = row.querySelectorAll(':scope > div');
          if (cells.length < 4) return;

          const dayText = cells[0].textContent.trim().toUpperCase();
          const timeText = cells[1].textContent.trim();

          const matchesDay =
            (dayType === 'weekday' && dayText.indexOf('WEEKDAYS') === 0) ||
            (dayType === 'friday' && dayText.indexOf('FRIDAY') === 0) ||
            (dayType === 'saturday' && dayText.indexOf('SATURDAY') === 0) ||
            (dayType === 'sunday' && dayText.indexOf('SUNDAY') === 0);

          if (!matchesDay) return;

          const times = timeText.match(/(\d{2}):(\d{2})\s*[–-]\s*(\d{2}):(\d{2})/);
          if (!times) return;

          const start = Number(times[1]) + Number(times[2]) / 60;
          let end = Number(times[3]) + Number(times[4]) / 60;
          if (end === 0) end = 24;

          if (hour >= start && hour < end) {
            row.classList.add('schedule-active');
          }
        });
      }

      function updateTitles() {

        const now =
          new Date();

        const days = [
          "Sunday",
          "Monday",
          "Tuesday",
          "Wednesday",
          "Thursday",
          "Friday",
          "Saturday"
        ];

        const dayName =
          days[now.getDay()];

        const block =
          getCurrentScheduleBlock(
            now.getHours(),
            now.getDay()
          );

        updateActiveScheduleRow(now);

        const blockLabel =
          dayName +
          " – " +
          block.name;

        if (heroNowEl) {
          heroNowEl.textContent =
            blockLabel;
        }

        if (liveProgramTargetGenres) {
          liveProgramTargetGenres.innerHTML =
            block.genres
              .split("·")
              .map(function (genre) {
                const safeGenre = genre
                  .trim()
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
                return '<span class="random-live-program-target-pill">' +
                  safeGenre +
                  '</span>';
              })
              .join("");
        }

      }

      updateTitles();
      loadPublicSchedule();

      // Keep the public schedule and current-program display in sync with
      // the Radio RRR scheduler without requiring a GitHub deployment.
      setInterval(loadPublicSchedule, 60 * 1000);
      setInterval(
        updateTitles,
        60 * 1000
      );

      function setStatus(msg) {

        if (!statusEl) return;

        statusEl.style.display =
          "block";

        statusEl.textContent =
          "Status: " + msg;

      }

      function initAudioAnalyser() {

        /*
         * IMPORTANT: Do NOT route the station <audio> element through a
         * Web Audio MediaElementSource.  Doing that makes the AudioContext
         * part of the radio playback path; if the browser suspends that
         * context while the user changes the site's tabs, the stream can
         * remain "playing" but become completely silent.
         *
         * Radio RRR playback is therefore deliberately native HTMLMedia
         * playback.  The visualizer continues with its own animation below.
         */
        analyserReady = false;
        analyser = null;
        dataArray = null;

        if (audio) {
          audio.muted = false;
          audio.volume = 1;
        }

        setStatus(
          "visualizer active"
        );

      }

      function resize() {

        if (!canvas || !ctx)
          return;

        const dpr =
          window.devicePixelRatio || 1;

        const w =
          canvas.clientWidth;

        const h =
          canvas.clientHeight;

        canvas.width =
          w * dpr;

        canvas.height =
          h * dpr;

        ctx.setTransform(
          dpr,
          0,
          0,
          dpr,
          0,
          0
        );

      }

      resize();

      window.addEventListener(
        "resize",
        resize
      );

      const particles = [];

      const maxParticles = 90;

      class Particle {

        constructor(
          x,
          y,
          vx,
          vy,
          hue,
          size
        ) {

          this.x = x;
          this.y = y;
          this.vx = vx;
          this.vy = vy;
          this.hue = hue;
          this.size = size;
          this.alpha = 1;
          this.decay = 0.012;

        }

        update() {

          this.x += this.vx;
          this.y += this.vy;

          this.vy += 0.08;

          this.alpha -=
            this.decay;

        }

        draw(ctx) {

          ctx.save();

          ctx.globalAlpha =
            Math.max(
              0,
              this.alpha
            );

          const g =
            ctx.createRadialGradient(
              this.x,
              this.y,
              0,
              this.x,
              this.y,
              this.size
            );

          g.addColorStop(
            0,
            `hsla(${this.hue}, 100%, 70%, 1)`
          );

          g.addColorStop(
            1,
            `hsla(${this.hue}, 100%, 50%, 0)`
          );

          ctx.fillStyle = g;

          ctx.beginPath();

          ctx.arc(
            this.x,
            this.y,
            this.size,
            0,
            Math.PI * 2
          );

          ctx.fill();

          ctx.restore();

        }

      }

      let prevBass = 0;
      let prevMid = 0;
      let prevTreble = 0;

      const smoothing = 0.7;

      function drawFrame() {

        if (!canvas || !ctx) {

          requestAnimationFrame(
            drawFrame
          );

          return;

        }

        const t =
          performance.now() / 1000;

        const w =
          canvas.clientWidth;

        const h =
          canvas.clientHeight;

        const cx =
          w / 2;

        const cy =
          h / 2;

        let bass =
          0.25 +
          0.15 *
          Math.sin(t * 1.7);

        let mid =
          0.20 +
          0.10 *
          Math.sin(t * 2.1 + 1.0);

        let treble =
          0.18 +
          0.08 *
          Math.sin(t * 2.6 + 2.2);

        let spectrum = null;

        if (
          analyserReady &&
          analyser &&
          dataArray
        ) {

          analyser.getByteFrequencyData(
            dataArray
          );

          spectrum =
            dataArray;

          const bassEnd =
            Math.floor(
              dataArray.length * 0.10
            );

          const midEnd =
            Math.floor(
              dataArray.length * 0.40
            );

          let bassSum = 0;
          let midSum = 0;
          let trebleSum = 0;

          for (
            let i = 0;
            i < bassEnd;
            i++
          ) {
            bassSum +=
              dataArray[i];
          }

          for (
            let i = bassEnd;
            i < midEnd;
            i++
          ) {
            midSum +=
              dataArray[i];
          }

          for (
            let i = midEnd;
            i < dataArray.length * 0.80;
            i++
          ) {
            trebleSum +=
              dataArray[i];
          }

          bass =
            (bassSum /
              Math.max(
                1,
                bassEnd
              )) / 255;

          mid =
            (midSum /
              Math.max(
                1,
                midEnd - bassEnd
              )) / 255;

          treble =
            (trebleSum /
              Math.max(
                1,
                dataArray.length * 0.80 - midEnd
              )) / 255;

          bass =
            prevBass *
              smoothing +
            bass *
              (1 - smoothing);

          mid =
            prevMid *
              smoothing +
            mid *
              (1 - smoothing);

          treble =
            prevTreble *
              smoothing +
            treble *
              (1 - smoothing);

          prevBass =
            bass;

          prevMid =
            mid;

          prevTreble =
            treble;

        }

        ctx.fillStyle =
          "rgba(0, 0, 0, 0.30)";

        ctx.fillRect(
          0,
          0,
          w,
          h
        );

        const bg =
          ctx.createRadialGradient(
            cx,
            cy,
            0,
            cx,
            cy,
            Math.max(w, h) * 0.7
          );

        bg.addColorStop(
          0,
          `hsla(${(t * 20) % 360}, 75%, 18%, 0.14)`
        );

        bg.addColorStop(
          0.5,
          `hsla(${(t * 15 + 180) % 360}, 85%, 12%, 0.10)`
        );

        bg.addColorStop(
          1,
          "rgba(0, 0, 0, 0)"
        );

        ctx.fillStyle = bg;

        ctx.fillRect(
          0,
          0,
          w,
          h
        );

        if (
          bass > 0.35 &&
          Math.random() > 0.55
        ) {

          const angle =
            Math.random() *
            Math.PI *
            2;

          const speed =
            1.8 +
            bass * 4.5;

          const vx =
            Math.cos(angle) *
            speed;

          const vy =
            Math.sin(angle) *
            speed -
            1.8;

          const hue =
            (
              t * 55 +
              Math.random() * 70
            ) % 360;

          particles.push(
            new Particle(
              cx,
              cy,
              vx,
              vy,
              hue,
              3 + bass * 9
            )
          );

        }

        for (
          let i = particles.length - 1;
          i >= 0;
          i--
        ) {

          particles[i].update();

          particles[i].draw(ctx);

          if (
            particles[i].alpha <= 0
          ) {
            particles.splice(
              i,
              1
            );
          }

        }

        if (
          particles.length >
          maxParticles
        ) {

          particles.splice(
            0,
            particles.length -
              maxParticles
          );

        }

        const ringCount = 5;

        for (
          let i = 0;
          i < ringCount;
          i++
        ) {

          const radius =
            Math.min(w, h) *
              (0.28 + i * 0.085) +
            bass * 38;

          const rotation =
            t *
              (0.18 + i * 0.11) +
            i *
              Math.PI /
              3;

          ctx.save();

          ctx.translate(
            cx,
            cy
          );

          ctx.rotate(
            rotation
          );

          ctx.beginPath();

          const segments = 64;

          for (
            let j = 0;
            j <= segments;
            j++
          ) {

            const ang =
              (j / segments) *
              Math.PI *
              2;

            const r =
              radius +
              Math.sin(
                j * 0.45 +
                t * 2.0
              ) *
              12 *
              mid;

            const x =
              Math.cos(ang) *
              r;

            const y =
              Math.sin(ang) *
              r;

            if (j === 0)
              ctx.moveTo(x, y);
            else
              ctx.lineTo(x, y);

          }

          ctx.closePath();

          const hue =
            (
              t * 30 +
              i * 60
            ) % 360;

          ctx.strokeStyle =
            `hsla(${hue}, 100%, 62%, ${0.28 + bass * 0.35})`;

          ctx.lineWidth =
            2 +
            bass * 3.2;

          ctx.shadowBlur =
            18 +
            bass * 22;

          ctx.shadowColor =
            `hsla(${hue}, 100%, 60%, 1)`;

          ctx.stroke();

          ctx.restore();

        }

        ctx.shadowBlur = 0;

        if (spectrum) {

          const barCount = 120;

          const barWidth = 3;

          const innerRadius =
            Math.min(w, h) *
            0.12;

          for (
            let i = 0;
            i < barCount;
            i++
          ) {

            const angle =
              (i / barCount) *
              Math.PI *
              2 -
              Math.PI / 2;

            const specIndex =
              Math.floor(
                (i / barCount) *
                spectrum.length
              );

            let value =
              (spectrum[specIndex] || 0) /
              255;

            const nextIndex =
              Math.min(
                specIndex + 1,
                spectrum.length - 1
              );

            const nextValue =
              (spectrum[nextIndex] || 0) /
              255;

            value =
              value * 0.7 +
              nextValue * 0.3;

            const barHeight =
              value *
              Math.min(w, h) *
              0.35;

            const x1 =
              cx +
              Math.cos(angle) *
              innerRadius;

            const y1 =
              cy +
              Math.sin(angle) *
              innerRadius;

            const x2 =
              cx +
              Math.cos(angle) *
              (
                innerRadius +
                barHeight
              );

            const y2 =
              cy +
              Math.sin(angle) *
              (
                innerRadius +
                barHeight
              );

            const hue =
              (
                i * 3 +
                t * 55
              ) % 360;

            const grad =
              ctx.createLinearGradient(
                x1,
                y1,
                x2,
                y2
              );

            grad.addColorStop(
              0,
              `hsla(${hue}, 100%, 50%, 0.35)`
            );

            grad.addColorStop(
              1,
              `hsla(${hue}, 100%, 70%, ${0.75 + value * 0.25})`
            );

            ctx.beginPath();

            ctx.moveTo(
              x1,
              y1
            );

            ctx.lineTo(
              x2,
              y2
            );

            ctx.strokeStyle =
              grad;

            ctx.lineWidth =
              barWidth;

            ctx.lineCap =
              "round";

            ctx.stroke();

          }

        }

        const orbRadius =
          18 +
          bass * 40 +
          Math.sin(t * 4) * 6;

        const orb =
          ctx.createRadialGradient(
            cx,
            cy,
            0,
            cx,
            cy,
            orbRadius * 2
          );

        orb.addColorStop(
          0,
          `hsla(${(t * 100) % 360}, 100%, 92%, 1)`
        );

        orb.addColorStop(
          0.3,
          `hsla(${(t * 100 + 60) % 360}, 100%, 70%, 0.85)`
        );

        orb.addColorStop(
          0.6,
          `hsla(${(t * 100 + 120) % 360}, 100%, 52%, 0.45)`
        );

        orb.addColorStop(
          1,
          "rgba(255, 0, 170, 0)"
        );

        ctx.fillStyle =
          orb;

        ctx.beginPath();

        ctx.arc(
          cx,
          cy,
          orbRadius * 1.5,
          0,
          Math.PI * 2
        );

        ctx.fill();

        requestAnimationFrame(
          drawFrame
        );

      }

      requestAnimationFrame(
        drawFrame
      );

      const kick =
        document.getElementById(
          "kickPlay"
        );

      if (kick && audio) {

        kick.addEventListener(
          "click",
          function () {

            if (audio.paused) {

              setStatus(
                "connecting..."
              );

              kick.textContent =
                "Connecting...";

              if (!audioCtx)
                initAudioAnalyser();
              else if (
                audioCtx.state ===
                "suspended"
              )
                audioCtx.resume();

              audio.play()
                .then(function () {

                  setStatus(
                    "playing"
                  );

                  kick.textContent =
                    "Pause";

                })
                .catch(function (e) {

                  console.error(
                    "Audio Play Error:",
                    e
                  );

                  setStatus(
                    "error: " +
                    (
                      e &&
                      e.message
                        ? e.message
                        : "could not start stream"
                    )
                  );

                  kick.textContent =
                    "Play";

                });

            } else {

              audio.pause();

              setStatus(
                "paused"
              );

              kick.textContent =
                "Play";

            }

          }
        );

        audio.addEventListener(
          "play",
          function () {

            if (
              audioCtx &&
              audioCtx.state ===
              "suspended"
            ) {
              audioCtx.resume();
            }

            if (!audioCtx) {
              initAudioAnalyser();
            }

            setStatus(
              "playing"
            );

            kick.textContent =
              "Pause";

          }
        );

        audio.addEventListener(
          "pause",
          function () {

            setStatus(
              "paused"
            );

            kick.textContent =
              "Play";

          }
        );

        audio.addEventListener(
          "error",
          function () {

            setStatus(
              "audio error (stream offline or blocked)"
            );

            kick.textContent =
              "Play";

          }
        );

      }

      // Keep the station's native media output explicitly unmuted.
      // This is independent of the Live DJ video, which has its own mute state.
      if (audio) {
        audio.muted = false;
        audio.volume = 1;
      }

    });
