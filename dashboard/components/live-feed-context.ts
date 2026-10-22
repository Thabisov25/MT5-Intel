"use client";

import { createContext, useContext } from "react";
import type { AutoSelection, AutoStatus } from "@/lib/auto";
import type { Forecast } from "@/lib/forecast";
import type { LiveStatus } from "@/lib/live";
import type { Setups, Timeframe } from "@/lib/setups";

/**
 * The shape of the Live page's feed, and the context it is read through.
 *
 * This is a module of its own, with no component in it, for one reason: Fast Refresh only updates a
 * module in place when every export from it is a component. Exporting the hook beside the provider
 * makes the dev server fall back to a full reload of the page on every edit, and Next says so out
 * loud in the console. Splitting the two is the fix it recommends.
 *
 * The feed itself - one tick, three payloads, committed together - is in `live-feed.tsx`.
 *
 * `AutoView` is the one payload that is not simply what `/api/auto` returns. Two different things
 * are called "the switch": the artifact `auto.json` carries the *resolved* state the watcher last
 * computed from the .env master and the runtime file, and `auto-switch.json` carries the runtime
 * toggle the panel owns. Reading only the artifact is why arming the switch looked like it did not
 * take: for up to a cycle the knob snapped back to the value the watcher had not yet overwritten.
 * Both are kept here, and the panel reads the toggle for the control and the artifact for the
 * account of it.
 */
export const FEED_POLL_MS = 10_000;

export interface AutoView {
  /** The watcher's artifact: what the last cycle did, and why. */
  status: AutoStatus | null;
  /** The runtime switch file this console writes, read back as it is on disk. */
  toggle: { enabled: boolean; reason?: string; updatedUtc?: string } | null;
  /** The saved strategy selection, or null when none has been saved from this page. */
  selection: AutoSelection | null;
}

export interface FeedState {
  live: LiveStatus;
  setups: Setups | null;
  /** The `?symbol=` choice the page was rendered with; the polls give it to the same routes. */
  symbol: string | null;
  /** The `?tf=` choice rendered with, for the same reason the symbol is here. */
  timeframe: Timeframe;
  auto: AutoView;
  /** The ranked strategy forecast, built from the setups and the labelled journal on one tick. */
  forecast: Forecast | null;
  /** When the last full set of payloads landed, on the reader's own clock. */
  checkedAtMs: number | null;
  /** A request is in flight. */
  pending: boolean;
  /** The last check did not come back, so what is on screen is the last good reading. */
  failed: boolean;
}

export interface LiveFeedValue extends FeedState {
  setAuto: (update: (current: AutoView) => AutoView) => void;
  /** Check now, without waiting for the tick. */
  refresh: () => void;
}

export const FeedContext = createContext<LiveFeedValue | null>(null);

export function useLiveFeed(): LiveFeedValue {
  const value = useContext(FeedContext);
  if (!value) {
    throw new Error("useLiveFeed must be used inside <LiveFeedProvider>.");
  }
  return value;
}
