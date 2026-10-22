import { readFile } from "node:fs/promises";
import path from "node:path";

export interface LiveStatus {
  available: boolean;
  reason?: string;
  ts?: string;
  heartbeatAgeS?: number;
  connected?: boolean;
  symbol?: string;
  /** Per-symbol readings for every watched symbol after the primary, keyed by symbol. */
  symbols?: Record<string, SymbolLive>;
  barEpoch?: number | null;
  barAgeS?: number | null;
  spreadPips?: number | null;
  gateVerdict?: string;
  gateReasons?: string[];
  recId?: number | null;
  watcherAlive?: boolean;
  mode?: string;
  marketState?: string;
  heartbeatTtlS?: number;
}

const MAX_HEARTBEAT_AGE_S = 180;

/**
 * One watched symbol's own reading, as the status's `symbols` map carries it.
 *
 * The heartbeat is the watcher's; everything else - the bar that symbol last saw, how wide its
 * spread is, what its own cycle's gate said - is per symbol, so a view of EURUSD can say EURUSD's
 * numbers rather than silently quoting the primary market's.
 */
export interface SymbolLive {
  available: boolean;
  ts?: string;
  connected?: boolean;
  barEpoch?: number | null;
  barAgeS?: number | null;
  spreadPips?: number | null;
  gateVerdict?: string;
  gateReasons?: string[];
  recId?: number | null;
}

function asString(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function liveStatusPath(): string {
  return process.env.MT5_LIVE_STATUS ?? path.resolve(process.cwd(), "..", "dashboard", "live-status.json");
}

export async function readLiveStatus(): Promise<LiveStatus> {
  const file = liveStatusPath();
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ file, "utf8");
  } catch {
    return { available: false, reason: "The watcher has not written a status file yet." };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { available: false, reason: "The status file is not valid JSON." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { available: false, reason: "The status file has an unexpected shape." };
  }
  const data = parsed as Record<string, unknown>;
  const ts = asString(data.ts, "");
  const parsedTs = Date.parse(ts);
  const heartbeatAgeS = Number.isFinite(parsedTs) ? Math.round((Date.now() - parsedTs) / 1000) : null;
  const symbols: Record<string, SymbolLive> = {};
  if (typeof data.symbols === "object" && data.symbols !== null) {
    for (const [name, value] of Object.entries(data.symbols as Record<string, unknown>)) {
      if (typeof value !== "object" || value === null) continue;
      const row = value as Record<string, unknown>;
      symbols[name.toUpperCase()] = {
        available: row.available === true,
        ts: typeof row.ts === "string" ? row.ts : undefined,
        connected: typeof row.connected === "boolean" ? row.connected : undefined,
        barEpoch: asNumberOrNull(row.bar_epoch),
        barAgeS: asNumberOrNull(row.bar_age_s),
        spreadPips: asNumberOrNull(row.spread_pips),
        gateVerdict: asString(row.gate_verdict, ""),
        gateReasons: Array.isArray(row.gate_reasons) ? row.gate_reasons.map(String) : [],
        recId: asNumberOrNull(row.rec_id),
      };
    }
  }
  return {
    available: true,
    ts: ts || undefined,
    heartbeatAgeS: heartbeatAgeS ?? undefined,
    connected: typeof data.connected === "boolean" ? data.connected : undefined,
    symbol: typeof data.symbol === "string" ? data.symbol : undefined,
    barEpoch: asNumberOrNull(data.bar_epoch),
    barAgeS: asNumberOrNull(data.bar_age_s),
    spreadPips: asNumberOrNull(data.spread_pips),
    gateVerdict: asString(data.gate_verdict, ""),
    gateReasons: Array.isArray(data.gate_reasons) ? data.gate_reasons.map(String) : [],
    recId: asNumberOrNull(data.rec_id),
    watcherAlive: typeof data.watcher_alive === "boolean" ? data.watcher_alive : undefined,
    mode: typeof data.mode === "string" ? data.mode : undefined,
    marketState: typeof data.market_state === "string" ? data.market_state : undefined,
    heartbeatTtlS: asNumberOrNull(data.heartbeat_ttl_s) ?? undefined,
    symbols: Object.keys(symbols).length > 0 ? symbols : undefined,
  };
}

export function heartbeatStale(status: LiveStatus): boolean {
  if (!status.available || typeof status.heartbeatAgeS !== "number") return true;
  // Idle/heartbeat TTL comes from the watcher itself so idle polling (5 min) is not "stale".
  const ttl = status.heartbeatTtlS ?? MAX_HEARTBEAT_AGE_S;
  return status.heartbeatAgeS > ttl + 60;
}
