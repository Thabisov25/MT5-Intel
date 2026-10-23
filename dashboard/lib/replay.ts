import { readFile } from "node:fs/promises";
import path from "node:path";
import {
  isReplayFamily,
  isReplaySymbol,
  type FamilyReplay,
  type ReplayBar,
  type ReplayTrade,
} from "./replay-constants";

/**
 * The family replay artifact, as `tools/family_replay_export.py` writes it: the bars
 * a family was graded on and one graded row per taken trade, so the strategy page can
 * draw *when a trade could have been taken and how it ended*.
 *
 * Every trade here came out of the research core's own pinned trio (`one_at_a_time`
 * for which signals became trades, `walk_trade` for where they ended, `closed_r` for
 * their net R) — the same calls the Live tab and the research reports make — so a box
 * on this chart is the same trade the rest of the console shows.
 *
 * The types and constants live in `replay-constants.ts`, which carries no `node:fs`
 * import: this module reads disk, so anything a client component needs is re-exported
 * from there instead of here.
 */

export {
  REPLAY_FAMILIES,
  REPLAY_STALE_S,
  REPLAY_SYMBOLS,
  isReplayFamily,
  isReplaySymbol,
  type FamilyReplay,
  type ReplayBar,
  type ReplayFamily,
  type ReplayHit,
  type ReplaySummary,
  type ReplaySymbol,
  type ReplayTrade,
} from "./replay-constants";

/** The market a page asks for when it does not name one: the watcher's primary. */
export const DEFAULT_REPLAY_SYMBOL = "USDJPY";

export function replayDir(): string {
  return process.env.MT5_REPLAY_DIR ?? path.resolve(process.cwd(), "..", "dashboard");
}

/**
 * One artifact per family *and* market. The symbol is part of the name because the two
 * markets are different prices on different digits, so a single file per family would have
 * one market silently overwrite the other.
 */
export function replayPath(family: string, symbol: string): string {
  const market = isReplaySymbol(symbol) ? symbol : DEFAULT_REPLAY_SYMBOL;
  return path.join(replayDir(), `family-replay-${family}-${market}.json`);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The cached artifact for one family, or null. A missing or unreadable file reads as
 * "nothing has been replayed yet" — the same tolerance every artifact reader here has
 * for a file another process may not have written.
 */
export async function readFamilyReplay(
  family: string,
  symbol: string = DEFAULT_REPLAY_SYMBOL,
): Promise<FamilyReplay | null> {
  if (!isReplayFamily(family) || !isReplaySymbol(symbol)) return null;
  let text: string;
  try {
    text = await readFile(replayPath(family, symbol), "utf8");
  } catch {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const data = parsed as Record<string, unknown>;
  const rawBars = Array.isArray(data.bars) ? data.bars : [];
  const bars: ReplayBar[] = [];
  for (const entry of rawBars) {
    const row = entry as Record<string, unknown>;
    const time = num(row.time);
    if (time === null) continue;
    bars.push({
      time,
      open: num(row.open) ?? 0,
      high: num(row.high) ?? 0,
      low: num(row.low) ?? 0,
      close: num(row.close) ?? 0,
    });
  }
  const rawTrades = Array.isArray(data.trades) ? data.trades : [];
  const trades: ReplayTrade[] = [];
  for (const entry of rawTrades) {
    const row = entry as Record<string, unknown>;
    const entryTime = num(row.entry_time);
    const exitTime = num(row.exit_time);
    const hit = row.hit;
    if (entryTime === null || exitTime === null) continue;
    if (hit !== "tp" && hit !== "sl" && hit !== "time") continue;
    trades.push({
      side: row.side === "sell" ? "sell" : "buy",
      signal_time: num(row.signal_time) ?? entryTime,
      entry_time: entryTime,
      exit_time: exitTime,
      entry: num(row.entry) ?? 0,
      sl: num(row.sl) ?? 0,
      tp: num(row.tp) ?? 0,
      exit: num(row.exit) ?? 0,
      hit,
      still_open: row.still_open === true,
      net_r: num(row.net_r),
    });
  }
  const rawSummary = (data.summary ?? {}) as Record<string, unknown>;
  return {
    generated_utc: typeof data.generated_utc === "string" ? data.generated_utc : "",
    symbol: typeof data.symbol === "string" ? data.symbol : "",
    timeframe: typeof data.timeframe === "string" ? data.timeframe : "H1",
    digits: num(data.digits),
    bars_counted: num(data.bars_counted) ?? bars.length,
    max_hold_bars: num(data.max_hold_bars) ?? 120,
    family: typeof data.family === "string" ? data.family : family,
    bars,
    trades,
    summary: {
      n: num(rawSummary.n) ?? trades.length,
      tp: num(rawSummary.tp) ?? 0,
      sl: num(rawSummary.sl) ?? 0,
      time: num(rawSummary.time) ?? 0,
      open: num(rawSummary.open) ?? 0,
      net_r_mean: num(rawSummary.net_r_mean),
      net_r_sum: num(rawSummary.net_r_sum),
    },
  };
}

/** Seconds between the replay's generation and now; null when it cannot be parsed. */
export function replayAgeS(replay: FamilyReplay): number | null {
  if (!replay.generated_utc) return null;
  const ms = new Date(replay.generated_utc).getTime();
  return Number.isNaN(ms) ? null : Math.max(0, Math.round((Date.now() - ms) / 1000));
}
