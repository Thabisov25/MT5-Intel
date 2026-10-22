"use client";

import { useEffect } from "react";

/**
 * Two wake events arrive together: returning to a tab fires `focus`, `visibilitychange` and
 * sometimes `pageshow`, all within the same instant, and a scheduled tick can land on top of them.
 * This is the window in which they are one refresh rather than three round trips.
 */
const WAKE_DEDUPE_MS = 400;

/**
 * The one interval every auto-refreshing panel on this console uses.
 *
 * Two rules, and the second one is the point:
 *
 * 1. It fires on schedule, always. Browsers throttle a hidden tab's timers, so a panel that used a
 *    plain interval came back from the background with numbers from before it was hidden and with
 *    its next tick up to a minute away - which is read as a console that has stopped keeping up.
 * 2. Coming back is a refresh of its own. `focus`, `visibilitychange` and `pageshow` all mean the
 *    page is in front of someone again, and every one of them fires a refresh immediately rather
 *    than waiting for the timer to catch up. They arrive in bursts (a return fires two or three of
 *    them), so a loader has to tolerate several calls in the same instant.
 *
 * It deliberately never asks `document.hidden` whether it is allowed to fire. Skipping ticks behind
 * that flag looks like free efficiency and is not: an embedded webview that is not being composited
 * reports `hidden` while it is on screen in front of the reader, and a panel that trusted it stopped
 * refreshing entirely inside exactly that window. A live console that is quietly stale is a worse
 * failure than one doing a little work in a tab nobody is watching, and a genuinely hidden tab is
 * throttled by the browser anyway - which is what rule 2 covers.
 *
 * `refresh` must be stable (a `useCallback`, or a function that does not change identity), because
 * a new one on every render would restart the interval and it would never reach its interval.
 *
 * A burst of wake events is one refresh, not three: returning to a tab fires `focus`,
 * `visibilitychange` and sometimes `pageshow` in the same instant, and a scheduled tick can land on
 * top of them.
 */
export function useVisiblePoll(refresh: () => void, intervalMs: number): void {
  useEffect(() => {
    let lastRun = 0;
    const run = () => {
      const now = Date.now();
      if (now - lastRun < WAKE_DEDUPE_MS) return;
      lastRun = now;
      refresh();
    };
    run();
    const timer = setInterval(run, intervalMs);
    document.addEventListener("visibilitychange", run);
    window.addEventListener("focus", run);
    window.addEventListener("pageshow", run);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", run);
      window.removeEventListener("focus", run);
      window.removeEventListener("pageshow", run);
    };
  }, [refresh, intervalMs]);
}
