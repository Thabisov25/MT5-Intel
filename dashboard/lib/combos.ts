/**
 * Two arrows on one candle, read as one thing with a name instead of two rules contradicting.
 *
 * A candle two rules point at from opposite sides is not a failed signal: it is a repeatable event
 * with an identity — *these two rules, these two sides, this one bar* — and the chart draws it every
 * time it happens. That identity is what this file names. Each rule keeps a single letter, so an
 * exact combination reads as a short handle (`G↓ L↑` = pullback_scalp selling and trend_pullback
 * buying) that is stable, is derived from the payload rather than remembered between visits, and can
 * be written next to the arrows on the chart.
 *
 * The names are the structure. The direction is a separate question, and it was measured rather than
 * assumed: `COMBINATION_STUDY` and `COMBINATION_RECORD` are what the exported chart history
 * (mt5intel_H1.csv, 41 combination candles, 39 resolved) says about where these candles sit and
 * which side paid. Read the numbers before reading a combination as a signal: the placement reading
 * the panel prints is deliberately weaker than "buy at a low, sell at a high", because that rule
 * scored below a coin flip on this history.
 */

/** The catalogue id this family is filed under; it never appears as a row of its own. */
export const COMBO_FAMILY = "combo";

/**
 * One letter per research family, assigned alphabetically over the ids the rules are registered
 * under. Fixed on purpose: a combination's name is the payload's family ids put through this table,
 * so the same two arrows always read the same way and no letter has to be stored anywhere.
 */
export const FAMILY_CODES: Record<string, string> = {
  asian_fade: "A",
  carry_trend: "B",
  compression_breakout: "C",
  daily_momentum: "D",
  ma_crossover: "E",
  orb_london: "F",
  pullback_scalp: "G",
  range_extreme_mr: "H",
  rsi_mean_reversion: "I",
  sr_flip: "J",
  sweep_reclaim: "K",
  trend_pullback: "L",
};

/** The letter a family is named by, or `?` for an id the catalogue does not know yet. */
export function familyCode(id: string): string {
  return FAMILY_CODES[id] ?? "?";
}

export interface ComboMember {
  family: string;
  side: "buy" | "sell";
}

/** `↑` is a buy, `↓` a sell. Both letters and side are in the name, so nothing is left to context. */
export function sideMark(side: string | null): string {
  return side === "buy" ? "↑" : side === "sell" ? "↓" : "?";
}

/**
 * The combination's name: every member as letter plus side, in alphabetical order.
 *
 * Sorting is what makes it an identity rather than a list — the export groups rows by bar and the
 * payload lists rules in whatever order the writer walked them, and both have to produce the same
 * name for the same candle.
 */
export function combinationCode(members: readonly ComboMember[]): string {
  return members
    .map((member) => `${familyCode(member.family)}${sideMark(member.side)}`)
    .sort()
    .join(" ");
}

/** How often a combination has fired on one candle, and which side paid when it did. */
export interface CombinationRecord {
  /** Candles in the exported history where exactly these rules pointed at the same bar. */
  seen: number;
  /** Of those candles that have finished, how many times the buy side paid. */
  buyPaid: number | null;
  /** …and how many times the sell side did. */
  sellPaid: number | null;
}

/**
 * The measured record of every combination the exported history contains, keyed by its code.
 *
 * Transcribed from `mt5intel_H1.csv` (the file the chart itself is drawn from) by
 * `tools/_combo_table.py`, the same way the strategy catalogue carries its measured status rather
 * than a claim. `seen` counts every candle; `buyPaid`/`sellPaid` count only the finished ones, so
 * they can sum to less than `seen`, and are null where the candle is still open.
 */
export const COMBINATION_RECORD: Record<string, CombinationRecord> = {
  "G↓ L↑": { seen: 4, buyPaid: 2, sellPaid: 1 },
  "C↑ G↑ H↓": { seen: 3, buyPaid: 2, sellPaid: 1 },
  "C↑ H↓": { seen: 3, buyPaid: 0, sellPaid: 3 },
  "G↑ H↓": { seen: 3, buyPaid: 2, sellPaid: 1 },
  "D↓ L↑": { seen: 2, buyPaid: 0, sellPaid: 2 },
  "E↓ L↑": { seen: 2, buyPaid: 1, sellPaid: 1 },
  "I↓ K↑": { seen: 2, buyPaid: 2, sellPaid: 0 },
  "B↑ E↓": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "B↑ E↓ K↑": { seen: 1, buyPaid: null, sellPaid: null },
  "B↑ L↓": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "C↑ G↑ H↓ K↑ L↓": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "C↑ H↓ J↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "C↑ L↓": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "C↓ D↓ H↑ L↑": { seen: 1, buyPaid: 0, sellPaid: 1 },
  "C↓ D↓ L↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "D↓ H↑ L↑": { seen: 1, buyPaid: 0, sellPaid: 1 },
  "E↑ H↓ L↓": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "E↑ I↓": { seen: 1, buyPaid: 0, sellPaid: 1 },
  "E↓ G↓ L↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "G↑ H↓ L↓": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "G↓ H↑": { seen: 1, buyPaid: 0, sellPaid: 1 },
  "G↓ H↑ L↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "G↓ K↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "G↓ K↑ L↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "H↓ J↑": { seen: 1, buyPaid: 0, sellPaid: 1 },
  "I↑ J↓": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "I↓ J↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "J↓ K↑": { seen: 1, buyPaid: 1, sellPaid: 0 },
  "J↓ L↑": { seen: 1, buyPaid: 0, sellPaid: 1 },
};

/**
 * What the exported history says about combinations as a class, so the panel states a measurement
 * instead of an expectation.
 *
 * The placement rule — buy when the pair of arrows sits at a low, sell when it sits at a high — is
 * the natural reading of a candle full of arrows, and it was the first thing checked. It does not
 * hold: over the 34 resolved candles that sat in the bottom or top third of their 96-bar range it
 * called the right side 13 times (38%), and at the extremes measured as fresh highs and fresh lows
 * over the same window it called 12 and was right 5. The member kinds do not separate the candles
 * either: on the 28 candles that held both a rule that follows through and one that waits for a
 * pullback, the follow-through member paid 16 times and the pull-back member 12 — a coin flip. What
 * the history does show is a mild buy skew (24 of 39), on a pair that spent the sample rising, which
 * is a fact about USDJPY in this window and not about the combination.
 */
export const COMBINATION_STUDY = {
  /** The file every number here was read from, and the tool that reads it. */
  source: "mt5intel_H1.csv, via tools/combo_table.py",
  /** The window the placement is measured against, the same 96 bars the view's context uses. */
  rangeBars: 96,
  /** Candles two rules pointed at from opposite sides, and how many have finished. */
  candles: 41,
  resolved: 39,
  /** The side that paid on those candles: a buy-and-hold skew, not a combination property. */
  buyPaid: 24,
  sellPaid: 15,
  /** At a low buy, at a high sell — candles it called, and calls that were right. */
  placementCalls: 34,
  placementRight: 13,
  /** The same rule read as fresh 96-bar lows and highs instead of thirds. */
  extremeCalls: 12,
  extremeRight: 5,
  /** Candles holding both a follow-through rule and a pull-back rule, and which kind paid. */
  mixedCandles: 28,
  followThroughPaid: 16,
  pullBackPaid: 12,
} as const;

/** The record for one code, or a zero sighting so a card can always say something true. */
export function combinationRecord(code: string): CombinationRecord {
  return COMBINATION_RECORD[code] ?? { seen: 0, buyPaid: null, sellPaid: null };
}
