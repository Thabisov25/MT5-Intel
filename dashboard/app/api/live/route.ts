import { NextResponse } from "next/server";
import { readLiveStatus } from "@/lib/live";

// The watcher writes this file; the browser always needs the latest copy.
export async function GET() {
  const status = await readLiveStatus();
  return NextResponse.json(status, { headers: { "Cache-Control": "no-store" } });
}
