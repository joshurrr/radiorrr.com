def main():
    start_engine_heartbeat()
    print("Radio RRR Candidate Audio Scout", flush=True)
    print(f"Router: {RADIO_ROUTER_URL}", flush=True)
    print(f"Sample: {SAMPLE_SECONDS}s", flush=True)
    print(
        "Mode: sequential live-DJ scan; current relay skipped"
        if SKIP_CURRENT_RELAY
        else "Mode: sequential live-DJ scan; current relay included",
        flush=True,
    )
    print("", flush=True)

    cycle = 0

    while True:
        try:
            cycle += 1
            priority_scanned = scan_priority_djs()
            live_djs = get_live_djs()
            current = get_current_relay_identity()

            if SKIP_CURRENT_RELAY and current:
                scan_djs = [
                    dj for dj in live_djs
                    if (
                        (str(dj.get("platform") or "TikTok").casefold(),
                         dj["username"].casefold())
                        != current
                    )
                ]
            else:
                scan_djs = live_djs

            if not scan_djs:
                print(
                    "[Scout] No candidate DJs available; "
                    "waiting...",
                    flush=True,
                )
                time.sleep(30)
                continue

            print(
                f"[Scout] === Cycle {cycle}: "
                f"{len(live_djs)} live, "
                f"{len(scan_djs)} candidates ===",
                flush=True,
            )

            for index, dj in enumerate(scan_djs, start=1):
                priority_scanned.update(scan_priority_djs())
                if (str(dj.get("platform") or "TikTok").casefold(), dj["username"].casefold()) in priority_scanned:
                    continue

                # Re-check the live list before each expensive sample so
                # DJs who have gone offline are not unnecessarily analysed.
                try:
                    current_live = get_live_djs()
                    live_keys = {
                        (
                            str(x.get("platform") or "TikTok").casefold(),
                            x["username"].casefold(),
                        )
                        for x in current_live
                    }
                    dj_key = (
                        str(dj.get("platform") or "TikTok").casefold(),
                        dj["username"].casefold(),
                    )
                    if dj_key not in live_keys:
                        print(
                            f"[Scout] Skipping "
                            f"{dj.get('platform') or 'TikTok'} @{dj['username']} "
                            "— no longer live",
                            flush=True,
                        )
                        continue

                    # If the DJ has become the main relay while we were
                    # scanning, leave the current relay to the fast detector.
                    current = get_current_relay_identity()
                    if (
                        SKIP_CURRENT_RELAY
                        and current
                        and dj_key == current
                    ):
                        print(
                            f"[Scout] Skipping "
                            f"{dj.get('platform') or 'TikTok'} @{dj['username']} "
                            "— now current relay",
                            flush=True,
                        )
                        continue

                    print(
                        f"[Scout] {index}/{len(scan_djs)} "
                        f"{dj.get('platform') or 'TikTok'} @{dj['username']}",
                        flush=True,
                    )

                    results, bpm_result, speech_result = analyse_dj(dj)
                    post_results(dj, results, bpm_result, speech_result)
                    print_results(dj, results, bpm_result, speech_result)

                except Exception as error:
                    print(
                        f"[Scout] Analysis failed for "
                        f"{dj.get('platform') or 'TikTok'} @{dj['username']}: "
                        f"{type(error).__name__}: {error}",
                        flush=True,
                    )
                finally:
                    # The per-DJ relay is only needed for this sample.
                    # Release it immediately so the next candidate does
                    # not accumulate another FFmpeg process.
                    release_relay(dj)

                time.sleep(SCAN_IDLE_SECONDS)

            print(
                f"[Scout] === Cycle {cycle} complete ===",
                flush=True,
            )

        except KeyboardInterrupt:
            print("\nStopping candidate scout.", flush=True)
            break
        except Exception as error:
            print(
                f"[Scout] Cycle error: {type(error).__name__}: {error}",
                flush=True,
            )
            time.sleep(15)