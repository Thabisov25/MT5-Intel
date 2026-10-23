/**
 * The parts of the replay module that are safe to ship to the browser: constants and the
 * family whitelist. `lib/replay.ts` reads artifacts from disk (`node:fs`), which cannot be
 * bundled into a client component's chunk — so anything a "use client" panel needs lives
 * here, and the reader re-exports it to keep one name for every server-side consumer.
 */

/** A replay older than this is refreshed on page load rather than drawn as current. */
export const REPLAY_STALE_S = 6 * 3600;

/** Same whitelist the exporter and the API routes enforce — the family names a file. */
export const REPLAY_FAMILIES = [
  "sweep_reclaim",
  "carry_trend",
  "compression_breakout",
  "trend_pullback",
  "range_extreme_mr",
  "daily_momentum",
  "ma_crossover",
  "rsi_mean_reversion",
  "sr_flip",
  "pullback_scalp",
  "orb_london",
  "asian_fade",
] as const;

export type ReplayFamily = (typeof REPLAY_FAMILIES)[number];

export function isReplayFamily(value: string): value is ReplayFamily {
  return (REPLAY_FAMILIES as readonly string[]).includes(value);
}

/**
 * The markets a replay can be built for, in tab order.
 *
 * Same whitelist the exporter and the API routes enforce. Both are markets the journal
 * filters by, and the strategy page is read the same way: one market at a time, with the
 * choice carried in the URL so a EURUSD view is actually about EURUSD.
 */
export const REPLAY_SYMBOLS = ["USDJPY", "EURUSD"] as const;

export type ReplaySymbol = (typeof REPLAY_SYMBOLS)[number];

export function isReplaySymbol(value: string): value is ReplaySymbol {
  return (REPLAY_SYMBOLS as readonly string[]).includes(value);
}

export interface ReplayBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export type ReplayHit = "tp" | "sl" | "time";

export interface ReplayTrade {
  side: "buy" | "sell";
  signal_time: number;
  entry_time: number;
  exit_time: number;
  entry: number;
  sl: number;
  tp: number;
  /** Where the trade actually ended: the barrier price, or the closing price on a time exit. */
  exit: number;
  hit: ReplayHit;
  /** True while the hold window has not elapsed — drawn neutral, like a time exit. */
  still_open: boolean;
  /** Net R after costs, or null while the trade is still running. */
  net_r: number | null;
}

export interface ReplaySummary {
  n: number;
  tp: number;
  sl: number;
  time: number;
  open: number;
  net_r_mean: number | null;
  net_r_sum: number | null;
}

export interface FamilyReplay {
  generated_utc: string;
  symbol: string;
  timeframe: string;
  digits: number | null;
  bars_counted: number;
  max_hold_bars: number;
  family: string;
  bars: ReplayBar[];
  trades: ReplayTrade[];
  summary: ReplaySummary;
}
