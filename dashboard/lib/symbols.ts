import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { Setups, setupsPath } from "@/lib/setups";
import { Trades, tradesPath } from "@/lib/trades";

/**
 * Which symbols this console can show, in a stable order.
 *
 * Two sources, deliberately, because a symbol can exist in one and not yet the other: the
 * watcher's per-symbol live views (`setups.json` for the primary, `setups_<SYMBOL>.json` for
 * every watched market) and the trade journal, which is account-wide - an XAUUSD position taken
 * by hand has its row there long before any watcher builds its card view. Each file is read for
 * the `symbol` it declares rather than trusting its file name, because the shared artifacts' own
 * symbol fields are the one thing both writers and readers agree on.
 */
export const ALL_SYMBOLS = "ALL";

export interface SymbolList {
  /** Watched symbols, primary first: pages that show a card view have one for each of these. */
  watched: string[];
  /** The symbol of setups.json, when it has been written at all. */
  primary: string | null;
  /** Every symbol present in the journal (so Trades can offer the filter tabs from real data). */
  traded: string[];
}

export async function listSymbols(): Promise<SymbolList> {
  const watched: string[] = [];
  const primary = await setupsSymbol("setups.json");
  if (primary) watched.push(primary);

  const folder = path.dirname(setupsPath());
  let names: string[] = [];
  try {
    names = (await readdir(folder)).filter((name) => /^setups_[A-Z0-9]{1,24}\.json$/i.test(name));
  } catch {
    names = [];
  }
  for (const name of names) {
    const found = await setupsSymbol(name);
    if (found && !watched.includes(found)) watched.push(found);
  }

  const traded: string[] = [];
  const journal = await readTrades();
  if (journal) {
    for (const row of journal.trades) {
      const name = row.symbol.toUpperCase();
      if (name && !traded.includes(name)) traded.push(name);
    }
  }
  return { watched, primary, traded };
}

async function setupsSymbol(name: string): Promise<string | null> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ path.join(path.dirname(setupsPath()), name), "utf8");
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

async function readTrades(): Promise<Trades | null> {
  let text: string;
  try {
    text = await readFile(/*turbopackIgnore: true*/ tradesPath(), "utf8");
  } catch {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null) return null;
    const rows = (parsed as Record<string, unknown>).trades;
    return { trades: Array.isArray(rows) ? rows : [] } as unknown as Trades;
  } catch {
    return null;
  }
}

/**
 * The `?symbol=` query value, read the one way every page reads it.
 *
 * "ALL" is the one non-symbol answer, and only the Trades page offers it; anything that does not
 * look like a symbol name (a path is the risk) reads as nothing, which every reader here already
 * treats as "no choice".
 */
export function normalizeSymbolParam(value: string | string[] | undefined | null): string | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (typeof raw !== "string") return null;
  const candidate = raw.trim().toUpperCase();
  if (candidate === ALL_SYMBOLS) return ALL_SYMBOLS;
  if (!/^[A-Z0-9]{1,24}$/.test(candidate)) return null;
  return candidate;
}

/** A query's chosen symbol as the setups readers want it: none for "all", name otherwise. */
export function setupsFilter(symbol: string | null): string | null {
  return symbol === ALL_SYMBOLS ? null : symbol;
}

/** The Setups payload's own symbol, or null when no view was written. */
export function setupsSymbolOf(view: Setups | null): string | null {
  const name = view?.symbol?.trim().toUpperCase() ?? "";
  return name || null;
}
