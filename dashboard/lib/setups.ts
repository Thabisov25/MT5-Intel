import { readFile } from "node:fs/promises";
import path from "node:path";

/**
 * The live per-strategy view the watcher writes: the position each rule is in, priced, with
 * whether that trade is still open and how many re-fires it stood aside for. Read tolerantly —
 * the file is written by another process on a timer, so it can be missing, mid-replace, or from
 * an older version of the writer (the counts then read as 0, which is why they are nullable).
 */

export type SetupStatus = "open" | "target" | "stop" | "time exit" | "none" | "error";

export interface LatestSignal {
  side: "buy" | "sell";
  signalUtc: string | null;
  signalEpoch: number | null;
  ageS: number | null;
  barsSinceSignal: number | null;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  stopPips: number | null;
  tpR: number | null;
  barsHeld: number | null;
  rNow: number | null;
  /** Where the market stood on this signal's own bar (the fade rule reads this one, not the row's). */
  rangePos: number | null;
  freshHigh: boolean | null;
  freshLow: boolean | null;
}

export interface SetupRow {
  family: string;
  /** The settings this rule ran with, when its name alone would not identify them. */
  params: string | null;
  side: "buy" | "sell" | null;
  status: SetupStatus;
  signalUtc: string | null;
  /** The signal bar as an epoch second: the key the watcher and the chart agree a pair on. */
  signalEpoch: number | null;
  ageS: number | null;
  barsSinceSignal: number | null;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  stopPips: number | null;
  tpR: number | null;
  barsHeld: number | null;
  rNow: number | null;
  rResult: number | null;
  rToStop: number | null;
  rToTarget: number | null;
  closedUtc: string | null;
  /** Where the market stood on this card's own signal bar: 0 is the range low, 1 the high.
   * The `confluence` fade rule reads this — placement is measured where the signal spoke, not
   * where the view's last closed bar happens to sit (the writer follows setups.placement_at). */
  rangePos: number | null;
  freshHigh: boolean | null;
  freshLow: boolean | null;
  /** Signals this rule fired while this one position was already running (D-035). */
  resignals: number | null;
  /** The newest signal fired while this position was already running, if any. */
  latestSignal: LatestSignal | null;
  /** All signals the rule produced, and how many of them were trades it could take. */
  signals: number | null;
  taken: number | null;
  note: string | null;
}

export interface SetupContext {
  lastClosedUtc: string | null;
  close: number | null;
  lastPrice: number | null;
  atrPips: number | null;
  emaFastPeriod: number | null;
  emaSlowPeriod: number | null;
  emaFast: number | null;
  emaSlow: number | null;
  emaTrend: "up" | "down" | null;
  smaSepPips: number | null;
  changePips: Record<string, number | null>;
  rangeBars: number | null;
  rangeHigh: number | null;
  rangeLow: number | null;
  rangePos: number | null;
  freshHigh: boolean | null;
  freshLow: boolean | null;
}

export interface Setups {
  generatedUtc: string;
  symbol: string;
  timeframe: string;
  clock: string;
  digits: number;
  maxHoldBars: number | null;
  bars: number | null;
  asofBarUtc: string | null;
  /** The newest bar's own clock reading - the candle the chart has open - as an epoch second. */
  asofBarEpoch: number | null;
  openCount: number;
  context: SetupContext;
  setups: SetupRow[];
}

const STATUSES: readonly SetupStatus[] = ["open", "target", "stop", "time exit", "none", "error"];

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function setupsPath(): string {
  return process.env.MT5_SETUPS_FILE ?? path.resolve(process.cwd(), "..", "dashboard", "setups.json");
}

/**
 * The per-symbol live view's file, or the primary's legacy one when no symbol is named.
 *
 * The watcher writes the primary's cards to the name this console has always read and a copy
 * under the symbol's own name; every other watched symbol has only its own name. The symbol is
 * validated against the same shape the artifacts carry, so a hostile query string can name
 * nothing outside this folder.
 */
const SYMBOL_IN_FILE = /^[A-Z0-9]{1,24}$/;

/**
 * The frames the watcher writes informational views for, and the one it trades.
 *
 * An allow-list, not a pattern: a query that names a timeframe is a request for one of the
 * artifacts the watcher actually writes, so anything else reads as the default view rather
 * than as a file name to probe for. H1 keeps its own entry because it is the watch's own
 * trading frame and carries the research registry; the others are read-only views.
 */
export const TIMEFRAMES = ["H1", "M30", "M15"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export function normalizeTimeframe(value: string | string[] | undefined | null): Timeframe {
  const raw = Array.isArray(value) ? value[0] : value;
  if (typeof raw !== "string") return "H1";
  const candidate = raw.trim().toUpperCase();
  return (TIMEFRAMES as readonly string[]).includes(candidate) ? (candidate as Timeframe) : "H1";
}

/**
 * The per-symbol, per-timeframe live view's file, or the primary's legacy one when nothing is
 * named.
 *
 * The watcher writes the primary's H1 cards to the name this console has always read and a copy
 * under the symbol's own name; every other watched symbol has only its own name. M30 and M15
 * have their own files per symbol - `setups_<SYMBOL>_<TF>.json` - written by the watcher's
 * informational refresh, so a frame's reading cannot be confused with the artifacts the auto
 * engine trades on. Both symbol and timeframe are validated against the same shapes the
 * artifacts carry, so a hostile query string can name nothing outside this folder.
 */
export function setupsPathFor(
  symbol: string | null | undefined,
  timeframe: string = "H1",
): string {
  const wanted = typeof symbol === "string" ? symbol.trim().toUpperCase() : "";
  if (!wanted || !SYMBOL_IN_FILE.test(wanted) || wanted === "ALL") return setupsPath();
  const tf = normalizeTimeframe(timeframe);
  if (tf === "H1") return path.join(path.dirname(setupsPath()), `setups_${wanted}.json`);
  return path.join(path.dirname(setupsPath()), `setups_${wanted}_${tf}.json`);
}

/**
 * The primary symbol's name, read from the legacy file the watcher always writes it to.
 *
 * A frame view has to know *whose* `setups_<SYMBOL>_<TF>.json` to read, and the default page
 * names no symbol - the primary is implied. `setups.json` carries that name in its own payload,
 * so one small read supplies it. Defined here, not in lib/symbols.ts, because symbols imports
 * this module and the import would be circular.
 */
async function primarySetupsSymbol(): Promise<string | null> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ setupsPath(), "utf8");
  } catch {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null) return null;
    const value = (parsed as Record<string, unknown>).symbol;
    if (typeof value !== "string" || !/^[A-Z0-9]{1,24}$/i.test(value)) return null;
    return value.toUpperCase();
  } catch {
    return null;
  }
}

function parseLatestSignal(raw: unknown): LatestSignal | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const s = raw as Record<string, unknown>;
  const side = s.side === "buy" || s.side === "sell" ? s.side : null;
  if (side === null) return null;
  return {
    side,
    signalUtc: str(s.signal_utc),
    signalEpoch: num(s.signal_epoch),
    ageS: num(s.age_s),
    barsSinceSignal: num(s.bars_since_signal),
    entry: num(s.entry),
    sl: num(s.sl),
    tp: num(s.tp),
    stopPips: num(s.stop_pips),
    tpR: num(s.tp_r),
    barsHeld: num(s.bars_held),
    rNow: num(s.r_now),
    rangePos: num(s.range_pos),
    freshHigh: bool(s.fresh_high),
    freshLow: bool(s.fresh_low),
  };
}

function parseRow(raw: unknown): SetupRow | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  const family = str(row.family);
  const status = str(row.status);
  if (family === null || status === null || !STATUSES.includes(status as SetupStatus)) return null;
  const side = row.side === "buy" || row.side === "sell" ? row.side : null;
  return {
    family,
    params: str(row.params),
    side,
    status: status as SetupStatus,
    signalUtc: str(row.signal_utc),
    signalEpoch: num(row.signal_epoch),
    ageS: num(row.age_s),
    barsSinceSignal: num(row.bars_since_signal),
    entry: num(row.entry),
    sl: num(row.sl),
    tp: num(row.tp),
    stopPips: num(row.stop_pips),
    tpR: num(row.tp_r),
    barsHeld: num(row.bars_held),
    rNow: num(row.r_now),
    rResult: num(row.r_result),
    rToStop: num(row.r_to_stop),
    rToTarget: num(row.r_to_target),
    closedUtc: str(row.closed_utc),
    rangePos: num(row.range_pos),
    freshHigh: bool(row.fresh_high),
    freshLow: bool(row.fresh_low),
    resignals: num(row.resignals),
    latestSignal: parseLatestSignal(row.latest_signal),
    signals: num(row.signals),
    taken: num(row.taken),
    note: str(row.note),
  };
}

function parseContext(raw: unknown): SetupContext {
  const empty: SetupContext = {
    lastClosedUtc: null,
    close: null,
    lastPrice: null,
    atrPips: null,
    emaFastPeriod: null,
    emaSlowPeriod: null,
    emaFast: null,
    emaSlow: null,
    emaTrend: null,
    smaSepPips: null,
    changePips: {},
    rangeBars: null,
    rangeHigh: null,
    rangeLow: null,
    rangePos: null,
    freshHigh: null,
    freshLow: null,
  };
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return empty;
  const c = raw as Record<string, unknown>;
  const changes: Record<string, number | null> = {};
  if (typeof c.change_pips === "object" && c.change_pips !== null) {
    for (const [key, value] of Object.entries(c.change_pips as Record<string, unknown>)) {
      changes[key] = num(value);
    }
  }
  return {
    lastClosedUtc: str(c.last_closed_utc),
    close: num(c.close),
    lastPrice: num(c.last_price),
    atrPips: num(c.atr_pips),
    emaFastPeriod: num(c.ema_fast_period),
    emaSlowPeriod: num(c.ema_slow_period),
    emaFast: num(c.ema_fast),
    emaSlow: num(c.ema_slow),
    emaTrend: c.ema_trend === "up" || c.ema_trend === "down" ? c.ema_trend : null,
    smaSepPips: num(c.sma_sep_pips),
    changePips: changes,
    rangeBars: num(c.range_bars),
    rangeHigh: num(c.range_high),
    rangeLow: num(c.range_low),
    rangePos: num(c.range_pos),
    freshHigh: bool(c.fresh_high),
    freshLow: bool(c.fresh_low),
  };
}

export async function readSetups(
  symbol?: string | null,
  timeframe: string = "H1",
): Promise<Setups | null> {
  // The default view names no symbol and reads the primary's artifacts. H1 has a legacy name
  // (`setups.json`) that works without one, but every other frame lives on the primary symbol's
  // own name, so an unnamed market has to learn its name before it can read its frame -
  // otherwise `?tf=m30` on the default page would silently read H1's file.
  const tf = normalizeTimeframe(timeframe);
  if ((typeof symbol !== "string" || symbol.trim() === "") && tf !== "H1") {
    symbol = await primarySetupsSymbol();
    if (symbol === null) return null; // nothing written yet; there is no frame view to name
  }
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ setupsPathFor(symbol, tf), "utf8");
  } catch {
    return null; // the watcher has not written one yet (or does not watch this symbol)
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const root = parsed as Record<string, unknown>;
  const rows = Array.isArray(root.setups) ? root.setups : [];
  const setups = rows.map(parseRow).filter((row): row is SetupRow => row !== null);
  return {
    generatedUtc: str(root.generated_utc) ?? "",
    symbol: str(root.symbol) ?? "",
    timeframe: str(root.timeframe) ?? "",
    clock: str(root.clock) ?? "server",
    // Prices are stored as the symbol quotes them; fall back to the FX convention of 5 digits.
    digits: num(root.digits) ?? 5,
    maxHoldBars: num(root.max_hold_bars),
    bars: num(root.bars),
    asofBarUtc: str(root.asof_bar_utc),
    asofBarEpoch: num(root.asof_bar_epoch),
    openCount: num(root.open_count) ?? setups.filter((row) => row.status === "open").length,
    context: parseContext(root.context),
    setups,
  };
}
