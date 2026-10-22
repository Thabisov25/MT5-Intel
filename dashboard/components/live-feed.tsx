"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  FeedContext,
  FEED_POLL_MS,
  useLiveFeed,
  type AutoView,
  type FeedState,
  type LiveFeedValue,
} from "@/components/live-feed-context";
import { useVisiblePoll } from "@/components/poll";
import type { Forecast } from "@/lib/forecast";
import type { LiveStatus } from "@/lib/live";
import type { Setups, Timeframe } from "@/lib/setups";

/**
 * One clock for the whole Live page, so "the heartbeat" is a thing the reader can watch.
 *
 * The three blocks on that page used to poll themselves: the strip and the switch every 10 s, the
 * per-rule table every 30 s. So two heartbeats in three painted a strip that had moved and a table
 * that had not, and a payload the watcher had already rewritten sat on screen for up to half a
 * minute - which reads as a page that is partly updating, which is how it was reported.
 *
 * Here the three payloads are fetched together, on one interval, and committed to state in the same
 * render. Nothing else about them changed: they are the same three route handlers, read from the
 * same files, and the panels still render what the server sent them first. The shared interval also
 * means the return to this tab is a refresh, rather than a wait for a throttled timer.
 *
 * The one guarantee this keeps beyond "they arrive together": a slow answer from an older check
 * never overwrites a newer one, because each check carries a sequence number and only the newest is
 * allowed to land. That matters on this page more than most, because the payloads are three
 * separate files written by three separate processes and any of them can be mid-replace.
 */
export function LiveFeedProvider({
  live,
  setups,
  symbol,
  timeframe,
  auto,
  forecast: initialForecast,
  children,
}: {
  live: LiveStatus;
  setups: Setups | null;
  /** The page's own `?symbol=` choice, so the polls fetch the same market the server rendered. */
  symbol: string | null;
  /** The page's own `?tf=` choice - one named frame's artifacts, polled as such. */
  timeframe: Timeframe;
  auto: AutoView;
  forecast: Forecast | null;
  children: ReactNode;
}) {
  const [state, setState] = useState<FeedState>({
    live,
    setups,
    symbol,
    timeframe,
    auto,
    forecast: initialForecast,
    checkedAtMs: null,
    pending: false,
    failed: false,
  });
  const seq = useRef(0);

  const load = useCallback(() => {
    const mine = ++seq.current;
    setState((current) => ({ ...current, pending: true }));
    const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    const frame = `${query ? "&" : "?"}tf=${timeframe.toLowerCase()}`;
    void Promise.all([
      fetch("/api/live", { cache: "no-store" }),
      fetch(`/api/setups${query}${frame}`, { cache: "no-store" }),
      fetch("/api/auto", { cache: "no-store" }),
      fetch(`/api/forecast${query}`, { cache: "no-store" }),
    ])
      .then(async ([liveRes, setupsRes, autoRes, forecastRes]) => {
        const [nextLive, nextSetups, nextAuto, nextForecast] = await Promise.all([
          liveRes.ok ? (liveRes.json() as Promise<LiveStatus | null>) : null,
          setupsRes.ok ? (setupsRes.json() as Promise<Setups | null>) : null,
          autoRes.ok ? (autoRes.json() as Promise<Partial<AutoView>>) : null,
          forecastRes.ok ? (forecastRes.json() as Promise<Forecast | null>) : null,
        ]);
        if (mine !== seq.current) return;
        setState((current) => ({
          live: nextLive ?? current.live,
          setups: nextSetups ?? current.setups,
          symbol: current.symbol,
          timeframe: current.timeframe,
          auto: nextAuto
            ? {
                status: nextAuto.status ?? current.auto.status,
                toggle: nextAuto.toggle ?? current.auto.toggle,
                selection: nextAuto.selection ?? current.auto.selection,
              }
            : current.auto,
          forecast: nextForecast ?? current.forecast,
          checkedAtMs: Date.now(),
          pending: false,
          // One payload failing does not throw the others away: a half-updated page that says so
          // is more useful than a blank one, and the next check is ten seconds away.
          failed: !liveRes.ok || !setupsRes.ok || !autoRes.ok || !forecastRes.ok,
        }));
      })
      .catch(() => {
        if (mine !== seq.current) return;
        // The last good reading stays on screen: a console that blanks itself on one failed read
        // is less useful than one that says the read failed.
        setState((current) => ({ ...current, pending: false, failed: true }));
      });
  }, [symbol, timeframe]);

  useVisiblePoll(load, FEED_POLL_MS);

  const setAuto = useCallback((update: (current: AutoView) => AutoView) => {
    setState((current) => ({ ...current, auto: update(current.auto) }));
  }, []);

  const value = useMemo<LiveFeedValue>(
    () => ({ ...state, setAuto, refresh: load }),
    [state, setAuto, load],
  );

  return <FeedContext.Provider value={value}>{children}</FeedContext.Provider>;
}

/** "3 s" past a minute, "1 m" past that: enough to tell a quiet market from a stopped console. */
function shortAge(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`;
  return `${Math.round(seconds / 60)} m`;
}

/**
 * "checked 3 s ago", ticking every second.
 *
 * The point of it is that a live page has to be visibly alive between writes: the watcher's own
 * cycle is 30 s and its artifacts move on their own timers, so without a clock of the reader's own
 * the page sits perfectly still for long stretches and there is no way to tell a quiet market from
 * a stopped console. It counts from the last check rather than from the last *change*, so it also
 * says when the console itself has lost the watcher.
 */
export function FeedClock() {
  const { checkedAtMs, pending, failed } = useLiveFeed();
  const [nowMs, setNowMs] = useState<number | null>(null);

  // One beat at a time rather than one interval: the first beat is due now (so the clock starts at
  // 0 s instead of a second of "reading…"), and each beat schedules the next, which stops cleanly
  // on unmount. The reader's own clock is the only thing here that ticks - the payloads behind it
  // arrive on the feed's tick, and this is what makes the difference between them visible.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const beat = () => {
      setNowMs(Date.now());
      timer = setTimeout(beat, 1000);
    };
    timer = setTimeout(beat, 0);
    return () => clearTimeout(timer);
  }, []);

  if (failed) {
    return <span className="pill pill-warn">last check failed</span>;
  }
  if (checkedAtMs === null || nowMs === null) {
    return <span className="muted">reading the watcher…</span>;
  }
  const ageS = Math.max(0, (nowMs - checkedAtMs) / 1000);
  return (
    <span className="muted">
      {pending ? "checking" : "checked"} {shortAge(ageS)} ago
    </span>
  );
}
