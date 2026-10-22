/**
 * What each research family actually does, in the system's own words.
 *
 * This is the one place the rules are described for a reader: every table that shows a
 * family id renders the name, the id tag and this description next to each other, so no
 * page ever asks the reader to know what `orb_london` means or to click somewhere else.
 *
 * Rules are transcribed from src/mt5_intel/research/strategies.py (the eight families) and
 * strategies_scalp.py (the four scalping templates). `evidence` is the measured status as of
 * the 2026-10-11 runs, not a claim. Several rules carry a confirmation window (how many bars
 * a setup has to complete) and one carries an ATR hysteresis band: both are the tolerance
 * that keeps a pattern from being missed because it took one candle longer than the textbook
 * version allowed, and both are swept rather than assumed.
 *
 * The catalogue also carries one entry that is not a rule — `combo`, the name a candle takes when two
 * of the rules above point at it from opposite sides. It is here so that a reader meets two arrows on
 * one candle in the same place as the rules that drew them; it is excluded from `RULE_IDS` below,
 * because it never issues a card and no trade can be filed under it.
 */

import { COMBO_FAMILY } from "@/lib/combos";

export interface StrategyInfo {
  name: string;
  /** What the rule waits for, before any numbers. */
  waits: string;
  entry: string;
  stop: string;
  target: string;
  /** Timeframes it is native to / tested on. */
  timeframes: string;
  /** Where the rule comes from. */
  origin: string;
  /** Measured status from the iteration runs. */
  evidence: string;
}

export const STRATEGIES: Record<string, StrategyInfo> = {
  carry_trend: {
    name: "Carry-gated trend",
    waits:
      "A day where the policy-rate spread and the slow trend point the same way.",
    entry:
      "When the Fed-minus-BoJ rate sign agrees with the close versus its 100/200-bar average, take the carry side.",
    stop: "4 x ATR(20) from entry.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "D1 native; also tested on M15",
    origin: "Our own spec; carry crash dates reproduced against Federal Reserve H.10",
    evidence:
      "The only family that passed every gate (D1, and again on M15). Deliberately not wired into the live watcher.",
  },
  daily_momentum: {
    name: "Daily momentum (TSMOM)",
    waits: "The sign of the trailing 120-bar return to flip.",
    entry:
      "Take the new direction at the next bar's open after the sign flips (Moskowitz-Ooi-Pedersen, adapted to spot).",
    stop: "3 x ATR(20) from entry.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "D1 native",
    origin: "Academic TSMOM, re-implemented rather than copied",
    evidence:
      "Rejected. A small edge exists before costs (+0.122R gross on M15) and costs erase it (-0.125R net).",
  },
  sweep_reclaim: {
    name: "Sweep and reclaim",
    waits: "Price to spike through a prior swing level and close back inside it.",
    entry:
      "A bar wicks at least 0.05-0.15 ATR (the swept depth) past a confirmed swing low/high and closes back on the other side. By default sweep and reclaim share one bar; with the 3-bar reclaim window the sweep arms the setup and any of the next three bars may close back across the same frozen level.",
    stop: "2 x ATR(14) from entry, floored at 5 pips.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "H1, M15, M5",
    origin: "In-house hypothesis (H-001), no published dated example",
    evidence:
      "Rejected everywhere; negative even before costs. The 3-bar reclaim window widens how much it recognises (about 1.4x the signals) without changing the sign.",
  },
  compression_breakout: {
    name: "Compression breakout",
    waits: "A quiet box to break out of.",
    entry:
      "A close outside a 48-bar box whose width is at the low end of its own 200-bar history.",
    stop: "1 x ATR(14) from entry, floored at 5 pips.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "H1, M15, M5",
    origin: "In-house hypothesis (H-002)",
    evidence: "Rejected; the edge is about zero before costs and costs decide the sign.",
  },
  trend_pullback: {
    name: "Trend pullback",
    waits: "A pullback against a slow trend.",
    entry:
      "When the fast average is above the slow one, buy a bar that dips at least k x ATR back to the fast average.",
    stop: "k x ATR(14) from entry, floored at 5 pips.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "H1, M15, M5",
    origin: "In-house hypothesis (H-004)",
    evidence: "Rejected; the edge is about zero before costs.",
  },
  range_extreme_mr: {
    name: "Range edge reversion",
    waits: "A stretched move to the edge of a multi-day range.",
    entry:
      "Fade a close in the top or bottom 5% of a 96-bar range when RSI is extreme.",
    stop: "1.5 x ATR(14) from entry, floored at 5 pips.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "H1, M15, M5",
    origin: "In-house hypothesis (H-005)",
    evidence: "Rejected; negative even before costs.",
  },
  sr_flip: {
    name: "Support/resistance flip",
    waits: "A swing zone to break, then price to come back and hold it.",
    entry:
      "A close beyond the zone, then within 4-6 bars a retest bar that closes back beyond the break.",
    stop: "0.5 x ATR(14) past the retest bar's extreme.",
    target: "2R or 3R, from the fixed multiples the sources cite.",
    timeframes: "M5, M15 (intraday template)",
    origin: "FXOpen break-and-retest; MultiBank's 0.5-1.0 ATR stop buffer",
    evidence:
      "Rejected. On M15 the sample is large (n=439) and negative before costs (-0.115R gross).",
  },
  pullback_scalp: {
    name: "Value-line retracement",
    waits: "A trend, and a pullback into the fast average.",
    entry:
      "With the 20 EMA above the 50 SMA, buy a bar that trades 0.5-1 ATR into the 20 EMA, then a bar within the confirmation window closes back up. The window is one bar (the next bar only) by default and three when swept.",
    stop: "0.25 x ATR(14) past the pullback bar's extreme.",
    target: "1.5R or 2R (sources cite the prior high/low or a 1.272/1.618 extension).",
    timeframes: "M5, M15, H1 (intraday template)",
    origin: "ACY Securities lesson: 20 EMA value line, 50 SMA bias",
    evidence:
      "Rejected, and the clearest failure in the set: 1,454 M15 trades return -0.001R with zero cost charged, so there is no edge to rescue. The 3-bar confirmation window wins on train at H1 and M15 but the held-out result is still negative.",
  },
  ma_crossover: {
    name: "Moving-average crossover",
    waits: "The fast moving average to cross the slow one.",
    entry:
      "Long when the fast average crosses above the slow one, short when it crosses below, entered at the next bar's open. Default 20/50 SMA; 50/200 SMA (the golden cross) and 9/21 EMA are swept, and an optional ATR band must be cleared before the state may flip, so a hairline cross in a flat market cannot whipsaw it.",
    stop: "1.5 x ATR(14) from entry, floored at 5 pips.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "D1, H1, M15 (all timeframes; 50/200 is a daily-scale pair)",
    origin:
      "Textbook crossover: Investopedia's 50/200 golden cross, with StockCharts/OANDA's 20/50 intermediate pair and a Connors-style fast variant",
    evidence:
      "Rejected on every split tested. On H1 the edge is positive before costs (+0.062R gross, n=176) and costs erase it (-0.065R); on D1 the test window is positive but fails to beat its placebo (+0.055R against +0.095R), which is exactly the case the gate exists to refuse.",
  },
  rsi_mean_reversion: {
    name: "RSI mean reversion",
    waits: "An overbought or oversold RSI reading, then the turn back through the level.",
    entry:
      "Wilder's RSI(14) at 30/70. The extreme reading arms the setup and the first bar within three whose RSI closes back through the level enters; the faster 'immediate' reading (fade the extreme bar itself) is swept alongside. RSI(2) at 10/90 and 5/95 ride along as the Connors comparison.",
    stop: "1.5 x ATR(14) from entry, floored at 5 pips.",
    target: "2R, or a time exit after the hold limit.",
    timeframes: "D1, H1, M15",
    origin: "Wilder's RSI (New Concepts, 1978) with Connors' RSI(2) levels from StockCharts",
    evidence:
      "Rejected on H1 and M15, where costs erase a small gross edge. On D1 it passed every gate on the 70/30 split (test +0.099R, n=53) but not across splits - at a 50% split it is -0.034R - so it stays a research candidate.",
  },
  orb_london: {
    name: "London opening range",
    waits: "The first 15-30 minutes of the London session to define a range, then a break of it.",
    entry:
      "A bar closes 0.25 ATR beyond the 07:00 UTC range edge. One trade per side per day.",
    stop: "1 x ATR(14) from entry, or the opposite range edge; both source conventions are tested.",
    target: "2R or 3R, from the 2:1 and 3:1 the sources cite.",
    timeframes: "M5, M15 (intraday template)",
    origin: "FXOpen opening-range breakout and its 2024 session guide",
    evidence:
      "Rejected. Signals are rare once the cost rule applies: 374 raw over 312 days, 83 kept.",
  },
  /**
   * The one entry that is not a rule: it is what a candle becomes when two of the rules above point
   * at it from opposite sides.
   *
   * It is filed in the same catalogue as the rules on purpose. A reader who sees two arrows on one
   * candle needs one answer to "what am I looking at", and that answer belongs next to the two
   * things that produced it rather than somewhere else. It has no signal function and no card of its
   * own in the payload: `dashboard/lib/combos.ts` names each exact combination and carries what the
   * exported history measured about it, and the Live panel renders it from the members' own rows.
   */
  combo: {
    name: "Combination (two rules, one candle)",
    waits: "Two of the rules in this catalogue to point at the same candle from opposite sides.",
    entry:
      "Nothing of its own. Each member keeps its own side and its own entry at the next bar's open, so the combination is an event to recognise rather than an order to send, and its name is the members' letters: `G↓ L↑` is pullback_scalp selling and trend_pullback buying the same bar.",
    stop: "Each member's own stop. Two opposite stops on one candle bracket the entry, they do not replace it.",
    target: "Each member's own target, in R from its own stop.",
    timeframes: "Any timeframe a member fires on; measured on H1",
    origin:
      "Derived, not authored: triggered by the families above, so it has no code of its own in src/mt5_intel/research. Read from the same exported chart history the indicator draws, not from a backtest of its own.",
    evidence:
      "Measured on the exported history (41 combination candles, 39 resolved). The natural reading - buy when the two arrows sit at a low, sell when they sit at a high - does not survive it: that rule called 34 candles on the bottom and top thirds of the 96-bar range and was right 13 times, and at fresh 96-bar extremes it called 12 and was right 5. The member kinds do not separate them either: where a follow-through rule met a pull-back rule the follow-through member paid 16 of 28. What is there is a buy skew (24 of 39) on a pair that rose all sample. So: recognised, named and counted, deliberately not traded.",
  },
  confluence: {
    name: "Confluence (agreeing pair)",
    waits: "Two or more allowed strategies to signal on the same closed bar.",
    entry:
      "Two modes, chosen on the dashboard (the selection file carries it; `members` is the default). Members: when two or more allowed strategies point the same way on the same candle, entered at the next bar's open on that side. Fade: the side comes from the market's placement on the agreeing bar itself — top third of the 96-bar range sells, bottom third buys, the middle stands down; where the fade flips the agreeing side, the group's tightest card is mirrored: same entry, stop and target ridden to the fade side at the same distance and R.",
    stop: "The tightest stop among the agreeing strategies (mirrored, same distance, when the fade flips the side).",
    target: "The target of the tightest card, same R (mirrored, when the fade flips the side).",
    timeframes: "H1",
    origin: "Composite of agreeing rules in src/mt5_intel/execution.py",
    evidence:
      "Both modes replayed on the exported history (tools/confluence_fade_replay.py, USDJPY H1 and M15, 303 and 217 agreeing groups, 20k bars), both with fixed barriers and with the watcher's own stop management replayed (breakeven lock at 1R, trail from 2R). H1: members' side +0.045R fixed / −0.017R managed; fade −0.199R fixed / −0.192R managed — the fade sells at highs went straight into the original stop without ever reaching the +1R a breakeven lock needs to arm (81 of 125). M15: both lose; the fade loses less (−0.23R fixed) than the members' side (−0.36R). The fade buys at lows are the one robust half on H1 (+0.09R fixed). Recorded, not hidden: the fade is offered as a deliberate choice and trades only when selected.",
  },
  asian_fade: {
    name: "Asian range fade",
    waits: "A false break of the overnight range.",
    entry:
      "A bar trades 0.5 ATR beyond the 00:00-06:00 UTC range and closes back inside, fade at the next open. The false break and the reclaim may share one bar (the default) or the break may arm the setup and any of the next three bars close back inside the same frozen range.",
    stop: "0.25 x ATR(14) past the wick that broke the range.",
    target: "The range midpoint, or the far edge (the sources' stated first target).",
    timeframes: "M5, M15 (intraday template)",
    origin: "FXNX Tokyo session range trap; one of four cited session windows",
    evidence:
      "Rejected. Its structural stop is the tightest of the four (1.8 pips), so the spread screens out almost every signal; on H1 the 3-bar reclaim window roughly doubles the signals (149 to 330 cards) and both stay negative.",
  },
};

const UNKNOWN: StrategyInfo = {
  name: "Unrecognised family",
  waits: "This id is not in the strategy catalogue.",
  entry: "Unknown rule.",
  stop: "Unknown.",
  target: "Unknown.",
  timeframes: "Unknown",
  origin: "Not documented",
  evidence: "No recorded status.",
};

export function strategyInfo(id: string): StrategyInfo {
  return STRATEGIES[id] ?? UNKNOWN;
}

export function strategyName(id: string): string {
  return (STRATEGIES[id] ?? UNKNOWN).name;
}

/**
 * The families that can produce a trade: the twelve rules, and not the combination.
 *
 * The strategy picker on the trades page and the label API both take this list rather than the
 * catalogue's keys, because `combo` is in the catalogue to be *read* - two arrows on one candle
 * belong next to the rules that drew them - and it never issues a card. A trade filed under it would
 * be filed under something that cannot trade.
 */
export const RULE_IDS: readonly string[] = Object.keys(STRATEGIES).filter(
  (id) => id !== COMBO_FAMILY,
);

/** The four costed scalp templates, for labelling groups in a table. */
export const SCALP_IDS: readonly string[] = [
  "sr_flip",
  "pullback_scalp",
  "orb_london",
  "asian_fade",
];
