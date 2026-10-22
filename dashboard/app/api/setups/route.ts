import { NextResponse } from "next/server";
import { readSetups } from "@/lib/setups";

// The watcher rewrites this file on its own timer; the browser needs the latest copy.The
// `?symbol=` query names which market's view: none reads the primary, any watched symbol reads
// its own `setups_<SYMBOL>.json`, and anything unspellable reads as none - a query is not a path.
// `?tf=` names which frame's cards the view should show: an allow-listed timeframe
// (lib/setups.ts TIMEFRAMES) reads its own `setups_<SYMBOL>_<TF>.json`, anything else reads
// the H1 view the watcher trades on, so an unknown frame cannot resolve to a file on disk.
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const raw = params.get("symbol");
  const symbol = raw && /^[A-Z0-9]{1,24}$/i.test(raw) && raw.toUpperCase() !== "ALL" ? raw.toUpperCase() : null;
  const setups = await readSetups(symbol, params.get("tf") ?? "H1");
  return NextResponse.json(setups, { headers: { "Cache-Control": "no-store" } });
}
