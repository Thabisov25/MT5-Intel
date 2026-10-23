import { NextResponse } from "next/server";
import { readMarketBars, readMarketWatch } from "@/lib/market";

/**
 * Real market data for the terminal (master plan §12–13), served from the exporter's
 * artifacts. `?symbol=` is validated against a strict pattern before it reaches a
 * path; `?tf=` is allow-listed (H1/M30/M15). Nothing here fabricates a price —
 * missing exports come back as explicit unavailable states.
 */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const symbol = params.get("symbol");
  const timeframe = params.get("tf");
  const [bars, watch] = await Promise.all([readMarketBars(symbol, timeframe), readMarketWatch()]);
  return NextResponse.json({ bars, watch }, { headers: { "Cache-Control": "no-store" } });
}
