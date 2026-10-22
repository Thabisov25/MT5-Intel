import { readFile } from "node:fs/promises";
import path from "node:path";

/**
 * The watcher's reason-code history, written to `dashboard/gate-history.json` (next to
 * `live-status.json`) by `mt5_intel.watch` and summarised by `mt5_intel.gatehistory`.
 *
 * The dashboard has no SQLite driver, so it reads that artifact rather than the journal.
 * A code list alone cannot tell you whether a code is a market condition or a check that
 * is not implemented yet; counting how often each code cleared does.
 */

export interface GateCodeStat {
  code: string;
  firstSeenTs: string;
  lastSeenTs: string;
  cyclesPresent: number;
  cyclesSinceFirstSeen: number;
  /** Newest cycle this code was absent from, or null if it never was. */
  lastClearedTs: string | null;
  inLatestCycle: boolean;
  everCleared: boolean;
}

export interface GateHourBucket {
  /** UTC hour, 0-23. */
  hour: number;
  /** Cycles journaled in this hour: the denominator of every rate below. */
  cycles: number;
  /** Reason code -> how many of those cycles contained it. */
  codes: Record<string, number>;
}

export interface GateSessionBucket {
  /** Session label, e.g. `London + New York` or `off-hours`. */
  session: string;
  cycles: number;
  codes: Record<string, number>;
  /** UTC hours this bucket actually covered; not a range (see `gatehistory.py`). */
  hoursSeen: number[];
}

/** What the last rollup refresh read and wrote, so the panel can show the summary's cost. */
export interface GateRollupInfo {
  hoursKept: number;
  hoursWritten: number;
  rowsRead: number;
  prunedHours: number;
  unplacedCycles: number;
}

export interface GateHistory {
  generatedAt: string;
  cycles: number;
  firstTs: string | null;
  lastTs: string | null;
  verdicts: Record<string, number>;
  codesTotal: number;
  /** Canonical gate codes that have not appeared in the window at all. */
  neverSeen: string[];
  codes: GateCodeStat[];
  /** Where the codes fall in the UTC day, all 24 hours, including empty ones. */
  byHour: GateHourBucket[];
  /** Same, bucketed by which FX centre's local session was open. */
  bySession: GateSessionBucket[];
  /** False when the IANA time-zone database is missing, so sessions could not be resolved. */
  sessionsAvailable: boolean;
  sessionsNote: string | null;
  /** Cycles that could not be placed in an hour, and so are in no hour or session bucket. */
  unparsedTs: number;
  /** Null on an artifact written before the hourly rollup existed. */
  rollup: GateRollupInfo | null;
  windowDays: number;
}

export type GateHistoryStatus =
  | { available: true; history: GateHistory }
  | { available: false; reason: string };

export function gateHistoryPath(): string {
  return (
    process.env.MT5_GATE_HISTORY ?? path.resolve(process.cwd(), "..", "dashboard", "gate-history.json")
  );
}

class BadArtifact extends Error {}

function expectObject(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new BadArtifact(`${where} is not an object`);
  }
  return value as Record<string, unknown>;
}

function expectString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new BadArtifact(`${where} is not a string`);
  return value;
}

function expectNumber(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new BadArtifact(`${where} is not a finite number`);
  }
  return value;
}

function expectBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new BadArtifact(`${where} is not a boolean`);
  return value;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function parseCode(raw: unknown, index: number): GateCodeStat {
  const where = `codes[${index}]`;
  const row = expectObject(raw, where);
  return {
    code: expectString(row.code, `${where}.code`),
    firstSeenTs: expectString(row.first_seen_ts, `${where}.first_seen_ts`),
    lastSeenTs: expectString(row.last_seen_ts, `${where}.last_seen_ts`),
    cyclesPresent: expectNumber(row.cycles_present, `${where}.cycles_present`),
    cyclesSinceFirstSeen: expectNumber(
      row.cycles_since_first_seen,
      `${where}.cycles_since_first_seen`,
    ),
    lastClearedTs: optionalString(row.last_cleared_ts),
    inLatestCycle: expectBoolean(row.in_latest_cycle, `${where}.in_latest_cycle`),
    everCleared: expectBoolean(row.ever_cleared, `${where}.ever_cleared`),
  };
}

function parseVerdicts(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [verdict, count] of Object.entries(expectObject(raw ?? {}, "verdicts"))) {
    if (typeof count === "number" && Number.isFinite(count)) out[verdict] = count;
  }
  return out;
}

/** Reason code -> cycle count. Unusable entries are dropped, never coerced to zero. */
function parseCodeCounts(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [code, count] of Object.entries(expectObject(raw ?? {}, "codes"))) {
    if (typeof count === "number" && Number.isFinite(count)) out[code] = count;
  }
  return out;
}

function parseHours(raw: unknown): GateHourBucket[] {
  if (!Array.isArray(raw)) return [];
  const out: GateHourBucket[] = [];
  for (const item of raw) {
    const row = expectObject(item, "by_hour[]");
    const hour = expectNumber(row.hour, "by_hour[].hour");
    if (hour < 0 || hour > 23) continue;
    out.push({
      hour,
      cycles: expectNumber(row.cycles, "by_hour[].cycles"),
      codes: parseCodeCounts(row.codes),
    });
  }
  return out.sort((a, b) => a.hour - b.hour);
}

function parseRollup(raw: unknown): GateRollupInfo | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  const num = (key: string): number => {
    const value = row[key];
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
  };
  return {
    hoursKept: num("hours_kept"),
    hoursWritten: num("hours_written"),
    rowsRead: num("rows_read"),
    prunedHours: num("pruned_hours"),
    unplacedCycles: num("unplaced_cycles"),
  };
}

function parseSessions(raw: unknown): GateSessionBucket[] {
  if (!Array.isArray(raw)) return [];
  const out: GateSessionBucket[] = [];
  for (const item of raw) {
    const row = expectObject(item, "by_session[]");
    out.push({
      session: expectString(row.session, "by_session[].session"),
      cycles: expectNumber(row.cycles, "by_session[].cycles"),
      codes: parseCodeCounts(row.codes),
      hoursSeen: Array.isArray(row.hours_seen)
        ? row.hours_seen.filter((h): h is number => typeof h === "number" && Number.isFinite(h))
        : [],
    });
  }
  return out;
}

function parseHistory(text: string): GateHistory {
  const root = expectObject(JSON.parse(text), "gate history");
  return {
    generatedAt: expectString(root.generated_at, "generated_at"),
    cycles: expectNumber(root.cycles, "cycles"),
    firstTs: optionalString(root.first_ts),
    lastTs: optionalString(root.last_ts),
    verdicts: parseVerdicts(root.verdicts),
    codesTotal: expectNumber(root.codes_total, "codes_total"),
    neverSeen: Array.isArray(root.never_seen) ? root.never_seen.filter((c): c is string => typeof c === "string") : [],
    codes: Array.isArray(root.codes) ? root.codes.map(parseCode) : [],
    // An artifact written by an older watcher has no time breakdown yet: report it as empty
    // rather than failing the whole panel, which still carries the per-code counts.
    byHour: parseHours(root.by_hour),
    bySession: parseSessions(root.by_session),
    sessionsAvailable: typeof root.sessions_available === "boolean" ? root.sessions_available : true,
    sessionsNote: optionalString(root.sessions_note),
    unparsedTs: typeof root.unparsed_ts === "number" && Number.isFinite(root.unparsed_ts) ? root.unparsed_ts : 0,
    rollup: parseRollup(root.rollup),
    windowDays: expectNumber(root.window_days, "window_days"),
  };
}

/** Missing or unreadable artifacts are reported, not hidden: the panel says which it is. */
export async function readGateHistory(): Promise<GateHistoryStatus> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ gateHistoryPath(), "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { available: false, reason: "No gate-history artifact yet — the watcher writes it on its first cycle." };
    }
    return { available: false, reason: `The gate-history artifact could not be read: ${String(error)}` };
  }
  try {
    return { available: true, history: parseHistory(text) };
  } catch (error) {
    const message = error instanceof SyntaxError ? "it is not valid JSON" : String(error);
    return { available: false, reason: `The gate-history artifact could not be parsed: ${message}` };
  }
}
