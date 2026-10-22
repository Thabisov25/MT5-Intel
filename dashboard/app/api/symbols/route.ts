import { NextResponse } from "next/server";
import { listSymbols } from "@/lib/symbols";

/**
 * The symbols this console can show, so the header's tabs are built from the artifacts the
 * watcher actually wrote rather than a duplication of the watcher's own config.
 *
 * Read fresh rather than cached: the watcher adds a symbol's view when it starts building one,
 * and the strip should follow it rather than remember an answer.
 */
export async function GET() {
  const { watched, traded } = await listSymbols();
  const symbols: string[] = [];
  for (const name of watched) if (!symbols.includes(name)) symbols.push(name);
  for (const name of traded) if (!symbols.includes(name)) symbols.push(name);
  return NextResponse.json({ symbols }, { headers: { "Cache-Control": "no-store" } });
}
