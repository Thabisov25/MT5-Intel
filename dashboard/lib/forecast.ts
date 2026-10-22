import type { Setups, SetupRow, SetupStatus } from "@/lib/setups";
import type { Trades } from "@/lib/trades";

/**
 * One question, answered from two records the console already keeps:
 *
 * "Which of the strategies that have a card right now is the one worth looking at first?"
 *
 * The score is deliberately simple and deliberately *traceable*. A strategy is ranked by what the
 * account actually did under it - the win rate of the trades labelled with it in the journal - and
 * it is lifted when the live book says something extra: another strategy pointing the same way on
 * the same candle (confluence), or a re-signal on the newest bar. Nothing is invented: a strategy
 * with no labelled history is scored on what is on the chart instead and says so, and every row
 * carries the sentences that decided it so a number is never trusted without its reasons.
 *
 * This is a pointer, not advice. The watcher's own gate decides what may trade, and the forecast
 * only ranks what the reader looks at first.
 */

export type ForecastTier = "favour" | "watch" | "struggle" | "quiet";

export interface ForecastRecord {
  /** Labelled trades with a decided result (a win or a loss). */
  decided: number;
  winRate: number | null;
  net: number;
  /** The last few labelled results, oldest first: "WWLLW" reads faster than a fraction. */
  run: string;
}

export interface ForecastAgreement {
  /** The other strategies agreeing on the same candle, same side. */
  partners: string[];
  side: "buy" | "sell";
  /** Still inside its entry window, so it is live now rather than a position being watched. */
  fresh: boolean;
  /** The candle the arrows stand on, as the chart prints it. */
  arrowUtc: string | null;
  barsHeld: number | null;
}

export interface ForecastRow {
  family: string;
  status: SetupStatus;
  side: "buy" | "sell" | null;
  tier: ForecastTier;
  record: ForecastRecord | null;
  agreement: ForecastAgreement | null;
  /** Where the running card stands in R, for open rows. */
  rNow: number | null;
  /** The position is live again on the newest bar (a re-fire while it runs). */
  refire: boolean;
  reasons: string[];
}

export interface Forecast {
  rows: ForecastRow[];
  timeframe: string;
  clock: string;
  asofBarUtc: string | null;
  generatedUtc: string;
}

const MIN_SAMPLE = 3;

const TIER_ORDER: Record<ForecastTier, number> = { favour: 0, watch: 1, struggle: 2, quiet: 3 };

/** Which members of the live book agree with `family` on one candle, same side. */
function partnersOf(setups: Setups, family: string): ForecastAgreement | null {
  const mine = setups.setups.find(
    (row) => row.family === family && row.status === "open" && row.side !== null,
  );
  if (!mine) return null;
  const partners = setups.setups
    .filter(
      (row) =>
        row.family !== family &&
        row.status === "open" &&
        row.side !== null &&
        row.side === mine.side &&
        row.signalEpoch !== null &&
        row.signalEpoch === mine.signalEpoch,
    )
    .map((row) => row.family);
  if (partners.length === 0) return null;
  return {
    partners,
    // The `find` that produced `mine` already filtered the null sides out; the type cannot see it.
    side: mine.side as "buy" | "sell",
    // Still enterable only in the first bars; matching confluence.ts MAX_ENTRY_LAG_BARS (1).
    fresh: mine.barsHeld === null || mine.barsHeld <= 1,
    arrowUtc: mine.signalUtc,
    barsHeld: mine.barsHeld,
  };
}

function recordOf(trades: Trades | null, family: string): ForecastRecord | null {
  if (!trades) return null;
  const decided: number[] = [];
  let net = 0;
  for (const row of trades.trades) {
    if (row.label !== family) continue;
    net += row.net;
    if (row.status !== "closed") continue;
    if (row.net > 0) decided.push(1);
    else if (row.net < 0) decided.push(-1);
  }
  if (decided.length === 0) return null;
  const wins = decided.filter((n) => n > 0).length;
  return {
    decided: decided.length,
    winRate: wins / decided.length,
    net,
    run: decided
      .slice(-6)
      .map((n) => (n > 0 ? "W" : "L"))
      .join(""),
  };
}

function reasonsFor(row: SetupRow, record: ForecastRecord | null, agreement: ForecastAgreement | null, refire: boolean): string[] {
  const reasons: string[] = [];
  if (row.status === "open") {
    reasons.push(
      row.barsHeld === null || row.barsHeld === 0
        ? "card just entered - live now"
        : `card is ${row.barsHeld} bars into its move`,
    );
  } else if (record === null && agreement === null) {
    reasons.push("no card and no record yet");
  }
  if (refire) reasons.push("re-signaled on the newest bar while running");
  if (agreement) {
    reasons.push(
      `${agreement.partners.length} other ${agreement.partners.length === 1 ? "strategy agrees" : "strategies agree"} on the same candle (${agreement.side})`,
    );
  }
  if (record) {
    const pct = Math.round((record.winRate ?? 0) * 100);
    reasons.push(
      `your record: ${record.run || "none"} (${pct}% of ${record.decided} labelled), ${record.net >= 0 ? "+" : ""}${record.net.toFixed(2)} net`,
    );
  }
  return reasons;
}

function tierFor(setups: Setups, row: SetupRow, record: ForecastRecord | null, agreement: ForecastAgreement | null, refire: boolean): ForecastTier {
  // "Good" asks both questions: did most labelled trades win, and is the money actually ahead. A
  // six-out-of-ten record that is deep in the red is not a good one, and a rank that lifted it
  // would be one the reader stops trusting the first time the two disagree.
  const recordGood = record !== null && record.decided >= MIN_SAMPLE && (record.winRate ?? 0) >= 0.5 && record.net > 0;
  const recordBad = record !== null && record.decided >= MIN_SAMPLE && ((record.winRate ?? 0) < 0.5 || record.net < 0);

  // A fresh agreement is the strongest thing on the page: two rules reading one bar the same way,
  // which the chart itself paints green. A winning record under it seals it; a losing one argues
  // against acting on it.
  if (agreement?.fresh && !recordBad) return "favour";
  if (row.status === "open" && recordGood) return "favour";
  if (row.status === "open" || refire) return "watch";
  if (agreement) return "watch";
  if (recordBad) return "struggle";
  if (recordGood) return "watch";
  const closed = setups.setups.find((r) => r.family === row.family && r.status !== "open" && r.status !== "none" && r.status !== "error");
  if (closed) return "quiet";
  if (record !== null || row.status !== "none") return "watch";
  return "quiet";
}

/**
 * Rank the live book: every family with a card, a record, or an agreement, best-first.
 *
 * `setups` alone is enough when the journal has nothing labelled yet; both being null returns null,
 * so the panel can say the forecast cannot be built rather than implying it read an empty one.
 */
export function buildForecast(setups: Setups | null, trades: Trades | null): Forecast | null {
  if (!setups) return null;

  // Prefer the per-family view over per-trade contexts: context is captured when the trade is
  // *seen*, while a label on the journal is the trader's own word and that is the answer wanted.
  const known = new Map<string, SetupRow>();
  for (const row of setups.setups) {
    if (![ "none", "error" ].includes(row.status)) {
      const previous = known.get(row.family);
      if (!previous || (previous.status !== "open" && row.status === "open")) known.set(row.family, row);
    }
  }
  const families = new Set<string>(known.keys());
  if (trades) for (const row of trades.trades) if (row.label) families.add(row.label);

  const rows: ForecastRow[] = [];
  for (const family of families) {
    const setup = known.get(family) ?? null;
    // A family only in the journal has no live card: its statuses are read as the record's alone.
    const row: SetupRow = setup ?? {
      family,
      params: null,
      side: null,
      status: "none",
      signalUtc: null,
      signalEpoch: null,
      ageS: null,
      barsSinceSignal: null,
      entry: null,
      sl: null,
      tp: null,
      stopPips: null,
      tpR: null,
      barsHeld: null,
      rNow: null,
      rResult: null,
      rToStop: null,
      rToTarget: null,
      closedUtc: null,
      rangePos: null,
      freshHigh: null,
      freshLow: null,
      resignals: null,
      latestSignal: null,
      signals: null,
      taken: null,
      note: null,
    };
    const record = recordOf(trades, family);
    const agreement = partnersOf(setups, family);
    const refire = row.status === "open" && row.latestSignal !== null;
    const tier = tierFor(setups, row, record, agreement, refire);
    rows.push({
      family,
      status: row.status,
      side: row.side,
      tier,
      record,
      agreement,
      refire,
      rNow: row.status === "open" ? row.rNow : null,
      reasons: reasonsFor(row, record, agreement, refire),
    });
  }
  rows.sort(
    (a, b) =>
      TIER_ORDER[a.tier] - TIER_ORDER[b.tier] ||
      (b.record?.winRate ?? 0.5) - (a.record?.winRate ?? 0.5),
  );
  return {
    rows,
    timeframe: setups.timeframe,
    clock: setups.clock,
    asofBarUtc: setups.asofBarUtc,
    generatedUtc: setups.generatedUtc,
  };
}
