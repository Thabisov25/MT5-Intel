import { connection } from "next/server";
import { Terminal } from "@/components/terminal/terminal";
import { readAuto } from "@/lib/auto";
import { readLiveStatus } from "@/lib/live";
import { readMarketBars, readMarketWatch } from "@/lib/market";
import { normalizeTimeframe, readSetups, type Timeframe } from "@/lib/setups";
import { listSymbols, normalizeSymbolParam } from "@/lib/symbols";
import { readTradesWithLabels } from "@/lib/trades";

// Reads the watcher's artifacts on every request so the terminal is never served stale.
export const instant = false;

export const metadata = {
  title: "MT5 Intel · terminal",
};

/**
 * The MT5 web terminal (master plan §7/§12). USD/JPY is the default instrument — the
 * primary market the intelligence system is validated for. All panels are fed from the
 * real artifacts: market export (bars + ticks), the watcher's card views and journal,
 * and the auto registry. Nothing is fabricated; missing exports render unavailable
 * states inside the workspace.
 */
export default async function TerminalPage({
  searchParams,
}: {
  searchParams: Promise<{ symbol?: string; tf?: string }>;
}) {
  await connection();
  const params = await searchParams;
  const symbol = (normalizeSymbolParam(params.symbol) ?? "USDJPY").toUpperCase();
  const timeframe: Timeframe = normalizeTimeframe(params.tf);

  const [bars, watch, setups, journal, live, auto, { watched, traded }] = await Promise.all([
    readMarketBars(symbol, timeframe),
    readMarketWatch(),
    readSetups(symbol, timeframe),
    readTradesWithLabels(),
    readLiveStatus(),
    readAuto(),
    listSymbols(),
  ]);

  const symbols: string[] = [];
  for (const name of [symbol, ...watched, ...traded]) {
    if (name && !symbols.includes(name)) symbols.push(name);
  }

  return (
    <Terminal
      initial={{ bars, watch, setups, journal, live, auto }}
      symbols={symbols}
      initialSymbol={symbol}
      initialTimeframe={timeframe}
    />
  );
}
