import { open, readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { readGateHistory, type GateHistoryStatus } from "@/lib/gate-history";
import { heartbeatStale, readLiveStatus, type LiveStatus } from "@/lib/live";
import { listIterations, type IterationEntry } from "@/lib/research";
import { listReports, type ReportEntry } from "@/lib/reports";
/**
 * One place that answers "is everything actually running, and what did it last say?".
 *
 * The console is a set of separate processes (the launcher starts a dashboard, a watcher and
 * one diagnostic pass). This module reads the same artifacts those processes leave behind —
 * the tracked-pid file, the log folder, the watcher heartbeat and the report folder — so the
 * Health page reports observed state, not a claim.
 */

export type Tone = "ok" | "warn" | "bad";

export interface ProcessInfo {
  name: string;
  pid: number;
  alive: boolean;
}

export interface LogInfo {
  /** Base file name, e.g. `launcher.log`. */
  name: string;
  bytes: number;
  modifiedMs: number | null;
  /** Number of rotated backups (`launcher.log.1` … `.3`). */
  backups: number;
  tail: string[];
}

export interface ComponentHealth {
  name: string;
  tone: Tone;
  /** Short state word shown as a pill, e.g. `live`, `stale`, `not started`. */
  state: string;
  detail: string;
}

export interface PerSymbolWatch {
  symbol: string;
  tone: Tone;
  /** Short state word shown as a pill, e.g. `reporting`, `starved`, `idle`. */
  state: string;
  /** The symbol's own bar reading, as its view carried it, or why there is none. */
  readings: string;
  /** What the symbol's own auto step last said, or why it last said nothing. */
  decision: string;
  decisionAgeS: number | null;
}

export interface HealthSnapshot {
  generatedAt: string;
  dashboardUptimeS: number;
  logsDir: string;
  reportsDir: string;
  processes: ProcessInfo[];
  pidsRecordedMs: number | null;
  logs: LogInfo[];
  logBytesTotal: number;
  live: LiveStatus;
  gateHistory: GateHistoryStatus;
  components: ComponentHealth[];
  /** One row per extra watched symbol, checking it is not silently starving. */
  symbolFeeds: PerSymbolWatch[];
  latestDiagnostic: ReportEntry | null;
  diagnosticCount: number;
  iterations: IterationEntry[];
  overall: Tone;
  overallNote: string;
}

/** The launcher caps `launcher.log` at 2 MB before rotating; shown so a large log is visible. */
export const LOG_CAP_BYTES = 2 * 1024 * 1024;

const LOG_FILE = /^(.+\.log)(\.(\d+))?$/;
const TAIL_BYTES = 8192;
const TAIL_LINES = 12;

export function logsDirectory(): string {
  return process.env.MT5_LOG_DIR ?? path.resolve(process.cwd(), "..", "logs");
}

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function readProcesses(): Promise<{ processes: ProcessInfo[]; recordedMs: number | null }> {
  const file = path.join(logsDirectory(), "launcher-pids.json");
  let recordedMs: number | null;
  try {
    recordedMs = (await stat(/*turbopackIgnore: true*/ file)).mtimeMs;
  } catch {
    return { processes: [], recordedMs: null };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(/*turbopackIgnore: true*/ file, "utf8"));
  } catch {
    return { processes: [], recordedMs };
  }
  const rows = Array.isArray(parsed) ? parsed : [];
  const processes: ProcessInfo[] = [];
  for (const row of rows) {
    if (typeof row !== "object" || row === null) continue;
    const record = row as Record<string, unknown>;
    const pid = record.pid;
    if (typeof pid !== "number" || !Number.isFinite(pid)) continue;
    processes.push({
      name: typeof record.name === "string" ? record.name : "unknown",
      pid,
      alive: isAlive(pid),
    });
  }
  return { processes, recordedMs };
}

async function readTail(file: string): Promise<string[]> {
  let handle;
  try {
    handle = await open(/*turbopackIgnore: true*/ file, "r");
    const info = await handle.stat();
    const start = Math.max(0, info.size - TAIL_BYTES);
    const length = info.size - start;
    if (length <= 0) return [];
    const buffer = Buffer.alloc(length);
    await handle.read(buffer, 0, length, start);
    return buffer
      .toString("utf8")
      .split(/\r?\n/)
      .filter((line) => line.length > 0)
      .slice(-TAIL_LINES);
  } catch {
    return [];
  } finally {
    await handle?.close().catch(() => {});
  }
}

async function readLogs(): Promise<LogInfo[]> {
  const dir = logsDirectory();
  let names: string[];
  try {
    names = await readdir(/*turbopackIgnore: true*/ dir);
  } catch {
    return [];
  }
  const groups = new Map<string, { backups: number }>();
  for (const name of names) {
    const match = LOG_FILE.exec(name);
    if (!match) continue;
    const base = match[1];
    const group = groups.get(base) ?? { backups: 0 };
    if (match[2]) group.backups += 1;
    groups.set(base, group);
  }
  const logs: LogInfo[] = [];
  for (const [base, group] of groups) {
    const file = path.join(dir, base);
    let bytes = 0;
    let modifiedMs: number | null = null;
    try {
      const info = await stat(/*turbopackIgnore: true*/ file);
      bytes = info.size;
      modifiedMs = info.mtimeMs;
    } catch {
      // Rotated out mid-read, or locked by the writer; the backup count still shows.
    }
    logs.push({
      name: base,
      bytes,
      modifiedMs,
      backups: group.backups,
      tail: bytes > 0 ? await readTail(file) : [],
    });
  }
  return logs.sort((a, b) => (b.modifiedMs ?? 0) - (a.modifiedMs ?? 0));
}

function watcherComponent(live: LiveStatus, processes: ProcessInfo[]): ComponentHealth {
  const tracked = processes.find((p) => p.name === "watcher");
  const trackedNote = tracked ? `tracked pid ${tracked.pid}${tracked.alive ? "" : " (exited)"}` : "not tracked";
  if (!live.available) {
    return {
      name: "Watch loop",
      tone: "warn",
      state: "not reporting",
      detail: `${live.reason ?? "No heartbeat file."} (${trackedNote})`,
    };
  }
  if (heartbeatStale(live)) {
    return {
      name: "Watch loop",
      tone: "bad",
      state: "stale",
      detail: `Heartbeat ${live.heartbeatAgeS ?? "?"} s old, past its ${live.heartbeatTtlS ?? 180} s TTL. Last write ${live.ts ?? "unknown"} (${trackedNote}).`,
    };
  }
  const idle = live.mode === "idle" || live.marketState === "closed";
  return {
    name: "Watch loop",
    tone: "ok",
    state: idle ? "idle" : "live",
    detail: `${idle ? "Market closed: polling slowly" : `Watching ${live.symbol ?? "the symbol"}`}, heartbeat ${live.heartbeatAgeS ?? "?"} s ago (${trackedNote}).`,
  };
}

/**
 * A cycle is 30 s on an open market, so an auto decision older than this is not a cadence.
 * Same margin the heartbeat staleness check uses (its 180 s TTL plus a 60 s grace).
 */
const MAX_DECISION_AGE_S = 240;

/**
 * One extra watched symbol's own decision file, read just deep enough to answer one question:
 * did this market's cycle say what it did? The watcher writes `auto_<SYMBOL>.json` next to the
 * primary's `auto.json` every cycle the symbol had a reading for - which is why a step that has
 * stopped seeing the symbol shows up here first as a file that has stopped moving.
 */
interface ExtraDecisionFile {
  readable: boolean;
  reason?: string;
  generatedUtc?: string;
  placed: number;
  refused: number;
  closed: number;
  managed: number;
  errors: number;
  notes: number;
}

async function readExtraDecision(symbol: string): Promise<ExtraDecisionFile> {
  const dir = process.env.MT5_AUTO
    ? path.dirname(process.env.MT5_AUTO)
    : path.resolve(process.cwd(), "..", "dashboard");
  const file = path.join(dir, `auto_${symbol}.json`);
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ file, "utf8");
  } catch {
    return {
      readable: false,
      reason: `no auto_${symbol}.json yet`,
      placed: 0,
      refused: 0,
      closed: 0,
      managed: 0,
      errors: 0,
      notes: 0,
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return {
      readable: false,
      reason: `auto_${symbol}.json is not valid JSON`,
      placed: 0,
      refused: 0,
      closed: 0,
      managed: 0,
      errors: 0,
      notes: 0,
    };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return {
      readable: false,
      reason: `auto_${symbol}.json has an unexpected shape`,
      placed: 0,
      refused: 0,
      closed: 0,
      managed: 0,
      errors: 0,
      notes: 0,
    };
  }
  const data = parsed as Record<string, unknown>;
  const count = (value: unknown): number => (Array.isArray(value) ? value.length : 0);
  return {
    readable: true,
    generatedUtc: typeof data.generated_utc === "string" ? data.generated_utc : undefined,
    placed: count(data.placed),
    refused: count(data.refused),
    closed: count(data.closed),
    managed: count(data.managed),
    errors: count(data.errors),
    notes: count(data.notes),
  };
}

function worstTone(tones: Tone[]): Tone {
  if (tones.includes("bad")) return "bad";
  if (tones.includes("warn")) return "warn";
  return "ok";
}

export async function readSymbolFeeds(live: LiveStatus): Promise<PerSymbolWatch[]> {
  const extras = Object.keys(live.symbols ?? {});
  if (extras.length === 0) return [];
  // The checks below mean "this cycle owed the symbol a reading". When the market is closed the
  // watcher idles for minutes at a stretch and writes every view as unavailable, and when the
  // watch loop itself has stopped neither view nor decision can be trusted as evidence of drift -
  // both are flagged by the Watch loop component instead, per market.
  const enforcing =
    live.available && !heartbeatStale(live) && live.mode === "watching" && live.marketState === "open";

  const rows = await Promise.all(
    extras.map(async (symbol) => {
      const view = live.symbols?.[symbol];
      if (!enforcing) {
        return {
          symbol,
          tone: "ok" as Tone,
          state: live.mode === "idle" || live.marketState === "closed" ? "idle" : "paused",
          readings: "not required while the watcher is idle or stale",
          decision: "not required while the watcher is idle or stale",
          decisionAgeS: null,
        };
      }
      if (!view || !view.available) {
        return {
          symbol,
          tone: "warn" as Tone,
          state: "no reading",
          readings:
            "the cycle filed no reading for this market" +
            (view?.barAgeS == null ? " — no bar age either" : ` — bar ${view?.barAgeS} s old`),
          decision: "a cycle without a reading files no decision",
          decisionAgeS: null,
        };
      }
      const readings =
        view.barAgeS == null
          ? `view is there but carries no bar age`
          : `bar ${view.barAgeS} s old` + (view.spreadPips != null ? `, spread ${view.spreadPips.toFixed(1)} pips` : "");
      const barTone: Tone = view.barAgeS == null ? "warn" : "ok";

      const decision = await readExtraDecision(symbol);
      if (!decision.readable) {
        return {
          symbol,
          tone: "bad" as Tone,
          state: "starved",
          readings,
          decision: decision.reason + " — the auto step has reported nothing for this market",
          decisionAgeS: null,
        };
      }
      const generatedMs = decision.generatedUtc ? Date.parse(decision.generatedUtc) : NaN;
      const decisionAgeS = Number.isFinite(generatedMs)
        ? Math.max(0, Math.round((Date.now() - generatedMs) / 1000))
        : null;
      if (decisionAgeS == null || decisionAgeS > MAX_DECISION_AGE_S) {
        return {
          symbol,
          tone: "bad" as Tone,
          state: "stale decision",
          readings,
          decision:
            decisionAgeS == null
              ? "the decision file carries no timestamp"
              : `last decision ${decisionAgeS} s ago, past the ${MAX_DECISION_AGE_S} s cycle window`,
          decisionAgeS,
        };
      }
      const actedOn = decision.placed + decision.refused + decision.closed + decision.managed;
      const quiet = actedOn === 0 && decision.notes === 0 && decision.errors === 0;
      if (quiet) {
        return {
          symbol,
          tone: "warn" as Tone,
          state: "silent",
          readings,
          decision:
            `the step wrote a view this cycle (age ${decisionAgeS} s) but placed, refused, closed, managed nothing and gave no reason`,
          decisionAgeS,
        };
      }
      const actedOnParts: string[] = [];
      if (decision.placed) actedOnParts.push(`placed ${decision.placed}`);
      if (decision.refused) actedOnParts.push(`refused ${decision.refused}`);
      if (decision.closed) actedOnParts.push(`closed ${decision.closed}`);
      if (decision.managed) actedOnParts.push(`managed ${decision.managed}`);
      if (decision.notes) actedOnParts.push(`${decision.notes} note${decision.notes === 1 ? "" : "s"}`);
      if (decision.errors) actedOnParts.push(`${decision.errors} error${decision.errors === 1 ? "" : "s"}`);
      return {
        symbol,
        tone: barTone,
        state: "reporting",
        readings,
        decision: `${actedOnParts.join(", ")} · ${decisionAgeS} s ago`,
        decisionAgeS,
      };
    }),
  );
  return rows;
}

function diagnosticComponent(latest: ReportEntry | null, processes: ProcessInfo[], count: number): ComponentHealth {
  const tracked = processes.find((p) => p.name === "diagnostic");
  if (tracked?.alive) {
    return {
      name: "Diagnostic",
      tone: "ok",
      state: "running",
      detail: `One pass is running now (pid ${tracked.pid}).`,
    };
  }
  if (!latest) {
    return {
      name: "Diagnostic",
      tone: "warn",
      state: "no runs",
      detail: "No diagnostic report in the reports folder yet.",
    };
  }
  if (!latest.ok) {
    return {
      name: "Diagnostic",
      tone: "bad",
      state: "unreadable",
      detail: `${latest.id}: ${latest.error}`,
    };
  }
  const { overall } = latest.report;
  const tone: Tone = overall === "FAIL" || overall === "NOT_VERIFIED" ? "warn" : "ok";
  return {
    name: "Diagnostic",
    tone,
    state: overall.toLowerCase().replace("_", " "),
    detail: `Latest of ${count} run${count === 1 ? "" : "s"}: ${latest.report.generatedUtc} (overall ${overall}).`,
  };
}

export async function readHealth(): Promise<HealthSnapshot> {
  const [live, reports, iterations, { processes, recordedMs }, logs, gateHistory] = await Promise.all([
    readLiveStatus(),
    listReports(),
    listIterations(),
    readProcesses(),
    readLogs(),
    readGateHistory(),
  ]);
  const symbolFeeds = await readSymbolFeeds(live);

  const latest = reports[0] ?? null;
  const components: ComponentHealth[] = [
    {
      name: "Dashboard",
      tone: "ok",
      state: "up",
      detail: `Serving this page. Process up ${Math.round(process.uptime())} s; it writes nothing itself, it only reads.`,
    },
    watcherComponent(live, processes),
    diagnosticComponent(latest, processes, reports.length),
  ];

  // One row per extra watched symbol, so a market that has quietly stopped being seen - the way
  // the extras once sat out every cycle while the primary traded on - is a line on this page
  // rather than something only the account balance would eventually notice.
  if (symbolFeeds.length > 0) {
    const drifted = symbolFeeds.filter((row) => row.tone !== "ok");
    components.push({
      name: "Extra symbols",
      tone: worstTone(symbolFeeds.map((row) => row.tone)),
      state:
        drifted.length === 0
          ? `${symbolFeeds.length} reporting`
          : `${drifted.length} of ${symbolFeeds.length} drifting`,
      detail:
        drifted.length === 0
          ? symbolFeeds.map((row) => row.symbol).join(", ") + " each reported a reading and a decision this cycle."
          : drifted.map((row) => `${row.symbol}: ${row.state}`).join(", ") + ".",
    });
  }

  const overall: Tone = components.some((c) => c.tone === "bad")
    ? "bad"
    : components.some((c) => c.tone === "warn")
      ? "warn"
      : "ok";

  const bad = components.filter((c) => c.tone === "bad").map((c) => c.name);
  const warn = components.filter((c) => c.tone === "warn").map((c) => c.name);
  const overallNote =
    overall === "ok"
      ? "Every console component is running and reporting."
      : overall === "bad"
        ? `Needs attention: ${[...bad, ...warn].join(", ")}.`
        : `Running, but check: ${warn.join(", ")}.`;

  return {
    generatedAt: new Date().toISOString(),
    dashboardUptimeS: Math.round(process.uptime()),
    logsDir: logsDirectory(),
    reportsDir: process.env.MT5_REPORT_DIR ?? path.resolve(process.cwd(), "..", "reports"),
    processes,
    pidsRecordedMs: recordedMs,
    logs,
    logBytesTotal: logs.reduce((sum, log) => sum + log.bytes, 0),
    live,
    gateHistory,
    components,
    symbolFeeds,
    latestDiagnostic: latest,
    diagnosticCount: reports.length,
    iterations,
    overall,
    overallNote,
  };
}

/** `1234` -> `1.2 kB`; used for log sizes. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

const TONE_CLASS: Record<Tone, string> = { ok: "pill-pass", warn: "pill-warn", bad: "pill-fail" };

export function toneClass(tone: Tone): string {
  return TONE_CLASS[tone];
}
