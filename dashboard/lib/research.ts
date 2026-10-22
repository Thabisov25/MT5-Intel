import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

// One file per `python -m mt5_intel.research.iterate <TF>` run, written next to the
// replay and diagnostic reports. Names are stamped, so reverse lexical order is newest.
const ITERATE_FILE = /^research-iterate-\d{8}-\d{6}\.json$/;
const MAX_RUNS = 12;

export interface CostHurdle {
  rawSignals: number;
  keptSignals: number;
  keptShare: number | null;
  medianStopPips: number | null;
  minStopPips: number | null;
  spreadPipsMedian: number;
  floorPips: number;
}

export interface IterationFamily {
  family: string;
  params: Record<string, string | number>;
  trainMean: number | null;
  trainN: number;
  testMean: number | null;
  testN: number;
  placeboMean: number | null;
  stressedMean: number | null;
  grossTrainMean: number | null;
  grossTestMean: number | null;
  validated: boolean;
  hurdle: CostHurdle | null;
}

export interface IterationCost {
  spreadPipsMedian: number;
  minStopSpreadMult: number;
  floorPips: number;
}

export interface IterationRun {
  id: string;
  stamp: string;
  timeframe: string;
  barCount: number;
  firstUtc: string;
  lastUtc: string;
  trainEnd: number;
  trainBars: number;
  testBars: number;
  testFraction: number;
  maxHoldBars: number;
  scalpingIncluded: boolean;
  cost: IterationCost | null;
  families: IterationFamily[];
  validated: string[];
  verdict: string;
  validatedOnlyN: number | null;
  validatedOnlyMean: number | null;
}

export type IterationEntry =
  | { ok: true; run: IterationRun }
  | { ok: false; id: string; error: string };

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function expectObject(value: unknown, where: string): Json {
  if (!isObject(value)) throw new Error(`${where} is not an object`);
  return value;
}

function expectString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new Error(`${where} is not a string`);
  return value;
}

function expectNumber(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${where} is not a finite number`);
  }
  return value;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function expectBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${where} is not a boolean`);
  return value;
}

function parseParams(raw: unknown, where: string): Record<string, string | number> {
  const obj = expectObject(raw, where);
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === "number" || typeof value === "string") out[key] = value;
  }
  return out;
}

function parseHurdle(raw: unknown, where: string): CostHurdle | null {
  if (raw === null || raw === undefined) return null;
  const h = expectObject(raw, where);
  const kept = expectNumber(h.kept_signals, `${where}.kept_signals`);
  return {
    rawSignals: expectNumber(h.raw_signals, `${where}.raw_signals`),
    keptSignals: kept,
    keptShare: optionalNumber(h.kept_share),
    medianStopPips: optionalNumber(h.median_stop_pips),
    minStopPips: optionalNumber(h.min_stop_pips),
    spreadPipsMedian: expectNumber(h.spread_pips_median, `${where}.spread_pips_median`),
    floorPips: expectNumber(h.floor_pips, `${where}.floor_pips`),
  };
}

function parseFamilies(raw: unknown): IterationFamily[] {
  if (!Array.isArray(raw)) throw new Error("families is not an array");
  return raw.map((item, index) => {
    const where = `families[${index}]`;
    const row = expectObject(item, where);
    return {
      family: expectString(row.family, `${where}.family`),
      params: parseParams(row.best_train ?? {}, `${where}.best_train`),
      trainMean: optionalNumber(row.train_mean),
      trainN: expectNumber(row.train_n, `${where}.train_n`),
      testMean: optionalNumber(row.test_mean),
      testN: expectNumber(row.test_n, `${where}.test_n`),
      placeboMean: optionalNumber(row.placebo_mean),
      stressedMean: optionalNumber(row.stressed_mean),
      grossTrainMean: optionalNumber(row.gross_train_mean),
      grossTestMean: optionalNumber(row.gross_test_mean),
      validated: expectBoolean(row.validated, `${where}.validated`),
      hurdle: parseHurdle(row.hurdle, `${where}.hurdle`),
    };
  });
}

function parseCost(raw: unknown): IterationCost | null {
  if (raw === undefined || raw === null) return null;
  const c = expectObject(raw, "cost");
  return {
    spreadPipsMedian: expectNumber(c.spread_pips_median, "cost.spread_pips_median"),
    minStopSpreadMult: expectNumber(c.min_stop_spread_mult, "cost.min_stop_spread_mult"),
    floorPips: expectNumber(c.floor_pips, "cost.floor_pips"),
  };
}

function parseValidated(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is string => typeof item === "string");
}

function parseRun(id: string, text: string): IterationRun {
  const root = expectObject(JSON.parse(text), "iterate report");
  const trainEnd = expectNumber(root.train_end, "train_end");
  const barCount = expectNumber(root.bar_count, "bar_count");
  const validatedOnly = isObject(root.validated_only) ? root.validated_only : null;
  return {
    id,
    stamp: expectString(root.stamp, "stamp"),
    timeframe: expectString(root.timeframe, "timeframe"),
    barCount,
    firstUtc: expectString(root.first_utc, "first_utc"),
    lastUtc: expectString(root.last_utc, "last_utc"),
    trainEnd,
    trainBars: trainEnd,
    testBars: Math.max(0, barCount - trainEnd),
    testFraction: expectNumber(root.test_fraction, "test_fraction"),
    maxHoldBars: expectNumber(root.max_hold_bars, "max_hold_bars"),
    scalpingIncluded: expectBoolean(root.scalping_included, "scalping_included"),
    cost: parseCost(root.cost),
    families: parseFamilies(root.families),
    validated: parseValidated(root.validated),
    verdict: expectString(root.verdict, "verdict"),
    validatedOnlyN: validatedOnly ? optionalNumber(validatedOnly.n) : null,
    validatedOnlyMean: validatedOnly ? optionalNumber(validatedOnly.net_r_mean) : null,
  };
}

export function researchDirectory(): string {
  return process.env.MT5_REPORT_DIR ?? path.resolve(process.cwd(), "..", "reports");
}

export async function listIterations(): Promise<IterationEntry[]> {
  const dir = researchDirectory();
  let names: string[];
  try {
    names = await readdir(/*turbopackIgnore: true*/ dir);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const files = names
    .filter((name) => ITERATE_FILE.test(name))
    .sort()
    .reverse()
    .slice(0, MAX_RUNS);
  return Promise.all(
    files.map(async (name): Promise<IterationEntry> => {
      const id = name.replace(/\.json$/, "");
      try {
        const text = await readFile(path.join(/*turbopackIgnore: true*/ dir, name), "utf8");
        return { ok: true, run: parseRun(id, text) };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return { ok: false, id, error: message };
      }
    }),
  );
}

export function formatR(value: number | null): string {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(3)}`;
}

// Grid keys and values are machine words; render them as the reader would say them.
const PARAM_LABELS: Record<string, string> = {
  k: "swing bars",
  min_pool_age: "pool age",
  atr_mult: "sweep depth",
  lookback: "lookback",
  squeeze_q: "squeeze",
  fast: "fast avg",
  slow: "slow avg",
  k_atr: "pullback ATR",
  hi_rsi: "RSI high",
  lo_rsi: "RSI low",
  stop_atr: "stop ATR",
  ma_days: "MA bars",
  swing_k: "swing bars",
  retest_bars: "retest window",
  tp_r: "target",
  stop_mode: "cost rule",
  fade_atr: "fade depth",
  target: "target",
  range_bars: "range length",
  trigger_atr: "break buffer",
  stop_ref: "stop at",
};

const PARAM_VALUES: Record<string, string> = {
  filter: "drop tight stops",
  widen: "widen stop to floor",
  off: "no cost rule",
  mid: "range midpoint",
  far: "far range edge",
  atr: "1 ATR",
  opposite: "opposite edge",
};

export function formatParams(params: Record<string, string | number>): string {
  const parts = Object.entries(params).map(([key, value]) => {
    const label = PARAM_LABELS[key] ?? key;
    const shown = typeof value === "string" ? (PARAM_VALUES[value] ?? value) : String(value);
    return `${label} ${shown}`;
  });
  return parts.length === 0 ? "defaults" : parts.join(", ");
}
