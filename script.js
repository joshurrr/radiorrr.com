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

      const heroProgramTime =
        document.getElementById("heroProgramTime");

      const liveProgramTargetGenres =
        document.getElementById("liveProgramTargetGenres");
      const heroProgramTargetLabel =
        document.querySelector(".hero-program-target-row > strong");
      const heroProgramBpm =
        document.getElementById("heroProgramBpm");
      const heroProgramExplainer = document.getElementById("heroProgramExplainer");

      let featuredHeroOverrideActive = false;
      let featuredHeroGenres = [];
      let featuredHeroBpm = "";

      function applyFeaturedHeroOverride() {
        if (!featuredHeroOverrideActive) return false;

        if (heroProgramTargetLabel) {
          heroProgramTargetLabel.textContent = "LIVE SOUND:";
        }

        if (liveProgramTargetGenres) {
          liveProgramTargetGenres.innerHTML = featuredHeroGenres.length
            ? featuredHeroGenres.map(getGenrePillHtml).join("")
            : "";
        }

        if (heroProgramBpm) {
          heroProgramBpm.textContent = featuredHeroBpm
            ? "BPM " + featuredHeroBpm
            : "";
        }

        if (heroProgramExplainer) {
          heroProgramExplainer.textContent =
            "Featured DJ is live — showing the sound currently being detected.";
        }

        return true;
      }

      window.RadioRRRSetFeaturedHeroMode = function (active, bpm) {
        const nextActive = Boolean(active);
        featuredHeroOverrideActive = nextActive;

        if (nextActive) {
          const bpmNumber = Number(bpm);
          featuredHeroBpm = Number.isFinite(bpmNumber)
            ? String(Math.round(bpmNumber))
            : "";
          applyFeaturedHeroOverride();
        } else {
          featuredHeroBpm = "";
          updateTitles();
          if (document.getElementById("rrrDjDiscovery")) {
            renderDjDiscoveryProgramContext();
          }
        }
      };

      const liveDjsList =
        document.getElementById("liveDjsList");

      const djDiscoveryHost =
        document.getElementById("djDiscoveryHost");

      const liveRefresh =
        document.getElementById("liveRefresh");

      const randomLiveDj =
        document.getElementById("randomLiveDj");

      const randomLiveDjName =
        document.getElementById("randomLiveDjName");
      const randomLiveDjPlatform =
        document.getElementById("randomLiveDjPlatform");
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

      const TAB_VIEW_BY_TARGET = {
        "live-section": "live",
        "stream-section": "audio",
        "events-section": "schedule",
        "tools-section": "tools"
      };

      const TAB_TARGET_BY_VIEW = {
        live: "live-section",
        audio: "stream-section",
        schedule: "events-section",
        tools: "tools-section"
      };

      const TOOL_ROUTE_CONFIG = {
        "live-djs": {
          path: "/tools/live-djs/",
          title: "Live DJs Streaming Now – Find Live DJ Sets | Radio RRR",
          description: "Discover DJs streaming live now across supported platforms. Browse live genres, BPM and audio analysis, then listen directly through Radio RRR."
        },
        "bpm-detector": {
          path: "/tools/bpm-detector/",
          title: "Live Stream BPM Detector – Detect BPM from Online Audio | Radio RRR",
          description: "Detect the BPM of a live online audio or HLS stream. Paste a direct stream URL and analyse a short sample to estimate its tempo."
        },
        "dj-speech-detector": {
          path: "/tools/dj-speech-detector/",
          title: "DJ Speech Detector – Detect Talking in Live DJ Streams | Radio RRR",
          description: "Detect talking in live DJ streams with voice-versus-music analysis that measures repeated microphone speech and music ducking."
        },
        "stuck-frame-effect": {
          path: "/tools/stuck-frame-effect/",
          title: "Stuck Frame Effect – Beat-Synced Video Freeze Tool | Radio RRR",
          description: "Upload a video and automatically create short stuck-frame freezes synchronised to the detected beat."
        },
        "stream-health": {
          path: "/tools/stream-health/",
          title: "HLS Stream Health Test – Check Buffering & Playback | Radio RRR",
          description: "Run a passive 60-second HLS stream health test that measures buffering, playback stalls, dropped frames, HLS errors and DJ-switch recovery."
        }
      };

      const TOOLS_HUB_PATH = "/tools/";
      const TOOLS_HUB_METADATA = {
        title: "Free DJ & Live Music Tools | Radio RRR",
        description: "Free browser tools for live DJs and online radio: find DJs live now, detect BPM, detect DJ speech, test HLS stream health and create beat-synced video effects.",
        canonical: "https://radiorrr.com/tools/"
      };

      const DEFAULT_PAGE_METADATA = {
        title: "RadioRRR | Online Radio & Live DJ Discovery",
        description: "RadioRRR is an online radio station and live DJ discovery platform. Listen to RadioRRR and discover DJs broadcasting live across social platforms.",
        canonical: "https://radiorrr.com/"
      };

      function normalisePathname(pathname) {
        const value = String(pathname || "/").replace(/\/{2,}/g, "/");
        if (value === "/") return "/";
        return value.endsWith("/") ? value : value + "/";
      }

      function getToolRouteFromLocation() {
        const pathname = normalisePathname(window.location.pathname);
        return Object.keys(TOOL_ROUTE_CONFIG).find(function (route) {
          return TOOL_ROUTE_CONFIG[route].path === pathname;
        }) || "";
      }

      function getTabTargetFromLocation() {
        const pathname = normalisePathname(window.location.pathname);
        if (getToolRouteFromLocation() || pathname === TOOLS_HUB_PATH) return "tools-section";

        const params = new URLSearchParams(window.location.search);
        const view = String(params.get("view") || "live").trim().toLowerCase();
        return TAB_TARGET_BY_VIEW[view] || "live-section";
      }

      function copyUnrelatedQueryParams(fromUrl, toUrl) {
        fromUrl.searchParams.forEach(function (value, key) {
          if (key !== "view") toUrl.searchParams.append(key, value);
        });
      }

      function updateTabUrl(targetId) {
        const view = TAB_VIEW_BY_TARGET[targetId] || "live";
        const currentUrl = new URL(window.location.href);
        const url = new URL("/", window.location.origin);

        // Keep unrelated query parameters such as a manually selected ?dj=.
        copyUnrelatedQueryParams(currentUrl, url);

        if (view === "tools") {
          url.pathname = TOOLS_HUB_PATH;
        } else if (view !== "live") {
          url.searchParams.set("view", view);
        }

        window.history.pushState({ rrrView: view }, "", url);
      }

      function updateToolUrl(route) {
        const config = TOOL_ROUTE_CONFIG[route];
        if (!config) return;

        const currentUrl = new URL(window.location.href);
        const url = new URL(config.path, window.location.origin);
        copyUnrelatedQueryParams(currentUrl, url);
        window.history.pushState({ rrrTool: route }, "", url);
      }

      function updateMetaContent(selector, value) {
        const el = document.querySelector(selector);
        if (el) el.setAttribute("content", value);
      }

      function applyRouteMetadata(toolRoute) {
        const config = TOOL_ROUTE_CONFIG[toolRoute];
        const onToolsHub = normalisePathname(window.location.pathname) === TOOLS_HUB_PATH;
        const title = config ? config.title : (onToolsHub ? TOOLS_HUB_METADATA.title : DEFAULT_PAGE_METADATA.title);
        const description = config ? config.description : (onToolsHub ? TOOLS_HUB_METADATA.description : DEFAULT_PAGE_METADATA.description);
        const canonical = config
          ? new URL(config.path, window.location.origin).href
          : (onToolsHub ? TOOLS_HUB_METADATA.canonical : DEFAULT_PAGE_METADATA.canonical);

        document.title = title;
        updateMetaContent('meta[name="description"]', description);
        updateMetaContent('meta[property="og:title"]', title);
        updateMetaContent('meta[property="og:description"]', description);
        updateMetaContent('meta[property="og:url"]', canonical);
        updateMetaContent('meta[name="twitter:title"]', title);
        updateMetaContent('meta[name="twitter:description"]', description);

        const canonicalEl = document.querySelector('link[rel="canonical"]');
        if (canonicalEl) canonicalEl.setAttribute("href", canonical);
      }

      function applyToolRoute(toolRoute) {
        const toolsSection = document.getElementById("tools-section");
        if (!toolsSection) return;

        const detailMode = Boolean(toolRoute && TOOL_ROUTE_CONFIG[toolRoute]);
        const healthDashboard = toolsSection.querySelector(".system-health-dashboard");
        const toolsHeading = toolsSection.querySelector(".tools-section-heading");
        const toolsHubGrid = toolsSection.querySelector("#toolsHubGrid");
        const detailList = toolsSection.querySelector(".tool-detail-list");
        const streamHealthPanel = toolsSection.querySelector("#toolStreamHealthPanel");
        const liveDjsPanel = toolsSection.querySelector("#liveDjsToolPanel");
        const isLiveDjsRoute = toolRoute === "live-djs";
        const isStreamHealthRoute = toolRoute === "stream-health";

        if (healthDashboard) healthDashboard.hidden = detailMode;
        if (toolsHeading) toolsHeading.hidden = detailMode;
        if (toolsHubGrid) toolsHubGrid.hidden = detailMode;
        if (detailList) detailList.hidden = !detailMode || isLiveDjsRoute || isStreamHealthRoute;
        if (streamHealthPanel) streamHealthPanel.hidden = !isStreamHealthRoute;
        if (liveDjsPanel) liveDjsPanel.hidden = !isLiveDjsRoute;
        if (isLiveDjsRoute) window.setTimeout(loadLiveDjsTool, 0);

        toolsSection.querySelectorAll("[data-tool-intro]").forEach(function (intro) {
          intro.hidden = !detailMode || intro.getAttribute("data-tool-intro") !== toolRoute;
        });

        toolsSection.querySelectorAll(".tools-action-list > .tool-row").forEach(function (row) {
          row.hidden = !detailMode || row.getAttribute("data-tool-route") !== toolRoute;
        });
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

      function applyLocationState() {
        const toolRoute = getToolRouteFromLocation();
        switchTab(getTabTargetFromLocation());
        applyToolRoute(toolRoute);
        applyRouteMetadata(toolRoute);
      }

      navLinks.forEach(link => {

        link.addEventListener(
          "click",
          function (e) {

            const targetId =
              e.currentTarget.getAttribute(
                "data-target"
              );

            if (targetId === "tools-section") {
              return;
            }

            // Keep the media elements mounted for the existing in-page radio views.
            e.preventDefault();
            if (targetId) {
              updateTabUrl(targetId);
              applyLocationState();
            }

          }
        );

      });

      document.querySelectorAll("[data-tool-route-link]").forEach(function (link) {
        link.addEventListener("click", function (e) {
          const route = e.currentTarget.getAttribute("data-tool-route-link");
          if (!TOOL_ROUTE_CONFIG[route]) return;
          // These are real static pages so crawlers and refreshes receive HTTP 200.
          // Allow the browser to follow the href normally.
        });
      });

      document.querySelectorAll("[data-tool-back]").forEach(function (link) {
        link.addEventListener("click", function () {
          // Real /tools/ navigation is intentional for crawlability and clean page metadata.
        });
      });

      window.addEventListener("popstate", applyLocationState);

      // Direct visits and browser refreshes open the requested view without
      // reconstructing the page or touching either media element.
      applyLocationState();

      /* RADIO ROUTER LIVE DJ PLAYER */

      const RADIO_ROUTER_STREAM_URL = "https://stream.radiorrr.com/api/live-stream";

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

      // Hard browser-player recovery. The normal HLS handlers below get the
      // first chance to recover a short stall. If playback time still stops
      // advancing for 12 seconds, rebuild the existing HLS player and rejoin
      // the live edge without changing DJ or creating a second media element.
      const LIVE_PLAYBACK_HARD_STALL_MS = 12 * 1000;
      const LIVE_PLAYBACK_WATCHDOG_MS = 2 * 1000;
      let livePlaybackLastTime = 0;
      let livePlaybackLastProgressAt = Date.now();
      let livePlaybackHasProgressed = false;
      let livePlaybackHardRecoveryInProgress = false;

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

      // Paint the existing live video into the blurred background canvas.
      // This restores the portrait-video side fill without opening a second
      // HLS connection or creating a second browser video decoder.
      let liveDjBackgroundFrame = 0;
      let liveDjBackgroundLastPaint = 0;
      const LIVE_DJ_BACKGROUND_FPS = 12;

      function stopLiveDjBackgroundFill() {
        if (liveDjBackgroundFrame) {
          window.cancelAnimationFrame(liveDjBackgroundFrame);
          liveDjBackgroundFrame = 0;
        }

        liveDjBackgroundLastPaint = 0;

        if (liveDjBackground && liveDjBackground.getContext) {
          const ctx = liveDjBackground.getContext("2d");
          if (ctx) {
            ctx.clearRect(
              0,
              0,
              liveDjBackground.width || 1,
              liveDjBackground.height || 1
            );
          }
        }
      }

      function paintLiveDjBackground(now) {
        if (!liveDjBackground || !liveDjVideo || document.hidden) {
          liveDjBackgroundFrame = window.requestAnimationFrame(paintLiveDjBackground);
          return;
        }

        const frameInterval = 1000 / LIVE_DJ_BACKGROUND_FPS;
        if (now - liveDjBackgroundLastPaint < frameInterval) {
          liveDjBackgroundFrame = window.requestAnimationFrame(paintLiveDjBackground);
          return;
        }

        liveDjBackgroundLastPaint = now;

        if (
          liveDjVideo.readyState >= 2 &&
          liveDjVideo.videoWidth > 0 &&
          liveDjVideo.videoHeight > 0
        ) {
          const rect = liveDjBackground.getBoundingClientRect();

          // The background is deliberately low resolution. CSS supplies the
          // blur, so full player resolution would only waste CPU/GPU time.
          const targetWidth = Math.max(240, Math.round(rect.width * 0.55));
          const targetHeight = Math.max(180, Math.round(rect.height * 0.55));

          if (
            liveDjBackground.width !== targetWidth ||
            liveDjBackground.height !== targetHeight
          ) {
            liveDjBackground.width = targetWidth;
            liveDjBackground.height = targetHeight;
          }

          const sourceWidth = liveDjVideo.videoWidth;
          const sourceHeight = liveDjVideo.videoHeight;
          const sourceRatio = sourceWidth / sourceHeight;
          const targetRatio = targetWidth / targetHeight;

          let sx = 0;
          let sy = 0;
          let sw = sourceWidth;
          let sh = sourceHeight;

          // Crop like object-fit: cover so the blurred layer fills the frame.
          if (sourceRatio > targetRatio) {
            sw = sourceHeight * targetRatio;
            sx = (sourceWidth - sw) / 2;
          } else {
            sh = sourceWidth / targetRatio;
            sy = (sourceHeight - sh) / 2;
          }

          const ctx = liveDjBackground.getContext("2d", { alpha: false });
          if (ctx) {
            ctx.drawImage(
              liveDjVideo,
              sx, sy, sw, sh,
              0, 0, targetWidth, targetHeight
            );
          }
        }

        liveDjBackgroundFrame = window.requestAnimationFrame(paintLiveDjBackground);
      }

      function startLiveDjBackgroundFill() {
        if (!liveDjBackground || !liveDjVideo) return;

        // Preserve the mobile stability rule: mobile keeps exactly one live
        // video presentation path and uses the existing ambient CSS fallback.
        if (isMobileLivePlayer) {
          stopLiveDjBackgroundFill();
          return;
        }

        if (!liveDjBackgroundFrame) {
          liveDjBackgroundFrame = window.requestAnimationFrame(paintLiveDjBackground);
        }
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
        liveDjUnmute.textContent = isMuted ? "🔊 UNMUTE" : "🔇 MUTE";
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

      async function updateLiveGenresDetected(aiGenre, featuredIdentity, relayIdentity, featuredPlatform) {
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

        // Keep the previous genre rows visible while the fresh detector result
        // is being fetched. Clearing them here caused the Current DJ card to
        // collapse for the duration of the request, producing a visible page
        // jump for listeners who were scrolled further down.
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
            const identityUsernameMatch = selectedIdentity.match(/(?:^|:)username:([^:]+)$/i);
            const username = identityUsernameMatch
              ? identityUsernameMatch[1]
              : selectedIdentity.replace(/^username:/i, "").replace(/^@/, "");
            const platform = String(featuredPlatform || "TikTok").trim() || "TikTok";
            const response = await fetch(
              getFreshUrl(
                "https://api.radiorrr.com/api/ai-genre?username=" +
                encodeURIComponent(username) +
                "&platform=" + encodeURIComponent(platform)
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

        featuredHeroGenres = genres.map(item =>
          item.genre.replace(/^.*---/, "")
        );
        applyFeaturedHeroOverride();

        // Keep the CURRENT PROGRAM card in sync with a live Featured DJ.
        // This only changes what the card displays; the listener's tuning
        // preset and the scheduled program data remain untouched.
        if (featuredHeroOverrideActive && document.getElementById("rrrDjDiscovery")) {
          renderDjDiscoveryProgramContext();
        }

        // New detector data is ready. Replace the old rows now, in the same
        // rendering turn, instead of leaving the panel empty during the fetch.
        liveGenresDetectedList.innerHTML = "";

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

        const platform = getDJPlatform(dj);
        const cacheKey = platform.toLowerCase() + ":" + username.toLowerCase();
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
                encodeURIComponent(username) +
                "&platform=" + encodeURIComponent(platform)
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

      function attachLiveHls(streamUrl) {
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
          livePlaybackLastTime = Number(liveDjVideo.currentTime) || 0;
          livePlaybackLastProgressAt = Date.now();
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

        return hls;
      }

      function hardRecoverLivePlayback() {
        if (
          livePlaybackHardRecoveryInProgress ||
          !liveDjVideo ||
          !activeLiveStreamUrl ||
          document.hidden
        ) {
          return;
        }

        livePlaybackHardRecoveryInProgress = true;

        const streamUrl = activeLiveStreamUrl;
        const streamKey = activeLiveStreamKey;
        const preserveAudio = userRequestedAudio;

        console.warn(
          "Radio RRR: live player stopped advancing; rebuilding HLS at the live edge"
        );

        try {
          if (window.radioRrrHls) {
            window.radioRrrHls.destroy();
            window.radioRrrHls = null;
          }

          // Clear the wedged MediaSource/decoder state, but keep the same video
          // element and the same selected/default DJ.
          liveDjVideo.pause();
          liveDjVideo.removeAttribute("src");
          liveDjVideo.load();

          liveDjVideo.autoplay = true;
          liveDjVideo.playsInline = true;
          liveDjVideo.muted = !preserveAudio;
          liveDjVideo.defaultMuted = !preserveAudio;
          updateLiveDjMuteButton();

          if (window.Hls && Hls.isSupported()) {
            attachLiveHls(streamUrl);
          } else if (
            liveDjVideo.canPlayType("application/vnd.apple.mpegurl")
          ) {
            liveDjVideo.src = getFreshUrl(streamUrl);
            liveDjVideo.addEventListener(
              "loadedmetadata",
              resumeLivePlayback,
              { once: true }
            );
            liveDjVideo.addEventListener(
              "canplay",
              resumeLivePlayback,
              { once: true }
            );
            resumeLivePlayback();
          }
        } catch (error) {
          console.warn("Radio RRR hard playback recovery failed:", error);
        }

        window.setTimeout(function () {
          // If Stage 3 changed the relay while recovery was underway, normal
          // relay-selection code owns the newly attached stream.
          if (activeLiveStreamKey === streamKey) {
            userRequestedAudio = preserveAudio;
            liveDjVideo.muted = !preserveAudio;
            liveDjVideo.defaultMuted = !preserveAudio;
            updateLiveDjMuteButton();
            liveDjVideo.play().catch(() => {});
          }

          livePlaybackLastTime = Number(liveDjVideo.currentTime) || 0;
          livePlaybackLastProgressAt = Date.now();
          livePlaybackHardRecoveryInProgress = false;
        }, 1500);
      }

      function showLiveDjPlayer(dj) {
        if (!liveDjVideo || !randomLiveDj) return;

        // Preserve the listener's explicit audio choice across automatic DJ
        // handoffs. HLS/browser recovery can temporarily flip video.muted, so
        // prefer the persisted user choice instead of treating that transient
        // muted state as a new request from the listener.
        if (activeLiveStreamKey && !livePlaybackHardRecoveryInProgress) {
          const savedAudioChoice = liveDjVideo.dataset.rrrUserAudio;
          if (savedAudioChoice === "1") {
            userRequestedAudio = true;
          } else if (savedAudioChoice === "0") {
            userRequestedAudio = false;
          } else {
            userRequestedAudio = !liveDjVideo.muted;
          }
          liveDjVideo.dataset.rrrUserAudio = userRequestedAudio ? "1" : "0";
          liveDjVideo.defaultMuted = !userRequestedAudio;
        }

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
        const platform = getDJPlatform(dj);
        const streamUrl = isManualSelection && username
          ? RADIO_ROUTER_STREAM_URL + "?dj=" + encodeURIComponent(username) +
            "&platform=" + encodeURIComponent(platform)
          : RADIO_ROUTER_STREAM_URL;

        activeLiveUsername = username;
        activeLiveStreamKey = (isManualSelection ? "manual:" : "default:") +
          (getDJIdentity(dj) || String(name));
        if (randomLiveDj) {
          randomLiveDj.dataset.username = username;
          randomLiveDj.dataset.platform = platform;
        }
        activeLiveStreamUrl = streamUrl;
        livePlaybackLastTime = Number(liveDjVideo.currentTime) || 0;
        livePlaybackLastProgressAt = Date.now();
        livePlaybackHasProgressed = false;
        liveDjVideo.autoplay = true;
        liveDjVideo.playsInline = true;
        liveDjVideo.muted = !userRequestedAudio;
        liveDjVideo.defaultMuted = !userRequestedAudio;
        liveDjVideo.dataset.rrrUserAudio = userRequestedAudio ? "1" : "0";
        updateLiveDjMuteButton();
        startLiveDjBackgroundFill();
        randomLiveDjName.textContent = "🎧 " + String(name);
        updateCurrentDJPlatform(dj);
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

          attachLiveHls(streamUrl);

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

        stopLiveDjBackgroundFill();

        const liveCandidatesAvailable = currentLiveDJs.length > 0;

        if (randomLiveDj) {
          // /api/live can briefly return live DJs before the backend relay has
          // finished resolving. Keep the main station panel visible in that
          // state instead of making the whole Current DJ section disappear.
          randomLiveDj.style.display = liveCandidatesAvailable ? "block" : "none";
        }

        if (liveCandidatesAvailable) {
          if (randomLiveDjName) {
            randomLiveDjName.textContent = "🎧 Waiting for station relay…";
            updateCurrentDJPlatform(null);
          }

          if (liveDjPlaceholder) {
            const placeholderText = liveDjPlaceholder.querySelector("span");
            if (placeholderText) {
              placeholderText.textContent = "Station relay is connecting…";
            }
          }

          const player = liveDjVideo && liveDjVideo.closest(".radio-router-player");
          if (player) player.classList.remove("live-active");
        }

        updateCurrentDJPlatform(null);
        updateMainDJMatchScore(null);
        updateLiveDjProfile(null);
      }

      if (liveDjUnmute && liveDjVideo) {
        liveDjUnmute.addEventListener("click", function () {
          // Record the state the user is explicitly requesting.
          const willUnmute = liveDjVideo.muted;
          userRequestedAudio = willUnmute;
          liveDjVideo.dataset.rrrUserAudio = willUnmute ? "1" : "0";
          liveDjVideo.muted = !willUnmute;
          liveDjVideo.defaultMuted = !willUnmute;
          updateLiveDjMuteButton();
          liveDjVideo.play().catch(() => {});
        });

        liveDjVideo.addEventListener("volumechange", updateLiveDjMuteButton);
        updateLiveDjMuteButton();
      }

      if (liveDjVideo) {
        liveDjVideo.addEventListener("timeupdate", function () {
          const currentTime = Number(liveDjVideo.currentTime) || 0;

          if (Math.abs(currentTime - livePlaybackLastTime) >= 0.05) {
            livePlaybackLastTime = currentTime;
            livePlaybackLastProgressAt = Date.now();
            livePlaybackHasProgressed = true;
          }
        });

        liveDjVideo.addEventListener("playing", function () {
          livePlaybackLastTime =
            Number(liveDjVideo.currentTime) || livePlaybackLastTime;
          livePlaybackLastProgressAt = Date.now();
          livePlaybackHasProgressed = true;
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

        window.setInterval(function () {
          if (
            document.hidden ||
            !activeLiveStreamUrl ||
            livePlaybackHardRecoveryInProgress ||
            !livePlaybackHasProgressed
          ) {
            return;
          }

          // Respect an intentional pause when the browser still has playable
          // media. A wedged player commonly reports paused with low readyState,
          // so that state remains eligible for recovery.
          if (liveDjVideo.paused && liveDjVideo.readyState >= 3) {
            return;
          }

          if (
            Date.now() - livePlaybackLastProgressAt >=
            LIVE_PLAYBACK_HARD_STALL_MS
          ) {
            hardRecoverLivePlayback();
          }
        }, LIVE_PLAYBACK_WATCHDOG_MS);

        document.addEventListener("visibilitychange", function () {
          if (!document.hidden) {
            // Time spent in a background tab is not a playback stall.
            livePlaybackLastTime = Number(liveDjVideo.currentTime) || 0;
            livePlaybackLastProgressAt = Date.now();
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

      function getDJPlatform(dj) {
        return String(
          dj && dj.platform || "TikTok"
        ).trim() || "TikTok";
      }

      function getDJPlatformBadgeHtml(dj, extraClass) {
        const platform = getDJPlatform(dj);
        const normalized = platform.toLowerCase();
        const iconUrl = normalized === "tiktok"
          ? "https://cdn.simpleicons.org/tiktok/FFFFFF"
          : normalized === "youtube"
            ? "https://cdn.simpleicons.org/youtube/FF0000"
            : "";

        if (normalized !== "twitch" && !iconUrl) return "";

        const className = "dj-platform-badge" +
          (extraClass ? " " + extraClass : "") +
          " platform-" + normalized;

        const badgeContent = normalized === "twitch"
          ? '<span class="dj-platform-wordmark" aria-hidden="true">twitch</span>'
          : '<img src="' + escapeAttr(iconUrl) + '" alt="" aria-hidden="true">';

        return '<span class="' + escapeAttr(className) + '" title="' +
          escapeAttr(platform) + '" aria-label="' + escapeAttr(platform) + '">' +
          badgeContent +
        '</span>';
      }

      function updateCurrentDJPlatform(dj) {
        if (!randomLiveDjPlatform) return;

        const badge = dj ? getDJPlatformBadgeHtml(dj, "current-dj-platform-badge") : "";
        randomLiveDjPlatform.innerHTML = badge;
        randomLiveDjPlatform.hidden = !badge;
      }

      function getDJIdentity(dj) {
        const platform = getDJPlatform(dj).toLowerCase();
        const username = getDJUsername(dj).toLowerCase();

        if (username) return "platform:" + platform + ":username:" + username;

        const name = String(
          dj && (dj.name || dj.display_name || "")
        ).trim().toLowerCase().replace(/\s+/g, " ");

        return name ? "platform:" + platform + ":name:" + name : "";
      }

      function getDJMatchKey(dj) {
        const username = getDJUsername(dj).toLowerCase();
        if (!username) return "";
        return getDJPlatform(dj).toLowerCase() + ":" + username;
      }

      function updateMainDJMatchScore(dj) {
        if (!randomLiveDjMatch) return;

        // Do not replace randomLiveDjMatch.innerHTML here. index.html owns the
        // dedicated #randomLiveDjBpm field, so rebuilding this container was
        // deleting the BPM element every time the current DJ refreshed.
        let valueEl = randomLiveDjMatch.querySelector(".main-dj-match-value");
        if (!valueEl) {
          const left = document.createElement("span");
          left.className = "rrr-program-match-left";
          left.innerHTML =
            '<span class="main-dj-match-label">🎯 CURRENT PROGRAM MATCH</span> ' +
            '<span class="main-dj-match-value">—</span>';
          randomLiveDjMatch.prepend(left);
          valueEl = left.querySelector(".main-dj-match-value");
        }

        const labelEl = randomLiveDjMatch.querySelector(".main-dj-match-label");
        const customSoundActive =
          djDiscoveryCustomised && djDiscoverySelectedGenres.length > 0;

        let score;
        if (customSoundActive) {
          const liveGenres = getLiveAIGenres(dj || {});
          score = liveGenres.length
            ? getLiveSoundMatchScore(dj || {}, djDiscoverySelectedGenres) * 100
            : NaN;
          if (labelEl) labelEl.textContent = "🎯 YOUR SOUND MATCH";
          randomLiveDjMatch.title = "Match against your Custom Program genres";
        } else {
          const key = getDJMatchKey(dj || {});
          score = Number(key ? currentLiveMatchScores[key] : NaN);
          if (labelEl) labelEl.textContent = "🎯 CURRENT PROGRAM MATCH";
          randomLiveDjMatch.title = "RRR genre compatibility score out of 100";
        }

        if (!Number.isFinite(score)) {
          valueEl.textContent = "—";
          valueEl.className = "main-dj-match-value";
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

        const programMatchValueClass =
          rounded >= 50
            ? "program-match-green"
            : rounded >= 30
              ? "program-match-yellow"
              : rounded >= 11
                ? "program-match-orange"
                : "program-match-red";

        valueEl.textContent = rounded + "%";
        valueEl.className =
          "main-dj-match-value " + programMatchValueClass;
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
        const platform = getDJPlatform(dj).toLowerCase();

        return (
          dj.live_url ||
          dj.url ||
          (username
            ? (platform === "twitch"
              ? "https://www.twitch.tv/" + username
              : "https://www.tiktok.com/@" + username + "/live")
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

      /* YOUR LIVE SOUND
         Radio RRR's current scheduled genres are the starting preset. Visitors
         can remove or add genres locally in this browser, and the LIVE DJ list
         is re-ranked from each DJ's current AI genre detections. */
      let djDiscoveryDJs = [];
      let djDiscoveryGenreCatalogue = [];
      let djDiscoverySelectedGenres = [];
      let djDiscoveryPresetGenres = [];
      let djDiscoveryCustomised = false;
      let djDiscoveryEditorOpen = false;
      let djDiscoveryAddGenresOpen = false;
      let djDiscoveryShowAll = false;
      let djDiscoveryRenderId = 0;
      let djDiscoveryResizeTimer = null;
      let djDiscoveryScheduledProgramTitle = "";
      let djDiscoveryGenreSearch = "";
      let customSoundAutoRetuneTimer = null;
      let customSoundAutoRetuneInFlight = false;

      const DJ_DISCOVERY_SAVED_STATIONS_KEY = "radiorrr.savedStations.v1";
      let djDiscoverySavedStations = loadDjDiscoverySavedStations();

      function loadDjDiscoverySavedStations() {
        try {
          const parsed = JSON.parse(localStorage.getItem(DJ_DISCOVERY_SAVED_STATIONS_KEY) || "[]");
          if (!Array.isArray(parsed)) return [];

          return parsed.map(station => ({
            id: String(station && station.id || ""),
            name: String(station && station.name || "").trim(),
            genres: uniqueDiscoveryGenres(station && station.genres),
            bpmMin: station && station.bpmMin != null ? station.bpmMin : null,
            bpmMax: station && station.bpmMax != null ? station.bpmMax : null,
            avoidGenres: Array.isArray(station && station.avoidGenres) ? station.avoidGenres.slice() : [],
            allowAiDjs: station && station.allowAiDjs !== false,
            createdAt: String(station && station.createdAt || ""),
            updatedAt: String(station && station.updatedAt || "")
          })).filter(station => station.id && station.name && station.genres.length);
        } catch (error) {
          console.warn("Could not load saved Radio RRR stations:", error);
          return [];
        }
      }

      function persistDjDiscoverySavedStations() {
        try {
          localStorage.setItem(
            DJ_DISCOVERY_SAVED_STATIONS_KEY,
            JSON.stringify(djDiscoverySavedStations)
          );
          return true;
        } catch (error) {
          console.warn("Could not save Radio RRR stations:", error);
          return false;
        }
      }

      function makeSavedStationId() {
        return "station_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
      }

      function defaultSavedStationName() {
        const labels = uniqueDiscoveryGenres(djDiscoverySelectedGenres);
        if (!labels.length) return "My Station";
        if (labels.length === 1) return labels[0];
        return labels.slice(0, 2).join(" + ");
      }

      function openDjDiscoveryStationModal() {
        const modal = document.getElementById("djDiscoveryStationModal");
        const input = document.getElementById("djDiscoveryStationName");
        if (!modal || !input || !djDiscoverySelectedGenres.length) return;

        input.value = defaultSavedStationName();
        modal.hidden = false;
        document.body.classList.add("rrr-station-modal-open");
        window.setTimeout(function () {
          input.focus();
          input.select();
        }, 0);
      }

      function closeDjDiscoveryStationModal() {
        const modal = document.getElementById("djDiscoveryStationModal");
        if (modal) modal.hidden = true;
        document.body.classList.remove("rrr-station-modal-open");
      }

      function saveCurrentSoundAsStation(name) {
        const stationName = String(name || "").trim().slice(0, 48);
        const genres = uniqueDiscoveryGenres(djDiscoverySelectedGenres);
        if (!stationName || !genres.length) return false;

        const now = new Date().toISOString();
        const existing = djDiscoverySavedStations.find(station =>
          station.name.toLowerCase() === stationName.toLowerCase()
        );

        if (existing) {
          existing.genres = genres.slice();
          existing.updatedAt = now;
        } else {
          djDiscoverySavedStations.push({
            id: makeSavedStationId(),
            name: stationName,
            genres: genres.slice(),
            bpmMin: null,
            bpmMax: null,
            avoidGenres: [],
            allowAiDjs: true,
            createdAt: now,
            updatedAt: now
          });
        }

        if (!persistDjDiscoverySavedStations()) return false;
        renderDjDiscoverySavedStations();
        return true;
      }

      function renderDjDiscoverySavedStations() {
        const wrap = document.getElementById("djDiscoverySavedStations");
        const list = document.getElementById("djDiscoverySavedStationsList");
        if (!wrap || !list) return;

        if (!djDiscoverySavedStations.length) {
          wrap.hidden = true;
          list.innerHTML = "";
          return;
        }

        wrap.hidden = false;
        list.innerHTML = djDiscoverySavedStations.map(station =>
          '<div class="dj-discovery-station-item">' +
            '<button type="button" class="dj-discovery-station-load" data-load-station="' +
              escapeAttr(station.id) + '" title="Load ' + escapeAttr(station.name) + '">' +
              '<strong>' + escapeHtml(station.name) + '</strong>' +
              '<span>' + escapeHtml(station.genres.join(" · ")) + '</span>' +
            '</button>' +
            '<button type="button" class="dj-discovery-station-delete" data-delete-station="' +
              escapeAttr(station.id) + '" aria-label="Delete ' + escapeAttr(station.name) + '">×</button>' +
          '</div>'
        ).join("");

        list.querySelectorAll("[data-load-station]").forEach(button => {
          button.addEventListener("click", async function () {
            const id = this.getAttribute("data-load-station") || "";
            const station = djDiscoverySavedStations.find(item => item.id === id);
            if (!station || !station.genres.length) return;

            djDiscoverySelectedGenres = uniqueDiscoveryGenres(station.genres);
            djDiscoveryCustomised = true;
            djDiscoveryEditorOpen = false;
            djDiscoveryAddGenresOpen = false;
            djDiscoveryShowAll = false;

            renderDjDiscoveryProgramContext();
            renderDjDiscoverySelectedGenres();
            renderDjDiscoveryChips();
            renderDjDiscoveryResults();
            await applyBestCustomSoundDj();
          });
        });

        list.querySelectorAll("[data-delete-station]").forEach(button => {
          button.addEventListener("click", function () {
            const id = this.getAttribute("data-delete-station") || "";
            const station = djDiscoverySavedStations.find(item => item.id === id);
            if (!station) return;
            if (!window.confirm('Delete saved station "' + station.name + '"?')) return;

            djDiscoverySavedStations = djDiscoverySavedStations.filter(item => item.id !== id);
            persistDjDiscoverySavedStations();
            renderDjDiscoverySavedStations();
          });
        });
      }

      function getDiscoveryGenres(dj) {
        // Detector history supplies the stable list of genre choices shown in
        // the selector. Current live matching below uses current-session scans.
        const learnedGenres = Array.isArray(dj && dj.rrr_learned_genres)
          ? dj.rrr_learned_genres
          : [];

        const rawSources = learnedGenres.length
          ? [learnedGenres]
          : [
              dj && dj.genre,
              dj && dj.genre_keywords,
              dj && dj.genres,
              dj && dj.rrr_genres
            ];

        const raw = [];
        rawSources.forEach(source => {
          if (Array.isArray(source)) {
            raw.push(...source);
          } else if (source && typeof source === "object") {
            if (source.genre) raw.push(source.genre);
            else raw.push(...Object.values(source));
          } else if (source) {
            raw.push(...String(source).split(/[,|]/));
          }
        });

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

      function normaliseDiscoveryGenre(value) {
        const normalized = normalizeGenreForMatch(value);
        if (/^(?:dnb|d and b|drum and bass)$/.test(normalized)) return "drum and bass";
        return normalized;
      }

      function liveGenreMatchesSelection(detectedGenre, selectedGenre) {
        const detected = normaliseDiscoveryGenre(detectedGenre);
        const selected = normaliseDiscoveryGenre(selectedGenre);
        if (!detected || !selected) return false;
        return detected === selected || detected.includes(selected) || selected.includes(detected);
      }

      function getLiveSoundMatchScore(dj, selectedGenres) {
        const selected = Array.isArray(selectedGenres) ? selectedGenres : [];
        if (!selected.length) return 0;

        const raw = Array.isArray(dj && dj.ai_genres) ? dj.ai_genres : [];
        let total = 0;

        selected.forEach(selectedGenre => {
          let best = 0;

          raw.forEach(item => {
            const genre = item && typeof item === "object" ? item.genre : item;
            if (!liveGenreMatchesSelection(genre, selectedGenre)) return;

            let confidence = Number(
              item && typeof item === "object" ? item.confidence : 0
            );
            if (confidence > 1 && confidence <= 100) confidence /= 100;
            if (Number.isFinite(confidence)) {
              best = Math.max(best, Math.max(0, Math.min(1, confidence)));
            }
          });

          total += best;
        });

        // Average across the whole recipe. A DJ matching several selected
        // genres therefore ranks above one that only matches a single genre.
        return total / selected.length;
      }

      function getLiveSoundMatchEvidence(dj, selectedGenres) {
        const selected = Array.isArray(selectedGenres) ? selectedGenres : [];
        const raw = Array.isArray(dj && dj.ai_genres) ? dj.ai_genres : [];

        return selected.map(selectedGenre => {
          let bestConfidence = 0;
          let bestDetectedGenre = "";

          raw.forEach(item => {
            const rawGenre = item && typeof item === "object" ? item.genre : item;
            if (!liveGenreMatchesSelection(rawGenre, selectedGenre)) return;

            let confidence = Number(
              item && typeof item === "object" ? item.confidence : 0
            );
            if (confidence > 1 && confidence <= 100) confidence /= 100;
            if (!Number.isFinite(confidence)) confidence = 0;
            confidence = Math.max(0, Math.min(1, confidence));

            if (confidence >= bestConfidence) {
              bestConfidence = confidence;
              bestDetectedGenre = String(rawGenre || "").split("---").pop().trim();
            }
          });

          return {
            selectedGenre: String(selectedGenre || "").trim(),
            detectedGenre: bestDetectedGenre,
            confidence: bestConfidence,
            matched: Boolean(bestDetectedGenre)
          };
        });
      }

      window.RadioRRRGetCustomSoundMatchState = function (username, platform) {
        if (!djDiscoveryCustomised || !djDiscoverySelectedGenres.length) {
          return { active: false, score: null };
        }

        const wantedUsername = String(username || "").replace(/^@/, "").trim().toLowerCase();
        const wantedPlatform = String(platform || "").trim().toLowerCase();
        const dj = djDiscoveryDJs.find(item => {
          const itemUsername = getDJUsername(item).replace(/^@/, "").trim().toLowerCase();
          const itemPlatform = getDJPlatform(item).trim().toLowerCase();
          return itemUsername === wantedUsername && (!wantedPlatform || itemPlatform === wantedPlatform);
        });

        if (!dj || !getLiveAIGenres(dj).length) {
          return { active: true, score: null };
        }

        return {
          active: true,
          score: Math.max(0, Math.min(100, Math.round(
            getLiveSoundMatchScore(dj, djDiscoverySelectedGenres) * 100
          )))
        };
      };

      function uniqueDiscoveryGenres(genres) {
        const out = [];
        const seen = new Set();

        (Array.isArray(genres) ? genres : []).forEach(genre => {
          const value = String(genre || "").trim();
          if (!value || !isMusicGenre(value)) return;
          const key = normaliseDiscoveryGenre(value);
          if (!key || seen.has(key)) return;
          seen.add(key);
          out.push(value);
        });

        return out;
      }

      function sameDiscoveryGenreSet(a, b) {
        const aa = uniqueDiscoveryGenres(a).map(normaliseDiscoveryGenre).sort();
        const bb = uniqueDiscoveryGenres(b).map(normaliseDiscoveryGenre).sort();
        return aa.length === bb.length && aa.every((value, index) => value === bb[index]);
      }

      function setDjDiscoveryPresetGenres(genres) {
        const nextPreset = uniqueDiscoveryGenres(genres);
        const presetChanged = !sameDiscoveryGenreSet(
          nextPreset,
          djDiscoveryPresetGenres
        );

        djDiscoveryPresetGenres = nextPreset;

        if (!djDiscoveryCustomised || !djDiscoverySelectedGenres.length) {
          djDiscoverySelectedGenres = nextPreset.slice();
          djDiscoveryCustomised = false;
        }

        if (presetChanged || document.getElementById("rrrDjDiscovery")) {
          renderDjDiscoveryProgramContext();
          renderDjDiscoverySelectedGenres();
          renderDjDiscoveryChips();
          renderDjDiscoveryResults();
        }
      }

      async function renderDjDiscovery(favourites, liveDJs) {
        if (!djDiscoveryHost) return;

        const renderId = ++djDiscoveryRenderId;
        const favouriteList = Array.isArray(favourites) ? favourites.slice() : [];
        const liveList = Array.isArray(liveDJs) ? liveDJs.slice() : [];
        const favouriteByIdentity = new Map();

        favouriteList.forEach(favourite => {
          const identity = getDJIdentity(favourite);
          if (identity) favouriteByIdentity.set(identity, favourite);
        });

        // Only LIVE DJs participate in results. Favourite/catalogue data is
        // merged in solely for names/photos/profile metadata and selector genres.
        djDiscoveryDJs = liveList.map(live => {
          const favourite = favouriteByIdentity.get(getDJIdentity(live));
          const merged = favourite ? mergeDJRecords(live, favourite) : { ...live };
          merged.live = true;
          return merged;
        });
        djDiscoveryGenreCatalogue = favouriteList;

        if (renderId !== djDiscoveryRenderId) return;

        let section = document.getElementById("rrrDjDiscovery");
        if (!section) {
          section = document.createElement("section");
          section.id = "rrrDjDiscovery";
          section.className = "dj-discovery";
          djDiscoveryHost.appendChild(section);
        }

        section.innerHTML =
          '<div class="dj-discovery-program">' +
            '<div class="dj-discovery-program-copy">' +
              '<div class="dj-discovery-program-kicker">CURRENT PROGRAM</div>' +
              '<div class="dj-discovery-program-title-row">' +
                '<div class="dj-discovery-program-name" id="djDiscoveryProgramName">Radio RRR</div>' +
                '<div class="dj-discovery-program-time" id="djDiscoveryProgramTime"></div>' +
              '</div>' +
              '<div class="dj-discovery-program-note">Scheduled sound for this program</div>' +
              '<div class="dj-discovery-program-genres" id="djDiscoveryProgramGenres"></div>' +
              '<div class="dj-discovery-program-state" id="djDiscoveryProgramState" hidden></div>' +
            '</div>' +
            '<button type="button" class="dj-discovery-edit-button" id="djDiscoveryEditSound">🎚 EDIT / TUNE SOUND</button>' +
          '</div>' +
          '<div class="dj-discovery-editor" id="djDiscoveryEditor" hidden>' +
            '<div class="dj-discovery-editor-head">' +
              '<div>' +
                '<div class="dj-discovery-title" id="djDiscoveryEditorTitle">🎚️ TUNE YOUR SOUND</div>' +
                '<div class="dj-discovery-subtitle" id="djDiscoveryEditorSubtitle">Started from the current program. Add or remove genres to change the live DJ ranking below.</div>' +
              '</div>' +
              '<div class="dj-discovery-editor-actions">' +
                '<button type="button" class="dj-discovery-reset" id="djDiscoveryReset">↺ RESET TO PROGRAM</button>' +
                '<button type="button" class="dj-discovery-save-station" id="djDiscoverySaveStation" hidden>★ SAVE AS STATION</button>' +
                '<button type="button" class="dj-discovery-retune" id="djDiscoveryRetune" hidden>☷ TUNE SOUND</button>' +
                '<button type="button" class="dj-discovery-done" id="djDiscoveryDone">✓ DONE</button>' +
              '</div>' +
            '</div>' +
            '<div class="dj-discovery-selected" id="djDiscoverySelected"></div>' +
          '</div>' +
          '<div class="dj-discovery-saved-stations" id="djDiscoverySavedStations" hidden>' +
            '<div class="dj-discovery-saved-title">MY STATIONS</div>' +
            '<div class="dj-discovery-saved-list" id="djDiscoverySavedStationsList"></div>' +
          '</div>' +
          '<div class="dj-discovery-add-panel" id="djDiscoveryAddPanel" hidden>' +
            '<button type="button" class="dj-discovery-add-toggle" id="djDiscoveryAddToggle" aria-expanded="false">＋ ADD ANOTHER GENRE</button>' +
            '<div class="dj-discovery-add-content" id="djDiscoveryAddContent" hidden>' +
              '<div class="dj-discovery-section-title dj-discovery-add-title">ADD TO YOUR SOUND</div>' +
              '<div class="dj-discovery-genre-search-wrap">' +
                '<span class="dj-discovery-genre-search-icon" aria-hidden="true">⌕</span>' +
                '<input type="search" class="dj-discovery-genre-search" id="djDiscoveryGenreSearch" autocomplete="off" spellcheck="false" placeholder="Search genres — e.g. drum n bass" aria-label="Search available genres">' +
              '</div>' +
              '<div class="dj-discovery-chips" id="djDiscoveryChips"></div>' +
            '</div>' +
          '</div>' +
          '<div class="dj-discovery-section-title" id="djDiscoveryMatchesTitle">BEST LIVE MATCHES FOR THIS PROGRAM</div>' +
          '<div class="dj-discovery-scan-status" id="djDiscoveryScanStatus" role="status" aria-live="polite"></div>' +
          '<div class="dj-discovery-results" id="djDiscoveryResults"></div>' +
          '<div class="dj-discovery-more-wrap" id="djDiscoveryMoreWrap"></div>' +
          '<div class="dj-discovery-station-modal" id="djDiscoveryStationModal" hidden>' +
            '<div class="dj-discovery-station-modal-card" role="dialog" aria-modal="true" aria-labelledby="djDiscoveryStationModalTitle">' +
              '<div class="dj-discovery-station-modal-title" id="djDiscoveryStationModalTitle">SAVE AS STATION</div>' +
              '<div class="dj-discovery-station-modal-copy">Save this sound in this browser so you can retune to it later.</div>' +
              '<form id="djDiscoveryStationForm">' +
                '<label for="djDiscoveryStationName">Station name</label>' +
                '<input type="text" id="djDiscoveryStationName" maxlength="48" autocomplete="off" required>' +
                '<div class="dj-discovery-station-modal-actions">' +
                  '<button type="button" class="dj-discovery-station-cancel" id="djDiscoveryStationCancel">CANCEL</button>' +
                  '<button type="submit" class="dj-discovery-station-confirm">★ SAVE STATION</button>' +
                '</div>' +
              '</form>' +
            '</div>' +
          '</div>';

        const editSound = document.getElementById("djDiscoveryEditSound");
        if (editSound) {
          editSound.addEventListener("click", function () {
            // A fresh edit always starts from the sound currently shown by
            // the Radio RRR program rather than from an empty recipe.
            if (!djDiscoveryCustomised) {
              djDiscoverySelectedGenres = djDiscoveryPresetGenres.slice();
            }
            djDiscoveryGenreSearch = "";
            djDiscoveryEditorOpen = true;
            djDiscoveryAddGenresOpen = true;
            renderDjDiscoveryProgramContext();
            renderDjDiscoverySelectedGenres();
            renderDjDiscoveryChips();
            const genreSearch = document.getElementById("djDiscoveryGenreSearch");
            if (genreSearch) genreSearch.value = "";
          });
        }

        const retune = document.getElementById("djDiscoveryRetune");
        if (retune) {
          retune.addEventListener("click", function () {
            djDiscoveryEditorOpen = true;
            djDiscoveryAddGenresOpen = true;
            renderDjDiscoveryProgramContext();
            renderDjDiscoverySelectedGenres();
            renderDjDiscoveryChips();
          });
        }

        const saveStation = document.getElementById("djDiscoverySaveStation");
        if (saveStation) {
          saveStation.addEventListener("click", openDjDiscoveryStationModal);
        }

        const stationModal = document.getElementById("djDiscoveryStationModal");
        const stationCancel = document.getElementById("djDiscoveryStationCancel");
        const stationForm = document.getElementById("djDiscoveryStationForm");
        if (stationCancel) {
          stationCancel.addEventListener("click", closeDjDiscoveryStationModal);
        }
        if (stationModal) {
          stationModal.addEventListener("click", function (event) {
            if (event.target === stationModal) closeDjDiscoveryStationModal();
          });
        }
        if (stationForm) {
          stationForm.addEventListener("submit", function (event) {
            event.preventDefault();
            const input = document.getElementById("djDiscoveryStationName");
            const name = input ? input.value : "";
            if (!String(name || "").trim()) {
              if (input) input.focus();
              return;
            }
            if (saveCurrentSoundAsStation(name)) {
              closeDjDiscoveryStationModal();
            }
          });
        }

        const done = document.getElementById("djDiscoveryDone");
        if (done) {
          done.addEventListener("click", async function () {
            djDiscoveryEditorOpen = false;
            djDiscoveryAddGenresOpen = false;
            renderDjDiscoveryProgramContext();

            // A custom recipe should immediately affect this listener's live
            // playback, not just re-order the cards below. Pick the strongest
            // currently detected custom-sound match and use the existing
            // browser-local per-DJ relay. This must never change the shared
            // Radio RRR station relay or MP3 stream.
            if (djDiscoveryCustomised && djDiscoverySelectedGenres.length) {
              await applyBestCustomSoundDj();
            }
          });
        }


        const addToggle = document.getElementById("djDiscoveryAddToggle");
        if (addToggle) {
          addToggle.addEventListener("click", function () {
            djDiscoveryAddGenresOpen = !djDiscoveryAddGenresOpen;
            renderDjDiscoveryProgramContext();
            if (djDiscoveryAddGenresOpen) renderDjDiscoveryChips();
          });
        }

        const genreSearch = document.getElementById("djDiscoveryGenreSearch");
        if (genreSearch) {
          genreSearch.value = djDiscoveryGenreSearch;
          genreSearch.addEventListener("input", function () {
            djDiscoveryGenreSearch = this.value || "";
            renderDjDiscoveryChips();
          });
        }

        const reset = document.getElementById("djDiscoveryReset");
        if (reset) {
          reset.addEventListener("click", function () {
            djDiscoverySelectedGenres = djDiscoveryPresetGenres.slice();
            djDiscoveryCustomised = false;
            djDiscoveryAddGenresOpen = false;
            djDiscoveryShowAll = false;
            // While actively tuning, keep the editor in place so the listener
            // can immediately see the restored current-program recipe.
            if (!djDiscoveryEditorOpen) {
              djDiscoveryEditorOpen = false;
            }
            renderDjDiscoveryProgramContext();
            renderDjDiscoverySelectedGenres();
            renderDjDiscoveryChips();
            renderDjDiscoveryResults();
          });
        }

        renderDjDiscoveryProgramContext();
        renderDjDiscoverySelectedGenres();
        renderDjDiscoveryChips();
        renderDjDiscoveryResults();
        renderDjDiscoverySavedStations();
      }

      function renderDjDiscoveryProgramContext() {
        const nameEl = document.getElementById("djDiscoveryProgramName");
        const timeEl = document.getElementById("djDiscoveryProgramTime");
        const genresEl = document.getElementById("djDiscoveryProgramGenres");
        const stateEl = document.getElementById("djDiscoveryProgramState");
        const editEl = document.getElementById("djDiscoveryEditSound");
        const editorEl = document.getElementById("djDiscoveryEditor");
        const programEl = document.querySelector("#rrrDjDiscovery .dj-discovery-program");
        const kickerEl = document.querySelector("#rrrDjDiscovery .dj-discovery-program-kicker");
        const editorTitleEl = document.getElementById("djDiscoveryEditorTitle");
        const editorSubtitleEl = document.getElementById("djDiscoveryEditorSubtitle");
        const retuneEl = document.getElementById("djDiscoveryRetune");
        const saveStationEl = document.getElementById("djDiscoverySaveStation");
        const doneEl = document.getElementById("djDiscoveryDone");
        const resetEl = document.getElementById("djDiscoveryReset");
        const addPanelEl = document.getElementById("djDiscoveryAddPanel");
        const addToggleEl = document.getElementById("djDiscoveryAddToggle");
        const addContentEl = document.getElementById("djDiscoveryAddContent");
        const programNoteEl = document.querySelector("#rrrDjDiscovery .dj-discovery-program-note");

        if (heroNowEl && djDiscoveryScheduledProgramTitle) {
          heroNowEl.textContent = djDiscoveryCustomised
            ? djDiscoveryScheduledProgramTitle.replace(/\s+[–-]\s+.*$/, " – CUSTOM PROGRAM")
            : djDiscoveryScheduledProgramTitle;
        }

        if (nameEl) {
          nameEl.textContent = djDiscoveryCustomised
            ? "CUSTOM PROGRAM"
            : String(heroNowEl && heroNowEl.textContent || "CURRENT RADIO RRR PROGRAM").trim();
        }

        if (timeEl) {
          timeEl.textContent = String(heroProgramTime && heroProgramTime.textContent || "").trim();
        }

        if (genresEl) {
          const displayedProgramGenres =
            featuredHeroOverrideActive && featuredHeroGenres.length
              ? featuredHeroGenres
              : djDiscoveryPresetGenres;

          genresEl.innerHTML = displayedProgramGenres.length
            ? displayedProgramGenres.map(genre =>
                '<span class="dj-discovery-program-chip ' + getGenreNeonClass(genre) + '">' +
                escapeHtml(genre) + '</span>'
              ).join("")
            : '<span class="dj-discovery-selected-empty">Loading scheduled genres…</span>';
        }

        if (programNoteEl) {
          programNoteEl.textContent =
            featuredHeroOverrideActive && featuredHeroGenres.length
              ? "Live sound detected from the Featured DJ"
              : "Scheduled sound for this program";
        }

        if (stateEl) {
          stateEl.hidden = true;
          stateEl.textContent = "";
        }

        // The program card and sound editor intentionally occupy the same UI
        // role: once tuning begins, the program card disappears rather than
        // leaving two competing sound summaries on screen.
        if (programEl) {
          programEl.hidden = djDiscoveryEditorOpen || djDiscoveryCustomised;
          programEl.classList.remove("is-reference");
        }

        if (kickerEl) {
          kickerEl.textContent = "CURRENT PROGRAM";
        }

        if (editEl) {
          editEl.textContent = "🎚 EDIT / TUNE SOUND";
          editEl.hidden = false;
        }

        if (editorEl) {
          editorEl.hidden = !(djDiscoveryEditorOpen || djDiscoveryCustomised);
          editorEl.classList.toggle("is-summary", djDiscoveryCustomised && !djDiscoveryEditorOpen);
          editorEl.classList.toggle("is-editing", djDiscoveryEditorOpen);
        }

        if (editorTitleEl) {
          editorTitleEl.textContent = djDiscoveryCustomised
            ? "🎚️ CUSTOM PROGRAM"
            : (djDiscoveryEditorOpen ? "🎚️ TUNE YOUR SOUND" : "🎚️ YOUR SOUND");
        }

        if (editorSubtitleEl) {
          editorSubtitleEl.textContent = djDiscoveryEditorOpen
            ? "Started from the current program. Add or remove genres to change the live DJ ranking below."
            : "Live DJs are ranked against these selected genres.";
        }

        if (retuneEl) {
          retuneEl.hidden = djDiscoveryEditorOpen || !djDiscoveryCustomised;
        }

        if (saveStationEl) {
          saveStationEl.hidden = djDiscoveryEditorOpen || !djDiscoveryCustomised || !djDiscoverySelectedGenres.length;
        }

        if (doneEl) {
          doneEl.hidden = !djDiscoveryEditorOpen;
        }

        if (resetEl) {
          resetEl.hidden = !djDiscoveryEditorOpen;
        }

        if (addPanelEl) {
          addPanelEl.hidden = !djDiscoveryEditorOpen;
        }

        if (addToggleEl) {
          addToggleEl.setAttribute("aria-expanded", String(djDiscoveryAddGenresOpen));
          addToggleEl.textContent = djDiscoveryAddGenresOpen
            ? "− HIDE GENRES"
            : "＋ ADD ANOTHER GENRE";
        }

        if (addContentEl) {
          addContentEl.hidden = !djDiscoveryAddGenresOpen;
        }
      }

      function renderDjDiscoverySelectedGenres() {
        const selectedEl = document.getElementById("djDiscoverySelected");
        const resetEl = document.getElementById("djDiscoveryReset");
        if (!selectedEl) return;

        if (resetEl) {
          resetEl.hidden = !djDiscoveryEditorOpen;
        }

        if (!djDiscoverySelectedGenres.length) {
          selectedEl.innerHTML =
            '<span class="dj-discovery-selected-empty">Add at least one genre below to rank the live DJs.</span>';
          return;
        }

        if (!djDiscoveryEditorOpen) {
          selectedEl.innerHTML = djDiscoverySelectedGenres.map(genre =>
            '<span class="dj-discovery-selected-chip is-summary-chip ' +
            getGenreNeonClass(genre) + '">' + escapeHtml(genre) + '</span>'
          ).join("");
          return;
        }

        selectedEl.innerHTML = djDiscoverySelectedGenres.map(genre =>
          '<button type="button" class="dj-discovery-selected-chip ' +
          getGenreNeonClass(genre) + '" data-remove-genre="' +
          escapeAttr(genre) + '" aria-label="Remove ' + escapeAttr(genre) +
          ' from your sound">' + escapeHtml(genre) + '<span aria-hidden="true">×</span></button>'
        ).join("");

        selectedEl.querySelectorAll("[data-remove-genre]").forEach(button => {
          button.addEventListener("click", function () {
            const genre = this.getAttribute("data-remove-genre") || "";
            const normalized = normaliseDiscoveryGenre(genre);

            djDiscoverySelectedGenres = djDiscoverySelectedGenres.filter(
              item => normaliseDiscoveryGenre(item) !== normalized
            );
            djDiscoveryCustomised = !sameDiscoveryGenreSet(
              djDiscoverySelectedGenres,
              djDiscoveryPresetGenres
            );

            djDiscoveryShowAll = false;
            // Removing a selected genre must not collapse the available-genre
            // picker. Keep the tuning palette open so the listener can remove
            // several genres or immediately choose replacements.
            renderDjDiscoveryProgramContext();
            renderDjDiscoverySelectedGenres();
            renderDjDiscoveryChips();
            renderDjDiscoveryResults();
          });
        });
      }

      function getDjDiscoveryCatalogueGenres() {
        const counts = new Map();

        djDiscoveryGenreCatalogue.forEach(dj => {
          getDiscoveryGenres(dj).forEach(genre => {
            const key = genre.toLowerCase();
            if (!counts.has(key)) counts.set(key, { label: genre, count: 0 });
            counts.get(key).count++;
          });
        });

        const excludedDiscoveryGenre = /\b(?:gospel|religious|religion|christian|prayer|sermon|spoken[\s-]*(?:word|voice)|speech|talk|talking|dialogue|comedy|audiobook|audio[\s-]+book|radio[\s-]*play|education|educational|poetry|field[\s-]*recording|parody|non[\s-]*music)\b/i;

        return Array.from(counts.values())
          .filter(item => item.count > 3 && !excludedDiscoveryGenre.test(item.label))
          .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
      }

      function renderDjDiscoveryChips() {
        const chips = document.getElementById("djDiscoveryChips");
        if (!chips) return;

        const selectedKeys = new Set(
          djDiscoverySelectedGenres.map(normaliseDiscoveryGenre)
        );

        const searchKey = normaliseDiscoveryGenre(djDiscoveryGenreSearch);
        const genres = getDjDiscoveryCatalogueGenres().filter(item => {
          const genreKey = normaliseDiscoveryGenre(item.label);
          return !selectedKeys.has(genreKey) &&
            (!searchKey || genreKey.includes(searchKey));
        });

        if (!genres.length) {
          chips.innerHTML = searchKey
            ? '<span class="dj-discovery-selected-empty">No genres match “' +
              escapeHtml(djDiscoveryGenreSearch.trim()) + '”.</span>'
            : '<span class="dj-discovery-selected-empty">No additional detected genres are available right now.</span>';
          return;
        }

        chips.innerHTML = genres.map(item =>
          '<button type="button" class="dj-discovery-chip ' +
          getGenreNeonClass(item.label) + '" data-add-genre="' +
          escapeAttr(item.label) + '" aria-label="Add ' + escapeAttr(item.label) +
          ' to your sound">+ ' + escapeHtml(item.label) + '</button>'
        ).join("");

        chips.querySelectorAll("[data-add-genre]").forEach(button => {
          button.addEventListener("click", function () {
            const selectedGenre = this.getAttribute("data-add-genre") || "";
            if (!selectedGenre) return;

            djDiscoverySelectedGenres = uniqueDiscoveryGenres(
              djDiscoverySelectedGenres.concat([selectedGenre])
            );
            djDiscoveryCustomised = !sameDiscoveryGenreSet(
              djDiscoverySelectedGenres,
              djDiscoveryPresetGenres
            );

            djDiscoveryShowAll = false;
            renderDjDiscoveryProgramContext();
            renderDjDiscoverySelectedGenres();
            renderDjDiscoveryChips();
            renderDjDiscoveryResults();
          });
        });
      }

      async function applyBestCustomSoundDj() {
        const selectedGenres = djDiscoverySelectedGenres.slice();
        if (!selectedGenres.length || !djDiscoveryDJs.length) return false;

        const ranked = djDiscoveryDJs.map(dj => ({
          dj,
          score: getLiveSoundMatchScore(dj, selectedGenres)
        })).sort((a, b) => b.score - a.score ||
          String(a.dj.name || a.dj.username || "").localeCompare(
            String(b.dj.name || b.dj.username || "")
          )
        );

        const best = ranked[0];
        if (!best || !(best.score > 0)) {
          renderDjDiscoveryResults();
          return false;
        }

        const bestIdentity = getDJIdentity(best.dj);
        if (!bestIdentity) return false;

        if (currentFeaturedDJ && getDJIdentity(currentFeaturedDJ) === bestIdentity) {
          renderDjDiscoveryResults();
          return true;
        }

        if (liveDjVideo) {
          userRequestedAudio = !liveDjVideo.muted;
          liveDjVideo.dataset.rrrUserAudio = userRequestedAudio ? "1" : "0";
          liveDjVideo.defaultMuted = !userRequestedAudio;
        }

        try {
          manualFeaturedDJIdentity = bestIdentity;
          manualFeaturedDJMissingCount = 0;
          manualDJStreamFailureHandled = false;
          currentFeaturedDJ = best.dj;
          showLiveDjPlayer(best.dj);
          await updateLiveGenresDetected(
            null,
            manualFeaturedDJIdentity,
            "",
            best.dj && best.dj.platform
          );
          await loadLiveDJs();
          return true;
        } catch (error) {
          console.error("Radio RRR custom program auto-selection failed:", error);
          manualFeaturedDJIdentity = "";
          manualFeaturedDJMissingCount = 0;
          manualDJStreamFailureHandled = false;
          await loadLiveDJs();
          return false;
        }
      }

      function scheduleCustomSoundAutoRetune() {
        if (customSoundAutoRetuneTimer) {
          clearTimeout(customSoundAutoRetuneTimer);
          customSoundAutoRetuneTimer = null;
        }

        if (
          !djDiscoveryCustomised ||
          djDiscoveryEditorOpen ||
          !djDiscoverySelectedGenres.length ||
          customSoundAutoRetuneInFlight
        ) {
          return;
        }

        customSoundAutoRetuneTimer = window.setTimeout(async function () {
          customSoundAutoRetuneTimer = null;
          if (customSoundAutoRetuneInFlight) return;
          customSoundAutoRetuneInFlight = true;
          try {
            await applyBestCustomSoundDj();
          } finally {
            customSoundAutoRetuneInFlight = false;
          }
        }, 350);
      }

      function renderDjDiscoveryResults() {
        const resultsEl = document.getElementById("djDiscoveryResults");
        const statusEl = document.getElementById("djDiscoveryScanStatus");
        const matchesTitleEl = document.getElementById("djDiscoveryMatchesTitle");
        if (!resultsEl || !statusEl) return;

        const selectedGenres = djDiscoverySelectedGenres.slice();
        const usingRadioRrrPreset =
          !djDiscoveryCustomised &&
          sameDiscoveryGenreSet(selectedGenres, djDiscoveryPresetGenres);

        if (matchesTitleEl) {
          matchesTitleEl.textContent = usingRadioRrrPreset
            ? "BEST LIVE MATCHES FOR THIS PROGRAM"
            : "BEST LIVE MATCHES FOR YOUR SOUND";
        }

        if (!selectedGenres.length) {
          statusEl.className = "dj-discovery-scan-status idle";
          statusEl.textContent = djDiscoveryPresetGenres.length
            ? "Add a genre below to start ranking the live DJs."
            : "Loading the current Radio RRR sound…";
          resultsEl.innerHTML = "";
          return;
        }

        const featuredIdentity = currentFeaturedDJ
          ? getDJIdentity(currentFeaturedDJ)
          : "";
        const alternatives = djDiscoveryDJs.filter(
          dj => !featuredIdentity || getDJIdentity(dj) !== featuredIdentity
        );

        let ranked = [];
        let waitingCount = 0;

        if (usingRadioRrrPreset) {
          // The untouched preset must mirror Radio RRR's real backend ordering.
          // #1 is already the main live DJ above, so this grid begins at #2.
          ranked = alternatives.map(dj => {
            const programScore = Number(
              currentLiveMatchScores[getDJMatchKey(dj)]
            );

            return {
              dj,
              score: Number.isFinite(programScore)
                ? Math.max(0, Math.min(100, programScore)) / 100
                : null,
              programScore: Number.isFinite(programScore) ? programScore : -1
            };
          }).sort((a, b) => {
            if (b.programScore !== a.programScore) {
              return b.programScore - a.programScore;
            }

            return String(a.dj.name || a.dj.username || "").localeCompare(
              String(b.dj.name || b.dj.username || "")
            );
          });

          waitingCount = ranked.filter(item => item.score === null).length;
        } else {
          // Once the listener edits the recipe, rank the same live alternatives
          // against the selected genres using their current live AI detections.
          const scanned = [];
          const waiting = [];

          alternatives.forEach(dj => {
            const currentGenres = getLiveAIGenres(dj);
            if (currentGenres.length) scanned.push(dj);
            else waiting.push(dj);
          });

          ranked = scanned.map(dj => ({
            dj,
            score: getLiveSoundMatchScore(dj, selectedGenres)
          })).sort((a, b) => b.score - a.score ||
            String(a.dj.name || a.dj.username || "").localeCompare(
              String(b.dj.name || b.dj.username || "")
            ));

          waitingCount = waiting.length;
        }

        const totalLive = djDiscoveryDJs.length;
        const alternativeCount = alternatives.length;

        if (!totalLive) {
          statusEl.className = "dj-discovery-scan-status complete";
          statusEl.textContent = "No Radio RRR DJs are live right now.";
          resultsEl.innerHTML = "";
          return;
        }

        if (usingRadioRrrPreset) {
          statusEl.className = waitingCount > 0
            ? "dj-discovery-scan-status scanning"
            : "dj-discovery-scan-status complete";
          statusEl.innerHTML =
            '<strong>#1 is playing now</strong> · showing ' + alternativeCount +
            (alternativeCount === 1 ? ' ranked live alternative' : ' ranked live alternatives') +
            (waitingCount > 0
              ? ' · ' + waitingCount + (waitingCount === 1 ? ' awaiting a Program Match score' : ' awaiting Program Match scores')
              : '');
        } else if (waitingCount > 0) {
          const customSoundLabel = selectedGenres.length === 1
            ? selectedGenres[0]
            : selectedGenres.slice(0, 2).join(" + ") + (selectedGenres.length > 2 ? " + more" : "");
          const analysedCount = ranked.length;
          const bestScore = ranked.length
            ? Math.max(0, Math.round(Number(ranked[0].score || 0) * 100))
            : 0;
          statusEl.className = "dj-discovery-scan-status scanning custom-retuning";
          statusEl.innerHTML =
            '<span class="dj-discovery-scan-dot" aria-hidden="true"></span>' +
            '<span class="dj-discovery-retune-copy">' +
              '<strong>FINDING LIVE ' + escapeHtml(customSoundLabel.toUpperCase()) + ' DJs</strong>' +
              '<span>Radio RRR is checking live DJs, analysing their current audio and ranking the best matches for your custom sound. ' +
              'The main DJ will change automatically when a better match is found.</span>' +
              '<span class="dj-discovery-search-steps" aria-label="Live DJ search progress">' +
                '<span class="done">✓ Live DJs detected</span>' +
                '<span class="active">● Analysing audio</span>' +
                '<span>Comparing genres</span>' +
                '<span>Ranking matches</span>' +
              '</span>' +
              '<small>' + analysedCount + ' of ' + alternativeCount + ' live alternatives analysed · ' + waitingCount +
              (waitingCount === 1 ? ' waiting for fresh genre data' : ' waiting for fresh genre data') +
              (bestScore > 0 ? ' · best detected match ' + bestScore + '%' : '') +
              '</small>' +
            '</span>';
        } else {
          const hasCustomMatch = ranked.some(item => Number(item.score) > 0);
          const customSoundLabel = selectedGenres.length === 1
            ? selectedGenres[0]
            : selectedGenres.slice(0, 2).join(" + ") + (selectedGenres.length > 2 ? " + more" : "");
          const bestScore = ranked.length
            ? Math.max(0, Math.round(Number(ranked[0].score || 0) * 100))
            : 0;
          statusEl.className = hasCustomMatch
            ? "dj-discovery-scan-status complete custom-active"
            : "dj-discovery-scan-status scanning custom-retuning";
          statusEl.innerHTML = hasCustomMatch
            ? '<strong>✓ CUSTOM SOUND ACTIVE</strong> · ' + ranked.length + ' live DJs analysed · best ' + bestScore + '% match for ' + escapeHtml(customSoundLabel) + '.'
            : '<span class="dj-discovery-scan-dot" aria-hidden="true"></span>' +
              '<span class="dj-discovery-retune-copy">' +
                '<strong>STILL SEARCHING FOR LIVE ' + escapeHtml(customSoundLabel.toUpperCase()) + '</strong>' +
                '<span>No strong match has been detected yet. Radio RRR has analysed the currently available live DJs and will keep rescanning as fresh audio and genre data arrives. ' +
                'The cards below are the best currently available candidates while the search continues.</span>' +
                '<span class="dj-discovery-search-steps" aria-label="Live DJ search progress">' +
                  '<span class="done">✓ Live DJs detected</span>' +
                  '<span class="done">✓ Audio analysed</span>' +
                  '<span class="done">✓ Genres compared</span>' +
                  '<span class="active">● Rescanning</span>' +
                '</span>' +
                '<small>' + ranked.length + ' of ' + alternativeCount + ' live DJs analysed · no strong match yet · rescanning automatically</small>' +
              '</span>';
        }

        if (!ranked.length) {
          resultsEl.innerHTML =
            '<div class="dj-discovery-empty">Live alternatives are still waiting for current genre detections.</div>';
          return;
        }

        resultsEl.innerHTML = "";

        // Render the full ranked list first so the browser's actual grid layout
        // can tell us exactly how many cards fit on one row at this width.
        ranked.forEach((item, index) => {
          const dj = item.dj;
          const name = String(dj.name || dj.display_name || dj.username || "DJ").trim();
          const username = getDJUsername(dj);
          const profilePic = dj.profile_pic || dj.profile_picture || dj.avatar || dj.photo || "";
          const allLiveGenres = getLiveAIGenres(dj);
          const customEvidence = usingRadioRrrPreset
            ? []
            : getLiveSoundMatchEvidence(dj, selectedGenres);
          const customHits = customEvidence.filter(entry => entry.matched);
          const matchedDetectedKeys = new Set(
            customHits.map(entry => normaliseDiscoveryGenre(entry.detectedGenre))
          );
          const liveGenres = usingRadioRrrPreset
            ? allLiveGenres.slice(0, 6)
            : allLiveGenres.filter(genre =>
                !matchedDetectedKeys.has(normaliseDiscoveryGenre(genre))
              ).slice(0, Math.max(0, 6 - customHits.length));
          const hasScore = Number.isFinite(Number(item.score));
          const matchPercent = hasScore
            ? Math.max(0, Math.min(100, Math.round(Number(item.score) * 100)))
            : null;

          const card = document.createElement("button");
          card.type = "button";
          card.className = "dj-discovery-card is-live dj-discovery-live-select";
          if (!usingRadioRrrPreset) {
            card.classList.add(customHits.length ? "has-custom-hit" : "no-custom-hit");
          }
          card.setAttribute("aria-label", "Play " + name + " in this browser");

          const photo = profilePic
            ? '<img class="dj-discovery-thumb" src="' + escapeAttr(String(profilePic)) + '" alt="" loading="lazy">'
            : '<div class="dj-discovery-placeholder">🎧</div>';

          const rankNumber = index + 2;
          const matchLabel = usingRadioRrrPreset ? 'PROGRAM MATCH' : 'YOUR SOUND MATCH';
          const matchValueText = matchPercent === null ? '—' : matchPercent + '%';
          const matchClass = matchPercent === null
            ? 'match-low'
            : matchPercent >= 50
              ? 'match-high'
              : matchPercent >= 20
                ? 'match-mid'
                : 'match-low';
          const matchFillWidth = matchPercent === null ? 0 : matchPercent;

          const customGenreEvidenceHtml = usingRadioRrrPreset
            ? ""
            : (customHits.length
                ? customHits.map(entry => {
                    const percent = Math.round(entry.confidence * 100);
                    const title = entry.detectedGenre &&
                      normaliseDiscoveryGenre(entry.detectedGenre) !== normaliseDiscoveryGenre(entry.selectedGenre)
                        ? entry.selectedGenre + " matched " + entry.detectedGenre + " at " + percent + "%"
                        : entry.selectedGenre + " detected at " + percent + "%";
                    return '<span class="dj-discovery-custom-hit ' + getGenreNeonClass(entry.selectedGenre) +
                      '" title="' + escapeAttr(title) + '">✓ ' +
                      escapeHtml(entry.selectedGenre) + ' ' + percent + '%</span>';
                  }).join("")
                : '<span class="dj-discovery-custom-miss">NO SELECTED GENRE DETECTED YET</span>');

          card.innerHTML =
            '<div class="dj-discovery-rank">#' + rankNumber + '</div>' +
            '<div class="dj-discovery-card-top">' + photo +
              '<div class="dj-discovery-name-wrap">' +
                '<div class="dj-discovery-name" title="' + escapeAttr(name) + '">' + escapeHtml(name) + '</div>' +
                '<div class="dj-discovery-handle">@' + escapeHtml(username) + '</div>' +
              '</div>' +
            '</div>' +
            '<div class="dj-discovery-overlay"></div>' +
            getDJPlatformBadgeHtml(dj, "dj-discovery-platform") +
            '<div class="dj-discovery-status live"><span class="dj-discovery-status-dot"></span>LIVE NOW</div>' +
            '<div class="dj-discovery-genres" aria-label="Current live detected genres">' +
              customGenreEvidenceHtml +
              liveGenres.map(getGenrePillHtml).join("") +
            '</div>' +
            '<div class="dj-discovery-match-row">' +
              '<span>' + matchLabel + '</span>' +
              '<span class="dj-discovery-match-value">' + matchValueText + '</span>' +
            '</div>' +
            '<div class="dj-discovery-match-bar" aria-label="' + matchLabel + ' ' + matchValueText + '">' +
              '<div class="dj-discovery-match-fill ' + matchClass + '" style="width:' + matchFillWidth + '%"></div>' +
            '</div>' +
            '<div class="dj-discovery-play-hint">▶ PLAY THIS DJ</div>';

          card.addEventListener("click", async function () {
            if (card.dataset.switching === "1") return;
            const targetUsername = getDJUsername(dj);
            if (!targetUsername) return;

            if (liveDjVideo) {
              userRequestedAudio = !liveDjVideo.muted;
              liveDjVideo.dataset.rrrUserAudio = userRequestedAudio ? "1" : "0";
              liveDjVideo.defaultMuted = !userRequestedAudio;
            }

            card.dataset.switching = "1";
            try {
              manualFeaturedDJIdentity = getDJIdentity(dj);
              manualFeaturedDJMissingCount = 0;
              manualDJStreamFailureHandled = false;
              currentFeaturedDJ = dj;
              showLiveDjPlayer(dj);
              await updateLiveGenresDetected(
                null,
                manualFeaturedDJIdentity,
                "",
                dj && dj.platform
              );
              await loadLiveDJs();
            } catch (error) {
              console.error("Radio RRR custom live sound selection failed:", error);
              manualFeaturedDJIdentity = "";
              manualFeaturedDJMissingCount = 0;
              manualDJStreamFailureHandled = false;
              await loadLiveDJs();
            } finally {
              card.dataset.switching = "0";
            }
          });

          resultsEl.appendChild(card);
        });

        // Keep every ranked live alternative in one horizontal row.
        // CSS owns the sideways scrolling; do not hide cards or render SHOW ALL.
        const resultCards = Array.from(
          resultsEl.querySelectorAll(".dj-discovery-card")
        );
        resultCards.forEach(card => { card.style.display = ""; });

        const moreWrap = document.getElementById("djDiscoveryMoreWrap");
        if (moreWrap) moreWrap.innerHTML = "";
      }

      window.addEventListener("resize", function () {
        window.clearTimeout(djDiscoveryResizeTimer);
        djDiscoveryResizeTimer = window.setTimeout(function () {
          if (document.getElementById("djDiscoveryResults")) {
            renderDjDiscoveryResults();
          }
        }, 150);
      });

      /* LIVE DJs API */

      async function loadLiveDJs() {
        if (!liveDjsList) return;

        const requestId = ++liveDjRequestId;

        if (liveDjRequestController) {
          liveDjRequestController.abort();
        }

        liveDjRequestController = new AbortController();

        try {
          // /api/live is the single platform-neutral source for the station
          // relay, all live DJs and the complete DJ catalogue.
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

          const live = Array.isArray(data.live)
            ? data.live
            : [];

          currentLiveMatchScores = {};
          if (
            genreMatchData &&
            Array.isArray(genreMatchData.ranked)
          ) {
            genreMatchData.ranked.forEach(item => {
              const username = String(item.username || "")
                .replace(/^@/, "")
                .trim()
                .toLowerCase();
              const platform = String(item.platform || "TikTok")
                .trim()
                .toLowerCase() || "tiktok";
              const key = username ? platform + ":" + username : "";

              if (!key) return;

              const score = Number(item.score);
              if (Number.isFinite(score)) {
                currentLiveMatchScores[key] = score;
              }
            });
          }
          const favourites =
            Array.isArray(data.favourites)
              ? data.favourites
              : [];

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
            ? getDJIdentity(relayDj) ||
              String(
                relayDj.name ||
                relayDj.display_name ||
                "LIVE DJ"
              )
            : "";
          const aiGenre = data.ai_genre || null;

          currentLiveDJs = live;

          const liveKeys = new Set(
            live.map(dj => getDJMatchKey(dj)).filter(Boolean)
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
                  "manual:" + getDJIdentity(liveFeaturedOverride)
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
                updateCurrentDJPlatform(relayDj);
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
            routerIdentity,
            selectedFeaturedDj && selectedFeaturedDj.platform
          );

          // A newer 30-second refresh may have completed while this request was
          // waiting for /api/ai-genre. Never let an older refresh clear and
          // repaint the LIVE/DISCOVER area with stale DJ/relay data.
          if (requestId !== liveDjRequestId) return;

          // Do not collapse the LIVE/DISCOVER area while waiting for the
          // detector request above. Rebuild only after the async genre refresh
          // has completed so the browser never sees a temporarily short page.
          liveDjsList.innerHTML = "";

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
            otherLiveHeader.className = "rrr-other-live-djs-header";
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
            const secondaryLiveDJs = otherLiveDJs.map(dj => {
              const matchingFavourite = uniqueFavouriteDJs.find(
                favourite => getDJIdentity(favourite) === getDJIdentity(dj)
              );
              const merged = hydrateLiveDJProfile(dj, matchingFavourite);

              // Render the LIVE row immediately from the Scout data already
              // returned by /api/live. Do not hold the entire row waiting for
              // extra per-DJ HTTP requests; one slow/missing AI result was able
              // to delay every secondary LIVE card indefinitely.
              merged.rrr_live_detected_genres = parseLiveDetectedGenres(
                merged && merged.ai_genres
                  ? { genres: merged.ai_genres }
                  : null
              );

              return merged;
            });

            // Queue secondary LIVE DJs by PROGRAM MATCH score.
            // Highest score renders first, ready to take over next.
            secondaryLiveDJs.sort((a, b) => {
              const aKey = getDJMatchKey(a);
              const bKey = getDJMatchKey(b);

              const aScore = Number(currentLiveMatchScores[aKey]);
              const bScore = Number(currentLiveMatchScores[bKey]);

              const safeAScore = Number.isFinite(aScore) ? aScore : -1;
              const safeBScore = Number.isFinite(bScore) ? bScore : -1;

              if (safeBScore !== safeAScore) {
                return safeBScore - safeAScore;
              }

              const aName = String(
                a.name || a.display_name || getDJUsername(a) || ""
              );

              const bName = String(
                b.name || b.display_name || getDJUsername(b) || ""
              );

              return aName.localeCompare(bName);
            });

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

          // A Custom Program keeps re-evaluating as the 30-second live refresh
          // brings in fresh genre detections. This is browser-local only.
          scheduleCustomSoundAutoRetune();

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

        const platform = getDJPlatform(dj);
        const profileUrl =
          dj.profile_url ||
          (username
            ? (platform.toLowerCase() === "twitch"
              ? "https://www.twitch.tv/" + String(username).replace(/^@/, "")
              : "https://www.tiktok.com/@" + String(username).replace(/^@/, ""))
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
            ? (platform.toLowerCase() === "twitch"
              ? "https://www.twitch.tv/" + String(username).replace(/^@/, "")
              : "https://www.tiktok.com/@" + String(username).replace(/^@/, "") + "/live")
            : profileUrl);

        const isRouted =
          isLive &&
          routerIdentity &&
          getDJIdentity(dj) === routerIdentity;

        const isActuallyLive = isLive || dj.live === true;

        const matchKey = getDJMatchKey(dj);

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
            getDJPlatformBadgeHtml(dj, "dj-secondary-platform") +
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

            // A manual DJ change is a user gesture, so this is the safe point
            // to snapshot the listener's current audio choice before HLS is
            // torn down and attached to the newly selected DJ.
            if (liveDjVideo) {
              userRequestedAudio = !liveDjVideo.muted;
              liveDjVideo.dataset.rrrUserAudio = userRequestedAudio ? "1" : "0";
              liveDjVideo.defaultMuted = !userRequestedAudio;
            }

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
                "",
                dj && dj.platform
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

      // The scheduler API is the single source of truth for the public site.
      // If it is unavailable before any successful load, the schedule is shown
      // as unavailable rather than falling back to duplicated hard-coded data.
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
            bpm_min: row.bpm_min,
            bpm_max: row.bpm_max,
            genres: Array.isArray(row.genres) ? row.genres : []
          };
        });
      }

      function publicScheduleSignature(rows) {
        return JSON.stringify((rows || []).map(function (r) {
          return [r.start, r.end, r.name, r.bpm_min, r.bpm_max, r.genres];
        }));
      }

      function renderPublicSchedule(rows) {
        const container = document.getElementById("publicScheduleRows");
        if (!container) return;

        const calendarDays = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
        const normalisedRows = normalisePublicScheduleRows(rows);
        const byDay = {};
        calendarDays.forEach(function (day) {
          byDay[day] = normalisedRows
            .filter(function (r) { return r.day === day; })
            .sort(function (a, b) { return String(a.start).localeCompare(String(b.start)); });
        });

        // Start the public schedule with the listener's current local day, then
        // continue through the rest of the week in calendar order.
        const todayIndex = new Date().getDay();
        const displayDayOrder = calendarDays.slice(todayIndex).concat(calendarDays.slice(0, todayIndex));

        const displayRows = [];
        const weekdaySame = ["Monday", "Tuesday", "Wednesday", "Thursday"].every(function (day) {
          return publicScheduleSignature(byDay[day]) === publicScheduleSignature(byDay.Monday);
        });
        let weekdayGroupAdded = false;

        displayDayOrder.forEach(function (day) {
          const isGroupedWeekday = ["Monday", "Tuesday", "Wednesday", "Thursday"].includes(day);

          if (weekdaySame && byDay.Monday.length && isGroupedWeekday) {
            if (!weekdayGroupAdded) {
              byDay.Monday.forEach(function (r) {
                displayRows.push({ row: r, label: "WEEKDAYS · MON–THU" });
              });
              weekdayGroupAdded = true;
            }
            return;
          }

          byDay[day].forEach(function (r) {
            displayRows.push({ row: r, label: day.toUpperCase() });
          });
        });

        if (!displayRows.length) {
          container.innerHTML = '<div class="schedule-row" role="row"><div role="cell">—</div><div role="cell"><strong>Schedule unavailable</strong></div><div role="cell">—</div><div role="cell"></div><div class="schedule-bpm" role="cell">—</div></div>';
          return;
        }

        container.innerHTML = displayRows.map(function (item) {
          const r = item.row;
          const genres = r.genres.map(function (genre) {
            const safe = String(genre).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
            return '<span class="dj-genre-pill ' + publicGenreClass(genre) + '">' + safe + '</span>';
          }).join("");
          const safeName = String(r.name || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
          const bpmMin = Number(r.bpm_min);
          const bpmMax = Number(r.bpm_max);
          const bpmRange = Number.isFinite(bpmMin) && Number.isFinite(bpmMax)
            ? Math.round(bpmMin) + "–" + Math.round(bpmMax)
            : "—";
          return '<div class="schedule-row" role="row"><div role="cell">' + item.label + '</div><div role="cell"><strong>' + publicProgramIcon(r.name, r.start) + ' ' + safeName + '</strong></div><div role="cell">' + r.start + ' – ' + r.end + '</div><div role="cell"><div class="schedule-genres">' + genres + '</div></div><div class="schedule-bpm" role="cell">' + bpmRange + '</div></div>';
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

        if (!publicScheduleRows.length) return null;

        const dayName = publicDayName(dayIndex);
        const rowsForDay = publicScheduleRows.filter(function (row) {
          return row.day === dayName;
        });
        const current = rowsForDay.find(function (row) {
          return hour >= Number(row.start.split(":")[0]) + Number(row.start.split(":")[1]) / 60 &&
                 hour < (row.end === "00:00" ? 24 : Number(row.end.split(":")[0]) + Number(row.end.split(":")[1]) / 60);
        });

        if (!current) return null;

        return {
          start: Number(current.start.split(":")[0]) + Number(current.start.split(":")[1]) / 60,
          end: current.end === "00:00" ? 24 : Number(current.end.split(":")[0]) + Number(current.end.split(":")[1]) / 60,
          startText: current.start,
          endText: current.end,
          name: current.name,
          genres: current.genres.join(" · ")
        };

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

          const currentDayName = publicDayName(dayIndex).toUpperCase();
          const matchesDay =
            (dayType === 'weekday' && (
              dayText.indexOf('WEEKDAYS') === 0 ||
              dayText.indexOf(currentDayName) === 0
            )) ||
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

        if (!block) {
          if (heroNowEl) {
            heroNowEl.textContent =
              "Schedule unavailable";
          }

          if (liveProgramTargetGenres) {
            liveProgramTargetGenres.innerHTML = "";
          }

          if (heroProgramTime) {
            heroProgramTime.textContent = "";
          }
          if (heroProgramExplainer) heroProgramExplainer.textContent = "We find live DJs and automatically tune into the best match for this program.";
          setDjDiscoveryPresetGenres([]);

          return;
        }

        const blockLabel =
          dayName.toUpperCase() +
          " – " +
          String(block.name || "").toUpperCase();

        djDiscoveryScheduledProgramTitle = blockLabel;

        function formatHeroTime(value) {
          const parts = String(value || "").split(":");
          const hour24 = Number(parts[0]);
          const minute = Number(parts[1] || 0);
          if (!Number.isFinite(hour24)) return String(value || "");
          const suffix = hour24 >= 12 ? "PM" : "AM";
          const hour12 = (hour24 % 12) || 12;
          return hour12 + (minute ? ":" + String(minute).padStart(2, "0") : "") + suffix;
        }

        if (heroNowEl) {
          heroNowEl.textContent = djDiscoveryCustomised
            ? dayName.toUpperCase() + " – CUSTOM PROGRAM"
            : blockLabel;
        }

        if (heroProgramTime) {
          heroProgramTime.textContent =
            formatHeroTime(block.startText) + "–" + formatHeroTime(block.endText);
        }

        const heroGenres = block.genres.split("·").map(function (genre) { return genre.trim(); }).filter(Boolean);

        if (featuredHeroOverrideActive) {
          applyFeaturedHeroOverride();
        } else {
          if (heroProgramTargetLabel) {
            heroProgramTargetLabel.textContent = "TARGET SOUND:";
          }
          if (liveProgramTargetGenres) {
            liveProgramTargetGenres.innerHTML = heroGenres.map(getGenrePillHtml).join("");
          }
        }

        // The listener starts with Radio RRR's tuned current-program sound.
        // Personal edits stay local until they explicitly reset to the preset.
        setDjDiscoveryPresetGenres(heroGenres);
        if (heroProgramExplainer) {
          heroProgramExplainer.textContent = "We find live DJs and automatically tune into the best match for this program.";
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

      /* RRR TOOLS — PUBLIC BPM DETECTOR */
      const bpmDetectorUrl = document.getElementById("bpmDetectorUrl");
      const bpmDetectorStart = document.getElementById("bpmDetectorStart");
      const bpmDetectorResult = document.getElementById("bpmDetectorResult");
      const bpmDetectorValue = document.getElementById("bpmDetectorValue");
      const bpmDetectorConfidence = document.getElementById("bpmDetectorConfidence");
      const bpmDetectorStatus = document.getElementById("bpmDetectorStatus");

      function setBpmDetectorStatus(message, state) {
        if (!bpmDetectorStatus) return;
        bpmDetectorStatus.textContent = message;
        bpmDetectorStatus.classList.remove("error", "success");
        if (state) bpmDetectorStatus.classList.add(state);
      }

      async function detectStreamBpm() {
        if (!bpmDetectorUrl || !bpmDetectorStart) return;

        const url = String(bpmDetectorUrl.value || "").trim();
        if (!/^https?:\/\//i.test(url)) {
          setBpmDetectorStatus("Enter a direct http:// or https:// stream URL first.", "error");
          bpmDetectorUrl.focus();
          return;
        }

        bpmDetectorStart.disabled = true;
        bpmDetectorStart.textContent = "Analysing…";
        if (bpmDetectorResult) bpmDetectorResult.hidden = true;
        setBpmDetectorStatus("Sampling the stream for about 24 seconds…");

        try {
          const response = await fetch("https://api.radiorrr.com/api/tools/bpm", {
            method: "POST",
            cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: url })
          });

          let data = null;
          try {
            data = await response.json();
          } catch (e) {}

          if (!response.ok) {
            const detail = data && data.detail ? String(data.detail) : "Could not analyse this stream.";
            throw new Error(detail);
          }

          const bpm = Number(data && data.bpm);
          if (!Number.isFinite(bpm)) {
            throw new Error("The detector did not return a BPM reading.");
          }

          if (bpmDetectorValue) bpmDetectorValue.textContent = Math.round(bpm) + " BPM";

          const confidence = Number(data && data.confidence);
          if (bpmDetectorConfidence) {
            bpmDetectorConfidence.textContent = Number.isFinite(confidence)
              ? Math.round(Math.max(0, Math.min(1, confidence)) * 100) + "% confidence"
              : "";
          }

          if (bpmDetectorResult) bpmDetectorResult.hidden = false;
          setBpmDetectorStatus("Analysis complete.", "success");
        } catch (error) {
          setBpmDetectorStatus(error && error.message ? error.message : "Could not analyse this stream.", "error");
        } finally {
          bpmDetectorStart.disabled = false;
          bpmDetectorStart.textContent = "Detect BPM";
        }
      }

      if (bpmDetectorStart) {
        bpmDetectorStart.addEventListener("click", detectStreamBpm);
      }

      if (bpmDetectorUrl) {
        bpmDetectorUrl.addEventListener("keydown", function (event) {
          if (event.key === "Enter") {
            event.preventDefault();
            detectStreamBpm();
          }
        });
      }



      /* RRR TOOLS — STUCK FRAME EFFECT */
      const stuckFrameVideo = document.getElementById("stuckFrameVideo");
      const stuckFrameStart = document.getElementById("stuckFrameStart");
      const stuckFrameFileName = document.getElementById("stuckFrameFileName");
      const stuckFrameStatus = document.getElementById("stuckFrameStatus");
      const stuckFrameProgress = document.getElementById("stuckFrameProgress");
      const stuckFrameDownload = document.getElementById("stuckFrameDownload");
      let stuckFrameDownloadUrl = "";

      function setStuckFrameStatus(message, state) {
        if (!stuckFrameStatus) return;
        stuckFrameStatus.textContent = message;
        stuckFrameStatus.classList.remove("error", "success");
        if (state) stuckFrameStatus.classList.add(state);
      }

      function clearStuckFrameDownload() {
        if (stuckFrameDownloadUrl) {
          URL.revokeObjectURL(stuckFrameDownloadUrl);
          stuckFrameDownloadUrl = "";
        }
        if (stuckFrameDownload) {
          stuckFrameDownload.hidden = true;
          stuckFrameDownload.removeAttribute("href");
          stuckFrameDownload.removeAttribute("download");
        }
      }

      function selectedStuckFrameFile() {
        return stuckFrameVideo && stuckFrameVideo.files && stuckFrameVideo.files[0]
          ? stuckFrameVideo.files[0]
          : null;
      }

      if (stuckFrameVideo) {
        stuckFrameVideo.addEventListener("change", function () {
          clearStuckFrameDownload();
          const file = selectedStuckFrameFile();

          if (!file) {
            if (stuckFrameFileName) stuckFrameFileName.textContent = "No video selected.";
            if (stuckFrameStart) stuckFrameStart.disabled = true;
            setStuckFrameStatus("MP4, MOV, M4V, WebM and MKV videos are supported. Maximum upload size: 100 MB.");
            return;
          }

          if (stuckFrameFileName) {
            const sizeMb = file.size / (1024 * 1024);
            stuckFrameFileName.textContent = file.name + " · " + sizeMb.toFixed(1) + " MB";
          }

          if (file.size > 100 * 1024 * 1024) {
            if (stuckFrameStart) stuckFrameStart.disabled = true;
            setStuckFrameStatus("This video is larger than the 100 MB upload limit.", "error");
            return;
          }

          if (stuckFrameStart) stuckFrameStart.disabled = false;
          setStuckFrameStatus("Ready to analyse the beat and render the stuck-frame effect.");
        });
      }

      async function createStuckFrameEffect() {
        const file = selectedStuckFrameFile();
        if (!file || !stuckFrameStart) return;

        if (file.size > 100 * 1024 * 1024) {
          setStuckFrameStatus("This video is larger than the 100 MB upload limit.", "error");
          return;
        }

        clearStuckFrameDownload();
        stuckFrameStart.disabled = true;
        stuckFrameStart.textContent = "Processing…";
        if (stuckFrameProgress) stuckFrameProgress.hidden = false;
        setStuckFrameStatus("Uploading the video, detecting the beat and rendering the effect. This can take a minute or two.");

        try {
          const response = await fetch(
            "https://api.radiorrr.com/api/tools/stuck-frame-effect",
            {
              method: "POST",
              cache: "no-store",
              headers: {
                "Content-Type": file.type || "application/octet-stream",
                "X-RRR-Filename": encodeURIComponent(file.name)
              },
              body: file
            }
          );

          if (!response.ok) {
            let detail = "";
            try {
              const data = await response.json();
              detail = data && data.detail ? String(data.detail) : "";
            } catch (e) {
              try { detail = await response.text(); } catch (ignore) {}
            }
            throw new Error(detail || "Could not process this video.");
          }

          const blob = await response.blob();
          if (!blob.size) {
            throw new Error("The processed video was empty.");
          }

          stuckFrameDownloadUrl = URL.createObjectURL(blob);
          const originalBase = file.name.replace(/\.[^.]+$/, "") || "video";
          if (stuckFrameDownload) {
            stuckFrameDownload.href = stuckFrameDownloadUrl;
            stuckFrameDownload.download = originalBase + "-stuck-frame.mp4";
            stuckFrameDownload.hidden = false;
          }

          setStuckFrameStatus(
            "Effect complete. The stuck frames are synchronised to the detected beat.",
            "success"
          );
        } catch (error) {
          setStuckFrameStatus(
            error && error.message ? error.message : "Could not process this video.",
            "error"
          );
        } finally {
          stuckFrameStart.disabled = !selectedStuckFrameFile();
          stuckFrameStart.textContent = "Create Effect";
          if (stuckFrameProgress) stuckFrameProgress.hidden = true;
        }
      }

      if (stuckFrameStart) {
        stuckFrameStart.addEventListener("click", createStuckFrameEffect);
      }


      /* RRR TOOLS — DJ TALK DETECTOR */
      const talkDetectorStart = document.getElementById("talkDetectorStart");
      const talkDetectorStatus = document.getElementById("talkDetectorStatus");
      const talkDetectorResults = document.getElementById("talkDetectorResults");

      function setTalkDetectorStatus(message, state) {
        if (!talkDetectorStatus) return;
        talkDetectorStatus.textContent = message;
        talkDetectorStatus.classList.remove("error", "success");
        if (state) talkDetectorStatus.classList.add(state);
      }

      function getTalkActivityLevel(ratio) {
        if (ratio >= 0.55) {
          return { label: "Heavy talking", level: "heavy" };
        }
        if (ratio >= 0.20) {
          return { label: "Frequent talking", level: "frequent" };
        }
        if (ratio >= 0.08) {
          return { label: "Occasional talking", level: "occasional" };
        }
        return { label: "Music dominant", level: "music" };
      }

      function renderTalkDetectorResults(entries) {
        if (!talkDetectorResults) return;

        talkDetectorResults.innerHTML = "";

        if (!entries.length) {
          talkDetectorResults.hidden = false;
          const empty = document.createElement("div");
          empty.className = "talk-detector-empty";
          empty.textContent = "No recent talk-analysis samples are available yet.";
          talkDetectorResults.appendChild(empty);
          return;
        }

        entries.forEach(function (entry) {
          const ratio = Math.max(0, Math.min(1, Number(entry.speech_ratio) || 0));
          const confidenceValue = Number(entry.speech_confidence);
          const activity = getTalkActivityLevel(ratio);

          const row = document.createElement("div");
          row.className = "talk-detector-result " + activity.level;

          const identity = document.createElement("div");
          identity.className = "talk-detector-identity";

          const name = document.createElement("strong");
          name.textContent = String(entry.name || entry.username || "Live DJ");
          identity.appendChild(name);

          const meta = document.createElement("span");
          const platform = String(entry.platform || "TikTok");
          const username = String(entry.username || "").replace(/^@/, "");
          meta.textContent = platform + (username ? " · @" + username : "");
          identity.appendChild(meta);

          const meter = document.createElement("div");
          meter.className = "talk-detector-meter";
          meter.setAttribute("aria-hidden", "true");

          const fill = document.createElement("span");
          fill.style.width = Math.round(ratio * 100) + "%";
          meter.appendChild(fill);

          const reading = document.createElement("div");
          reading.className = "talk-detector-reading";

          const value = document.createElement("strong");
          value.textContent = Math.round(ratio * 100) + "%";
          value.title = "Conservative talk activity score from repeated voice-related music ducking";
          reading.appendChild(value);

          const label = document.createElement("span");
          label.className = "talk-detector-label";
          label.textContent = activity.label;
          reading.appendChild(label);

          if (Number.isFinite(confidenceValue) && confidenceValue > 0) {
            const confidence = document.createElement("small");
            confidence.textContent =
              Math.round(Math.max(0, Math.min(1, confidenceValue)) * 100) +
              "% confidence";
            reading.appendChild(confidence);
          }

          row.appendChild(identity);
          row.appendChild(meter);
          row.appendChild(reading);
          talkDetectorResults.appendChild(row);
        });

        talkDetectorResults.hidden = false;
      }

      async function scanLiveDjTalkActivity() {
        if (!talkDetectorStart) return;

        talkDetectorStart.disabled = true;
        talkDetectorStart.textContent = "Scanning…";
        if (talkDetectorResults) talkDetectorResults.hidden = true;
        setTalkDetectorStatus("Loading the latest live DJ talk-analysis samples…");

        try {
          const response = await fetch(
            "https://stream.radiorrr.com/api/genre-match",
            { cache: "no-store" }
          );

          if (!response.ok) {
            throw new Error("Talk detector API returned " + response.status + ".");
          }

          const data = await response.json();
          const ranked = Array.isArray(data && data.ranked) ? data.ranked : [];

          const entries = ranked
            .filter(function (entry) {
              return entry && Number.isFinite(Number(entry.speech_ratio));
            })
            .slice()
            .sort(function (a, b) {
              return Number(b.speech_ratio || 0) - Number(a.speech_ratio || 0);
            });

          renderTalkDetectorResults(entries);

          if (entries.length) {
            setTalkDetectorStatus(
              "Latest live samples loaded. Talk activity is a conservative voice-vs-music score, not a transcript.",
              "success"
            );
          } else {
            setTalkDetectorStatus(
              "No recent talk-analysis samples are available yet."
            );
          }
        } catch (error) {
          setTalkDetectorStatus(
            error && error.message
              ? error.message
              : "Could not load live talk-analysis results.",
            "error"
          );
        } finally {
          talkDetectorStart.disabled = false;
          talkDetectorStart.textContent = "🗣️ Scan Live DJs";
        }
      }

      if (talkDetectorStart) {
        talkDetectorStart.addEventListener("click", scanLiveDjTalkActivity);
      }

      /* RRR TOOLS — LIVE STATUS AND DIAGNOSTICS */
      const refreshTools = document.getElementById("refreshTools");
      const openScheduleTab = document.getElementById("openScheduleTab");
      let toolsRefreshInProgress = false;

      function setToolHealth(elementId, healthy, healthyText, unhealthyText) {
        const el = document.getElementById(elementId);
        if (!el) return;
        el.classList.remove("pending", "healthy", "unhealthy");
        el.classList.add(healthy ? "healthy" : "unhealthy");
        el.textContent = healthy ? healthyText : unhealthyText;
      }

      function renderToolPills(elementId, values) {
        const el = document.getElementById(elementId);
        if (!el) return;
        el.innerHTML = (Array.isArray(values) ? values : [])
          .filter(Boolean)
          .map(function (value) {
            return '<span class="tool-inline-pill">' + escapeHtml(String(value)) + '</span>';
          })
          .join("");
      }

      function getToolsScheduleState() {
        const now = new Date();
        const dayIndex = now.getDay();
        const dayName = publicDayName(dayIndex);
        const currentMinute = now.getHours() * 60 + now.getMinutes();
        const rowsToday = publicScheduleRows
          .filter(function (row) { return row.day === dayName; })
          .sort(function (a, b) {
            return (Number(a.start.split(":")[0]) * 60 + Number(a.start.split(":")[1])) -
                   (Number(b.start.split(":")[0]) * 60 + Number(b.start.split(":")[1]));
          });

        let current = null;
        let next = null;

        rowsToday.forEach(function (row) {
          const start = Number(row.start.split(":")[0]) * 60 + Number(row.start.split(":")[1]);
          let end = Number(row.end.split(":")[0]) * 60 + Number(row.end.split(":")[1]);
          if (end === 0) end = 1440;
          if (!current && currentMinute >= start && currentMinute < end) current = row;
          if (!next && start > currentMinute) next = row;
        });

        if (!next) {
          for (let offset = 1; offset <= 7 && !next; offset++) {
            const targetDayIndex = (dayIndex + offset) % 7;
            const targetDayName = publicDayName(targetDayIndex);
            const rows = publicScheduleRows
              .filter(function (row) { return row.day === targetDayName; })
              .sort(function (a, b) { return a.start.localeCompare(b.start); });
            if (rows.length) next = rows[0];
          }
        }

        return { current: current, next: next };
      }

      async function copyToolValue(button) {
        const value = button.getAttribute("data-copy-value") || "";
        if (!value) return;
        try {
          await navigator.clipboard.writeText(value);
        } catch (error) {
          const input = document.createElement("textarea");
          input.value = value;
          document.body.appendChild(input);
          input.select();
          document.execCommand("copy");
          input.remove();
        }
        const original = button.textContent;
        button.textContent = "Copied!";
        window.setTimeout(function () { button.textContent = original; }, 1200);
      }

      document.querySelectorAll(".tool-copy-button[data-copy-value]").forEach(function (button) {
        button.addEventListener("click", function () { copyToolValue(button); });
      });

      if (openScheduleTab) {
        openScheduleTab.addEventListener("click", function () {
          switchTab("events-section");
        });
      }

      async function refreshEngineStatus() {
        const entries = [
          ["radiorouter", "toolRouterEngine"],
          ["genre-detector", "toolDetectorEngine"],
          ["genre-candidate-scout", "toolScoutEngine"]
        ];
        const controller = new AbortController();
        const timeout = setTimeout(function () { controller.abort(); }, 8000);
        try {
          const response = await fetch(getFreshUrl("https://api.radiorrr.com/api/engines"), {
            cache: "no-store", signal: controller.signal
          });
          if (!response.ok) throw new Error("Engine status unavailable");
          const data = await response.json();
          entries.forEach(function (entry) {
            const state = data.engines && data.engines[entry[0]];
            const known = state && typeof state.running === "boolean";
            setToolHealth(entry[1], known && state.running, "Running", known ? "Not reporting" : "Unavailable");
          });
        } catch (error) {
          entries.forEach(function (entry) {
            setToolHealth(entry[1], false, "Running", "Unavailable");
          });
        } finally {
          clearTimeout(timeout);
        }
      }

      async function refreshToolsData() {
        const controller = new AbortController();
        const timeout = setTimeout(function () { controller.abort(); }, 8000);
        try {
          const responses = await Promise.allSettled([
            fetch(getFreshUrl("https://api.radiorrr.com/api/status"), { cache: "no-store", signal: controller.signal }),
            fetch(getFreshUrl("https://api.radiorrr.com/api/live"), { cache: "no-store", signal: controller.signal })
          ]);

          let statusData = null;
          let liveData = null;

          if (responses[0].status === "fulfilled" && responses[0].value.ok) {
            statusData = await responses[0].value.json();
          }
          if (responses[1].status === "fulfilled" && responses[1].value.ok) {
            liveData = await responses[1].value.json();
          }
          setToolHealth("toolApiHealth", !!statusData, "Healthy", "Unavailable");

          setToolHealth("toolVideoHealth", !!(statusData && statusData.video && statusData.video.healthy), "Healthy", statusData && statusData.video && typeof statusData.video.healthy === "boolean" ? "Offline" : "Unavailable");
          setToolHealth("toolAudioHealth", !!(statusData && statusData.audio && statusData.audio.healthy), "Healthy", statusData && statusData.audio && typeof statusData.audio.healthy === "boolean" ? "Offline" : "Unavailable");

          const audioStatus = statusData && statusData.audio;
          const videoStatus = statusData && statusData.video;
          const listenerEl = document.getElementById("toolListenerCount");
          const listeners = audioStatus && audioStatus.listeners;
          if (listenerEl) listenerEl.textContent = listeners != null && listeners !== "" && Number.isFinite(Number(listeners)) && Number(listeners) >= 0 ? String(Number(listeners)) : "—";

          const audioDetails = document.getElementById("toolAudioDetails");
          const bitrate = audioStatus && audioStatus.bitrate_kbps;
          if (audioDetails) audioDetails.textContent = bitrate != null && Number.isFinite(Number(bitrate)) && Number(bitrate) > 0 ? Number(bitrate) + " kbps · MP3" : "—";
          const viewerEl = document.getElementById("toolVideoViewerCount");
          const viewers = videoStatus && videoStatus.website_viewers;
          if (viewerEl) viewerEl.textContent = Number.isInteger(viewers) && viewers >= 0 ? String(viewers) : "Not available";
          const videoDetails = document.getElementById("toolVideoDetails");
          if (videoDetails) videoDetails.textContent = videoStatus && typeof videoStatus.playlist_ready === "boolean" ? (videoStatus.playlist_ready ? "Playlist ready" : "Playlist not ready") : "—";

          const relayEl = document.getElementById("toolRelayDj");
          if (relayEl) relayEl.textContent = statusData && statusData.relay_username ? "@" + String(statusData.relay_username).replace(/^@/, "") : "—";

          if (liveData) {
            const live = Array.isArray(liveData.live) ? liveData.live : [];
            const favourites = Array.isArray(liveData.favourites) ? liveData.favourites : [];
            const liveCountEl = document.getElementById("toolLiveDjCount");
            const favouriteCountEl = document.getElementById("toolFavouriteDjCount");
            if (liveCountEl) liveCountEl.textContent = String(live.length);
            if (favouriteCountEl) favouriteCountEl.textContent = String(favourites.length);

            if (relayEl && !(statusData && statusData.relay_username) && liveData.relay) {
              const relayUsername = getDJUsername(liveData.relay);
              if (relayUsername) relayEl.textContent = "@" + relayUsername.replace(/^@/, "");
            }
          }

        } catch (error) {
          console.warn("Radio RRR tools refresh failed:", error);
          setToolHealth("toolApiHealth", false, "Healthy", "Unavailable");
          setToolHealth("toolAudioHealth", false, "Healthy", "Unavailable");
          setToolHealth("toolVideoHealth", false, "Healthy", "Unavailable");
        } finally {
          clearTimeout(timeout);
        }
      }

      const monitoredHealthIds = ["toolApiHealth", "toolRouterEngine", "toolDetectorEngine", "toolScoutEngine", "toolAudioHealth", "toolVideoHealth"];

      function updateOverallHealth() {
        const checks = monitoredHealthIds.map(function (id) { return document.getElementById(id); });
        const allHealthy = checks.every(function (el) { return el && el.classList.contains("healthy"); });
        const problem = checks.some(function (el) { return el && ["Offline", "Not reporting"].includes(el.textContent); });
        const summary = document.getElementById("toolOverallHealth");
        if (!summary) return;
        summary.className = "tool-health " + (allHealthy ? "healthy" : problem ? "unhealthy" : "pending");
        summary.textContent = allHealthy ? "All monitored services healthy" : problem ? "Attention needed — one or more services report a problem" : "System health unknown — one or more checks unavailable";
      }

      async function refreshToolsPanel() {
        if (toolsRefreshInProgress) return;
        toolsRefreshInProgress = true;
        if (refreshTools) refreshTools.disabled = true;
        const summary = document.getElementById("toolOverallHealth");
        if (summary) {
          summary.className = "tool-health pending";
          summary.textContent = "Checking monitored services…";
        }
        monitoredHealthIds.forEach(function (id) {
          const el = document.getElementById(id);
          if (el) { el.className = "tool-health pending"; el.textContent = "Checking…"; }
        });
        ["toolLiveDjCount", "toolFavouriteDjCount", "toolListenerCount", "toolVideoViewerCount", "toolAudioDetails", "toolVideoDetails", "toolRelayDj"].forEach(function (id) {
          const el = document.getElementById(id);
          if (el) el.textContent = "—";
        });
        try {
          await Promise.allSettled([refreshEngineStatus(), refreshToolsData()]);
          updateOverallHealth();
          const updated = document.getElementById("toolLastRefresh");
          if (updated) updated.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        } finally {
          toolsRefreshInProgress = false;
          if (refreshTools) refreshTools.disabled = false;
        }
      }

      if (refreshTools) refreshTools.addEventListener("click", refreshToolsPanel);
      refreshToolsPanel();
      setInterval(refreshToolsPanel, 30 * 1000);

      /* RRR TOOLS — LIVE DJs */
      const openLiveDjsTool = document.getElementById("openLiveDjsTool");
      const liveDjsToolPanel = document.getElementById("liveDjsToolPanel");
      const liveDjsToolClose = document.getElementById("liveDjsToolClose");
      const liveDjsToolRefresh = document.getElementById("liveDjsToolRefresh");
      const liveDjsToolSearch = document.getElementById("liveDjsToolSearch");
      const liveDjsToolStatus = document.getElementById("liveDjsToolStatus");
      const liveDjsToolResults = document.getElementById("liveDjsToolResults");
      let liveDjsToolItems = [];
      let liveDjsToolLoading = false;

      function setLiveDjsToolOpen(open) {
        if (!liveDjsToolPanel) return;
        liveDjsToolPanel.hidden = !open;
        if (openLiveDjsTool) {
          openLiveDjsTool.setAttribute("aria-expanded", String(open));
        }
        if (open) {
          loadLiveDjsTool();
          window.setTimeout(function () {
            liveDjsToolPanel.scrollIntoView({ behavior: "smooth", block: "start" });
          }, 0);
        }
      }

      function getLiveDjsToolSearchText(dj) {
        return [
          dj && dj.name,
          dj && dj.display_name,
          getDJUsername(dj),
          getDJPlatform(dj)
        ].filter(Boolean).join(" ").toLowerCase();
      }

      function renderLiveDjsTool() {
        if (!liveDjsToolResults) return;

        const query = String(liveDjsToolSearch && liveDjsToolSearch.value || "")
          .trim().toLowerCase().replace(/^@/, "");
        const visible = liveDjsToolItems.filter(function (item) {
          return !query || getLiveDjsToolSearchText(item.dj).includes(query);
        });

        liveDjsToolResults.innerHTML = "";

        if (!visible.length) {
          const empty = document.createElement("div");
          empty.className = "live-djs-tool-empty";
          empty.textContent = liveDjsToolItems.length
            ? "No live DJs match that search."
            : "No DJs are currently detected live.";
          liveDjsToolResults.appendChild(empty);
          return;
        }

        visible.forEach(function (item) {
          const dj = item.dj;
          const username = getDJUsername(dj);
          const platform = getDJPlatform(dj);
          const name = String(dj.name || dj.display_name || username || "Live DJ");
          const profilePic = String(
            dj.profile_pic || dj.profile_picture || dj.avatar || dj.photo || ""
          );
          const genres = getLiveAIGenres(dj).slice(0, 5);
          const metrics = item.metrics || {};
          const matchScore = Number(metrics.raw_score ?? metrics.score);
          const safeScore = Number.isFinite(matchScore)
            ? Math.max(0, Math.min(100, Math.round(matchScore)))
            : null;
          const matchClass = safeScore === null
            ? "match-low"
            : safeScore >= 50
              ? "match-high"
              : safeScore >= 20
                ? "match-mid"
                : "match-low";
          const scoreText = safeScore === null ? "—" : safeScore + "%";
          const fillWidth = safeScore === null ? 0 : safeScore;

          const card = document.createElement("article");
          card.className = "live-djs-tool-card dj-card dj-live-switch-card";
          card.dataset.identity = getDJIdentity(dj);
          card.setAttribute("role", "button");
          card.setAttribute("tabindex", "0");
          card.setAttribute("aria-label", "Listen to " + name + " in this browser");

          const photoHtml = profilePic
            ? '<img class="dj-secondary-photo" src="' + escapeAttr(profilePic) + '" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\';">' +
              '<div class="dj-secondary-placeholder" style="display:none;">🎧</div>'
            : '<div class="dj-secondary-placeholder">🎧</div>';

          const genreHtml = genres.length
            ? genres.map(function (genre) {
                return '<span class="rrr-live-genre-bubble" title="Live detected: ' + escapeAttr(genre) + '">' +
                  escapeHtml(genre) + '</span>';
              }).join("")
            : '<span class="live-djs-tool-muted">Analysing live audio…</span>';

          card.innerHTML =
            photoHtml +
            '<div class="dj-secondary-overlay"></div>' +
            getDJPlatformBadgeHtml(dj, "dj-secondary-platform") +
            '<div class="dj-secondary-content">' +
              '<div class="dj-secondary-live"><span class="dj-secondary-live-dot"></span>LIVE</div>' +
              '<div class="dj-secondary-name" title="' + escapeAttr(name) + '">' + escapeHtml(name) + '</div>' +
              '<div class="live-djs-tool-handle">' +
                (username ? '@' + escapeHtml(username) + ' · ' : '') + escapeHtml(platform) +
              '</div>' +
              '<div class="dj-secondary-detected-title">LIVE DETECTED</div>' +
              '<div class="dj-secondary-detected-list live-djs-tool-detected" aria-label="Live detected genres">' + genreHtml + '</div>' +
              '<div class="dj-secondary-match-label"><span>Program Match</span><span class="dj-secondary-match-value">' + scoreText + '</span></div>' +
              '<div class="dj-secondary-match-bar" aria-label="Program Match ' + scoreText + '">' +
                '<div class="dj-secondary-match-fill ' + matchClass + '" style="width:' + fillWidth + '%"></div>' +
              '</div>' +
            '</div>';

          const switchToDj = async function () {
            if (!username || card.dataset.switching === "1") return;
            card.dataset.switching = "1";
            card.setAttribute("aria-busy", "true");
            try {
              manualFeaturedDJIdentity = getDJIdentity(dj);
              manualFeaturedDJMissingCount = 0;
              manualDJStreamFailureHandled = false;
              currentFeaturedDJ = dj;
              showLiveDjPlayer(dj);
              await updateLiveGenresDetected(
                null,
                manualFeaturedDJIdentity,
                "",
                platform
              );
              updateTabUrl("live-section");
              applyLocationState();
              await loadLiveDJs();
            } catch (error) {
              console.error("Radio RRR Live DJs tool selection failed:", error);
              manualFeaturedDJIdentity = "";
              manualFeaturedDJMissingCount = 0;
              manualDJStreamFailureHandled = false;
              if (liveDjsToolStatus) {
                liveDjsToolStatus.textContent = "Could not switch to that DJ. Refresh and try again.";
              }
            } finally {
              delete card.dataset.switching;
              card.removeAttribute("aria-busy");
            }
          };

          card.addEventListener("click", switchToDj);
          card.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              switchToDj();
            }
          });

          liveDjsToolResults.appendChild(card);
        });
      }

      async function loadLiveDjsTool() {
        if (!liveDjsToolResults || liveDjsToolLoading) return;
        liveDjsToolLoading = true;
        if (liveDjsToolRefresh) liveDjsToolRefresh.disabled = true;
        if (liveDjsToolStatus) liveDjsToolStatus.textContent = "Checking DJs currently detected live…";

        try {
          const responses = await Promise.all([
            fetch(getFreshUrl("https://api.radiorrr.com/api/live"), { cache: "no-store" }),
            fetch(getFreshUrl("https://api.radiorrr.com/api/genre-match"), { cache: "no-store" })
          ]);
          if (!responses[0].ok) throw new Error("Live API returned " + responses[0].status);

          const liveData = await responses[0].json();
          const matchData = responses[1].ok ? await responses[1].json() : null;
          const live = mergeUniqueDJs(Array.isArray(liveData && liveData.live) ? liveData.live : []);
          const metricMap = new Map();

          if (matchData && Array.isArray(matchData.ranked)) {
            matchData.ranked.forEach(function (entry) {
              const username = String(entry && entry.username || "").replace(/^@/, "").trim().toLowerCase();
              const platform = String(entry && entry.platform || "TikTok").trim().toLowerCase() || "tiktok";
              if (username) metricMap.set(platform + ":" + username, entry);
            });
          }

          liveDjsToolItems = live.map(function (dj) {
            const key = getDJPlatform(dj).toLowerCase() + ":" + getDJUsername(dj).toLowerCase();
            return { dj: dj, metrics: metricMap.get(key) || null };
          }).sort(function (a, b) {
            const aScore = Number(a.metrics && (a.metrics.raw_score ?? a.metrics.score));
            const bScore = Number(b.metrics && (b.metrics.raw_score ?? b.metrics.score));
            if (Number.isFinite(aScore) && Number.isFinite(bScore) && bScore !== aScore) return bScore - aScore;
            if (Number.isFinite(bScore) && !Number.isFinite(aScore)) return 1;
            if (Number.isFinite(aScore) && !Number.isFinite(bScore)) return -1;
            return String(a.dj.name || getDJUsername(a.dj)).localeCompare(String(b.dj.name || getDJUsername(b.dj)));
          });

          if (liveDjsToolStatus) {
            liveDjsToolStatus.textContent = liveDjsToolItems.length
              ? liveDjsToolItems.length + " DJ" + (liveDjsToolItems.length === 1 ? "" : "s") + " currently detected live."
              : "No DJs are currently detected live.";
          }
          renderLiveDjsTool();
        } catch (error) {
          console.error("Radio RRR Live DJs tool error:", error);
          liveDjsToolItems = [];
          if (liveDjsToolStatus) liveDjsToolStatus.textContent = "Could not load the current live DJ list.";
          renderLiveDjsTool();
        } finally {
          liveDjsToolLoading = false;
          if (liveDjsToolRefresh) liveDjsToolRefresh.disabled = false;
        }
      }

      if (liveDjsToolClose) {
        liveDjsToolClose.addEventListener("click", function () {
          updateTabUrl("tools-section");
          applyLocationState();
        });
      }
      if (liveDjsToolRefresh) liveDjsToolRefresh.addEventListener("click", loadLiveDjsTool);
      if (liveDjsToolSearch) liveDjsToolSearch.addEventListener("input", renderLiveDjsTool);

      /* RRR TOOLS — PASSIVE STREAM HEALTH TEST
         This observes the existing liveDjVideo/HLS instance only while the
         user starts a test. It never creates another stream, media element,
         analyser or network request. */
      const streamHealthStart = document.getElementById("streamHealthStart");
      const streamHealthStop = document.getElementById("streamHealthStop");
      const streamHealthReset = document.getElementById("streamHealthReset");
      const streamHealthLog = document.getElementById("streamHealthLog");
      const openStreamHealthTile = document.getElementById("openStreamHealthTile");
      const streamHealthPanel = document.getElementById("toolStreamHealthPanel");
      const streamHealthClose = document.getElementById("streamHealthClose");

      function setStreamHealthPanelOpen(open) {
        if (!streamHealthPanel) return;
        streamHealthPanel.hidden = !open;
        if (openStreamHealthTile) {
          openStreamHealthTile.setAttribute("aria-expanded", String(open));
        }
        if (open) {
          window.setTimeout(function () {
            streamHealthPanel.scrollIntoView({ behavior: "smooth", block: "start" });
          }, 0);
        }
      }

      if (streamHealthClose) {
        streamHealthClose.addEventListener("click", function () {
          window.location.href = "/tools/";
        });
      }

      const STREAM_HEALTH_TEST_MS = 60 * 1000;
      let streamHealthRunning = false;
      let streamHealthStartedAt = 0;
      let streamHealthTimer = null;
      let streamHealthStopTimer = null;
      let streamHealthBufferStartedAt = 0;
      let streamHealthObservedHls = null;
      let streamHealthFrameStart = null;
      let streamHealthPendingSwitchAt = 0;
      let streamHealthMetrics = null;

      function newStreamHealthMetrics() {
        return {
          stalls: 0,
          totalBufferMs: 0,
          longestBufferMs: 0,
          bufferAheadSamples: [],
          hlsErrors: 0,
          switches: 0,
          switchTimes: []
        };
      }

      function setStreamHealthText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
      }

      function streamHealthTimestamp() {
        return new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit"
        });
      }

      function logStreamHealth(message) {
        if (!streamHealthLog) return;
        const empty = streamHealthLog.querySelector(".stream-health-empty");
        if (empty) empty.remove();
        const row = document.createElement("div");
        row.className = "stream-health-event";
        row.textContent = streamHealthTimestamp() + "  " + message;
        streamHealthLog.appendChild(row);
        streamHealthLog.scrollTop = streamHealthLog.scrollHeight;
      }

      function getCurrentBufferAhead() {
        if (!liveDjVideo || !liveDjVideo.buffered || !liveDjVideo.buffered.length) return null;
        const current = Number(liveDjVideo.currentTime) || 0;
        for (let i = 0; i < liveDjVideo.buffered.length; i++) {
          const start = liveDjVideo.buffered.start(i);
          const end = liveDjVideo.buffered.end(i);
          if (current >= start && current <= end) {
            return Math.max(0, end - current);
          }
        }
        return 0;
      }

      function getVideoFrameSnapshot() {
        if (!liveDjVideo || typeof liveDjVideo.getVideoPlaybackQuality !== "function") return null;
        const quality = liveDjVideo.getVideoPlaybackQuality();
        return {
          dropped: Number(quality.droppedVideoFrames) || 0,
          total: Number(quality.totalVideoFrames) || 0
        };
      }

      function updateStreamHealthDisplay(finalState) {
        if (!streamHealthMetrics) streamHealthMetrics = newStreamHealthMetrics();
        const elapsedMs = streamHealthStartedAt
          ? Math.min(Date.now() - streamHealthStartedAt, STREAM_HEALTH_TEST_MS)
          : 0;
        const samples = streamHealthMetrics.bufferAheadSamples;
        const avgAhead = samples.length
          ? samples.reduce(function (sum, value) { return sum + value; }, 0) / samples.length
          : null;
        const avgSwitch = streamHealthMetrics.switchTimes.length
          ? streamHealthMetrics.switchTimes.reduce(function (sum, value) { return sum + value; }, 0) / streamHealthMetrics.switchTimes.length
          : null;

        let status = streamHealthRunning ? "Testing…" : (finalState || "Ready");
        if (!streamHealthRunning && finalState === "Complete") {
          if (streamHealthMetrics.totalBufferMs >= 5000 || streamHealthMetrics.hlsErrors >= 3) {
            status = "Poor stability";
          } else if (streamHealthMetrics.stalls > 0 || streamHealthMetrics.hlsErrors > 0) {
            status = "Minor glitches";
          } else {
            status = "Stable";
          }
        }

        setStreamHealthText("streamHealthStatus", status);
        setStreamHealthText("streamHealthElapsed", Math.round(elapsedMs / 1000) + "s");
        setStreamHealthText("streamHealthStalls", String(streamHealthMetrics.stalls));
        setStreamHealthText("streamHealthBufferTime", (streamHealthMetrics.totalBufferMs / 1000).toFixed(1) + "s");
        setStreamHealthText("streamHealthLongest", (streamHealthMetrics.longestBufferMs / 1000).toFixed(1) + "s");
        setStreamHealthText("streamHealthAhead", avgAhead === null ? "—" : avgAhead.toFixed(1) + "s");
        setStreamHealthText("streamHealthHlsErrors", String(streamHealthMetrics.hlsErrors));
        setStreamHealthText("streamHealthSwitches", String(streamHealthMetrics.switches));
        setStreamHealthText("streamHealthSwitchTime", avgSwitch === null ? "—" : (avgSwitch / 1000).toFixed(1) + "s");

        const currentFrames = getVideoFrameSnapshot();
        if (streamHealthFrameStart && currentFrames) {
          const dropped = Math.max(0, currentFrames.dropped - streamHealthFrameStart.dropped);
          const total = Math.max(0, currentFrames.total - streamHealthFrameStart.total);
          setStreamHealthText("streamHealthDropped", dropped + " / " + total);
        } else {
          setStreamHealthText("streamHealthDropped", "—");
        }
      }

      function finishStreamHealthBuffer() {
        if (!streamHealthRunning || !streamHealthBufferStartedAt) return;
        const duration = Math.max(0, Date.now() - streamHealthBufferStartedAt);
        streamHealthBufferStartedAt = 0;
        streamHealthMetrics.totalBufferMs += duration;
        streamHealthMetrics.longestBufferMs = Math.max(streamHealthMetrics.longestBufferMs, duration);
        logStreamHealth("Playback resumed after " + (duration / 1000).toFixed(2) + "s buffering");
      }

      function onStreamHealthWaiting() {
        if (!streamHealthRunning || streamHealthBufferStartedAt) return;
        streamHealthBufferStartedAt = Date.now();
        streamHealthMetrics.stalls += 1;
        logStreamHealth("Playback waiting / buffering");
        updateStreamHealthDisplay();
      }

      function onStreamHealthStalled() {
        if (!streamHealthRunning) return;
        if (!streamHealthBufferStartedAt) {
          streamHealthBufferStartedAt = Date.now();
          streamHealthMetrics.stalls += 1;
        }
        logStreamHealth("Media stalled event");
        updateStreamHealthDisplay();
      }

      function onStreamHealthPlaying() {
        if (!streamHealthRunning) return;
        finishStreamHealthBuffer();
        if (streamHealthPendingSwitchAt) {
          const duration = Date.now() - streamHealthPendingSwitchAt;
          streamHealthPendingSwitchAt = 0;
          streamHealthMetrics.switchTimes.push(duration);
          logStreamHealth("DJ switch playing in " + (duration / 1000).toFixed(2) + "s");
        }
        updateStreamHealthDisplay();
      }

      function onStreamHealthMediaError() {
        if (!streamHealthRunning) return;
        logStreamHealth("HTML video error");
      }

      function onStreamHealthHlsError(event, data) {
        if (!streamHealthRunning) return;
        streamHealthMetrics.hlsErrors += 1;
        const detail = data && (data.details || data.type)
          ? String(data.details || data.type)
          : "unknown";
        logStreamHealth("HLS error: " + detail + (data && data.fatal ? " (fatal)" : ""));
        updateStreamHealthDisplay();
      }

      function observeCurrentHlsForHealth() {
        if (!streamHealthRunning || !window.Hls) return;
        const current = window.radioRrrHls || null;
        if (current === streamHealthObservedHls) return;
        if (streamHealthObservedHls && typeof streamHealthObservedHls.off === "function") {
          try { streamHealthObservedHls.off(Hls.Events.ERROR, onStreamHealthHlsError); } catch (e) {}
        }
        streamHealthObservedHls = current;
        if (streamHealthObservedHls && typeof streamHealthObservedHls.on === "function") {
          streamHealthObservedHls.on(Hls.Events.ERROR, onStreamHealthHlsError);
        }
      }

      function onStreamHealthDjClick(event) {
        if (!streamHealthRunning) return;
        const card = event.target && event.target.closest
          ? event.target.closest("#liveDjsList .dj-live-switch-card")
          : null;
        if (!card) return;
        streamHealthMetrics.switches += 1;
        streamHealthPendingSwitchAt = Date.now();
        const name = card.querySelector(".dj-secondary-name");
        logStreamHealth("DJ switch requested" + (name ? ": " + name.textContent.trim() : ""));
        updateStreamHealthDisplay();
      }

      function streamHealthSample() {
        if (!streamHealthRunning) return;
        observeCurrentHlsForHealth();
        const ahead = getCurrentBufferAhead();
        if (ahead !== null && Number.isFinite(ahead)) {
          streamHealthMetrics.bufferAheadSamples.push(ahead);
        }
        updateStreamHealthDisplay();
      }

      function stopStreamHealthTest(completed) {
        if (!streamHealthRunning) return;
        finishStreamHealthBuffer();
        streamHealthRunning = false;
        if (streamHealthTimer) {
          clearInterval(streamHealthTimer);
          streamHealthTimer = null;
        }
        if (streamHealthStopTimer) {
          clearTimeout(streamHealthStopTimer);
          streamHealthStopTimer = null;
        }
        if (liveDjVideo) {
          liveDjVideo.removeEventListener("waiting", onStreamHealthWaiting);
          liveDjVideo.removeEventListener("stalled", onStreamHealthStalled);
          liveDjVideo.removeEventListener("playing", onStreamHealthPlaying);
          liveDjVideo.removeEventListener("error", onStreamHealthMediaError);
        }
        document.removeEventListener("click", onStreamHealthDjClick, true);
        if (streamHealthObservedHls && window.Hls && typeof streamHealthObservedHls.off === "function") {
          try { streamHealthObservedHls.off(Hls.Events.ERROR, onStreamHealthHlsError); } catch (e) {}
        }
        streamHealthObservedHls = null;
        if (streamHealthStart) streamHealthStart.disabled = false;
        if (streamHealthStop) streamHealthStop.disabled = true;
        updateStreamHealthDisplay(completed ? "Complete" : "Stopped");
        logStreamHealth(completed ? "60-second test complete" : "Test stopped");
      }

      function resetStreamHealthTest() {
        if (streamHealthRunning) stopStreamHealthTest(false);
        streamHealthStartedAt = 0;
        streamHealthBufferStartedAt = 0;
        streamHealthPendingSwitchAt = 0;
        streamHealthFrameStart = null;
        streamHealthMetrics = newStreamHealthMetrics();
        if (streamHealthLog) {
          streamHealthLog.innerHTML = '<div class="stream-health-empty">Start the test, then use Radio RRR normally or switch DJs. Monitoring is inactive while the test is stopped.</div>';
        }
        updateStreamHealthDisplay("Ready");
      }

      function startStreamHealthTest() {
        if (streamHealthRunning || !liveDjVideo) return;
        resetStreamHealthTest();
        streamHealthRunning = true;
        streamHealthStartedAt = Date.now();
        streamHealthFrameStart = getVideoFrameSnapshot();
        if (streamHealthStart) streamHealthStart.disabled = true;
        if (streamHealthStop) streamHealthStop.disabled = false;
        liveDjVideo.addEventListener("waiting", onStreamHealthWaiting);
        liveDjVideo.addEventListener("stalled", onStreamHealthStalled);
        liveDjVideo.addEventListener("playing", onStreamHealthPlaying);
        liveDjVideo.addEventListener("error", onStreamHealthMediaError);
        document.addEventListener("click", onStreamHealthDjClick, true);
        observeCurrentHlsForHealth();
        logStreamHealth("Test started — passive monitoring only");
        streamHealthSample();
        streamHealthTimer = setInterval(streamHealthSample, 1000);
        streamHealthStopTimer = setTimeout(function () {
          stopStreamHealthTest(true);
        }, STREAM_HEALTH_TEST_MS);
      }

      streamHealthMetrics = newStreamHealthMetrics();
      if (streamHealthStart) streamHealthStart.addEventListener("click", startStreamHealthTest);
      if (streamHealthStop) streamHealthStop.addEventListener("click", function () { stopStreamHealthTest(false); });
      if (streamHealthReset) streamHealthReset.addEventListener("click", resetStreamHealthTest);
      updateStreamHealthDisplay("Ready");

      function setStatus(msg) {

        if (!statusEl) return;

        statusEl.style.display =
          "block";

        statusEl.textContent =
          "Status: " + msg;

      }

      // Capture is a passive tap: native station playback never enters this graph.
      let capturedAudio = null;
      let capturedSource = null;
      let spectrumFrame = 0;
      let spectrumVisible = false;
      const reducedSpectrumMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
      const barLevels = new Float32Array(64);

      function initAudioAnalyser() {
        if (!audio || !ctx) return;
        const capture = audio.captureStream || audio.mozCaptureStream;
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!capture || !AC) return;
        try {
          if (!audioCtx) {
            audioCtx = new AC();
            analyser = audioCtx.createAnalyser();
            analyser.fftSize = 2048;
            analyser.smoothingTimeConstant = 0.86;
            analyser.minDecibels = -85;
            analyser.maxDecibels = -20;
            dataArray = new Uint8Array(analyser.frequencyBinCount);
            // A silent sink keeps analysis active without duplicating station sound.
            const silentSink = audioCtx.createGain();
            silentSink.gain.value = 0;
            analyser.connect(silentSink);
            silentSink.connect(audioCtx.destination);
            audioCtx.addEventListener("statechange", updateSpectrum);
          }
          if (!capturedAudio) {
            capturedAudio = capture.call(audio);
            capturedAudio.addEventListener("addtrack", initAudioAnalyser);
          }
          if (!capturedSource && capturedAudio.getAudioTracks().length) {
            capturedSource = audioCtx.createMediaStreamSource(capturedAudio);
            capturedSource.connect(analyser);
            analyserReady = true;
          }
          if (audioCtx.state === "suspended") audioCtx.resume().catch(updateSpectrum);
          updateSpectrum();
        } catch (error) {
          analyserReady = false;
          updateSpectrum();
        }
      }

      function resize() {
        if (!canvas || !ctx) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(canvas.clientWidth * dpr);
        canvas.height = Math.round(canvas.clientHeight * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }

      function updateSpectrum() {
        cancelAnimationFrame(spectrumFrame);
        spectrumFrame = 0;
        if (!ctx || !canvas) return;
        ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
        barLevels.fill(0);
        if (spectrumVisible && !document.hidden && !reducedSpectrumMotion.matches &&
            audio && !audio.paused && !audio.ended && !audio.error && audio.readyState >= 3 &&
            analyserReady && audioCtx.state === "running") {
          spectrumFrame = requestAnimationFrame(drawFrame);
        }
      }

      function drawFrame() {
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        ctx.clearRect(0, 0, w, h);
        try {
          analyser.getByteFrequencyData(dataArray);
          const left = w * 0.045;
          const step = w * 0.91 / barLevels.length;
          const baseline = h * 0.9;
          const binHz = audioCtx.sampleRate / analyser.fftSize;
          const highHz = Math.min(16000, audioCtx.sampleRate / 2);
          const gradient = ctx.createLinearGradient(left, 0, w - left, 0);
          gradient.addColorStop(0, "#22d3ee");
          gradient.addColorStop(0.36, "#5876ff");
          gradient.addColorStop(0.68, "#a56bfa");
          gradient.addColorStop(1, "#f044bd");
          ctx.fillStyle = gradient;
          ctx.shadowColor = "rgba(130, 90, 255, 0.45)";
          ctx.shadowBlur = 7;
          for (let i = 0; i < barLevels.length; i++) {
            const start = Math.max(1, Math.floor(40 * Math.pow(highHz / 40, i / 64) / binHz));
            const end = Math.min(dataArray.length, Math.max(start + 1,
              Math.ceil(40 * Math.pow(highHz / 40, (i + 1) / 64) / binHz)));
            let energy = 0;
            for (let j = start; j < end; j++) energy += dataArray[j];
            const level = energy / Math.max(1, end - start) / 255;
            barLevels[i] += (level - barLevels[i]) * 0.24;
            const height = barLevels[i] * h * 0.8;
            if (height > 0.5) ctx.fillRect(left + i * step, baseline - height,
              Math.max(1, step * 0.7), height);
          }
          ctx.shadowBlur = 0;
          spectrumFrame = requestAnimationFrame(drawFrame);
        } catch (error) {
          analyserReady = false;
          updateSpectrum();
        }
      }

      if (canvas && ctx) {
        resize();
        if (window.ResizeObserver) {
          new ResizeObserver(resize).observe(canvas);
        } else {
          window.addEventListener("resize", resize);
        }
        if (window.IntersectionObserver) {
          new IntersectionObserver(function (entries) {
            spectrumVisible = entries[0].isIntersecting;
            resize();
            updateSpectrum();
          }).observe(canvas);
        } else {
          spectrumVisible = true;
        }
        document.addEventListener("visibilitychange", updateSpectrum);
        reducedSpectrumMotion.addEventListener("change", updateSpectrum);
        if (audio) {
          audio.addEventListener("playing", initAudioAnalyser);
          ["pause", "ended", "error", "waiting", "emptied"].forEach(function (event) {
            audio.addEventListener(event, updateSpectrum);
          });
        }
      }

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
                audioCtx.resume().catch(updateSpectrum);

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
              audioCtx.resume().catch(updateSpectrum);
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

// Live DJ spectrum: observe the existing player; never set its playback state.
document.addEventListener("DOMContentLoaded", function () {
  const video = document.getElementById("liveDjVideo");
  const canvas = document.getElementById("liveDjSpectrum");
  const unmute = document.getElementById("liveDjUnmute");
  const AC = window.AudioContext || window.webkitAudioContext;
  const paint = canvas && canvas.getContext("2d");
  if (!video || !paint || !AC) return;

  let context, source, analyser, bins;
  let attempted = false;
  let busy = false;
  let failed = false;
  let visible = false;
  let timer = 0;
  const colors = paint.createLinearGradient(0, canvas.height, 0, 0);
  colors.addColorStop(0, "#22d3ee");
  colors.addColorStop(0.5, "#a855f7");
  colors.addColorStop(1, "#ff0066");

  function stop() {
    clearTimeout(timer);
    timer = 0;
    canvas.style.visibility = "hidden";
    paint.clearRect(0, 0, canvas.width, canvas.height);
  }

  function draw() {
    stop();
    if (failed || !analyser || !visible || document.hidden ||
        context.state !== "running" || video.paused || video.ended ||
        video.error || video.readyState < 3) return;
    try {
      analyser.getByteFrequencyData(bins);
      paint.fillStyle = colors;
      // Fixed-size canvas and 32 logarithmic bands; no per-frame allocation.
      for (let bar = 0; bar < 32; bar++) {
        const start = Math.floor(Math.pow(bins.length, bar / 32));
        const end = Math.min(bins.length,
          Math.max(start + 1, Math.floor(Math.pow(bins.length, (bar + 1) / 32))));
        let level = 0;
        for (let bin = start; bin < end; bin++) level = Math.max(level, bins[bin]);
        // Twelve discrete LEDs per band, with faint unlit segments like a mixer.
        const litSegments = Math.round(level / 255 * 12);
        for (let segment = 0; segment < 12; segment++) {
          paint.globalAlpha = segment < litSegments ? 1 : 0.08;
          paint.fillRect(bar * 20 + 3, canvas.height - (segment + 1) * 12, 14, 8);
        }
      }
      paint.globalAlpha = 1;
      canvas.style.visibility = "visible";
      timer = window.setTimeout(draw, 40);
    } catch (_) {
      // Analysis failure must not disconnect the independent audible path.
      failed = true;
      stop();
    }
  }

  async function activate(event) {
    if (!event.isTrusted || busy || (failed && !source)) return;
    busy = true;
    try {
      if (!context) {
        context = new AC();
        context.addEventListener("statechange", draw);
      }
      // Do not reroute native audio until a playback gesture unlocks Web Audio.
      if (context.state !== "running") await context.resume();
      if (context.state !== "running") return;
      if (!attempted) {
        const capture = video.captureStream || video.mozCaptureStream;
        if (!capture) {
          failed = true;
          stop();
          return;
        }

        analyser = context.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.8;
        bins = new Uint8Array(analyser.frequencyBinCount);

        // Analyse a passive capture of the video instead of routing the
        // video's audible output through Web Audio. Browsers may suspend an
        // AudioContext in a background tab; native video audio must continue
        // independently when that happens.
        const capturedStream = capture.call(video);
        source = context.createMediaStreamSource(capturedStream);
        source.connect(analyser);

        // Keep the analysis graph active without creating an audible path.
        const silentSink = context.createGain();
        silentSink.gain.value = 0;
        analyser.connect(silentSink);
        silentSink.connect(context.destination);

        attempted = true;
      }
      draw();
    } catch (_) {
      // A blocked resume remains retryable on the next playback gesture.
      if (attempted) failed = true;
      stop();
    } finally {
      busy = false;
    }
  }

  if (unmute) unmute.addEventListener("click", activate);
  video.addEventListener("click", activate);
  // Native media controls can consume clicks; observe their unmute/volume change.
  video.addEventListener("volumechange", function (event) {
    if (!video.muted && video.volume > 0) activate(event);
  });
  video.addEventListener("keydown", function (event) {
    if (event.key === " " || event.key === "Enter") activate(event);
  });
  ["playing", "canplay"].forEach(type => video.addEventListener(type, draw));
  ["pause", "ended", "waiting", "emptied", "error"].forEach(type =>
    video.addEventListener(type, stop));
  document.addEventListener("visibilitychange", draw);
  // Hiding the card stops drawing, never suspends the audible context.
  if (window.IntersectionObserver) {
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      draw();
    }).observe(canvas);
  } else {
    visible = true;
  }
});

// Count primary-player playback only; the decorative background is excluded.
document.addEventListener("DOMContentLoaded", function () {
  const video = document.getElementById("liveDjVideo");
  if (!video || !window.crypto || !window.crypto.randomUUID) return;
  let viewerId = crypto.randomUUID();
  try {
    const saved = localStorage.getItem("rrrVideoViewerId");
    if (saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(saved)) viewerId = saved;
    else localStorage.setItem("rrrVideoViewerId", viewerId);
  } catch (_) { /* Storage-blocked browsers count per page. */ }
  const sessionId = crypto.randomUUID();
  let lastTime = video.currentTime;
  let active = false;
  let leaving = false;
  let queue = Promise.resolve();

  function report(playing) {
    if (!playing && !active) return;
    active = playing;
    const body = JSON.stringify({ viewer_id: viewerId, session_id: sessionId, active: playing });
    // Preserve start/stop ordering so a late start cannot undo a pause.
    queue = queue.then(function () {
      return fetch("https://api.radiorrr.com/api/video-viewers/heartbeat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: body, credentials: "omit", keepalive: true,
        signal: AbortSignal.timeout(10000)
      });
    }).catch(function () { /* Counting must never affect playback. */ });
  }

  function canCount() {
    return !leaving && !video.paused && !video.ended && !video.error && video.readyState >= 3;
  }
  video.addEventListener("playing", function () {
    lastTime = video.currentTime;
    if (canCount()) report(true);
  });
  ["pause", "ended", "waiting", "emptied", "error"].forEach(function (event) {
    video.addEventListener(event, function () { report(false); });
  });
  window.setInterval(function () {
    const progressed = video.currentTime !== lastTime;
    lastTime = video.currentTime;
    report(canCount() && progressed);
  }, 20000);
  window.addEventListener("pagehide", function () { leaving = true; report(false); });
  window.addEventListener("pageshow", function () {
    leaving = false;
    lastTime = video.currentTime;
    if (canCount()) report(true);
  });
});
