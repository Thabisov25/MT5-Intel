import { NextResponse } from "next/server";
import {
  readAuto,
  readSelection,
  readSwitch,
  writeSelection,
  writeSwitch,
  type TpMode,
} from "@/lib/auto";

// The watcher rewrites the view every cycle and the switch is written here, so nothing on this
// route may be served from a cache.
export async function GET() {
  const [status, toggle, selection] = await Promise.all([readAuto(), readSwitch(), readSelection()]);
  return NextResponse.json(
    { status, toggle, selection },
    { headers: { "Cache-Control": "no-store" } },
  );
}

/**
 * The same-origin check on the one route in this app that changes something.
 *
 * A trading switch that any page on the machine can flip with a fetch is the kind of thing that
 * should not be possible by accident, so a request without an Origin, or with one that is not this
 * host, is refused rather than trusted. It is not authentication - this console is a local tool
 * with no accounts - it is the boundary that stops a random document from arming real trading.
 */
function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

// The composite, which is not a research family and so has no registry row of its own.
const CONFLUENCE = "confluence";
// A strategy name has to be something the registry could contain. This is the same bound the
// watcher applies to the file (`MAX_STRATEGY_NAME_CHARS`/`MAX_SELECTED_NAMES`), applied on the way
// in as well, so the two ends of this file agree about what a valid list is.
const NAME_RE = /^[A-Za-z0-9_]{1,40}$/;
const MAX_NAMES = 32;

type Payload =
  | { kind: "switch"; enabled: boolean }
  | {
      kind: "selection";
      strategies: string[];
      allowUnvalidated: boolean;
      confluenceMode: "members" | "fade";
      /** Per-strategy take-profit mode. Absent (the live page's older save shape) means "keep what the file has" rather than "reset to fixed" - a save that silently reset a chosen exit would be a bug with real money behind it. */
      tpMode?: Record<string, TpMode>;
    };

function parse(body: unknown): Payload | { error: string } {
  if (typeof body !== "object" || body === null) return { error: "Expected a JSON object." };
  const data = body as Record<string, unknown>;
  if (typeof data.enabled === "boolean") {
    return { kind: "switch", enabled: data.enabled };
  }
  if (!Array.isArray(data.strategies)) {
    return { error: "Send { enabled: boolean } or { strategies: string[] }." };
  }
  if (data.strategies.length > MAX_NAMES) {
    return { error: `At most ${MAX_NAMES} strategies can be selected.` };
  }
  const strategies: string[] = [];
  for (const value of data.strategies) {
    if (typeof value !== "string" || !NAME_RE.test(value)) {
      return { error: `"${String(value)}" is not a strategy name.` };
    }
    if (!strategies.includes(value)) strategies.push(value);
  }
  return {
    kind: "selection",
    strategies,
    // Strict on the way in: only a real boolean turns this on, exactly as the watcher reads it.
    allowUnvalidated: data.allowUnvalidated === true,
    // Same strictness for the confluence mode: anything but the two known words falls back to
    // the rule the research numbers were recorded under, so a typo cannot silently change the
    // way the composite enters.
    confluenceMode: data.confluenceMode === "fade" ? "fade" : "members",
    // The take-profit map, in the watcher's own shape but named as this app's field. A value that
    // is not one of the two modes is dropped rather than obeyed - the same drop the watcher
    // applies - so the two ends of this file agree. `undefined` is the live page's save shape: it
    // keeps whatever the file already carries, and an empty object is a real "every strategy
    // trades its fixed target" answer and is written as one.
    tpMode:
      typeof data.tpMode === "object" && data.tpMode !== null && !Array.isArray(data.tpMode)
        ? Object.fromEntries(
            Object.entries(data.tpMode as Record<string, unknown>).flatMap(
              ([name, mode]): [string, TpMode][] =>
                NAME_RE.test(name) && (mode === "fixed" || mode === "swing")
                  ? [[name, mode]]
                  : [],
            ),
          )
        : undefined,
  };
}

export async function POST(request: Request) {
  if (!isSameOrigin(request)) {
    return NextResponse.json(
      { error: "This switch can only be changed from the console itself." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Expected a JSON body." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  const parsed = parse(body);
  if ("error" in parsed) {
    return NextResponse.json({ error: parsed.error }, { status: 400, headers: { "Cache-Control": "no-store" } });
  }

  if (parsed.kind === "selection") {
    const status = await readAuto();
    const known = new Set((status.registry ?? []).map((row) => row.family));
    if (known.size > 0) {
      const unknown = parsed.strategies.filter((name) => name !== CONFLUENCE && !known.has(name));
      if (unknown.length > 0) {
        return NextResponse.json(
          { error: `Not in the research registry: ${unknown.join(", ")}.` },
          { status: 400, headers: { "Cache-Control": "no-store" } },
        );
      }
    }
    const selection = await readSelection();
    await writeSelection(
      parsed.strategies,
      parsed.allowUnvalidated,
      parsed.confluenceMode,
      // No map in the body means the save came from a form that does not know about take-profit
      // modes (or was written before they existed), and the file's own map then stands.
      parsed.tpMode ?? selection?.tpMode,
    );
  } else {
    await writeSwitch(parsed.enabled);
  }

  const [status, toggle, selection] = await Promise.all([readAuto(), readSwitch(), readSelection()]);
  return NextResponse.json({ status, toggle, selection }, { headers: { "Cache-Control": "no-store" } });
}
