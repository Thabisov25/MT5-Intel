import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

const REPLAY_FILE = /^replay-\d{8}-\d{6}\.json$/;
const MAX_RUNS = 12; // each run renders its full per-strategy breakdown inline

export interface ReplayGrade {
  netR: number;
  hit: "TP" | "SL" | "time-exit";
  barsHeld: number;
}

export interface ReplayCard {
  family: string;
  signalUtc: string;
  side: "buy" | "sell";
  entry: string;
  sl: string;
  tp: string;
  stopPips: number;
  tpR: number;
  grade: ReplayGrade;
}

export interface ReplaySummary {
  label: string;
  n: number;
  netRMean: number | null;
  hitTp?: number;
  hitSl?: number;
  netSumR?: number;
}

export interface ReplayRun {
  id: string;
  generatedUtc: string;
  symbol: string;
  timeframe: string;
  maxHoldBars: number;
  asofUtc: string;
  /** The moment the run treated as now, or null when it ran against the newest bar. */
  asofEpoch: number | null;
  results: ReplaySummary[];
  cards: ReplayCard[];
}

export type ReplayEntry =
  | { ok: true; run: ReplayRun }
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

function asofLabel(epoch: unknown, utc: unknown): string {
  // The Python writer stores both; fall back sensibly when either is absent.
  if (typeof utc === "string" && utc) return utc;
  if (typeof epoch === "number" && Number.isFinite(epoch)) {
    return new Date(epoch * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }
  return "latest bar";
}

function parseRun(id: string, text: string): ReplayRun {
  const root = expectObject(JSON.parse(text), "replay");
  const resultsRaw = Array.isArray(root.results) ? root.results : [];
  const results: ReplaySummary[] = resultsRaw.map((item, index) => {
    const row = expectObject(item, `results[${index}]`);
    return {
      label: expectString(row.label, `results[${index}].label`),
      n: expectNumber(row.n, `results[${index}].n`),
      netRMean: optionalNumber(row.net_r_mean),
      hitTp: optionalNumber(row.hit_tp) ?? undefined,
      hitSl: optionalNumber(row.hit_sl) ?? undefined,
      netSumR: optionalNumber(row.net_sum_r) ?? undefined,
    };
  });
  const cardsRaw = Array.isArray(root.cards) ? root.cards : [];
  const cards: ReplayCard[] = cardsRaw.map((item, index) => {
    const row = expectObject(item, `cards[${index}]`);
    const grade = expectObject(row.grade, `cards[${index}].grade`);
    const hit = expectString(grade.hit, `cards[${index}].grade.hit`);
    if (hit !== "TP" && hit !== "SL" && hit !== "time-exit") {
      throw new Error(`cards[${index}].grade.hit unknown: ${hit}`);
    }
    const side = expectString(row.side, `cards[${index}].side`);
    if (side !== "buy" && side !== "sell") {
      throw new Error(`cards[${index}].side unknown: ${side}`);
    }
    return {
      family: expectString(row.family, `cards[${index}].family`),
      signalUtc: expectString(row.signal_utc, `cards[${index}].signal_utc`),
      side,
      entry: expectString(row.entry, `cards[${index}].entry`),
      sl: expectString(row.sl, `cards[${index}].sl`),
      tp: expectString(row.tp, `cards[${index}].tp`),
      stopPips: expectNumber(row.stop_pips, `cards[${index}].stop_pips`),
      tpR: expectNumber(row.tp_r, `cards[${index}].tp_r`),
      grade: {
        netR: expectNumber(grade.net_r, `cards[${index}].grade.net_r`),
        hit,
        barsHeld: expectNumber(grade.bars_held, `cards[${index}].grade.bars_held`),
      },
    };
  });
  return {
    id,
    generatedUtc: expectString(root.generated_utc, "generated_utc"),
    symbol: expectString(root.symbol, "symbol"),
    timeframe: expectString(root.timeframe, "timeframe"),
    maxHoldBars: expectNumber(root.max_hold_bars, "max_hold_bars"),
    asofUtc: asofLabel(root.asof_epoch, root.asof_utc),
    asofEpoch: optionalNumber(root.asof_epoch),
    results,
    cards,
  };
}

/** "2026-10-11T08:23:07+00:00" reads as noise; show the minute the reader cares about. */
export function formatStamp(iso: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]} UTC` : iso;
}

/** An epoch second as "YYYY-MM-DD HH:MM UTC", for comparing a run's clock with the live bar. */
export function formatEpoch(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function replayDirectory(): string {
  return process.env.MT5_REPORT_DIR ?? path.resolve(process.cwd(), "..", "reports");
}

export async function listReplays(): Promise<ReplayEntry[]> {
  const dir = replayDirectory();
  let names: string[];
  try {
    names = await readdir(/*turbopackIgnore: true*/ dir);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const files = names.filter((name) => REPLAY_FILE.test(name)).sort().reverse().slice(0, MAX_RUNS);
  return Promise.all(
    files.map(async (name): Promise<ReplayEntry> => {
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

export async function getReplay(id: string): Promise<ReplayEntry | null> {
  if (!/^replay-\d{8}-\d{6}$/.test(id)) return null;
  const entries = await listReplays();
  return entries.find((entry) => (entry.ok ? entry.run.id : entry.id) === id) ?? null;
}
