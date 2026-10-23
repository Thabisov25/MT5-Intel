import { readFile } from "node:fs/promises";
import path from "node:path";

/**
 * Real market data exported by `src/mt5_intel/market_export.py` (master plan §12–13).
 * These readers never synthesize values: a missing artifact or an unreadable field is
 * reported as unavailable so the terminal can show a truthful state.
 */

export interface MarketBar {
  /** Bar open time as a UTC epoch second (MT5's own bar clock). */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface MarketBars {
  available: boolean;
  reason?: string;
  generatedUtc: string | null;
  symbol: string;
  timeframe: string;
  digits: number | null;
  bars: MarketBar[];
  /** Seconds between the artifact's write and now; the UI flags stale data with it. */
  ageS: number | null;
}

export interface MarketQuote {
  symbol: string;
  available: boolean;
  bid: number | null;
  ask: number | null;
  last: number | null;
  tickEpoch: number | null;
  digits: number | null;
  spreadPips: number | null;
}

export interface MarketWatchSnapshot {
  available: boolean;
  reason?: string;
  generatedUtc: string | null;
  quotes: Record<string, MarketQuote>;
  ageS: number | null;
}

/** Market data older than this is shown as stale rather than live. */
export const MARKET_STALE_S = 300;

export function marketDirectory(): string {
  return process.env.MT5_MARKET_DIR ?? path.resolve(process.cwd(), "..", "dashboard", "market");
}

const SYMBOL_RE = /^[A-Z0-9]{1,24}$/;
const TIMEFRAMES = ["H1", "M30", "M15"] as const;
export type MarketTimeframe = (typeof TIMEFRAMES)[number];

export function normalizeMarketTimeframe(value: string | null | undefined): MarketTimeframe {
  return (TIMEFRAMES as readonly string[]).includes(value ?? "") ? (value as MarketTimeframe) : "H1";
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function ageOf(generatedUtc: string | null): number | null {
  if (!generatedUtc) return null;
  const ms = new Date(generatedUtc).getTime();
  return Number.isNaN(ms) ? null : Math.max(0, Math.round((Date.now() - ms) / 1000));
}

/**
 * One symbol-frame's real bars. `symbol` is validated before it reaches the path —
 * a query is not a path.
 */
export async function readMarketBars(
  symbol: string | null | undefined,
  timeframe: string | null | undefined,
): Promise<MarketBars> {
  const empty = (reason: string): MarketBars => ({
    available: false,
    reason,
    generatedUtc: null,
    symbol: symbol ?? "",
    timeframe: normalizeMarketTimeframe(timeframe),
    digits: null,
    bars: [],
    ageS: null,
  });
  if (!symbol || !SYMBOL_RE.test(symbol)) return empty("no symbol named");
  const tf = normalizeMarketTimeframe(timeframe);
  const file = path.join(marketDirectory(), `bars_${symbol.toUpperCase()}_${tf}.json`);
  let text: string;
  try {
    text = await readFile(file, "utf8");
  } catch {
    return empty("no market export yet (run python -m mt5_intel.market_export)");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return empty("market export unreadable");
  }
  const record = parsed as Record<string, unknown>;
  const rawBars = Array.isArray(record.bars) ? record.bars : [];
  const bars: MarketBar[] = [];
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
      volume: num(row.volume),
    });
  }
  const generatedUtc = typeof record.generated_utc === "string" ? record.generated_utc : null;
  return {
    available: bars.length > 0,
    reason: bars.length > 0 ? undefined : "export contains no bars",
    generatedUtc,
    symbol: symbol.toUpperCase(),
    timeframe: tf,
    digits: num(record.digits),
    bars,
    ageS: ageOf(generatedUtc),
  };
}

/** The tick snapshot behind Market Watch: real bid/ask/last per symbol. */
export async function readMarketWatch(): Promise<MarketWatchSnapshot> {
  const file = path.join(marketDirectory(), "watch.json");
  let text: string;
  try {
    text = await readFile(file, "utf8");
  } catch {
    return {
      available: false,
      reason: "no tick export yet (run python -m mt5_intel.market_export)",
      generatedUtc: null,
      quotes: {},
      ageS: null,
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { available: false, reason: "tick export unreadable", generatedUtc: null, quotes: {}, ageS: null };
  }
  const record = parsed as Record<string, unknown>;
  const raw = (record.quotes ?? {}) as Record<string, unknown>;
  const quotes: Record<string, MarketQuote> = {};
  for (const [symbol, entry] of Object.entries(raw)) {
    const row = (entry ?? {}) as Record<string, unknown>;
    quotes[symbol] = {
      symbol,
      available: row.available === true,
      bid: num(row.bid),
      ask: num(row.ask),
      last: num(row.last),
      tickEpoch: num(row.tick_epoch),
      digits: num(row.digits),
      spreadPips: num(row.spread_pips),
    };
  }
  const generatedUtc = typeof record.generated_utc === "string" ? record.generated_utc : null;
  return {
    available: Object.keys(quotes).length > 0,
    reason: Object.keys(quotes).length > 0 ? undefined : "export contains no quotes",
    generatedUtc,
    quotes,
    ageS: ageOf(generatedUtc),
  };
}
