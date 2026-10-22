import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

export type Status = "PASS" | "WARN" | "FAIL" | "NOT_VERIFIED" | "INFO";

const STATUSES: readonly Status[] = ["PASS", "WARN", "FAIL", "NOT_VERIFIED", "INFO"];
const REPORT_FILE = /^diagnostic-\d{8}-\d{6}\.json$/;
const MAX_REPORTS = 50;

export interface Check {
  area: string;
  name: string;
  status: Status;
  detail: string;
}

export interface RiskRow {
  stopPips: number;
  riskPerMinLotZar: number;
  maxVolume: number;
  actualRiskZar: number;
  fits: boolean;
}

export interface Risk {
  referenceEquityZar: number;
  referenceEquityUsc: number;
  riskPct: number;
  riskBudgetUsc: number;
  riskBudgetZar: number;
  pipValuePerMinLotZar: number;
  maxStopPipsAtMinLot: number | null;
  table: RiskRow[];
}

export interface HistoryTimeframe {
  bars: number;
  firstUtc: string;
  lastUtc: string;
  spanDays: number;
  gapsWeekend: number;
  gapsUnexpected: number;
  longestUnexpectedGapH: number;
  utcClassified: boolean;
}

export interface HourRow {
  hourUtc: number;
  hourSast: number;
  samples: number;
  medianPips: number | null;
  p90Pips: number | null;
}

export interface SpreadByHour {
  utcClassified: boolean;
  barTimeframe: string;
  note: string;
  hours: HourRow[];
}

export interface Account {
  loginMasked: string;
  server: string;
  company: string;
  currency: string;
  leverage: number;
  mode: string;
  tradeAllowed: boolean;
  balance: number;
  equity: number;
}

export interface Report {
  id: string;
  generatedUtc: string;
  overall: Status;
  checks: Check[];
  account: Account | null;
  usdZar: { rate: number; source: string } | null;
  risk: Risk | null;
  history: Record<string, HistoryTimeframe>;
  spreadByHour: SpreadByHour | null;
  marketOpen: boolean | null;
  utcOffsetSeconds: number | null;
}

export type ReportEntry =
  | { ok: true; report: Report }
  | { ok: false; id: string; error: string };

type Json = Record<string, unknown>;

class InvalidReport extends Error {}

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function expectObject(value: unknown, where: string): Json {
  if (!isObject(value)) throw new InvalidReport(`${where} is not an object`);
  return value;
}

function expectString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new InvalidReport(`${where} is not a string`);
  return value;
}

function expectNumber(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new InvalidReport(`${where} is not a finite number`);
  }
  return value;
}

// The Python writer turns NaN/inf into null, so null is valid where a value can be unknown.
function optionalNumber(value: unknown, where: string): number | null {
  return value === null ? null : expectNumber(value, where);
}

function expectBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new InvalidReport(`${where} is not a boolean`);
  return value;
}

function expectArray(value: unknown, where: string): unknown[] {
  if (!Array.isArray(value)) throw new InvalidReport(`${where} is not an array`);
  return value;
}

function expectStatus(value: unknown, where: string): Status {
  const text = expectString(value, where);
  if (!(STATUSES as readonly string[]).includes(text)) {
    throw new InvalidReport(`${where} is not a known status: ${text}`);
  }
  return text as Status;
}

function parseChecks(raw: unknown): Check[] {
  return expectArray(raw, "checks").map((item, index) => {
    const row = expectObject(item, `checks[${index}]`);
    return {
      area: expectString(row.area, `checks[${index}].area`),
      name: expectString(row.name, `checks[${index}].name`),
      status: expectStatus(row.status, `checks[${index}].status`),
      detail: expectString(row.detail, `checks[${index}].detail`),
    };
  });
}

function parseAccount(raw: unknown): Account | null {
  if (raw === undefined) return null;
  const a = expectObject(raw, "data.account");
  return {
    loginMasked: expectString(a.login_masked, "account.login_masked"),
    server: expectString(a.server, "account.server"),
    company: expectString(a.company, "account.company"),
    currency: expectString(a.currency, "account.currency"),
    leverage: expectNumber(a.leverage, "account.leverage"),
    mode: expectString(a.mode, "account.mode"),
    tradeAllowed: expectBoolean(a.trade_allowed, "account.trade_allowed"),
    balance: expectNumber(a.balance, "account.balance"),
    equity: expectNumber(a.equity, "account.equity"),
  };
}

function parseRisk(raw: unknown): Risk | null {
  if (raw === undefined) return null;
  const r = expectObject(raw, "data.risk");
  return {
    referenceEquityZar: expectNumber(r.reference_equity_zar, "risk.reference_equity_zar"),
    referenceEquityUsc: expectNumber(r.reference_equity_usc, "risk.reference_equity_usc"),
    riskPct: expectNumber(r.risk_pct, "risk.risk_pct"),
    riskBudgetUsc: expectNumber(r.risk_budget_usc, "risk.risk_budget_usc"),
    riskBudgetZar: expectNumber(r.risk_budget_zar, "risk.risk_budget_zar"),
    pipValuePerMinLotZar: expectNumber(
      r.pip_value_per_min_lot_zar,
      "risk.pip_value_per_min_lot_zar",
    ),
    maxStopPipsAtMinLot: optionalNumber(
      r.max_stop_pips_at_min_lot,
      "risk.max_stop_pips_at_min_lot",
    ),
    table: expectArray(r.table, "risk.table").map((item, index) => {
      const row = expectObject(item, `risk.table[${index}]`);
      return {
        stopPips: expectNumber(row.stop_pips, `risk.table[${index}].stop_pips`),
        riskPerMinLotZar: expectNumber(
          row.risk_per_min_lot_zar,
          `risk.table[${index}].risk_per_min_lot_zar`,
        ),
        maxVolume: expectNumber(row.max_volume, `risk.table[${index}].max_volume`),
        actualRiskZar: expectNumber(row.actual_risk_zar, `risk.table[${index}].actual_risk_zar`),
        fits: expectBoolean(row.fits, `risk.table[${index}].fits`),
      };
    }),
  };
}

function parseHistory(raw: unknown): Record<string, HistoryTimeframe> {
  if (raw === undefined) return {};
  const h = expectObject(raw, "data.history");
  const out: Record<string, HistoryTimeframe> = {};
  for (const [timeframe, value] of Object.entries(h)) {
    const t = expectObject(value, `history.${timeframe}`);
    out[timeframe] = {
      bars: expectNumber(t.bars, `history.${timeframe}.bars`),
      firstUtc: expectString(t.first_utc, `history.${timeframe}.first_utc`),
      lastUtc: expectString(t.last_utc, `history.${timeframe}.last_utc`),
      spanDays: expectNumber(t.span_days, `history.${timeframe}.span_days`),
      gapsWeekend: expectNumber(t.gaps_weekend, `history.${timeframe}.gaps_weekend`),
      gapsUnexpected: expectNumber(t.gaps_unexpected, `history.${timeframe}.gaps_unexpected`),
      longestUnexpectedGapH: expectNumber(
        t.longest_unexpected_gap_h,
        `history.${timeframe}.longest_unexpected_gap_h`,
      ),
      utcClassified: expectBoolean(t.utc_classified, `history.${timeframe}.utc_classified`),
    };
  }
  return out;
}

function parseSpreadByHour(raw: unknown): SpreadByHour | null {
  if (raw === undefined) return null;
  const s = expectObject(raw, "data.spread_by_hour");
  return {
    utcClassified: expectBoolean(s.utc_classified, "spread.utc_classified"),
    barTimeframe: expectString(s.bar_timeframe, "spread.bar_timeframe"),
    note: expectString(s.note, "spread.note"),
    hours: expectArray(s.hours, "spread.hours").map((item, index) => {
      const row = expectObject(item, `spread.hours[${index}]`);
      return {
        hourUtc: expectNumber(row.hour_utc, `spread.hours[${index}].hour_utc`),
        hourSast: expectNumber(row.hour_sast, `spread.hours[${index}].hour_sast`),
        samples: expectNumber(row.samples, `spread.hours[${index}].samples`),
        medianPips: optionalNumber(row.median_pips, `spread.hours[${index}].median_pips`),
        p90Pips: optionalNumber(row.p90_pips, `spread.hours[${index}].p90_pips`),
      };
    }),
  };
}

function parseReport(id: string, text: string): Report {
  const root = expectObject(JSON.parse(text), "report");
  const data = root.data === undefined ? {} : expectObject(root.data, "data");
  const usd = data.usd_zar === undefined ? null : expectObject(data.usd_zar, "data.usd_zar");
  const offset =
    data.utc_offset === undefined ? null : expectObject(data.utc_offset, "data.utc_offset");
  return {
    id,
    generatedUtc: expectString(root.generated_utc, "generated_utc"),
    overall: expectStatus(root.overall, "overall"),
    checks: parseChecks(root.checks),
    account: parseAccount(data.account),
    usdZar: usd
      ? {
          rate: expectNumber(usd.rate, "usd_zar.rate"),
          source: expectString(usd.source, "usd_zar.source"),
        }
      : null,
    risk: parseRisk(data.risk),
    history: parseHistory(data.history),
    spreadByHour: parseSpreadByHour(data.spread_by_hour),
    marketOpen: data.market_open_utc === undefined ? null : expectBoolean(data.market_open_utc, "market_open_utc"),
    utcOffsetSeconds: offset ? optionalNumber(offset.seconds, "utc_offset.seconds") : null,
  };
}

export function reportDirectory(): string {
  return process.env.MT5_REPORT_DIR ?? path.resolve(process.cwd(), "..", "reports");
}

// Newest first. File names embed the UTC stamp, so reverse lexical order is chronological.
export async function listReports(): Promise<ReportEntry[]> {
  const dir = reportDirectory();
  let names: string[];
  try {
    names = await readdir(/*turbopackIgnore: true*/ dir);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const files = names.filter((name) => REPORT_FILE.test(name)).sort().reverse().slice(0, MAX_REPORTS);
  return Promise.all(
    files.map(async (name): Promise<ReportEntry> => {
      const id = name.replace(/\.json$/, "");
      try {
        const text = await readFile(path.join(/*turbopackIgnore: true*/ dir, name), "utf8");
        return { ok: true, report: parseReport(id, text) };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return { ok: false, id, error: message };
      }
    }),
  );
}

/**
 * The newest report on disk, from the file names alone - cheap enough to poll.
 *
 * Reading every report to answer "is there a newer one?" would parse up to 50 files, and the
 * question is asked every few seconds by a page that only needs the id.
 */
export async function latestReportId(): Promise<string | null> {
  const dir = reportDirectory();
  let names: string[];
  try {
    names = await readdir(/*turbopackIgnore: true*/ dir);
  } catch {
    return null;
  }
  const newest = names.filter((name) => REPORT_FILE.test(name)).sort().reverse()[0];
  return newest ? newest.replace(/\.json$/, "") : null;
}

export async function getReport(id: string): Promise<ReportEntry | null> {
  if (!/^diagnostic-\d{8}-\d{6}$/.test(id)) return null;
  const entries = await listReports();
  return entries.find((entry) => (entry.ok ? entry.report.id : entry.id) === id) ?? null;
}
