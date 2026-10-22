import { NextResponse } from "next/server";
import { buildForecast } from "@/lib/forecast";
import { readSetups } from "@/lib/setups";
import { filterTradesBySymbol, readTradesWithLabels } from "@/lib/trades";
import { normalizeSymbolParam, setupsFilter } from "@/lib/symbols";

// One payload for the forecast card: the two records the score reads, together, so the card and
// the two tables below it are reading the same files on the same tick - and, when a symbol is
// named, the same symbol's records, so a EURUSD card is never scored against an XAUUSD note.
export async function GET(request: Request) {
  const symbol = normalizeSymbolParam(new URL(request.url).searchParams.get("symbol"));
  const [setups, journal] = await Promise.all([
    readSetups(setupsFilter(symbol)),
    readTradesWithLabels(),
  ]);
  const trades = filterTradesBySymbol(journal, symbol);
  const forecast = buildForecast(setups, trades);
  return NextResponse.json(forecast, { headers: { "Cache-Control": "no-store" } });
}
