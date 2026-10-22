import { NextResponse } from "next/server";
import { RULE_IDS } from "@/lib/strategies";
import { readLabels, writeLabel } from "@/lib/trades";

/**
 * Record which strategy a trade was following, or clear it.
 *
 * This is the one write in the console, and it only ever writes the labels file - never the
 * journal, which the watcher owns. A same-origin check keeps a page on another site from reaching
 * the local console and labelling trades on it.
 */
export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  if (origin !== null) {
    // Compared against the Host header rather than the request URL: a dev server can be reached
    // as localhost and 127.0.0.1 at once, and the URL carries whichever the server was bound to,
    // not the name the browser actually asked for.
    let sameOrigin = false;
    try {
      sameOrigin = new URL(origin).host === request.headers.get("host");
    } catch {
      sameOrigin = false;
    }
    if (!sameOrigin) {
      return NextResponse.json({ error: "cross-origin writes are refused" }, { status: 403 });
    }
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "the body must be JSON" }, { status: 400 });
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return NextResponse.json({ error: "the body must be an object" }, { status: 400 });
  }
  const raw = body as Record<string, unknown>;
  const ticket = Number(raw.ticket);
  if (!Number.isInteger(ticket) || ticket <= 0) {
    return NextResponse.json({ error: "ticket must be a position ticket" }, { status: 400 });
  }

  const requested = raw.strategy;
  let strategy: string | null;
  if (requested === null || requested === undefined || requested === "") {
    strategy = null;
  } else if (typeof requested === "string" && RULE_IDS.includes(requested)) {
    strategy = requested;
  } else {
    return NextResponse.json({ error: "unknown strategy id" }, { status: 400 });
  }

  const note =
    typeof raw.note === "string" && raw.note.trim() !== "" ? raw.note.trim().slice(0, 200) : null;
  await writeLabel(ticket, strategy, note);
  return NextResponse.json({ labels: await readLabels() }, { headers: { "Cache-Control": "no-store" } });
}
