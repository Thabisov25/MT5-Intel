import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

/**
 * The trade journal the watcher writes, plus the labels the trader puts on it.
 *
 * Two files, two writers, one view. The watcher owns `trades.json` and rebuilds it from the
 * terminal's own positions and deal history; the labels - which strategy the trader was actually
 * following - are the trader's own record and are written here by the API route. Nothing writes
 * over anything else's file, so there is no lock to get wrong.
 *
 * Both reads are tolerant: the artifact is written by another process on a timer, so it can be
 * missing, mid-replace, or from an older version of the writer.
 */

export type TradeStatus = "open" | "closed";
export type Side = "buy" | "sell";

export interface TradeContextRow {
  family: string;
  params: string | null;
  side: Side | null;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  signalUtc: string | null;
  rNow: number | null;
}

export interface TradeRow {
  ticket: number;
  symbol: string;
  digits: number;
  side: Side | null;
  volume: number;
  status: TradeStatus;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  priceNow: number | null;
  profit: number;
  swap: number;
  commission: number;
  net: number;
  openedUtc: string | null;
  closedUtc: string | null;
  exit: number | null;
  comment: string | null;
  magic: number | null;
  deals: number;
  /** The strategies that had a live card when the trade was first seen. */
  context: TradeContextRow[];
  contextUtc: string | null;
  contextCapturedUtc: string | null;
  /** The strategy the trader says they were following, or null while it is unlabelled. */
  label: string | null;
  labelNote: string | null;
}

export interface Trades {
  generatedUtc: string;
  clock: string;
  symbol: string;
  account: number | null;
  currency: string | null;
  lookbackDays: number;
  contextAsofUtc: string | null;
  openCount: number;
  openNet: number;
  closedCount: number;
  closedNet: number;
  totalNet: number;
  wins: number;
  losses: number;
  winRate: number | null;
  trades: TradeRow[];
}

export interface TradeLabel {
  strategy: string;
  note: string | null;
  ts: string;
}

const LABEL_KEY = /^\d{1,12}$/;

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function side(value: unknown): Side | null {
  return value === "buy" || value === "sell" ? value : null;
}

export function tradesPath(): string {
  return process.env.MT5_TRADES_FILE ?? path.resolve(process.cwd(), "..", "dashboard", "trades.json");
}

export function labelsPath(): string {
  return process.env.MT5_TRADE_LABELS ?? path.resolve(process.cwd(), "..", "data", "trade-labels.json");
}

function parseContext(raw: unknown): TradeContextRow[] {
  if (!Array.isArray(raw)) return [];
  const rows: TradeContextRow[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) continue;
    const row = item as Record<string, unknown>;
    const family = str(row.family);
    if (family === null) continue;
    rows.push({
      family,
      params: str(row.params),
      side: side(row.side),
      entry: num(row.entry),
      sl: num(row.sl),
      tp: num(row.tp),
      signalUtc: str(row.signal_utc),
      rNow: num(row.r_now),
    });
  }
  return rows;
}

function parseRow(raw: unknown): TradeRow | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const row = raw as Record<string, unknown>;
  const ticket = num(row.ticket);
  const status = str(row.status);
  if (ticket === null || (status !== "open" && status !== "closed")) return null;
  return {
    ticket,
    symbol: str(row.symbol) ?? "",
    digits: num(row.digits) ?? 5,
    side: side(row.side),
    volume: num(row.volume) ?? 0,
    status,
    entry: num(row.entry),
    sl: num(row.sl),
    tp: num(row.tp),
    priceNow: num(row.price_now),
    profit: num(row.profit) ?? 0,
    swap: num(row.swap) ?? 0,
    commission: num(row.commission) ?? 0,
    net: num(row.net) ?? 0,
    openedUtc: str(row.opened_utc),
    closedUtc: str(row.closed_utc),
    exit: num(row.exit),
    comment: str(row.comment),
    magic: num(row.magic),
    deals: num(row.deals) ?? 0,
    context: parseContext(row.context),
    contextUtc: str(row.context_utc),
    contextCapturedUtc: str(row.context_captured_utc),
    label: null,
    labelNote: null,
  };
}

function parseTrades(parsed: unknown): Trades | null {
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const root = parsed as Record<string, unknown>;
  const rows = Array.isArray(root.trades) ? root.trades : [];
  const trades = rows.map(parseRow).filter((row): row is TradeRow => row !== null);
  const open = trades.filter((row) => row.status === "open");
  return {
    generatedUtc: str(root.generated_utc) ?? "",
    clock: str(root.clock) ?? "server",
    symbol: str(root.symbol) ?? "",
    account: num(root.account),
    currency: str(root.currency),
    lookbackDays: num(root.lookback_days) ?? 180,
    contextAsofUtc: str(root.context_asof_utc),
    openCount: num(root.open_count) ?? open.length,
    openNet: num(root.open_net) ?? 0,
    closedCount: num(root.closed_count) ?? trades.length - open.length,
    closedNet: num(root.closed_net) ?? 0,
    totalNet: num(root.total_net) ?? 0,
    wins: num(root.wins) ?? 0,
    losses: num(root.losses) ?? 0,
    winRate: num(root.win_rate),
    trades,
  };
}

export async function readTrades(): Promise<Trades | null> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ tradesPath(), "utf8");
  } catch {
    return null; // the watcher has not written one yet
  }
  try {
    return parseTrades(JSON.parse(text));
  } catch {
    return null;
  }
}

function parseLabels(parsed: unknown): Record<string, TradeLabel> {
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
  const out: Record<string, TradeLabel> = {};
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!LABEL_KEY.test(key)) continue;
    if (typeof value !== "object" || value === null || Array.isArray(value)) continue;
    const row = value as Record<string, unknown>;
    const strategy = str(row.strategy);
    if (strategy === null) continue;
    out[key] = { strategy, note: str(row.note), ts: str(row.ts) ?? "" };
  }
  return out;
}

export async function readLabels(): Promise<Record<string, TradeLabel>> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ labelsPath(), "utf8");
  } catch {
    return {};
  }
  try {
    return parseLabels(JSON.parse(text));
  } catch {
    return {};
  }
}

function inferAutoStrategy(comment: string | null): string | null {
  if (!comment) return null;
  const match = /^MTI\s+(?:time\s+)?([a-z0-9_]+)/i.exec(comment.trim());
  return match ? match[1] : null;
}

/**
 * The journal narrowed to one symbol, with its totals recomputed for that slice.
 *
 * The journal is account-wide on purpose - positions on XAUUSD taken by hand are real trades the
 * account made - but a page that names a symbol is asking about that symbol, so the slice carries
 * its own counts rather than showing the account's numbers beside one symbol's rows. The totals
 * are recomputed from the rows (net sign decides a win, as the watcher's builder does); the
 * untouched journal keeps the watcher's own summary figures.
 */
export function filterTradesBySymbol(
  source: Trades | null,
  symbol: string | null | undefined,
): Trades | null {
  if (!source) return null;
  const wanted = typeof symbol === "string" && symbol !== "ALL" ? symbol.trim().toUpperCase() : "";
  if (!wanted) return source;
  const rows = source.trades.filter((row) => row.symbol.trim().toUpperCase() === wanted);
  const open = rows.filter((row) => row.status === "open");
  const closed = rows.filter((row) => row.status === "closed");
  const netOf = (list: TradeRow[]) => list.reduce((sum, row) => sum + row.net, 0);
  const wins = closed.filter((row) => row.net > 0).length;
  const losses = closed.filter((row) => row.net < 0).length;
  const decided = wins + losses;
  return {
    ...source,
    symbol: wanted,
    openCount: open.length,
    openNet: netOf(open),
    closedCount: closed.length,
    closedNet: netOf(closed),
    totalNet: netOf(rows),
    wins,
    losses,
    winRate: decided > 0 ? wins / decided : null,
    trades: rows,
  };
}

/** The journal with the trader's own strategy choice (or the auto-trader's comment) folded onto each row. */
export async function readTradesWithLabels(): Promise<Trades | null> {
  const [trades, labels] = await Promise.all([readTrades(), readLabels()]);
  if (trades === null) return null;
  return {
    ...trades,
    trades: trades.trades.map((row) => {
      const label = labels[String(row.ticket)];
      if (label) {
        return { ...row, label: label.strategy, labelNote: label.note };
      }
      const autoStrat = inferAutoStrategy(row.comment);
      if (autoStrat) {
        return { ...row, label: autoStrat, labelNote: "auto-trade" };
      }
      return row;
    }),
  };
}

/**
 * Record, or clear, which strategy a trade was following.
 *
 * Written next to a temporary file and renamed over the real one, so a reader either sees the
 * previous labels or the new ones - never half a file. An empty strategy removes the row: a
 * cleared label is the absence of a record, not a record of nothing.
 */
export async function writeLabel(ticket: number, strategy: string | null, note: string | null): Promise<void> {
  const file = labelsPath();
  const labels = await readLabels();
  const key = String(ticket);
  if (strategy === null) {
    delete labels[key];
  } else {
    labels[key] = { strategy, note, ts: new Date().toISOString().slice(0, 19) + "Z" };
  }
  await mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  await writeFile(tmp, `${JSON.stringify(labels, null, 2)}\n`, "utf8");
  await rename(tmp, file);
}
