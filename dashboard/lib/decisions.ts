import path from "node:path";
import { DatabaseSync } from "node:sqlite";

/**
 * The manual decision journal (master plan §6.2/§7.6): the SAME `decisions` table the
 * Python `store.record_decision` writes, so a decision taken in the terminal lands in
 * one journal. Recording a decision never places, changes or closes an order — it is a
 * journal entry only, and the terminal says so on the control.
 */

export interface DecisionRow {
  ts: string;
  decision: string;
  note: string | null;
}

/** Matches the journal's own vocabulary (tests/test_store.py). */
export const DECISIONS = ["accepted", "rejected"] as const;
export type DecisionValue = (typeof DECISIONS)[number];

export function databasePath(): string {
  return process.env.MT5_DB ?? path.resolve(process.cwd(), "..", "data", "mt5.db");
}

function open(): DatabaseSync {
  const db = new DatabaseSync(databasePath());
  // The watcher writes this journal too; wait rather than failing a decision on a busy file.
  db.exec("PRAGMA busy_timeout = 5000");
  return db;
}

/** The decisions recorded against one recommendation, oldest first. */
export function readDecisions(recId: number): DecisionRow[] {
  if (!Number.isInteger(recId) || recId <= 0) return [];
  const db = open();
  try {
    const rows = db
      .prepare("SELECT ts, decision, note FROM decisions WHERE rec_id = ? ORDER BY id")
      .all(recId) as { ts: string; decision: string; note: string | null }[];
    return rows.map((row) => ({ ts: row.ts, decision: row.decision, note: row.note }));
  } finally {
    db.close();
  }
}

/** The newest decisions across recommendations, for the terminal's activity dock. */
export function recentDecisions(limit = 20): (DecisionRow & { recId: number })[] {
  const db = open();
  try {
    const rows = db
      .prepare(
        "SELECT rec_id, ts, decision, note FROM decisions ORDER BY id DESC LIMIT ?",
      )
      .all(Math.max(1, Math.min(100, limit))) as {
      rec_id: number;
      ts: string;
      decision: string;
      note: string | null;
    }[];
    return rows.map((row) => ({ recId: row.rec_id, ts: row.ts, decision: row.decision, note: row.note }));
  } finally {
    db.close();
  }
}

/**
 * Record one manual decision against a recommendation. Returns false when the
 * recommendation id does not exist — a decision must belong to a real watcher
 * recommendation, never to an id the client invented.
 */
export function writeDecision(recId: number, decision: DecisionValue, note: string | null): boolean {
  if (!Number.isInteger(recId) || recId <= 0) return false;
  const db = open();
  try {
    const exists = db.prepare("SELECT id FROM recommendations WHERE id = ?").get(recId);
    if (!exists) return false;
    const ts = new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
    db.prepare("INSERT INTO decisions(rec_id, ts, decision, note) VALUES (?, ?, ?, ?)").run(
      recId,
      ts,
      decision,
      note,
    );
    return true;
  } finally {
    db.close();
  }
}
