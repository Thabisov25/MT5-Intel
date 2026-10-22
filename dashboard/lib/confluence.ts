import type { AutoRejectedRow, AutoStatus } from "@/lib/auto";
import { combinationCode, type ComboMember } from "@/lib/combos";
import type { Setups, SetupRow } from "@/lib/setups";

/**
 * Two rules reading one candle the same way: the only thing a chart shows that a table cannot.
 *
 * The indicator paints it (research.export_mt5._mark_confluence) and the auto trader folds it into
 * a single `confluence` order (execution.plans/_confluence). Both key on the same thing — live
 * cards, same bar, same side — and this is the third reader of it, so the dashboard can show the
 * agreement the chart is showing and say what the watcher would do with it.
 *
 * Two things this file exists to keep straight, because both were being read wrong:
 *
 * * A candle two rules fired on is **not** automatically a signal. Opposite sides on one bar form no
 *   pair and nothing trades it, and it looks exactly like an agreement unless something says
 *   otherwise, so `combinationsOf` says it - and names it. It is a combination, not a contradiction:
 *   the same two rules on the same bar recur, so the event has an identity (`G↓ L↑`) and a measured
 *   record, both in `lib/combos.ts`. The identity is structural; the direction is a separate,
 *   measured question, and the export answered it "no reliable side", not "conflict".
 * * A running position is **not** an entry. The chart draws a live card as thick arrows with entry
 *   and dotted stop/target lines, whether or not any order exists, because the file it draws from is
 *   the research book. An entry is only possible on the bar after a signal, so a pair sixteen bars
 *   into its move is a position being watched, not a trade waiting to be taken.
 *
 * The two constants below are copies of the Python ones, named after where they come from so a
 * change there is findable from here.
 */

/** execution.MAX_ENTRY_LAG_BARS: a card is entered on the bar after its signal, plus one of slack. */
export const MAX_ENTRY_LAG_BARS = 1;

/** execution.MAX_SETUPS_AGE_S: a setups view older than this is read as too old to act on. */
export const MAX_SETUPS_AGE_S = 900;

/**
 * execution.CONFLUENCE_FADE_TOP/BOTTOM: the placement bands the `fade` mode reads, and the same
 * third-boundary `dashboard/lib/combos.ts` measured its placement study against.
 */
export const CONFLUENCE_FADE_TOP = 2 / 3;
export const CONFLUENCE_FADE_BOTTOM = 1 / 3;

/**
 * How the composite decides the side of the order it would send.
 *
 * `members` follows the agreeing families themselves; `fade` is the account holder's placement
 * rule - high/higher-high sells, low/lower-low buys, the middle stands down. The default is the
 * rule the research numbers were recorded under; the dashboard's selection (and the watcher's
 * artifact) says which one is in force.
 */
export type ConfluenceMode = "members" | "fade";

export function confluenceModeOf(auto: AutoStatus | null): ConfluenceMode {
  return auto?.selection?.confluenceMode === "fade" ? "fade" : "members";
}

/**
 * The fade rule read off a placement fraction, exactly as execution._fade_side decides it.
 *
 * The fraction is the one the writer measured on the agreeing bar itself (per-row `range_pos`,
 * `setups.placement_at`) - not the view's last-closed-bar context, which describes a newer candle
 * than the bar the family members spoke on. Null is a real answer, the fade's own stand-down:
 * the middle third, or a placement the writer could not read, means confluence takes no side.
 */
export function fadeSideOf(rangePos: number | null): "buy" | "sell" | null {
  if (rangePos === null) return null;
  if (rangePos >= CONFLUENCE_FADE_TOP) return "sell";
  if (rangePos <= CONFLUENCE_FADE_BOTTOM) return "buy";
  return null;
}

/** The candle as the chart labels it, and the same bar in UTC. */
export interface Stamp {
  /** The bar's own clock reading - the label drawn on the chart, e.g. `2026-10-14 07:00`. */
  chart: string;
  /** The same bar in UTC. Equal to `chart` when the broker runs on UTC. */
  utc: string;
  /** How far the chart's clock is ahead of UTC, e.g. `+3 h`. Null when they agree. */
  offset: string | null;
}

function clock(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 16).replace("T", " ");
}

/**
 * A bar's own clock reading, as the chart prints it next to the candle.
 *
 * Every stamp a reader compares against the chart has to come through here. The artifacts carry two
 * readings of the same bar - this one and UTC - and a panel that mixes them cannot be matched to the
 * chart by eye, which is exactly what happened: "11:00" was said about a candle labelled "15:00".
 */
export function chartClock(epoch: number | null | undefined): string | null {
  return typeof epoch === "number" && Number.isFinite(epoch) ? clock(epoch) : null;
}

/** A timeframe label as seconds, so "the next bar" can be placed on a clock. H1 -> 3600. */
export function frameSeconds(timeframe: string): number | null {
  const match = /^([MHWD])(\d+)$/.exec(timeframe.trim().toUpperCase());
  if (!match) return null;
  const unit = { M: 60, H: 3_600, D: 86_400, W: 604_800 }[match[1] as "M" | "H" | "D" | "W"];
  const seconds = unit * Number(match[2]);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

/**
 * A bar's two clock readings, optionally moved on by whole bars.
 *
 * `signal_epoch` is the bar's own reading - the one the chart prints on the candle - and
 * `signal_utc` is the same bar minus the broker's offset. On this account they are three hours
 * apart, which is why the panel could say "11:00" about a candle the chart calls "15:00" and the
 * two could not be matched by eye. The chart's label is the one the reader has in front of them, so
 * it leads and UTC follows.
 *
 * `shiftS` moves both readings together, which is how the *entry* bar is named: the signal is what a
 * rule reacts to, but the arrow the indicator draws stands one bar later, where the order would go.
 */
export function stampOf(epoch: number | null, utc: string | null, shiftS = 0): Stamp {
  if (epoch === null) {
    return { chart: utc ?? "an unstated candle", utc: utc ?? "?", offset: null };
  }
  // The broker's offset, recovered from the two readings the payload carries for the same bar.
  const parsed = utc === null ? null : Date.parse(`${utc.replace(" ", "T")}:00Z`);
  const offsetS = parsed === null || Number.isNaN(parsed) ? 0 : epoch - Math.floor(parsed / 1000);
  const chart = clock(epoch + shiftS);
  const inUtc = clock(epoch + shiftS - offsetS);
  const hours = Math.round(offsetS / 3600);
  return {
    chart,
    utc: inUtc,
    offset: hours === 0 ? null : `${hours > 0 ? "+" : ""}${hours} h`,
  };
}

/** One bar, one side, two or more rules. */
export interface Agreement {
  /** Stable across polls, so React can key a strip on it. */
  key: string;
  /** The agreeing members' own side - what the order follows in `members` mode. */
  side: "buy" | "sell";
  /** The same side, named explicitly for the readers that want the pair, not the mode. */
  membersSide: "buy" | "sell";
  /** The fade rule's verdict on this bar; null when the middle says nothing or it cannot read. */
  fadeSide: "buy" | "sell" | null;
  /** True when the mode in force would send the order against the agreeing members. */
  mirrored: boolean;
  /** Where the agreeing bar itself sat in its range (the writer measured it on that bar). */
  placement: Placement | null;
  /** The signal bar: what the rules reacted to. */
  stamp: Stamp;
  /** The entry bar - the candle the chart's arrows actually stand on. */
  arrowStamp: Stamp;
  /** The signal bar, as the watcher and the chart agree a pair on. */
  signalUtc: string;
  entry: number;
  /** The stop and target the order would carry, from the tightest stop in the group. */
  stop: number;
  target: number;
  /** The row `stop` and `target` were taken from — `_confluence` picks the tightest stop. */
  tightest: SetupRow;
  families: string[];
  rows: SetupRow[];
  /** How far into the move the pair is. 0 is the bar after its signal; past 1 the entry has gone. */
  barsHeld: number | null;
  /** Where the running pair stands in R, net of the spread, or null when it is closed. */
  rNow: number | null;
  /**
   * Still inside its entry window, so it is something to act on rather than a position to watch.
   *
   * This is the line between the two things a candle full of arrows can be, and it is what decides
   * whether the panel highlights it: an old pair is a position, and highlighting it next to a real
   * entry teaches the reader to ignore both.
   */
  fresh: boolean;
}

/**
 * Where the candle the arrows stand on sat in its own range, when the view can place it at all.
 *
 * The view measures the range position and the fresh high/low of the last closed bar, and the arrows
 * stand on that bar only while the combination is one bar into its move. At any other age the
 * numbers describe a later candle and are not applied - a placement read off the wrong bar would be
 * worse than none, which is the mistake this whole file exists to avoid.
 */
export interface Placement {
  /** 0 is the low of the range, 1 the high. */
  fraction: number;
  /** How many bars the range was measured over, for naming it. */
  bars: number | null;
  band: "bottom" | "middle" | "top";
  freshHigh: boolean;
  freshLow: boolean;
}

/**
 * One bar, two rules, opposite sides: an event with a name.
 *
 * Two rules pointing opposite ways on one candle is the same *combination* every time it happens,
 * so it is identified rather than dismissed: `code` names the exact member set and sides, and the
 * catalogue carries how often that combination has fired and which side paid. The members keep their
 * own sides and prices - this is one candle, not one order - so the card can show both and say
 * plainly that neither is traded by the watcher.
 */
export interface Combination {
  key: string;
  /** The name of the exact combination, e.g. `G↓ L↑`. */
  code: string;
  /** The same members as data, in the code's own order. */
  members: ComboMember[];
  /** The entry bar, where both arrows are drawn. */
  arrowStamp: Stamp;
  /** Every member's row, ordered as the code orders them: the two sides' prices come from these. */
  rows: SetupRow[];
  /** How far into its move the combination is, when the members agree on that. */
  barsHeld: number | null;
  /** Where the arrows' candle sat in its range, or null when the view cannot place that candle. */
  placement: Placement | null;
  /** Inside its entry window, so the chart's arrows are on a candle worth finding in the table. */
  fresh: boolean;
}

function openRows(setups: Setups): SetupRow[] {
  return setups.setups.filter((row) => row.status === "open" && row.side !== null);
}

function keyOf(row: SetupRow): string {
  return row.signalEpoch === null ? `${row.side}|${row.signalUtc}` : `e${row.signalEpoch}`;
}

function groupBy(rows: SetupRow[]): Map<string, SetupRow[]> {
  const groups = new Map<string, SetupRow[]>();
  for (const row of rows) {
    const key = keyOf(row);
    const group = groups.get(key);
    if (group) group.push(row);
    else groups.set(key, [row]);
  }
  return groups;
}

function tightestOf(rows: SetupRow[]): SetupRow {
  // The tightest stop decides the order's exits and, through the stop, its size: the same money at
  // risk buys more of a tighter stop. Ties break alphabetically so the card does not flicker.
  return [...rows].sort(
    (a, b) =>
      (a.stopPips ?? Number.POSITIVE_INFINITY) - (b.stopPips ?? Number.POSITIVE_INFINITY) ||
      a.family.localeCompare(b.family),
  )[0];
}

/**
 * Every candle two or more running cards agree on, newest first.
 *
 * Only running cards take part — a finished trade is not live alongside anything — and only the
 * same side counts. That is exactly the filter the chart's green arrows and `execution.plans` use:
 * if the two ever disagreed, the chart and the order would be describing different candles.
 *
 * Each agreement carries both readings of the composite, because both are on the page:
 * `side` is the members' own side (what the order follows in `members` mode), `fadeSide` the
 * placement rule's verdict on this bar. `orderSide(agreement, auto)` folds them under the mode
 * actually in force, which is what the watcher's journal will hold - and where a mirror puts the
 * order against the agreeing families, `mirrored` says so.
 */
export function agreementsOf(setups: Setups): Agreement[] {
  const out: Agreement[] = [];
  const shift = frameSeconds(setups.timeframe) ?? 0;
  for (const rows of groupBy(openRows(setups)).values()) {
    const buys = rows.filter((row) => row.side === "buy");
    const sells = rows.filter((row) => row.side === "sell");
    const side = buys.length >= sells.length ? buys : sells;
    if (side.length < 2) continue;
    const tightest = tightestOf(side);
    const membersSide = tightest.side as "buy" | "sell";
    // The fade rule reads the agreeing bar itself; the context's range read is the newer read.
    const fadeSide = fadeSideOf(tightest.rangePos);
    out.push({
      key: `${tightest.side}|${tightest.signalUtc}`,
      side: membersSide,
      membersSide,
      fadeSide,
      mirrored: fadeSide !== null && fadeSide !== membersSide,
      placement:
        tightest.rangePos === null
          ? null
          : {
              fraction: tightest.rangePos,
              bars: setups.context.rangeBars,
              band:
                tightest.rangePos <= CONFLUENCE_FADE_BOTTOM
                  ? "bottom"
                  : tightest.rangePos >= CONFLUENCE_FADE_TOP
                    ? "top"
                    : "middle",
              freshHigh: tightest.freshHigh === true,
              freshLow: tightest.freshLow === true,
            },
      stamp: stampOf(tightest.signalEpoch, tightest.signalUtc),
      arrowStamp: stampOf(tightest.signalEpoch, tightest.signalUtc, shift),
      signalUtc: tightest.signalUtc ?? "",
      entry: tightest.entry ?? 0,
      stop: tightest.sl ?? 0,
      target: tightest.tp ?? 0,
      tightest,
      families: side.map((row) => row.family).sort(),
      rows: [...side].sort((a, b) => (a.stopPips ?? 0) - (b.stopPips ?? 0)),
      barsHeld: tightest.barsHeld,
      rNow: tightest.rNow,
      fresh: tightest.barsHeld !== null && tightest.barsHeld <= MAX_ENTRY_LAG_BARS,
    });
  }
  out.sort((a, b) => (a.signalUtc < b.signalUtc ? 1 : a.signalUtc > b.signalUtc ? -1 : 0));
  return out;
}

/** The side the order would carry: the mode in force folds the two readings together. */
export function orderSideOf(agreement: Agreement, auto: AutoStatus | null): "buy" | "sell" {
  return confluenceModeOf(auto) === "fade"
    ? (agreement.fadeSide ?? agreement.side)
    : agreement.side;
}

/**
 * Where the candle the arrows stand on sits in the range the view measured.
 *
 * Only readable while the combination is one bar into its move, because that is when the view's
 * context and the arrow candle are the same bar: the writer measures the last *closed* bar, and an
 * entry bar is the last closed bar for exactly that age. Older or newer and this returns null, so
 * the panel says it cannot place the candle instead of quoting a reading about a different one.
 */
function placementOf(setups: Setups, barsHeld: number | null): Placement | null {
  if (barsHeld !== 1) return null;
  const { rangePos, rangeBars, rangeHigh, rangeLow, freshHigh, freshLow } = setups.context;
  if (rangePos === null || rangeHigh === null || rangeLow === null) return null;
  return {
    fraction: rangePos,
    bars: rangeBars,
    band: rangePos <= 1 / 3 ? "bottom" : rangePos >= 2 / 3 ? "top" : "middle",
    freshHigh: freshHigh === true,
    freshLow: freshLow === true,
  };
}

/**
 * Candles two or more running cards fired on from opposite sides, newest first, each named.
 *
 * The members are ordered the way the name orders them - letter then side - so a row in the table
 * and the card above it agree on which member comes first. `tightestOf` is deliberately not used
 * here: an agreement collapses to one order because both halves want the same side, and a
 * combination has no single order to collapse to.
 */
export function combinationsOf(setups: Setups): Combination[] {
  const out: Combination[] = [];
  const shift = frameSeconds(setups.timeframe) ?? 0;
  for (const rows of groupBy(openRows(setups)).values()) {
    const buys = rows.filter((row) => row.side === "buy");
    const sells = rows.filter((row) => row.side === "sell");
    if (!buys.length || !sells.length) continue;
    // Family order is letter order - the letters are assigned alphabetically by id - so the rows and
    // the name in the card cannot disagree about which member comes first.
    const ordered = [...rows].sort((a, b) => a.family.localeCompare(b.family));
    const members: ComboMember[] = ordered.map((row) => ({
      family: row.family,
      side: row.side as "buy" | "sell",
    }));
    const code = combinationCode(members);
    const first = ordered[0];
    const barsHeld = ordered.every((row) => row.barsHeld === first.barsHeld)
      ? first.barsHeld
      : null;
    out.push({
      key: `x${first.signalUtc}`,
      code,
      members,
      arrowStamp: stampOf(first.signalEpoch, first.signalUtc, shift),
      rows: ordered,
      barsHeld,
      placement: placementOf(setups, barsHeld),
      fresh: barsHeld !== null && barsHeld <= MAX_ENTRY_LAG_BARS,
    });
  }
  out.sort((a, b) => (a.arrowStamp.chart < b.arrowStamp.chart ? 1 : -1));
  return out;
}

export interface AutoReading {
  /** Every reason the watcher would not send a *new* order for this pair, in the order it checks. */
  blockers: string[];
  /** True only when nothing above blocked it. A reading of the artifacts, not a promise of a fill. */
  tradeable: boolean;
  /** The live order this signal produced, when the account holds one. */
  held: { ticket: number | null; strategy: string; families?: string[] } | null;
  /** The journal's record of the broker refusing this signal, when it did. */
  brokerRefused: AutoRejectedRow | null;
  /** The side the order would actually carry: the mode in force, folding both readings. */
  orderSide: "buy" | "sell";
  /** Which confluence mode the reading was made under, and whether it flipped the members. */
  confluenceMode: ConfluenceMode;
  mirrored: boolean;
}

const px = (value: number, digits: number) => value.toFixed(digits);

function matches(
  row: { side?: string; signalUtc?: string | null; families?: string[] },
  a: Agreement,
  side: string,
): boolean {
  if (row.side !== side) return false;
  return (
    row.signalUtc === a.signalUtc ||
    (row.families ?? []).some((family) => a.families.includes(family))
  );
}

/**
 * Whether auto-trading would send an order for this agreement, and everything it would not.
 *
 * A *reading of the artifacts*, not a promise: the watcher decides, and it re-reads them next cycle.
 * Every blocker is collected rather than stopping at the first, the same way the gate records every
 * reason code it found — a pair refused for two reasons must not hide the second behind the first.
 *
 * The checks are the ones `execution.plans`/`execution.cycle` apply, in that order: the switch, the
 * terminal, the market, the age of the view, how far into its move the pair already is, whether each
 * family may trade at all, the position cap, and whether this signal was already taken.
 */
export function readAgreement(
  agreement: Agreement,
  auto: AutoStatus | null,
  viewAgeS: number | null,
  digits: number,
): AutoReading {
  const blockers: string[] = [];
  void digits;
  if (!auto || !auto.available) {
    blockers.push("the watcher has not written an auto-trading view yet");
    return {
      blockers,
      tradeable: false,
      held: null,
      brokerRefused: null,
      orderSide: agreement.side,
      confluenceMode: "members",
      mirrored: false,
    };
  }

  const mode = confluenceModeOf(auto);
  const mirrored = mode === "fade" && agreement.mirrored;
  const orderSide = orderSideOf(agreement, auto);

  const openRow = (auto.open ?? []).find((row) => matches(row, agreement, orderSide));
  const rejectedRow =
    (auto.rejected ?? []).find((row) => matches(row, agreement, orderSide)) ?? null;
  const held = openRow
    ? {
        ticket: openRow.ticket ?? null,
        strategy: openRow.strategy ?? "",
        families: openRow.families,
      }
    : null;

  if (auto.switch && !(auto.switch.masterEnv && auto.switch.enabled)) {
    blockers.push(
      auto.switch.masterEnv
        ? "the dashboard switch is off"
        : "AUTO_TRADE is off in the .env, so the switch cannot arm anything",
    );
  }
  if (auto.terminal && auto.terminal.tradeAllowed === false) {
    blockers.push("MT5's Algo Trading button is off");
  }
  if (auto.market && auto.market.marketOpen === false) {
    blockers.push("the market is closed");
  }
  if (viewAgeS !== null && viewAgeS > MAX_SETUPS_AGE_S) {
    blockers.push(
      `the view these cards come from has not been rebuilt for ${Math.round(viewAgeS / 60)} m, and the watcher treats an old view as unreadable`,
    );
  }
  if (agreement.barsHeld !== null && agreement.barsHeld > MAX_ENTRY_LAG_BARS) {
    // Not "the pair is old": the pair is running and the chart is right to draw it. What has gone is
    // the entry - the trade the rule pointed at was the bar after its signal, and that bar is past.
    blockers.push(
      `the entry window is closed: a card is entered on the bar after its signal with ${MAX_ENTRY_LAG_BARS} bar of slack, and this pair is already ${agreement.barsHeld} bars into its move`,
    );
  }
  if (mode === "fade" && agreement.fadeSide === null) {
    // The fade's own stand-down, not a technical blocker: the placement rule has no side for this
    // bar, so the composite sends nothing - and this reads as real information, in the same voice
    // as "the entry window is closed" above.
    const band = agreement.placement?.band ?? "middle";
    blockers.push(
      band === "middle"
        ? `the fade rule stands down: the agreeing bar sits in the middle third (${Math.round((agreement.placement?.fraction ?? 0) * 100)}%) of its ${agreement.placement?.bars ?? 96}-bar range, so confluence takes no side here - the members' own cards trade individually`
        : "the fade rule cannot read this bar's placement, and an unreadable placement stands down",
    );
  }

  const allowed = new Set(auto.allow?.allowed ?? []);
  const refused = agreement.families.filter((name) => !allowed.has(name));
  if (refused.length) {
    blockers.push(
      `${refused.join(" and ")} ${refused.length === 1 ? "is" : "are"} refused by the auto-trade list, and every half of an agreement has to be allowed for the pair to trade`,
    );
  } else if (!allowed.has("confluence")) {
    blockers.push(
      "`confluence` is not on the auto-trade list, so an agreement is not traded as a pair at all",
    );
  }

  const cap = auto.settings?.maxPositions ?? null;
  const positions = auto.market?.openPositions ?? null;
  if (cap !== null && positions !== null && positions >= cap) {
    blockers.push(`the position cap is reached (${positions} of ${cap})`);
  }
  if (openRow) {
    blockers.push("the account already holds a position on this signal, and one signal is one");
  } else if ((auto.placed ?? []).some((row) => matches(row, agreement, orderSide))) {
    blockers.push("this signal was already taken, and one signal is one position");
  }

  return {
    blockers,
    tradeable: blockers.length === 0,
    held,
    brokerRefused: rejectedRow,
    orderSide,
    confluenceMode: mode,
    mirrored,
  };
}

/**
 * The order the pair would become, as the panel and the journal describe it.
 *
 * `side` is the side the watcher would send under the mode in force - the members' side, or the
 * fade side of the mirrored geometry. The levels shown are still the agreeing group's tightest
 * card's: a mirror rides them to the other side at the same distance and the same R, which is
 * what the engine carries out and what this line must not misquote.
 */
export function orderLine(agreement: Agreement, digits: number, side?: "buy" | "sell"): string {
  const stopPips = agreement.tightest.stopPips;
  const shown = side ?? agreement.side;
  // A mirrored order cannot quote the members' stop and target - they are on the other side of
  // the entry. The mirror rides both to the fade side at the same distance and the same R
  // multiple, which is exactly what execution._mirrored carries out.
  const flip = shown === agreement.side ? 1 : -1;
  const stop = agreement.entry + (agreement.stop - agreement.entry) * flip;
  const target = agreement.entry + (agreement.target - agreement.entry) * flip;
  return `${shown} ${px(agreement.entry, digits)} · stop ${px(stop, digits)} · target ${px(target, digits)}${
    stopPips === null ? "" : ` · ${stopPips.toFixed(1)} pips risk`
  }`;
}
