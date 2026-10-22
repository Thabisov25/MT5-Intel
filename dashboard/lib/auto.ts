import { readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

/**
 * The auto-trading view: what the watcher decided, and the switch that arms it.
 *
 * Two files, deliberately. `auto.json` is written by the watcher every cycle and is read-only here
 * - it is the account of what the last cycle did, including why nothing was traded. The switch is
 * a separate file this app writes and the watcher reads, so turning trading on or off is one small
 * write with no effect on the record of what has already happened.
 */

export interface AutoSwitchState {
  enabled: boolean;
  masterEnv: boolean;
  runtime: boolean;
  reason: string;
  updatedUtc?: string;
}

export interface AutoAccount {
  login?: number | null;
  currency?: string | null;
  tradeMode?: string;
  equity?: number | null;
}

export interface AutoAllow {
  requested: string[];
  allowed: string[];
  refused: { strategy: string; reason: string }[];
  /** Allowed but with no validated row behind it. Never silent: the panel shouts about these. */
  unvalidated: string[];
  maxPositions?: number;
  riskPct?: number;
  magic?: number;
}

/**
 * One family in the research registry, as the picker offers it.
 *
 * The whole list, not just the validated ones: the panel's job is to let the account holder
 * choose, and a choice made without knowing that a family is still a candidate would not be one.
 */
export interface AutoRegistryRow {
  family: string;
  status: string;
  timeframe?: string;
  n?: number | null;
  meanR?: number | null;
  edgeR?: number | null;
  stressedR?: number | null;
  foldsPositive?: number | null;
  updatedTs?: string | null;
}

/**
 * Which strategies may trade, and who decided.
 *
 * `source` is the part worth keeping: "env" means the .env list is in force and nothing has been
 * saved from this page, "dashboard" means the file below has been. Without it, a list that is
 * saved but not yet read by the watcher looks identical to one that is in force.
 */
export interface AutoSelection {
  strategies: string[];
  allowUnvalidated: boolean;
  source: string;
  updatedUtc?: string | null;
  /** How the `confluence` composite decides its side: "members" (the agreeing families' own side) or "fade" (high/higher-high sells, low/lower-low buys, middle stands down). Absent or unknown reads as "members". */
  confluenceMode?: "members" | "fade";
  /**
   * Per-strategy take-profit mode, in force: "fixed" keeps the strategy's own planned target, "swing"
   * aims just past the previous confirmed swing the artifact carried with the card. Absent for a
   * strategy means "fixed", and an empty map - the .env fallback - means every strategy is fixed.
   */
  tpMode?: Record<string, "fixed" | "swing">;
}

/** The two take-profit modes the watcher resolves an order's target by, exactly as it reads them. */
export const TP_MODES = ["fixed", "swing"] as const;
export type TpMode = (typeof TP_MODES)[number];

/**
 * A `tp_mode` map read in strictly, or undefined when the artifact carries no such map.
 *
 * The watcher drops unknown values rather than obeying them, and this reads the same way only one
 * level sooner: a "swing" spelled wrongly here would be saved as fixed-by-default, so it is read
 * as fixed-by-default here too and the comparison the page shows is always the watcher's own.
 */
function asTpModeMap(value: unknown): Record<string, TpMode> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const map: Record<string, TpMode> = {};
  for (const [name, mode] of Object.entries(value as Record<string, unknown>)) {
    if (name && (mode === "fixed" || mode === "swing")) map[name] = mode;
  }
  return Object.keys(map).length ? map : undefined;
}

export interface AutoDecision {
  strategy: string;
  families?: string[];
  side?: string;
  signalUtc?: string | null;
  entry?: number | null;
  sl?: number | null;
  tp?: number | null;
  volume?: number | null;
  riskAmount?: number | null;
  ticket?: number | null;
  retcode?: number | null;
  reason?: string;
}

export interface AutoOpenRow {
  id?: number;
  strategy?: string;
  families?: string[];
  side?: string;
  ticket?: number | null;
  volume?: number | null;
  entry?: number | null;
  sl?: number | null;
  tp?: number | null;
  signalUtc?: string | null;
  expiresEpoch?: number | null;
}

/**
 * A reservation the terminal refused, read back from the journal.
 *
 * These outlive the cycle that produced them on purpose: a refused signal is spent and never
 * retried, so without this list an armed switch that has had every entry refused looks exactly
 * like an armed switch that has had nothing to do.
 */
export interface AutoRejectedRow {
  strategy: string;
  families?: string[];
  side?: string;
  signalUtc?: string | null;
  entry?: number | null;
  sl?: number | null;
  tp?: number | null;
  volume?: number | null;
  /** The broker's refusal code: 10030 is its filling mode, 10027 the terminal's Algo button. */
  retcode?: number | null;
  brokerComment?: string | null;
  openTs?: string | null;
}

export interface AutoStatus {
  available: boolean;
  reason?: string;
  generatedUtc?: string;
  switch?: AutoSwitchState;
  selection?: AutoSelection;
  registry?: AutoRegistryRow[];
  terminal?: { tradeAllowed: boolean | null; reason?: string | null };
  account?: AutoAccount;
  allow?: AutoAllow;
  settings?: {
    master: boolean;
    allowReal: boolean;
    symbol: string;
    riskPct: number;
    maxPositions: number;
    magic: number;
  };
  open: AutoOpenRow[];
  placed: AutoDecision[];
  refused: AutoDecision[];
  /** The broker's own refusals from the journal, newest first - not just this cycle's. */
  rejected: AutoRejectedRow[];
  closed: AutoDecision[];
  /** The cycle's own stop/target management pass: handoffs and reversal closes, what it touched. */
  managed: AutoDecision[];
  errors: string[];
  notes: string[];
  market?: {
    barAgeS?: number | null;
    spreadPips?: number | null;
    marketOpen?: boolean | null;
    dailyLossPct?: number | null;
    openPositions?: number | null;
    circuitBreakerTripped?: boolean | null;
  };
}

/** Live status may also be written elsewhere, and this one follows the same convention. */
export function autoStatusPath(): string {
  return process.env.MT5_AUTO ?? path.resolve(process.cwd(), "..", "dashboard", "auto.json");
}

export function autoSwitchPath(): string {
  return (
    process.env.MT5_AUTO_SWITCH ??
    path.resolve(process.cwd(), "..", "dashboard", "auto-switch.json")
  );
}

export function autoSelectionPath(): string {
  return (
    process.env.MT5_AUTO_SELECTION ??
    path.resolve(process.cwd(), "..", "dashboard", "auto-strategies.json")
  );
}

function asString(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === "string") {
    try {
      const parsed: unknown = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      /* not JSON */
    }
  }
  return [];
}

function asOptionalFamilies(value: unknown): string[] | undefined {
  const list = asStringArray(value);
  return list.length > 0 ? list : undefined;
}

function asDecisions(value: unknown): AutoDecision[] {
  if (!Array.isArray(value)) return [];
  return value.filter((row) => typeof row === "object" && row !== null).map((row) => {
    const data = row as Record<string, unknown>;
    const families = Array.isArray(data.families) ? data.families.map(String) : undefined;
    return {
      strategy: asString(data.strategy, ""),
      families,
      side: typeof data.side === "string" ? data.side : undefined,
      signalUtc: typeof data.signal_utc === "string" ? data.signal_utc : null,
      entry: asNumberOrNull(data.entry),
      sl: asNumberOrNull(data.sl),
      tp: asNumberOrNull(data.tp),
      volume: asNumberOrNull(data.volume),
      riskAmount: asNumberOrNull(data.risk_amount),
      ticket: asNumberOrNull(data.ticket),
      retcode: asNumberOrNull(data.retcode),
      reason: typeof data.reason === "string" ? data.reason : undefined,
    };
  });
}

export async function readAuto(): Promise<AutoStatus> {
  const file = autoStatusPath();
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ file, "utf8");
  } catch {      return {
        available: false,
        reason: "The watcher has not written an auto-trading view yet.",
        open: [],
        placed: [],
        refused: [],
        rejected: [],
        closed: [],
        managed: [],
        errors: [],
        notes: [],
      };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {      return {
        available: false,
        reason: "The auto-trading view is not valid JSON.",
        open: [],
        placed: [],
        refused: [],
        rejected: [],
        closed: [],
        managed: [],
        errors: [],
        notes: [],
      };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {      return {
        available: false,
        reason: "The auto-trading view has an unexpected shape.",
        open: [],
        placed: [],
        refused: [],
        rejected: [],
        closed: [],
        managed: [],
        errors: [],
        notes: [],
      };
  }
  const data = parsed as Record<string, unknown>;
  const rawSwitch = data.switch as Record<string, unknown> | undefined;
  const rawAccount = (data.account ?? {}) as Record<string, unknown>;
  const rawAllow = (data.allow ?? {}) as Record<string, unknown>;
  const rawSettings = (data.settings ?? {}) as Record<string, unknown>;
  const rawSelection = (data.selection ?? null) as Record<string, unknown> | null;
  const rawTerminal = (data.terminal ?? null) as Record<string, unknown> | null;
  const rawMarket = (data.market ?? {}) as Record<string, unknown>;
  const refusedNames = Array.isArray(rawAllow.refused)
    ? (rawAllow.refused as unknown[])
        .filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
        .map((row) => ({ strategy: asString(row.strategy, ""), reason: asString(row.reason, "") }))
    : [];
  return {
    available: true,
    generatedUtc: asString(data.generated_utc, ""),
    selection: rawSelection
      ? {
          strategies: asStringArray(rawSelection.strategies),
          allowUnvalidated: rawSelection.allow_unvalidated === true,
          source: asString(rawSelection.source, ""),
          confluenceMode:
            rawSelection.confluence_mode === "fade" || rawSelection.confluence_mode === "members"
              ? rawSelection.confluence_mode
              : undefined,
          tpMode: asTpModeMap((rawSelection as Record<string, unknown>).tp_modes),
        }
      : undefined,
    registry: Array.isArray(data.registry)
      ? (data.registry as unknown[])
          .filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
          .map((row) => {
            const evidence = (row.evidence ?? {}) as Record<string, unknown>;
            return {
              family: asString(row.family, ""),
              status: asString(row.status, ""),
              timeframe: typeof row.timeframe === "string" ? row.timeframe : undefined,
              n: asNumberOrNull(evidence.n),
              meanR: asNumberOrNull(evidence.mean_r),
              edgeR: asNumberOrNull(evidence.edge_vs_placebo_r),
              stressedR: asNumberOrNull(evidence.stressed_mean_r),
              foldsPositive: asNumberOrNull(evidence.folds_positive_share),
              updatedTs: typeof row.updated_ts === "string" ? row.updated_ts : null,
            };
          })
          .filter((row) => row.family !== "")
      : [],
    terminal: rawTerminal
      ? {
          tradeAllowed:
            typeof rawTerminal.trade_allowed === "boolean" ? rawTerminal.trade_allowed : null,
          reason: typeof rawTerminal.reason === "string" ? rawTerminal.reason : null,
        }
      : undefined,
    switch: rawSwitch
      ? {
          enabled: rawSwitch.enabled === true,
          masterEnv: rawSwitch.master_env === true,
          runtime: rawSwitch.runtime === true,
          reason: asString(rawSwitch.reason, ""),
          updatedUtc: typeof rawSwitch.updated_utc === "string" ? rawSwitch.updated_utc : undefined,
        }
      : undefined,
    account: {
      login: asNumberOrNull(rawAccount.login),
      currency: typeof rawAccount.currency === "string" ? rawAccount.currency : null,
      tradeMode: asString(rawAccount.trade_mode, ""),
      equity: asNumberOrNull(rawAccount.equity),
    },
    allow: {
      requested: asStringArray(rawAllow.requested),
      allowed: asStringArray(rawAllow.allowed),
      refused: refusedNames,
      unvalidated: asStringArray(rawAllow.unvalidated),
      maxPositions: asNumberOrNull(rawAllow.max_positions) ?? undefined,
      riskPct: asNumberOrNull(rawAllow.risk_pct) ?? undefined,
      magic: asNumberOrNull(rawAllow.magic) ?? undefined,
    },
    settings: {
      master: rawSettings.master === true,
      allowReal: rawSettings.allow_real === true,
      symbol: asString(rawSettings.symbol, ""),
      riskPct: asNumberOrNull(rawSettings.risk_pct) ?? 0,
      maxPositions: asNumberOrNull(rawSettings.max_positions) ?? 0,
      magic: asNumberOrNull(rawSettings.magic) ?? 0,
    },
    open: Array.isArray(data.open)
      ? (data.open as unknown[])
          .filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
          .map((row) => ({
            id: row.id as number | undefined,
            strategy: asString(row.strategy, ""),
            families: asOptionalFamilies(row.families),
            side: asString(row.side, ""),
            ticket: asNumberOrNull(row.ticket),
            volume: asNumberOrNull(row.volume),
            entry: asNumberOrNull(row.entry),
            sl: asNumberOrNull(row.sl),
            tp: asNumberOrNull(row.tp),
            signalUtc: typeof row.signal_utc === "string" ? row.signal_utc : null,
            expiresEpoch: asNumberOrNull(row.expires_epoch),
          }))
      : [],
    placed: asDecisions(data.placed),
    refused: asDecisions(data.refused),
    rejected: Array.isArray(data.rejected)
      ? (data.rejected as unknown[])
          .filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
          .map((row) => ({
            strategy: asString(row.strategy, ""),
            families: asOptionalFamilies(row.families),
            side: typeof row.side === "string" ? row.side : undefined,
            signalUtc: typeof row.signal_utc === "string" ? row.signal_utc : null,
            entry: asNumberOrNull(row.entry),
            sl: asNumberOrNull(row.sl),
            tp: asNumberOrNull(row.tp),
            volume: asNumberOrNull(row.volume),
            retcode: asNumberOrNull(row.retcode),
            brokerComment:
              typeof row.broker_comment === "string" ? row.broker_comment : null,
            openTs: typeof row.open_ts === "string" ? row.open_ts : null,
          }))
      : [],
    closed: asDecisions(data.closed),
    managed: asDecisions(data.managed),
    errors: asStringArray(data.errors),
    notes: asStringArray(data.notes),
    market: {
      barAgeS: asNumberOrNull(rawMarket.bar_age_s),
      spreadPips: asNumberOrNull(rawMarket.spread_pips),
      marketOpen:
        typeof rawMarket.market_open === "boolean" ? rawMarket.market_open : null,
      dailyLossPct: asNumberOrNull(rawMarket.daily_loss_pct),
      openPositions: asNumberOrNull(rawMarket.open_positions),
      circuitBreakerTripped:
        typeof rawMarket.circuit_breaker_tripped === "boolean"
          ? rawMarket.circuit_breaker_tripped
          : null,
    },
  };
}

/**
 * The saved selection, or null when this page has never saved one.
 *
 * Null is not "nothing may trade": it means the .env list applies. The panel says which of the two
 * is in force rather than substituting its own answer, because the watcher is the one that decides.
 */
export async function readSelection(): Promise<AutoSelection | null> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ autoSelectionPath(), "utf8");
  } catch {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
    const data = parsed as Record<string, unknown>;
    if (!Array.isArray(data.strategies)) return null;
    return {
      strategies: data.strategies.map(String),
      allowUnvalidated: data.allow_unvalidated === true,
      source: "dashboard",
      updatedUtc: typeof data.updated_utc === "string" ? data.updated_utc : null,
      confluenceMode:
        data.confluence_mode === "fade" || data.confluence_mode === "members"
          ? data.confluence_mode
          : undefined,
      tpMode: asTpModeMap(data.tp_mode),
    };
  } catch {
    return null;
  }
}

/**
 * Write the selection, replacing the file in one step - the same rename the switch uses, for the
 * same reason: the watcher reads this every cycle and must never see a half-written document, whose
 * safe reading would be an empty allow-list.
 */
export async function writeSelection(
  strategies: string[],
  allowUnvalidated: boolean,
  confluenceMode?: "members" | "fade",
  tpMode?: Record<string, TpMode>,
): Promise<void> {
  const file = autoSelectionPath();
  const payload = {
    strategies,
    allow_unvalidated: allowUnvalidated,
    // Absent from the payload means "members" as far as the watcher reads it, but writing the
    // default explicitly is kinder to the reader: the artifact says which rule is in force even
    // before the watcher's own view is rebuilt.
    confluence_mode: confluenceMode === "fade" ? "fade" : "members",
    // Written per strategy, in the watcher's own shape (`tp_mode`). Not writing it would be safe
    // - an absent map reads as all-fixed - but a save that silently reset a chosen exit would be
    // a bug with real money behind it, so the map is written even when empty.
    tp_mode: tpMode ?? {},
    updated_utc: new Date().toISOString(),
    note: "Written by the dashboard's auto-trading panel. The watcher re-reads this every cycle.",
  };
  const tmp = `${file}.tmp`;
  await writeFile(tmp, JSON.stringify(payload, null, 2), "utf8");
  await rename(tmp, file);
}

export async function readSwitch(): Promise<{ enabled: boolean; reason?: string; updatedUtc?: string }> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ autoSwitchPath(), "utf8");
  } catch {
    return { enabled: false };
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { enabled: false };
    }
    const data = parsed as Record<string, unknown>;
    return {
      enabled: data.enabled === true,
      reason: typeof data.reason === "string" ? data.reason : undefined,
      updatedUtc: typeof data.updated_utc === "string" ? data.updated_utc : undefined,
    };
  } catch {
    return { enabled: false };
  }
}

/**
 * Write the switch, replacing the file in one step.
 *
 * A rename rather than a truncating write, because the watcher reads this file every cycle: a read
 * that landed mid-write would see an empty or half-written document, and this file's safe reading
 * is "off". A rename means the watcher sees the old switch or the new one and never a torn one.
 */
export async function writeSwitch(enabled: boolean, reason?: string): Promise<void> {
  const file = autoSwitchPath();
  const payload = {
    enabled,
    updated_utc: new Date().toISOString(),
    reason: reason ?? (enabled ? "Turned on from the dashboard." : "Turned off from the dashboard."),
  };
  const tmp = `${file}.tmp`;
  await writeFile(tmp, JSON.stringify(payload, null, 2), "utf8");
  await rename(tmp, file);
}
