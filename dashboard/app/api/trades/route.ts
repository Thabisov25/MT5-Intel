import { NextResponse } from "next/server";
import { filterTradesBySymbol, readTradesWithLabels } from "@/lib/trades";

// The watcher rewrites the journal on its own timer; the browser needs the latest copy. The
// journal itself is account-wide on purpose, so the `?symbol=` query narrows the slice here -
// the reader gets one symbol's rows and one symbol's totals, recomputed for that slice, rather
// than one symbol's rows beside the account's numbers.
export async function GET(request: Request) {
  const raw = new URL(request.url).searchParams.get("symbol");
  const symbol = typeof raw === "string" ? raw.toUpperCase() : null;
  const journal = filterTradesBySymbol(await readTradesWithLabels(), symbol);
  return NextResponse.json(journal, { headers: { "Cache-Control": "no-store" } });
}
