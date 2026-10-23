import { NextResponse } from "next/server";
import {
  DECISIONS,
  readDecisions,
  recentDecisions,
  writeDecision,
  type DecisionValue,
} from "@/lib/decisions";

/**
 * Manual signal decisions for the terminal's intelligence panel (master plan §7.6).
 * GET with `?recId=` lists that recommendation's decisions; GET without it lists the
 * newest decisions across recommendations (the activity dock). POST records one.
 * All of it writes to the watcher's own journal — a decision is a journal entry,
 * never an order.
 */
export async function GET(request: Request) {
  const raw = new URL(request.url).searchParams.get("recId");
  if (raw === null) {
    return NextResponse.json(
      { decisions: recentDecisions() },
      { headers: { "Cache-Control": "no-store" } },
    );
  }
  const recId = Number.parseInt(raw, 10);
  if (!Number.isInteger(recId) || recId <= 0) {
    return NextResponse.json({ error: "recId must be a positive integer" }, { status: 400 });
  }
  return NextResponse.json(
    { recId, decisions: readDecisions(recId) },
    { headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "body must be JSON" }, { status: 400 });
  }
  const record = (body ?? {}) as Record<string, unknown>;
  const recId = typeof record.recId === "number" ? record.recId : NaN;
  const decision = record.decision;
  const note = typeof record.note === "string" && record.note.trim() !== "" ? record.note.trim().slice(0, 500) : null;

  if (!Number.isInteger(recId) || recId <= 0) {
    return NextResponse.json({ error: "recId must be a positive integer" }, { status: 400 });
  }
  if (typeof decision !== "string" || !(DECISIONS as readonly string[]).includes(decision)) {
    return NextResponse.json(
      { error: `decision must be one of ${DECISIONS.join(", ")}` },
      { status: 400 },
    );
  }

  const ok = writeDecision(recId, decision as DecisionValue, note);
  if (!ok) {
    return NextResponse.json({ error: "no such recommendation" }, { status: 404 });
  }
  return NextResponse.json(
    { recId, decisions: readDecisions(recId) },
    { status: 201, headers: { "Cache-Control": "no-store" } },
  );
}
