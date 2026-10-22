"""Auto-trading: the rules under which a live card may be turned into a real order.

Every other module in this project reads. This is the one module that can send an order, and it
exists behind deliberate acts, because the failure mode of getting this wrong is money:

* ``AUTO_TRADE`` in the .env is the hard master and is off by default. Off means off: the dashboard
  switch cannot arm anything, and nothing below is reached.
* The dashboard switch is the second act. Even with the master on, trading does nothing until it
  is turned on there, and the state lives in a file the watcher re-reads every cycle, so turning it
  off takes effect on the next cycle rather than on the next restart.
* An account that is not a demo account is refused unless ``AUTO_TRADE_ALLOW_REAL`` is *also* set.
  One switch arms trading; a second lets it be live money.
* **Which strategies may trade is a choice, and the choice is the account holder's.**
  ``AUTO_TRADE_STRATEGIES`` in the .env, or the dashboard's own selection file, which the watcher
  re-reads every cycle so the list changes without a restart. The file wins when it exists, the
  .env list is the fallback, and the artifact says which of the two is in force. Choosing a
  strategy that research has *not* validated is possible only through
  ``AUTO_TRADE_ALLOW_UNVALIDATED`` (or the same acknowledgement in the dashboard); without it such
  a name is refused. With it, every trade it allows is flagged unvalidated in the journal and on
  the panel, because a trade with no measured edge must never look like one that has.
* A strategy trades only if it is *chosen* (or is ``confluence``, whose constituent families must
  all be chosen) *and* carries a ``historically validated`` row in the registry for the watcher's
  own timeframe. A chosen-but-unvalidated strategy is refused, and says so.

The split of work follows the one the rest of the project already uses: the research side decides
(``research.setups`` says what each rule is in, and what its stop and target are), and this module
only turns an already-decided card into an order. It re-derives nothing about a strategy.

Four more rules keep an armed switch from becoming an unattended liability — and one management rule the account holder set next to them:

* **A strategy's exit target is the mode chosen for it.** ``fixed`` - the default everywhere -
  keeps the card's own planned multiple, the only exit the research numbers were graded under.
  ``swing`` is the account holder's structure rule, opted into per strategy on the selection
  (the dashboard's strategies page writes it): a sell targets just above the previous lower-low
  the signal bar had already confirmed, a buy just below the previous higher-high, `SWING_TP_BUFFER_R`
  short of the level so a fill lands while the level still holds. A swing the market has already
  passed, or one that was never known at the signal bar, falls back to the fixed target and the
  journal says which of the two rules chose the exit.
* **A confluence signal stops everything opposite on its symbol.** When the composite is actable (the bar it would trade), every opposing position on that symbol — journal-tracked or opened by hand, the terminal is what is searched — is closed at market, once per signal, and only while the armed switches are on. It does nothing while the switches are off, and it fires only when `confluence` is itself on the allow-list, because that is what builds a composite card at all.
* **One position per signal.** A row is reserved in the journal *before* the order is sent, keyed on
  ``(strategy, signal bar, side)``, so a re-fire, a restart or a crash cannot double a position.
* **Fresh entries only.** A card that has been running for hours is not entered - the point of this
  is to take the entry the rule pointed at, not to join a move that has already gone. A card more
  than ``MAX_ENTRY_LAG_BARS`` old, or whose price has already moved ``MAX_ENTRY_SLIP_R`` of the stop
  away from the planned entry, is skipped and says which of the two it was.
* **The broker holds the exits.** The card's own stop and target are attached to the order, so MT5
  closes the trade even if this process is not running. The watcher adds only the card's own time
  limit on top.
* **The terminal has to be allowed to trade.** MT5 refuses every request with retcode 10027 while
  its Algo Trading button is off, so that is checked before anything is sent - the refusal names
  the button instead of leaving the broker to look like the one that said no.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .gate import PASS, GateInputs, evaluate
from .store import Store

CODE_VERSION = "auto-v1"

# The composite the user asked for: two or more live cards that entered the same bar on the same
# side. It is a strategy in the allow-list like any other, with one twist: every family that makes
# it up must itself be allowed, so a pair cannot be traded through this name while one half of it
# is switched off.
CONFLUENCE = "confluence"

# How the composite decides which way it enters, when it is allowed to trade at all. "members"
# follows the agreeing strategies themselves - the tightest stop of the group plans the trade,
# unchanged since D-039. "fade" is the account holder's own rule: the entry's side is the market's
# placement on the agreeing bar, not the members' agreement - the top third of the 96-bar range
# sells, the bottom third buys, and the middle stands down (nothing is decided there); a group
# faded against its own members carries the tightest card's geometry mirrored. The replayed
# evidence (tools/confluence_fade_replay.py, both grading modes) is against it on H1 and for it
# nowhere much - it is offered as a choice, and the choice is stored so the artifact always says
# which rule was in force.
CONFLUENCE_MODE_MEMBERS = "members"
CONFLUENCE_MODE_FADE = "fade"
CONFLUENCE_MODES = (CONFLUENCE_MODE_MEMBERS, CONFLUENCE_MODE_FADE)
# Where "high" and "low" begin: the top and bottom third of the range window, the same boundary
# `dashboard/lib/combos.ts` measured its placement study against, so both readers answer the
# same question with the same numbers.
CONFLUENCE_FADE_TOP = 2 / 3
CONFLUENCE_FADE_BOTTOM = 1 / 3

# How a card's target is chosen. "fixed" is the strategy's own planned multiple the research
# numbers were recorded under. "swing" is the account holder's structure rule, opt-in per
# strategy: a sell exits just above the previous lower-low, a buy just below the previous
# higher-high - the level the market had already confirmed by the signal bar, which the setups
# artifact carries alongside each card (`swing_low`/`swing_high`, setups.known_swings). The
# buffer keeps the fill on the near side of the level: a touch of the exact low is a bounce,
# not a fill. When the level names no valid target for the trade's side - none was known, or
# it has already been passed - the card's own fixed target is used instead, and the order says
# so: a structure rule that cannot produce a structure exit must not trade unplaced.
TP_MODE_FIXED = "fixed"
TP_MODE_SWING = "swing"
TP_MODES = (TP_MODE_FIXED, TP_MODE_SWING)
SWING_TP_BUFFER_R = 0.25

# How the switch file is shaped. A JSON file rather than the journal, because the dashboard writes
# it and the watcher reads it, and neither should need the other's connection.
SWITCH_FILENAME = "auto-switch.json"
# The dashboard's strategy selection: which strategies may trade, and whether the account holder has
# acknowledged trading the ones research has not validated. A second file for the same reason the
# switch is one - the dashboard writes it and the watcher reads it every cycle, so neither needs the
# other's connection, and changing the list needs no restart.
SELECTION_FILENAME = "auto-strategies.json"
# Bounds on that file. It is written by another process, so what it says is treated as input: a name
# long enough to be a mistake, and a list long enough to be one, are dropped rather than obeyed.
MAX_SELECTED_NAMES = 32
MAX_STRATEGY_NAME_CHARS = 40

# A card is entered while it is still fresh. The watcher polls every 30 s and the setups artifact
# is rebuilt every 5 minutes, while the bars are hourly, so a card that just appeared is normally
# 0 bars old; 1 is the slack for a cycle that was missed, and past that the entry has gone.
MAX_ENTRY_LAG_BARS = 1
# ...and it is skipped if price has already run this far, as a fraction of the stop, past the
# planned entry. Without this a card re-read after a busy minute would still be "fresh" while the
# move it pointed at had already happened - which is the cost of entering late that this exists to
# avoid.
MAX_ENTRY_SLIP_R = 0.25
# The setups view the cards are read from is rebuilt every 5 minutes, and a card's freshness is
# measured in that view's own bars - which means a view that stopped being rebuilt an hour ago can
# still present a card as "just entered". The price guard above catches most of that (an old entry
# is no longer near the market), but not the case where price happens to have come back to it. So
# the artifact's age is checked too, and an unreadable age is treated as too old: same reasoning as
# an unverifiable spread in the gate - the safe reading of an unknown is "do not trade".
MAX_SETUPS_AGE_S = 900

# The order's filling mode is the symbol's choice, not ours. IOC is what the research fills assume
# (immediate, at the price read), but a broker that does not take it answers 10030 and refuses the
# request outright - which is how an armed watcher can refuse every entry for its filling mode while
# every other check passes. Nothing is executed on a 10030, so the next mode is the same intent
# tried again rather than a second order.
TRADE_RETCODE_INVALID_FILL = 10030
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_PLACED = 10008

# How many of the broker's own refusals travel with the auto-trading view. A refused reservation is
# spent and never retried, so the cycle that saw it is the last one that can report it; this is what
# keeps it visible on the panel for a while instead of for thirty seconds.
AUTO_REJECTED_SHOWN = 5

DEAL_BUY = 0
DEAL_SELL = 1

STATUS_PLACING = "placing"
STATUS_PLACED = "placed"
STATUS_REJECTED = "rejected"
STATUS_CLOSED = "closed"
STATUS_UNKNOWN = "unknown"


def _utc(epoch: int | None) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(int(epoch), UTC).isoformat(timespec="seconds")


# --- the switch --------------------------------------------------------------------------


@dataclass(frozen=True)
class Switch:
    """Whether orders may be sent, and which of the two switches decided it."""

    enabled: bool
    master: bool
    runtime: bool
    reason: str
    updated_utc: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "master_env": self.master,
            "runtime": self.runtime,
            "reason": self.reason,
            "updated_utc": self.updated_utc,
        }


def read_switch(path: Path, *, master: bool) -> Switch:
    """Resolve the switch. Two independent acts, each of which can only ever turn it *off*.

    The .env master is read first and short-circuits: a dashboard toggle that is on, in a checkout
    whose .env says ``AUTO_TRADE`` is not set, still trades nothing. A switch file that is missing,
    unreadable or malformed reads as off for the same reason a missing position state default-denies
    in the gate - the safe reading of an unknown is the one that sends no orders.
    """
    runtime = False
    updated: str | None = None
    note: str | None = None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict):
        runtime = raw.get("enabled") is True
        stamp = raw.get("updated_utc")
        updated = stamp if isinstance(stamp, str) else None
        reason = raw.get("reason")
        note = reason if isinstance(reason, str) and reason else None
    if not master:
        return Switch(
            enabled=False,
            master=False,
            runtime=runtime,
            reason="AUTO_TRADE is not set in .env: the dashboard switch cannot arm this.",
            updated_utc=updated,
        )
    if not runtime:
        return Switch(
            enabled=False,
            master=True,
            runtime=False,
            reason=note or "The dashboard switch is off.",
            updated_utc=updated,
        )
    return Switch(
        enabled=True,
        master=True,
        runtime=True,
        reason="Armed: the .env master is on and the dashboard switch is on.",
        updated_utc=updated,
    )


# --- what to trade, and who chose it ------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """Which strategies may trade, and where that list came from.

    Two sources, one shape: the dashboard writes a selection file the watcher re-reads every cycle,
    and the .env list is what applies when that file is missing. Which of the two is in force is
    carried rather than resolved away, because "the .env says so" and "you chose this on the Live
    tab" are different answers to the same question, and the panel has to give the right one.
    """

    strategies: tuple[str, ...]
    allow_unvalidated: bool
    source: str
    # How the `confluence` composite decides its side when it trades. Only the dashboard file can
    # set it (the .env fallback stays "members"), so the default is the behaviour the research
    # numbers were recorded under and a checkout nobody has opened the dashboard on does not
    # surprise them.
    confluence_mode: str = CONFLUENCE_MODE_MEMBERS
    # Per-strategy take-profit mode, in force. "fixed" is absent and means "use the strategy's
    # own planned target"; "swing" is the structure rule. The .env fallback is empty - every
    # strategy trades its fixed target - so a checkout nobody has opened the strategies page on
    # does not trade exits the research numbers were never recorded under.
    tp_modes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategies": list(self.strategies),
            "allow_unvalidated": self.allow_unvalidated,
            "source": self.source,
            "confluence_mode": self.confluence_mode,
            "tp_modes": dict(self.tp_modes),
        }


def read_selection(path: Path | None, settings: Settings) -> Selection:
    """Resolve which strategies may trade, from the dashboard's file or the .env.

    A missing, unreadable or malformed file is not an error and not a licence: it means the .env
    list applies, which is what a checkout nobody has opened the dashboard on has. That direction
    is the point - this file can only ever decide *which* of the permitted things trade, never
    whether trading happens at all, because both switches above are read before this.
    """
    if path is not None:
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            names = raw.get("strategies")
            if isinstance(names, list):
                picked = tuple(
                    name
                    for name in (str(row).strip() for row in names)
                    if name and len(name) <= MAX_STRATEGY_NAME_CHARS
                )[:MAX_SELECTED_NAMES]
                return Selection(
                    strategies=picked,
                    # The acknowledgement is the file's own field and reads as off when absent, so
                    # a file written by anything that does not know about it cannot turn it on.
                    allow_unvalidated=raw.get("allow_unvalidated") is True,
                    source="dashboard",
                    confluence_mode=(
                        raw.get("confluence_mode")
                        if isinstance(raw.get("confluence_mode"), str)
                        and raw.get("confluence_mode") in CONFLUENCE_MODES
                        else CONFLUENCE_MODE_MEMBERS
                    ),
                    # Same bounds the name list has, same reason: the file is another process's
                    # pen. A name longer than any strategy id, a value that is not one of the two
                    # modes, or a map longer than the cap is dropped, not obeyed.
                    tp_modes={
                        str(key)[:MAX_STRATEGY_NAME_CHARS]: str(value)
                        for key, value in list(raw.get("tp_mode").items())[:MAX_SELECTED_NAMES]
                        if isinstance(key, str)
                        and key
                        and raw.get("tp_mode").get(key) in {TP_MODE_FIXED, TP_MODE_SWING}
                    }
                    if isinstance(raw.get("tp_mode"), dict)
                    else {},
                )
    return Selection(
        strategies=tuple(settings.auto_strategies),
        allow_unvalidated=settings.auto_allow_unvalidated,
        source="env",
        confluence_mode=CONFLUENCE_MODE_MEMBERS,
        tp_modes={},
    )


@dataclass(frozen=True)
class Card:
    """One live card, as the setups view already decided it: what, which way, and where it exits.

    `families` is the agreement behind the card: the strategy's own name for an ordinary card, or
    every family that entered this bar this way for a `confluence` card.
    """

    strategy: str
    families: tuple[str, ...]
    side: str
    signal_epoch: int
    signal_utc: str | None
    entry: float
    sl: float
    tp: float
    stop_pips: float
    stop_distance: float
    tp_r: float
    bars_held: int
    r_now: float | None
    digits: int
    # Where the market stood on the card's own signal bar, as `setups.placement_at` measured it:
    # the input the `fade` confluence mode reads. None on rows written before the field existed,
    # and None reads as "cannot be faded" - a card against an unverifiable placement does not
    # fade, the same discipline as an unverifiable spread in the gate.
    range_pos: float | None = None
    fresh_high: bool | None = None
    fresh_low: bool | None = None
    # The previous swings the signal bar had already confirmed (setups.known_swings): what a
    # "swing" target is placed at. None reads as "no structural level was known", which falls
    # back to the fixed target - a target is not skipped because its structure is silent.
    swing_low: float | None = None
    swing_high: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "families": list(self.families),
            "side": self.side,
            "signal_epoch": self.signal_epoch,
            "signal_utc": self.signal_utc,
            "entry": self.entry,
            "sl": self.sl,
            "tp": self.tp,
            "stop_pips": self.stop_pips,
            "tp_r": self.tp_r,
            "bars_held": self.bars_held,
            "r_now": self.r_now,
            "range_pos": self.range_pos,
            "fresh_high": self.fresh_high,
            "fresh_low": self.fresh_low,
            "swing_low": self.swing_low,
            "swing_high": self.swing_high,
        }


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _live_row(row: Any, digits: int) -> Card | None:
    """One row of the setups artifact as a card, or None if it is not one we could act on."""
    if not isinstance(row, dict) or row.get("status") != "open":
        return None
    family = row.get("family")
    side = row.get("side")
    epoch = row.get("signal_epoch")
    entry = _number(row.get("entry"))
    sl = _number(row.get("sl"))
    tp = _number(row.get("tp"))
    if not isinstance(family, str) or not family or side not in {"buy", "sell"}:
        return None
    if not isinstance(epoch, int) or entry is None or sl is None or tp is None:
        return None
    stop_distance = abs(entry - sl)
    if stop_distance <= 0:
        return None
    return Card(
        strategy=family,
        families=(family,),
        side=side,
        signal_epoch=int(epoch),
        signal_utc=row.get("signal_utc") if isinstance(row.get("signal_utc"), str) else None,
        entry=entry,
        sl=sl,
        tp=tp,
        stop_pips=_number(row.get("stop_pips")) or 0.0,
        stop_distance=stop_distance,
        tp_r=_number(row.get("tp_r")) or 0.0,        bars_held=int(_number(row.get("bars_held")) or 0),
        r_now=_number(row.get("r_now")),
        digits=int(digits),
        range_pos=_number(row.get("range_pos")),
        fresh_high=row.get("fresh_high") if isinstance(row.get("fresh_high"), bool) else None,
        fresh_low=row.get("fresh_low") if isinstance(row.get("fresh_low"), bool) else None,
        swing_low=_number(row.get("swing_low")),
        swing_high=_number(row.get("swing_high")),
    )


def _signal_card(row: Any, digits: int) -> Card | None:
    """A row's newest re-fire, while it holds a trade, as the card it asks to act on now.

    A rule holding one position re-fires as its condition goes on holding, and the row's nested
    `latest_signal` is what it is signalling *currently* - possibly an opposite side asking its
    held trade to be closed. Without this, the cycle could only ever see the held trade's OWN
    signal bar, whose epoch can never be newer than itself: the hand-off and reversal paths
    below were machinery wired to a junction nothing ever arrived at, and every fresh signal
    sat behind the live trade until TP or SL ended it. The nested card enters through the same
    twin/opposite/dead desk as any other, so it can never become a second position on a symbol
    its strategy already holds - a same-side re-fire can only hand its target over, and an
    opposite-side one is the close-then-enter the settings already govern.
    """
    if not isinstance(row, dict):
        return None
    latest = row.get("latest_signal")
    if not isinstance(latest, dict):
        return None
    family = row.get("family")
    side = latest.get("side")
    epoch = latest.get("signal_epoch")
    entry = _number(latest.get("entry"))
    sl = _number(latest.get("sl"))
    tp = _number(latest.get("tp"))
    if not isinstance(family, str) or not family or side not in {"buy", "sell"}:
        return None
    if not isinstance(epoch, int) or entry is None or sl is None or tp is None:
        return None
    stop_distance = abs(entry - sl)
    if stop_distance <= 0:
        return None
    return Card(
        strategy=family,
        families=(family,),
        side=side,
        signal_epoch=int(epoch),
        signal_utc=latest.get("signal_utc") if isinstance(latest.get("signal_utc"), str) else None,
        entry=entry,
        sl=sl,
        tp=tp,
        stop_pips=_number(latest.get("stop_pips")) or 0.0,
        stop_distance=stop_distance,
        tp_r=_number(latest.get("tp_r")) or 0.0,
        # The freshness sematics match the top-level card's: how long ago this would-be entry
        # bar was, which is what MAX_ENTRY_LAG_BARS is measured on.
        bars_held=int(_number(latest.get("bars_held")) or 0),
        r_now=_number(latest.get("r_now")),
        digits=int(digits),
        range_pos=_number(latest.get("range_pos")),
        fresh_high=latest.get("fresh_high") if isinstance(latest.get("fresh_high"), bool) else None,
        fresh_low=latest.get("fresh_low") if isinstance(latest.get("fresh_low"), bool) else None,
        swing_low=_number(latest.get("swing_low")),
        swing_high=_number(latest.get("swing_high")),
    )


def _fade_side(card: Card) -> str | None:
    """The placement rule the account holder stated: a high sells, a low buys, the middle waits.

    Measured on the card's own signal bar - the read `setups.placement_at` carries, word for word
    as `market_context` measures the last closed bar. An unreadable placement (an unfaded window,
    a flat 96-bar range, a row written before the field existed) stands down rather than fades on
    a guess: the gate's own discipline, that the safe reading of an unknown is "do not trade".
    """
    pos = card.range_pos
    if pos is None:
        return None
    if pos >= CONFLUENCE_FADE_TOP:
        return "sell"
    if pos <= CONFLUENCE_FADE_BOTTOM:
        return "buy"
    return None


def _mirrored(card: Card, side: str) -> Card:
    """The card's own geometry, rehearsed on the fade side.

    The entry stays the price it was planned at; the stop and target ride to the fade side at the
    same distance and the same R multiple, so sizing, R arithmetic and the gate all read exactly
    what they read before - only the side has changed, which is what "mirror the agreeing group"
    means here.
    """
    tp_dist = card.tp_r * card.stop_distance
    if side == "sell":
        sl, tp = card.entry + card.stop_distance, card.entry - tp_dist
    else:
        sl, tp = card.entry - card.stop_distance, card.entry + tp_dist
    return Card(
        strategy=card.strategy,
        families=card.families,
        side=side,
        signal_epoch=card.signal_epoch,
        signal_utc=card.signal_utc,
        entry=card.entry,
        sl=sl,
        tp=tp,
        stop_pips=card.stop_pips,
        stop_distance=card.stop_distance,
        tp_r=card.tp_r,
        bars_held=card.bars_held,
        r_now=None,
        digits=card.digits,
        range_pos=card.range_pos,
        fresh_high=card.fresh_high,
        fresh_low=card.fresh_low,
        # Both swing levels ride along unchanged: which one the target reads is decided by the
        # *final* side, and the mirror may change exactly that. A level is a place on the price
        # line, not a property of a direction.
        swing_low=card.swing_low,
        swing_high=card.swing_high,
    )


def _confluence(group: list[Card], mode: str = CONFLUENCE_MODE_MEMBERS) -> Card | None:
    """Fold agreeing cards into one planned order, the *tightest stop* of the group deciding it.

    The entry is the same bar's next open for all of them anyway; what differs is the stop. The
    tightest one puts the exit nearest and - because the position is sized off the stop - gives the
    largest position for the same money at risk, which is the reading the user asked for when they
    said the agreeing arrows are the accurate ones.

    Two modes, and the None matters:

    * ``members`` - the agreeing strategies' own side plans the order, as this has always done.
    * ``fade`` - the account holder's placement rule above decides the side instead, and the
      tightest card's geometry rides along mirrored when the fade flips it. A group whose bar sits
      in the middle of the range, or whose placement cannot be read, folds into *nothing*: the
      composite stands down and the members' own cards trade individually, which is what the
      panel says.
    """
    tightest: Card = min(group, key=lambda card: card.stop_distance)
    if mode == CONFLUENCE_MODE_FADE:
        fade = _fade_side(tightest)
        if fade is None:
            return None  # the middle band: nothing is decided there, so nothing is traded twice
        side = fade
        geometry = tightest if fade == tightest.side else _mirrored(tightest, fade)
    else:
        side = tightest.side
        geometry = tightest
    # Deterministic order for the artifacts: alphabetical, one name per family even where a
    # merged fold would otherwise list a family twice.
    names = tuple(sorted({name for card in group for name in card.families}))
    return Card(
        strategy=CONFLUENCE,
        families=names,
        side=side,
        signal_epoch=geometry.signal_epoch,
        signal_utc=geometry.signal_utc,
        entry=geometry.entry,
        sl=geometry.sl,
        tp=geometry.tp,
        stop_pips=geometry.stop_pips,
        stop_distance=geometry.stop_distance,
        tp_r=geometry.tp_r,
        bars_held=geometry.bars_held,
        r_now=geometry.r_now,
        digits=geometry.digits,
        range_pos=geometry.range_pos,
        fresh_high=geometry.fresh_high,
        fresh_low=geometry.fresh_low,
        swing_low=geometry.swing_low,
        swing_high=geometry.swing_high,
    )


def _order_tp(card: Card, mode: str) -> tuple[float, float, str]:
    """The target one card's order actually carries, for the strategy's own TP mode.

    ``fixed`` is the card's own planned exit, unchanged. ``swing`` is the structure rule: a sell
    targets just above the previous lower-low the signal bar had confirmed, a buy just below
    the previous higher-high, the level a `SWING_TP_BUFFER_R` fraction of the stop short of it
    so the fill lands while the level still holds rather than exactly on a touch.

    The fallbacks say the whole design: no swing known for the trade's side, or a level that has
    already been passed (it would name a target on the wrong side of the entry), and the card's
    own fixed target is used as it stood. A structure rule that cannot produce a structure exit
    never trades unplaced, and the mode it actually filled under travels with the order in the
    journal, so a later reader knows which of the two rules chose the exit.
    """
    if mode != TP_MODE_SWING:
        return card.tp, card.tp_r, TP_MODE_FIXED
    level = card.swing_low if card.side == "sell" else card.swing_high
    if level is None:
        return card.tp, card.tp_r, TP_MODE_FIXED
    offset = SWING_TP_BUFFER_R * card.stop_distance
    target = round(level + offset if card.side == "sell" else level - offset, card.digits)
    if (card.side == "sell" and target >= card.entry) or (
        card.side == "buy" and target <= card.entry
    ):
        return card.tp, card.tp_r, TP_MODE_FIXED
    return target, abs(card.entry - target) / card.stop_distance, TP_MODE_SWING


def strategies_to_trade(
    settings: Settings,
    validated: set[str],
    *,
    strategies: tuple[str, ...] | None = None,
    allow_unvalidated: bool | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Split the chosen strategies into what will be traded and what was refused, with the reason.

    A strategy is tradeable when it is chosen *and* carries a validated row for the watcher's
    timeframe. `confluence` has no row of its own - it is a composite - so it is allowed only when
    at least one of the chosen families is validated, which keeps the composite tied to the
    evidence its halves actually have.

    The two keyword arguments are the dashboard's selection overriding the .env, and both default
    to the .env as well: a caller that says nothing about the acknowledgement gets the strict
    reading, so passing a selection can only ever refuse *more* than the .env did, never less.
    """
    chosen = settings.auto_strategies if strategies is None else strategies
    unvalidated_ok = settings.auto_allow_unvalidated if allow_unvalidated is None else allow_unvalidated
    allowed: list[str] = []
    refused: list[dict[str, str]] = []
    for name in chosen:
        if name == CONFLUENCE:
            continue
        if name in validated:
            allowed.append(name)
        elif unvalidated_ok:
            # Deliberately allowed, and only because something said so explicitly. A name that no
            # strategy answers to is inert rather than dangerous here: nothing generates a card
            # under it, so it can sit on the allow-list without ever becoming an order.
            allowed.append(name)
        else:
            refused.append({"strategy": name, "reason": "not historically validated on this timeframe"})
    if CONFLUENCE in chosen:
        # The composite has no registry row of its own - it is whatever agreed - so it is allowed
        # only when there are two families it could be made of. One family cannot agree with
        # itself, and saying that here is the difference between "the pair is armed" and "the pair
        # is waiting for a second rule you have not allowed yet", which is the answer the panel
        # needs. Which families may pair *this cycle* is decided in `plans`, where the cards are.
        families = [name for name in allowed if name != CONFLUENCE]
        if len(families) >= 2:
            allowed.append(CONFLUENCE)
        elif not families:
            refused.append({"strategy": CONFLUENCE, "reason": "none of its families are validated"})
        else:
            refused.append(
                {
                    "strategy": CONFLUENCE,
                    "reason": f"it needs two allowed families to pair; only {families[0]} is allowed",
                }
            )
    return allowed, refused


def plans(
    payload: Any,
    allowed: list[str],
    *,
    digits: int,
    confluence_mode: str = CONFLUENCE_MODE_MEMBERS,
) -> list[Card]:
    """The cards worth acting on this cycle: allowed, live, fresh, and not already overtaken.

    A `confluence` card supersedes the single-family cards it is made of, so an armed pair does not
    also fire each half of itself: the agreement *is* the strategy when it is switched on. In the
    `fade` mode the same is true of whichever agreeing groups on one bar fade the same side - two
    groups cannot both become the same order, so they fold into one composite - and a group whose
    bar sits in the middle of the range folds into nothing, leaving its members to trade
    individually.
    """
    if not isinstance(payload, dict):
        return []
    rows = payload.get("setups")
    if not isinstance(rows, list):
        return []
    allowed_set = set(allowed)
    live: list[Card] = []
    for row in rows:
        card = _live_row(row, digits)
        if card is not None:
            if card.bars_held > MAX_ENTRY_LAG_BARS:
                # The held trade's own signal bar ages out of the entry set as the trade runs;
                # the nested current signal (below) is kept appropriately fresh.
                card = None
            else:
                live.append(card)
        # The row's newest re-fire while it holds a trade is what the strategy is saying NOW,
        # and it acts on this one rather than waiting for the held trade to end on TP or SL.
        signal = _signal_card(row, digits)
        if signal is not None and signal.signal_epoch > (card.signal_epoch if card else -1):
            if signal.bars_held > MAX_ENTRY_LAG_BARS:
                continue
            live.append(signal)

    out: list[Card] = []
    if CONFLUENCE in allowed_set:
        # Only families that are themselves allowed can form an agreement: a pair is not tradeable
        # through the confluence name while one half of it is switched off or unvalidated.
        groups: dict[tuple[int, str], list[Card]] = {}
        for card in live:
            if card.strategy in allowed_set:
                groups.setdefault((card.signal_epoch, card.side), []).append(card)
        # Fold per group, then re-fold the folds: in the fade mode two agreeing groups on one bar
        # (a buy group and a sell group, the fade already picked their side) can land on the same
        # (bar, side) order, and one signal is one order. The composite keeps the tighter geometry
        # and names every family that asked for it, exactly as one bar's re-fires are folded above.
        folds: dict[tuple[int, str], list[Card]] = {}
        for key in sorted(groups):
            members = sorted(groups[key], key=lambda card: card.strategy)
            if len(members) < 2:
                continue
            built = _confluence(members, confluence_mode)
            if built is not None:
                folds.setdefault((built.signal_epoch, built.side), []).append(built)
        for key in sorted(folds):
            participants = sorted(folds[key], key=lambda card: card.strategy)
            out.append(_confluence(participants, CONFLUENCE_MODE_MEMBERS) if len(participants) > 1 else participants[0])
    superseded = {name for card in out for name in card.families}
    for card in live:
        if card.strategy in allowed_set and card.strategy not in superseded:
            out.append(card)
    # Newest signal bar first, then by name, so the order of entries in a cycle is deterministic.
    out.sort(key=lambda card: (-card.signal_epoch, card.strategy))
    return out


# --- sizing ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sizing:
    """How much to trade, and why there is nothing to trade when there is not."""

    volume: float
    risk_amount: float
    risk_per_lot: float
    fits: bool
    reason: str | None = None


def size_position(
    *,
    equity: float,
    risk_pct: float,
    stop_distance: float,
    tick_size: float,
    tick_value: float,
    volume_min: float,
    volume_step: float,
    volume_max: float,
) -> Sizing:
    """Volume for a stop at `stop_distance`, risking `risk_pct` of equity.

    Tick value is the account's own money per tick per lot, so this needs no exchange rate. The
    volume is floored to the broker's step - never rounded up, which would risk more than asked -
    and when even the minimum lot risks more than the budget the answer is "does not fit" rather
    than the nearest thing that does. That last case is the one worth being strict about: a size
    that silently exceeds the risk budget is exactly the failure this whole module exists to avoid.
    """
    risk_amount = float(equity) * float(risk_pct) / 100.0
    if risk_pct <= 0 or risk_amount <= 0:
        return Sizing(0.0, risk_amount, 0.0, False, "no risk budget is set")
    if stop_distance <= 0 or tick_size <= 0 or tick_value <= 0:
        return Sizing(0.0, risk_amount, 0.0, False, "sizing inputs are unreadable")
    risk_per_lot = stop_distance / tick_size * tick_value
    if risk_per_lot <= 0 or not math.isfinite(risk_per_lot):
        return Sizing(0.0, risk_amount, risk_per_lot, False, "the symbol's tick value is unreadable")
    raw = min(risk_amount / risk_per_lot, volume_max)
    step = volume_step if volume_step > 0 else 0.01
    steps = math.floor(raw / step + 1e-9)
    volume = round(steps * step, 2)
    if volume > volume_max:
        volume = round(volume_max, 2)
    if volume < volume_min - 1e-9:
        return Sizing(
            volume,
            risk_amount,
            risk_per_lot,
            False,
            f"the smallest position ({volume_min:g} lots) risks more than {risk_amount:.2f}",
        )
    return Sizing(volume, risk_amount, risk_per_lot, True)


# --- the order surface -------------------------------------------------------------------


@dataclass(frozen=True)
class OrderResult:
    """What the terminal said about one request. A refusal is a result, not an exception."""

    ok: bool
    retcode: int
    comment: str
    ticket: int | None = None
    price: float | None = None
    volume: float | None = None
    error: str | None = None


class MT5Trader:
    """The order-sending half of the terminal, kept to the smallest surface that can trade.

    `ReadOnlyMT5` is unchanged and still exposes no way to place anything; this class is separate
    so the read-only promise stays true of the research and reporting code, and the source scan
    pins the difference (see `tests/test_readonly.py`). The backend is name-mangled private, as
    there: the intent is that orders go through `placed` and `closed`, which set the stops and the
    magic number themselves.
    """

    def __init__(self, backend: Any) -> None:
        self.__backend = backend

    def account_info(self) -> Any:
        return self.__backend.account_info()

    def symbol_info(self, name: str) -> Any:
        return self.__backend.symbol_info(name)

    def positions_get(self, symbol: str | None = None) -> tuple[Any, ...]:
        rows = (
            self.__backend.positions_get()
            if symbol is None
            else self.__backend.positions_get(symbol=symbol)
        )
        return tuple(rows or ())

    def symbol_info_tick(self, name: str) -> Any:
        return self.__backend.symbol_info_tick(name)

    def terminal_info(self) -> Any:
        """The terminal itself, for the one question the account cannot answer.

        `account_info().trade_allowed` is the server's permission; this is the terminal's, and it is
        what the Algo Trading button sets. The two disagree in exactly the case that wastes a cycle:
        a demo that would accept orders in principle and a terminal that is not sending them. A read,
        like the getters above - the only methods here that can change anything are `placed` and
        `closed`.
        """
        return self.__backend.terminal_info()

    def __filling_modes(self, symbol: str) -> tuple[int, ...]:
        """The filling modes to try for this symbol, best first.

        The symbol advertises what it accepts as a bitmask, so the mode is read from there rather
        than assumed: IOC first, because an immediate fill at the price that was read is what the
        research fills assume; then FOK, which also fills at once; then RETURN, which costs one
        refused request to rule out. A symbol that reports nothing - or a test double with no such
        field - keeps IOC alone, which is what this sent unconditionally before.
        """
        backend = self.__backend
        mask = getattr(self.symbol_info(symbol), "filling_mode", None)
        modes: list[int] = []
        ioc = getattr(backend, "ORDER_FILLING_IOC", None)
        fok = getattr(backend, "ORDER_FILLING_FOK", None)
        back = getattr(backend, "ORDER_FILLING_RETURN", None)
        if isinstance(mask, int) and mask:
            if ioc is not None and mask & int(getattr(backend, "SYMBOL_FILLING_IOC", 2)):
                modes.append(ioc)
            if fok is not None and mask & int(getattr(backend, "SYMBOL_FILLING_FOK", 1)):
                modes.append(fok)
            if back is not None:
                modes.append(back)
        if not modes and ioc is not None:
            # The symbol said nothing about what it takes. IOC is what this used to send
            # unconditionally, so keeping it alone keeps that behaviour where it was working.
            modes.append(ioc)
        return tuple(modes)

    def placed(
        self,
        *,
        symbol: str,
        side: str,
        volume: float,
        sl: float,
        tp: float,
        comment: str,
        magic: int,
        deviation: int = 20,
    ) -> OrderResult:
        """Open a market position with its stop and target attached.

        The stops go on the request, not on a modification afterwards: between an unprotected fill
        and a second call that adds the stop there is a window where a network drop leaves the
        position with nothing on it. The broker also then closes the trade on its own, which is what
        makes an unattended position safe.
        """
        tick = self.symbol_info_tick(symbol)
        if tick is None:
            return OrderResult(False, 0, "the terminal returned no tick", error="no_tick")
        is_buy = side == "buy"
        price = float(tick.ask if is_buy else tick.bid)
        request = {
            "action": self.__backend.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": self.__backend.ORDER_TYPE_BUY if is_buy else self.__backend.ORDER_TYPE_SELL,
            "price": price,
            "sl": float(sl),
            "tp": float(tp),
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": comment,
            "type_time": self.__backend.ORDER_TIME_GTC,
        }
        return self.__send_filling(request, symbol)
    def modified(self, position: Any, *, sl: float, tp: float) -> OrderResult:
        """Move a position's protective stops in place, keeping the same ticket.

        This is the management surface: a stop that rises with the move and a target that a later
        signal improves are changes to the exits only - volume, entry and direction must never
        change here, which the SLTP action structurally cannot do. Whatever this passes stays with
        the position rather than creating a new one, so the broker holds the whole exit exactly as
        it did after the entry.
        """
        ticket = int(getattr(position, "ticket", 0) or 0)
        symbol = str(getattr(position, "symbol", "") or "")
        if not ticket or not symbol:
            return OrderResult(False, 0, "the position cannot be named well enough to modify", error="bad_position")
        request = {
            "action": self.__backend.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": symbol,
            "sl": float(sl),
            "tp": float(tp),
        }
        return self.__send(request)

    def closed(self, position: Any, *, comment: str, magic: int, deviation: int = 20) -> OrderResult:
        """Close a position at market, reading its own ticket, symbol and volume.

        A close is a deal on the opposite side of the position, and the ticket it names is the
        position's - which is what keeps a partial close from being applied to the wrong trade.
        """
        ticket = int(getattr(position, "ticket", 0) or 0)
        symbol = str(getattr(position, "symbol", "") or "")
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        if not ticket or not symbol or volume <= 0:
            return OrderResult(False, 0, "the position cannot be named well enough to close", error="bad_position")
        tick = self.symbol_info_tick(symbol)
        if tick is None:
            return OrderResult(False, 0, "the terminal returned no tick", error="no_tick")
        is_buy = int(getattr(position, "type", DEAL_BUY) or 0) == DEAL_BUY
        request = {
            "action": self.__backend.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": symbol,
            "volume": volume,
            "type": self.__backend.ORDER_TYPE_SELL if is_buy else self.__backend.ORDER_TYPE_BUY,
            "price": float(tick.bid if is_buy else tick.ask),
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": comment,
            "type_time": self.__backend.ORDER_TIME_GTC,
        }
        return self.__send_filling(request, symbol)

    def __send_filling(self, request: dict[str, Any], symbol: str) -> OrderResult:
        """Send a deal, walking the symbol's filling modes until one is accepted.

        A 10030 is the symbol saying it does not take this mode, and nothing was executed - so the
        next mode is the same intent attempted again, not a second order. Any other answer, a
        success or a refusal for another reason, stops the walk and is reported as it came.
        """
        modes = self.__filling_modes(symbol)
        if not modes:
            return OrderResult(
                False, 0, "this symbol reports no filling mode that could be used", error="no_filling"
            )
        result = OrderResult(False, 0, "the request was never sent", error="no_result")
        for mode in modes:
            request["type_filling"] = mode
            result = self.__send(request)
            if result.ok or result.retcode != TRADE_RETCODE_INVALID_FILL:
                return result
        return result

    def __send(self, request: dict[str, Any]) -> OrderResult:
        try:
            result = self.__backend.order_send(request)
        except Exception as exc:  # noqa: BLE001 - a refused request is a result, not a crash
            return OrderResult(False, 0, f"{type(exc).__name__}: {exc}", error="raised")
        if result is None:
            return OrderResult(False, 0, "the terminal returned no result", error="no_result")
        retcode = int(getattr(result, "retcode", 0) or 0)
        comment = str(getattr(result, "comment", "") or "")
        ok = retcode in (TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED)
        return OrderResult(
            ok=ok,
            retcode=retcode,
            comment=comment,
            ticket=int(getattr(result, "order", 0) or 0) or None,
            price=_number(getattr(result, "price", None)),
            volume=_number(getattr(result, "volume", None)),
            error=None if ok else f"retcode {retcode}",
        )


# --- the cycle ---------------------------------------------------------------------------


def card_gate(market: dict[str, Any], *, symbol: str, strategy_validated: str | None, risk_fits: bool | None) -> Any:
    """The full gate, with this card's own strategy and sizing filled in.

    The cycle's gate runs once for the market and can only say whether the *session* is sane; a
    per-card verdict needs two inputs only a card can supply - which family this is (and whether it
    is validated on this timeframe) and whether its own stop fits the risk budget. Every check runs,
    as in the cycle gate, so a blocked card reports all of its reasons rather than the first.
    """
    return evaluate(
        GateInputs(
            symbol=symbol,
            bar_age_s=market.get("bar_age_s"),
            spread_pips=market.get("spread_pips"),
            market_open=market.get("market_open"),
            news_verifiable=bool(market.get("news_verifiable")),
            news_excluded=bool(market.get("news_excluded")),
            circuit_breaker_tripped=bool(market.get("circuit_breaker_tripped", True)),
            daily_loss_pct=market.get("daily_loss_pct"),
            open_positions=market.get("open_positions"),
            validated_strategy=strategy_validated,
            risk_fits=risk_fits,
        )
    )


@dataclass
class Outcome:
    """One cycle of auto-trading, in the shape the dashboard and the journal both want."""

    switch: Switch | None = None
    selection: Selection | None = None
    terminal: dict[str, Any] = field(default_factory=dict)
    registry: list[dict[str, Any]] = field(default_factory=list)
    account: dict[str, Any] = field(default_factory=dict)
    allow: dict[str, Any] = field(default_factory=dict)
    placed: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)
    closed: list[dict[str, Any]] = field(default_factory=list)
    managed: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    open_rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # The broker's own refusals, read back from the journal rather than from this cycle: a rejected
    # reservation is spent, so the cycle that saw it is the only one that could report it.
    rejected: list[dict[str, Any]] = field(default_factory=list)

    def refuse(self, strategy: str, reason: str, **extra: Any) -> None:
        self.refused.append({"strategy": strategy, "reason": reason, **extra})


def _terminal_allows_trading(trader: MT5Trader) -> tuple[bool, str | None]:
    """Whether the terminal will actually send an order, and the reason when it will not.

    A terminal whose Algo Trading button is off answers every request with retcode 10027, and that
    is the one refusal this module can predict instead of suffering. Predicting it is the difference
    between a panel that says "click Algo Trading in MT5" and one that reports the broker rejecting
    orders for no visible reason. An unreadable terminal reads as not allowed: the same discipline
    as an unverifiable spread, because the safe reading of an unknown is "do not trade".
    """
    try:
        info = trader.terminal_info()
    except Exception as exc:  # noqa: BLE001 - an unreadable terminal is a refusal
        return False, f"the terminal would not report its state ({type(exc).__name__})."
    allowed = getattr(info, "trade_allowed", None)
    if allowed is True:
        return True, None
    if allowed is False:
        return False, (
            "Algo Trading is off in the MT5 terminal - click the Algo Trading button in its toolbar: "
            "an order sent now would be refused with retcode 10027."
        )
    return False, "the terminal did not report whether Algo Trading is on."


def _spec(trader: MT5Trader, symbol: str) -> Any:
    """The symbol's trading spec, or None. Read once per cycle; an unreadable one blocks trading."""
    try:
        return trader.symbol_info(symbol)
    except Exception:  # noqa: BLE001 - a spec that will not read is a refusal, not a crash
        return None


def _find(positions: tuple[Any, ...], ticket: int | None) -> Any | None:
    """The open position with this ticket, searched in a list read once for the cycle.

    Reads are terminal round-trips, and the reconciler asks about every open row: re-reading the
    whole position list per row would make a cycle's cost grow with what the account already holds.
    A position opened or closed earlier in the same cycle is tracked in memory, not looked up here.
    """
    if not ticket:
        return None
    for row in positions:
        if int(getattr(row, "ticket", 0) or 0) == ticket:
            return row
    return None


def _confluence_stop(
    trader: MT5Trader,
    store: Store,
    settings: Settings,
    positions: tuple[Any, ...],
    still_open: list[dict[str, Any]],
    candidates: list[Card],
    symbol: str,
    outcome: Outcome,
) -> None:
    """When the confluence composite fires, everything opposite on its symbol stands down.

    The account holder's rule, adopted as advice: a agreeing-pair signal is the market's best
    reading of its own direction, so a plain bet against it - whichever hand opened it - is the
    position most likely to bleed. This pass closes every opposing position on the composite's
    symbol at market, journal-tracked or manual (the terminal, not the journal, is what is
    searched: `by all` means trades this system never opened too), and only this symbol.

    Fires when a fresh confluence card is actable - the same bar the composite would trade - and
    it is scoped the way the rest of trading is: reached only on an armed cycle, and only where
    `confluence` is itself on the allow-list (which is what builds a composite card at all).

    Once per signal: the occurrence is journaled on (symbol, signal bar, side) and re-read before
    firing, so a second cycle cannot re-close what the holder re-opened by hand while the same
    bar was still within its entry window. A bar where the composite itself cancels (agreeing
    groups on both sides, which can happen in the `members` mode) closes nothing: the agreement
    had no single side, and a rule with no side does not stop anything.
    """
    stop_cards = [card for card in candidates if card.strategy == CONFLUENCE]
    sides = {card.side for card in stop_cards}
    if len(sides) > 1:
        outcome.notes.append(
            "Confluence agreed on both sides of one bar, so it closes nothing while it disagrees with itself."
        )
        return
    card = max(stop_cards, key=lambda c: c.signal_epoch)

    # Once per signal, in the journal: this pass runs every 30 s while a fresh card is in its
    # entry window (0 and 1 bar old both qualify), and the second read must fire nothing.
    fingerprint = {"symbol": settings.symbol_root, "signal_epoch": card.signal_epoch, "side": card.side}
    fired: set[tuple[str, int, str]] = set()
    try:
        for event in store.recent_events("confluence_stop", 200):
            payload = event.get("payload") or {}
            fired.add(
                (
                    str(payload.get("symbol")),
                    int(payload.get("signal_epoch") or 0),
                    str(payload.get("side")),
                )
            )
    except Exception:  # noqa: BLE001 - a journal that will not read must not block closes
        fired = set()
    key = (fingerprint["symbol"], fingerprint["signal_epoch"], fingerprint["side"])
    if key in fired:
        return

    opposite = "sell" if card.side == "buy" else "buy"
    closed_tickets = 0
    for position in list(positions):
        position_symbol = str(getattr(position, "symbol", "") or "")
        # Suffix tolerance, as the reconciler reads it: the broker may name the market USDJPYc.
        if not (position_symbol == symbol or position_symbol.startswith(symbol)):
            continue  # another symbol's market is not this decision
        kind = int(getattr(position, "type", DEAL_BUY) or 0)
        held_side = "buy" if kind == DEAL_BUY else "sell"
        if held_side != opposite:
            continue  # a same-side position is the composite's ally, not its casualty
        ticket = int(getattr(position, "ticket", 0) or 0)
        if not ticket:
            continue
        result = trader.closed(
            position,
            comment="MTI confluence stop"[:31],
            magic=settings.auto_magic,
        )
        if result.ok:
            closed_tickets += 1
            outcome.closed.append(
                {
                    "strategy": "confluence stop",
                    "ticket": ticket,
                    "side": held_side,
                    "reason": f"opposing {held_side} to a confluence {card.side} on {position_symbol}",
                }
            )
            for row in list(still_open):
                if row.get("ticket") == ticket:
                    # A journal-tracked position stops counting now, not at a reconcile a cycle
                    # later that would misreport this close as the broker's own doing.
                    store.close_auto_trade(
                        int(row["id"]), reason="confluence stop", close_ticket=result.ticket
                    )
                    still_open.remove(row)
        else:
            outcome.errors.append(
                f"confluence stop {position_symbol} #{ticket}: {result.comment or 'refused'}"
            )

    store.append_event("confluence_stop", {**fingerprint, "signal_utc": card.signal_utc, "closed": closed_tickets, "code_version": CODE_VERSION})
    if closed_tickets:
        outcome.notes.append(
            f"Confluence stop: closed {closed_tickets} opposing {opposite} position(s) on {symbol} "
            f"at a fresh confluence {card.side} signal."
        )
    else:
        outcome.notes.append(
            f"Confluence stop: no opposing {opposite} positions were open on {symbol}."
        )


# --- position management -----------------------------------------------------------------"


def _pip_size(digits: int) -> float:
    """One pip for a quote with this many digits, in price steps.
    
    A 3-digit or 5-digit quote carries a half-digit of precision, so the pip is a tenth above the
    point: 0.01 on JPY pairs (3 digits), 0.0001 on majors (5 digits). A 2- or 4-digit quote names
    pips with the point itself. The R multiples management works in come from the card's own pip
    stop, so this is what turns that stop back into a price distance.
    """
    point = 10.0**-digits
    return 10.0 * point if digits in (3, 5) else point


def _manage_stops(
    trader: MT5Trader,
    store: Store,
    settings: Settings,
    open_rows: list[dict[str, Any]],
    positions: tuple[Any, ...],
    symbol: str,
    outcome: Outcome,
) -> None:
    """Drag every supervised stop up with the move, once per cycle.

    The watcher arms a trade and the broker holds the exits, but nothing moved them after the
    entry - which is how a trade in profit can round-trip into its stop. This pass runs before
    entries every cycle, however long since the last signal was seen, and it has to run even when
    the card view is stale: an exit that must move does not wait for a fresh entry to exist.

    Terms are the trade's own R: the pip stop the card was planned with, so `profit_r` reads as how
    far the market has travelled in units of the risk that was taken. Two rules tighten the stop,
    and only ever in the profitable direction:

    * At `AUTO_BREAKEVEN_R` of profit the stop locks a fraction (`AUTO_LOCK_R`) of R beyond entry,
      so a trade that worked can no longer turn into a loss.
    * At `AUTO_TRAIL_R` it keeps the distance all the way down to `AUTO_TRAIL_DISTANCE_R` behind
      the current price, and every later improvement is the same rule reapplied: when the market
      has gone further, the stop goes with it, but it is never dragged back down.

    A move is only sent when it improves the stop the broker already holds, and only when it
    respects the broker's own minimum stop distance; a move that cannot be accepted yet is
    skipped and retried next cycle, never journaled as a failure. Nothing here widens a stop,
    touches a target, or can send an order: the worst outcome of a bug in this pass is a stop
    that stayed where it was.
    """
    for row in list(open_rows):
        try:
            ticket = row.get("ticket")
            position = _find(positions, ticket if isinstance(ticket, int) else None)
            if position is None:
                continue
            side = str(row.get("side") or "")
            if side not in ("buy", "sell"):
                continue
            is_buy = side == "buy"
            positional_digits = int(row.get("digits") or 0)
            if positional_digits <= 0:
                continue
            entry = _number(getattr(position, "price_open", None))
            if entry is None:
                continue
            r_distance = float(row.get("stop_pips") or 0.0) * _pip_size(positional_digits)
            if r_distance <= 0:
                continue
            tick_symbol = str(getattr(position, "symbol", "") or symbol)
            tick = trader.symbol_info_tick(tick_symbol)
            if tick is None:
                continue
            mark = float(tick.bid if is_buy else tick.ask)
            profit_r = ((mark - entry) if is_buy else (entry - mark)) / r_distance
            options: list[float] = []
            if profit_r >= settings.auto_breakeven_r:
                options.append(
                    entry + settings.auto_lock_r * r_distance
                    if is_buy
                    else entry - settings.auto_lock_r * r_distance
                )
            if profit_r >= settings.auto_trail_r:
                offset = settings.auto_trail_distance_r * r_distance
                options.append(mark - offset if is_buy else mark + offset)
            if not options:
                continue
            new_sl = max(options) if is_buy else min(options)
            point = 10.0**-positional_digits
            current_sl = float(getattr(position, "sl", 0.0) or 0.0)
            improves = current_sl <= 0 or (
                new_sl > current_sl + point if is_buy else new_sl < current_sl - point
            )
            target = float(getattr(position, "tp", 0.0) or 0.0)
            if target > 0 and (
                (is_buy and new_sl >= target) or (not is_buy and new_sl <= target)
            ):
                # The stop would sit on or past the target: the broker is closing the trade on
                # its own within the tick, and an inventoried stop there would only be refused.
                continue
            if not improves:
                continue
            spec = trader.symbol_info(tick_symbol)
            stops_points = getattr(spec, "trade_stops_level", None) if spec is not None else None
            min_gap = (float(stops_points) if isinstance(stops_points, (int, float)) else 0.0) * point
            if is_buy:
                if mark - new_sl < min_gap:
                    continue
            else:
                if new_sl - mark < min_gap:
                    continue
            result = trader.modified(position, sl=new_sl, tp=target)
            kind = "trail" if profit_r >= settings.auto_trail_r else "breakeven"
            if result.ok:
                store.update_auto_trade_stops(int(row["id"]), sl=new_sl)
                row["sl"] = new_sl
                store.append_event(
                    "auto_managed",
                    {
                        "id": row["id"],
                        "strategy": row.get("strategy"),
                        "ticket": ticket,
                        "kind": kind,
                        "profit_r": round(profit_r, 3),
                        "old_sl": current_sl or None,
                        "new_sl": new_sl,
                        "code_version": CODE_VERSION,
                    },
                )
                outcome.managed.append(
                    {
                        "strategy": row.get("strategy"),
                        "ticket": ticket,
                        "kind": kind,
                        "profit_r": round(profit_r, 3),
                        "sl": new_sl,
                    }
                )
            else:
                outcome.errors.append(f"stop move {row.get('strategy')}: {result.comment}")
                store.append_event(
                    "auto_manage_failed",
                    {
                        "id": row["id"],
                        "strategy": row.get("strategy"),
                        "ticket": ticket,
                        "kind": kind,
                        "retcode": result.retcode,
                        "comment": result.comment,
                        "code_version": CODE_VERSION,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - a dead watcher cannot manage anything either
            outcome.errors.append(f"management {row.get('strategy', '?')}: {type(exc).__name__}")


def run(
    *,
    trader: MT5Trader,
    store: Store,
    settings: Settings,
    setups: Any,
    market: dict[str, Any],
    switch_path: Path,
    now_epoch: int,
    offset_s: int | None,
    timeframe_s: int,
    max_hold_bars: int,
    validated: set[str],
    selection_path: Path | None = None,
    registry: list[dict[str, Any]] | None = None,
    symbol: str = "",
) -> Outcome:
    """One cycle of auto-trading: reconcile, exit what is due, then enter what is fresh.

    `symbol` names the market this cycle reads cards from and sends orders on. Empty means the
    caller's own primary (`settings.symbol_root`), which is what every caller historically was;
    a per-symbol step passes its symbol here and the whole cycle - spec, sizing, freshness, the
    dedup claim, the sent order - is that market's. The switch, allow-list, cap and account stay
    shared: one arming decision, one risk budget.

    Reconcile first, because a position that the broker closed on its stops must stop being counted
    before another is opened against the same cap. Exits second, so a trade that has hit its time
    limit frees its slot this cycle rather than the next. Entries last, and every decision - taken,
    refused or failed - is journaled with the reason. Reading and trading are separated on purpose:
    the switch, the chosen strategies, the terminal's own permission and the account are all read
    *before* the switch is honoured, so a panel showing an off switch can still say what it would
    trade and what would stop it. Only once the switch says on does anything below reach a position,
    a stop, or an order.
    """
    symbol = symbol or settings.symbol_root
    outcome = Outcome()
    outcome.registry = list(registry or [])
    outcome.switch = read_switch(switch_path, master=settings.auto_trade)
    # Read before any early return, so a switch that is armed but has had every entry refused still
    # says so. Without this the panel showed an armed switch, no positions and no decisions - which
    # is what two orders refused for their filling mode looked like from the outside.
    try:
        outcome.rejected = store.recent_rejected_auto_trades(AUTO_REJECTED_SHOWN)
    except Exception:  # noqa: BLE001 - a journal that will not read must not stop a cycle
        outcome.rejected = []
    # The selection and the terminal are read *before* the switch short-circuit, because the panel
    # has to be honest while the switch is off: which strategies are chosen, and whether the
    # terminal would send anything even if it were armed. Neither places or checks an order and
    # neither touches the account, so the wall still holds - off means no order, not no information.
    selection = read_selection(selection_path, settings)
    outcome.selection = selection
    terminal_ok, terminal_why = _terminal_allows_trading(trader)
    outcome.terminal = {"trade_allowed": terminal_ok, "reason": terminal_why}
    # The allow-list is resolved here for the same reason: it is a pure function of the selection and
    # the registry, and the panel needs it while the switch is off to say what *would* trade - which
    # is the question someone looking at an off switch is actually asking.
    allowed, refused_names = strategies_to_trade(
        settings,
        validated,
        strategies=selection.strategies,
        allow_unvalidated=selection.allow_unvalidated,
    )
    unvalidated_in_use = sorted(
        name for name in allowed if name != CONFLUENCE and name not in validated
    )
    outcome.allow = {
        "requested": list(selection.strategies),
        "allowed": allowed,
        "refused": refused_names,
        "selection": selection.as_dict(),
        "unvalidated": unvalidated_in_use,
        "max_positions": settings.auto_max_positions,
        "risk_pct": settings.auto_risk_pct,
        "magic": settings.auto_magic,
    }
    if unvalidated_in_use:
        outcome.notes.append(
            "Trading without a validated edge: "
            + ", ".join(unvalidated_in_use)
            + " (acknowledged on the allow-list; every entry is flagged unvalidated)."
        )
    # The account is read before the switch short-circuit as well, for the same reason: the panel can
    # then show the account, its equity and its trade mode before anything is armed, so a REAL
    # account is visible as such from the start rather than after the switch is turned on. Nothing
    # trades on an off switch either way - an account that cannot be read is only a refusal when the
    # switch is on and something is actually being attempted.
    account: Any = None
    try:
        account = trader.account_info()
    except Exception as exc:  # noqa: BLE001 - an unreadable account is a refusal
        outcome.errors.append(f"account: {type(exc).__name__}: {exc}")
    if account is None:
        if not outcome.errors:
            outcome.errors.append("account: the terminal returned no account")
        if outcome.switch.enabled:
            return outcome
        outcome.notes.append(outcome.switch.reason)
        return outcome

    # Not `or -1`: DEMO is 0, which is falsy, so that reading reports a demo account as
    # `unknown(-1)` - and it did, on this account, until it changed from a live login to this demo.
    # The live-account guard below is unaffected either way (REAL is 2, which is truthy), but the
    # panel would have been lying about which account it was looking at.
    raw_mode = getattr(account, "trade_mode", None)
    trade_mode = int(raw_mode) if isinstance(raw_mode, (int, float)) else -1
    # Held as a plain float so the journal can always be written: an equity that cannot be read
    # becomes 0.0, which sizes to nothing and so refuses every card - while still letting the
    # reconciler and the time exits run, which are the parts that must work even when the account
    # looks broken.
    equity = _number(getattr(account, "equity", None)) or 0.0
    outcome.account = {
        "login": getattr(account, "login", None),
        "currency": getattr(account, "currency", None),
        "trade_mode": {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(trade_mode, f"unknown({trade_mode})"),
        "equity": equity,
    }
    if not outcome.switch.enabled:
        outcome.notes.append(outcome.switch.reason)
        return outcome
    if not terminal_ok:
        # Before any card: with this off nothing below could be sent anyway, and saying it once names
        # the button instead of reporting every entry as a broker refusal.
        outcome.refuse("*", terminal_why or "the terminal is not allowed to trade")
        outcome.notes.append(terminal_why or "The terminal is not allowed to trade.")
        return outcome
    if trade_mode == 2 and not settings.auto_allow_real:
        outcome.refuse(
            "*",
            "the account is a REAL account and AUTO_TRADE_ALLOW_REAL is not set",
        )
        outcome.notes.append("Refusing to trade a live account: set AUTO_TRADE_ALLOW_REAL to allow it.")
        return outcome

    age = _setups_age_s(setups, now_epoch)
    outcome.allow["setups_age_s"] = age

    broker_now = now_epoch + int(offset_s or 0)
    # The terminal's positions are read unfiltered: the broker can suffix a symbol name (USDJPYc),
    # and filtering by the config's root would then miss the very positions this step must close.
    # Per-symbol scoping happens where it is exact - the journal rows and the orders sent.
    positions = trader.positions_get()
    open_rows = store.auto_trades_open(symbol)

    # Reconcile: a position the broker has closed (its stop or target, or by hand) stops counting.
    still_open: list[dict[str, Any]] = []
    for row in open_rows:
        ticket = row.get("ticket")
        live = _find(positions, ticket if isinstance(ticket, int) else None)
        if live is not None:
            still_open.append(row)
            continue
        reason = "broker" if ticket else "no ticket was ever recorded"
        store.close_auto_trade(int(row["id"]), reason=reason)
        store.append_event(
            "auto_closed",
            {
                "id": row["id"],
                "strategy": row.get("strategy"),
                "ticket": ticket,
                "reason": reason,
                "code_version": CODE_VERSION,
            },
        )
        outcome.closed.append(
            {"strategy": row.get("strategy"), "ticket": ticket, "reason": reason}
        )

    # The card's own time limit, on top of the stops the broker already holds.
    for row in list(still_open):
        expires = row.get("expires_epoch")
        if not isinstance(expires, int) or broker_now < expires:
            continue
        position = _find(positions, row.get("ticket") if isinstance(row.get("ticket"), int) else None)
        if position is None:
            continue
        result = trader.closed(position, comment=f"MTI time {row.get('strategy')}"[:31], magic=settings.auto_magic)
        store.append_event(
            "auto_exit",
            {
                "id": row["id"],
                "strategy": row.get("strategy"),
                "ticket": row.get("ticket"),
                "reason": "time limit",
                "retcode": result.retcode,
                "comment": result.comment,
                "code_version": CODE_VERSION,
            },
        )
        if result.ok:
            store.close_auto_trade(int(row["id"]), reason="time limit", close_ticket=result.ticket)
            still_open.remove(row)
            outcome.closed.append(
                {
                    "strategy": row.get("strategy"),
                    "ticket": row.get("ticket"),
                    "reason": "time limit",
                }
            )
        else:
            outcome.errors.append(f"time exit {row.get('strategy')}: {result.comment}")

    # Management.next: every supervised stop gets looked at before anything else happens, entries
    # included - the entries below can refuse, stall or be out-freshed, but the stop of a position
    # in profit must not depend on any of that.
    if settings.auto_manage:
        _manage_stops(trader, store, settings, still_open, positions, symbol, outcome)

    if not allowed:
        outcome.notes.append("Nothing is on the allow-list, so there is nothing to trade.")
        return outcome
    if age is None or age > MAX_SETUPS_AGE_S:
        # The reconciler and the time exits above still ran, so what is already open is still
        # supervised. Only new entries are withheld: the view that decides them is not trustworthy,
        # and a card read out of an hour-old file can look "just entered" while the market has left
        # the price it planned to enter at.
        outcome.notes.append(
            "The live setups view is "
            + (
                "not stamped, so its age cannot be read"
                if age is None
                else f"{age} s old, past its {MAX_SETUPS_AGE_S} s window"
            )
            + "; no new entries until the watcher rebuilds it."
        )
        return outcome

    spec = _spec(trader, symbol)
    payload_digits = 0
    if isinstance(setups, dict):
        payload_digits = int(_number(setups.get("digits")) or 0)
    digits = int(getattr(spec, "digits", 0) or payload_digits or 5)
    candidates = plans(setups, allowed, digits=digits, confluence_mode=selection.confluence_mode)

    # --- the confluence stop: when the composite fires, everything opposite stands down --------
    # Before entries: this pass closes opposing positions (journal-tracked or manual, this symbol
    # only, once per signal), and what it frees counts toward the cap below - a confluence entry
    # must not be refused by the very positions its own signal just stopped.
    if any(card.strategy == CONFLUENCE for card in candidates):
        _confluence_stop(
            trader, store, settings, positions, still_open, candidates, symbol, outcome
        )

    budget = max(0, int(settings.auto_max_positions) - len(still_open))
    outcome.open_rows = still_open
    if budget == 0:
        outcome.notes.append(
            f"{len(still_open)} auto position(s) open, at the configured cap of {settings.auto_max_positions}."
        )

    for card in candidates:
        # The target this card's order carries: the strategy's own take-profit mode. One decision
        # per card, made once at the top of the loop, so the hand-off and the entry and the journal
        # cannot disagree about which exit the rule chose.
        tp_mode = selection.tp_modes.get(card.strategy, TP_MODE_FIXED)
        order_tp, order_tp_r, tp_kind = _order_tp(card, tp_mode)
        # A swing target that fell back names why, once, wherever the order itself is recorded:
        # "the structure was silent, the fixed target fired" is a readable row later.
        tp_note = None if tp_kind == TP_MODE_SWING else (
            None if tp_mode != TP_MODE_SWING
            else "the previous swing named no valid target for this side, so the fixed target fired"
        )
        # --- a signal arriving while its own strategy is already holding a position here -------------
        # One strategy holds one position on one symbol at a time. A newer card of the same side
        # does not open a second copy of itself; instead its target is handed to the trade that is
        # already live, and an opposite-side card closes the trade it disagrees with before anything
        # else happens - which is what keeps a reversal from being watched to its stop.
        twin: dict[str, Any] | None = None
        opposite: dict[str, Any] | None = None
        for row in still_open:
            if row.get("strategy") != card.strategy or not isinstance(row.get("ticket"), int):
                continue
            if row.get("side") == card.side:
                twin = row
            elif opposite is None:
                opposite = row
        if twin is not None and settings.auto_tp_handoff:
            # Hand-off semantics: one strategy holds one position on one symbol at a time, and a
            # newer card of the same side can only improve the trade that is already live.
            inherited = int(card.signal_epoch) > int(twin.get("signal_epoch") or 0)
            holder_tp = float(twin.get("tp") or 0.0)
            point = 10.0**-digits  # one price step: the minimum that counts as an improvement
            better = (
                order_tp > holder_tp + point
                if card.side == "buy"
                else order_tp < holder_tp - point
            )
            if inherited and better:
                holder = _find(positions, int(twin["ticket"]))
                if holder is not None:
                    result = trader.modified(
                        holder,
                        sl=float(getattr(holder, "sl", 0.0) or 0.0),
                        tp=order_tp,
                    )
                    if result.ok:
                        store.update_auto_trade_stops(int(twin["id"]), tp=order_tp)
                        twin["tp"] = order_tp
                        store.append_event(
                            "auto_handoff",
                            {
                                "id": twin["id"],
                                "strategy": card.strategy,
                                "ticket": twin.get("ticket"),
                                "old_tp": holder_tp or None,
                                "new_tp": order_tp,
                                "signal_epoch": card.signal_epoch,
                                "code_version": CODE_VERSION,
                            },
                        )
                        outcome.managed.append(
                            {
                                "strategy": card.strategy,
                                "ticket": twin.get("ticket"),
                                "kind": "tp_handoff",
                                "tp": order_tp,
                            }
                        )
                    else:
                        outcome.errors.append(f"target handoff {card.strategy}: {result.comment}")
                # Never a second same-strategy, same-side position on one symbol: whatever the
                # hand-off did with its target, this card's entry is done.
                continue
            # The card traded nothing, but it must not read as an unnoticed card on the panel:
            # the reason is named here rather than swallowed.
            why = (
                "its target could not be moved onto the trade it holds"
                if inherited and better
                else "a target not better than the one its open trade is holding"
                if inherited
                else "an older bar than the position it holds"
            )
            store.append_event(
                "auto_refused",
                {
                    "strategy": card.strategy,
                    "signal_epoch": card.signal_epoch,
                    "reason": f"its strategy already holds a position; this card is {why}",
                    "code_version": CODE_VERSION,
                },
            )
            outcome.refuse(
                card.strategy,
                f"a same-strategy trade is already live; this card is {why}, no second position on one symbol",
                signal_utc=card.signal_utc,
            )
            continue
        # No twin, or the hand-off is off: the card keeps its old semantics, under the cap.
        if opposite is not None and settings.auto_reverse_close:
            fresh, _why = _fresh(trader, card, symbol)
            if fresh:
                holder = _find(positions, int(opposite["ticket"]))
                if holder is not None:
                    result = trader.closed(
                        holder,
                        comment=f"MTI reverse {card.strategy}"[:31],
                        magic=settings.auto_magic,
                    )
                    store.append_event(
                        "auto_exit",
                        {
                            "id": opposite["id"],
                            "strategy": opposite.get("strategy"),
                            "ticket": opposite.get("ticket"),
                            "reason": "opposite signal",
                            "retcode": result.retcode,
                            "comment": result.comment,
                            "code_version": CODE_VERSION,
                        },
                    )
                    if result.ok:
                        store.close_auto_trade(
                            int(opposite["id"]), reason="opposite signal", close_ticket=result.ticket
                        )
                        still_open.remove(opposite)
                        budget += 1
                        outcome.closed.append(
                            {
                                "strategy": opposite.get("strategy"),
                                "ticket": opposite.get("ticket"),
                                "reason": "opposite signal",
                            }
                        )
                        outcome.managed.append(
                            {
                                "strategy": card.strategy,
                                "ticket": opposite.get("ticket"),
                                "kind": "opposite_close",
                                "closed_strategy": opposite.get("strategy"),
                            }
                        )
                    else:
                        outcome.errors.append(
                            f"opposite close {card.strategy}: {result.comment}"
                        )
                # The close and the entry are separate acts on purpose: a fresh opposite card now
                # falls through to the same gate, sizing and dedup as any other entry.
        if budget <= 0:
            outcome.refuse(card.strategy, "the auto-trade position cap is reached")
            continue
        if store.auto_trade_taken(card.strategy, card.signal_epoch, card.side, symbol):
            continue  # already traded this signal here; re-firing or a restart must not double it
        sizing = _size(trader, settings, spec, equity, card)
        if not sizing.fits:
            store.append_event(
                "auto_refused",
                {
                    "strategy": card.strategy,
                    "signal_epoch": card.signal_epoch,
                    "reason": sizing.reason,
                    "code_version": CODE_VERSION,
                },
            )
            outcome.refuse(card.strategy, sizing.reason or "does not fit", signal_utc=card.signal_utc)
            continue
        # A card is validated when every family behind it is: for an ordinary card that is the
        # strategy itself, and for a confluence it is the agreement it is made of - the honest
        # reading of "two rules read this bar the same way and neither has a measured edge".
        validated_card = all(name in validated for name in card.families)
        gate = card_gate(
            market,
            symbol=symbol,
            # None is the gate's own NO_VALIDATED_STRATEGY code, so it is passed only when the card
            # really has a validated strategy - or when the acknowledgement says trading without
            # one was asked for, which is then recorded as unvalidated rather than passed off as
            # validated. The gate itself is unchanged: what a validated strategy *is* stays its
            # question, and this is the caller deciding what to answer it with.
            strategy_validated=(
                card.strategy if (validated_card or selection.allow_unvalidated) else None
            ),
            # Reached only when sizing fitted: a card that does not fit is refused above, where the
            # reason can name the risk budget, rather than being reported as a gate blocker.
            risk_fits=sizing.fits,
        )
        if gate.verdict != PASS:
            store.append_event(
                "auto_refused",
                {
                    "strategy": card.strategy,
                    "signal_epoch": card.signal_epoch,
                    "reason_codes": gate.reason_codes,
                    "code_version": CODE_VERSION,
                },
            )
            outcome.refuse(
                card.strategy,
                "the gate blocked it: " + ", ".join(gate.reason_codes),
                signal_utc=card.signal_utc,
            )
            continue
        ok, why = _fresh(trader, card, symbol)
        if not ok:
            store.append_event(
                "auto_refused",
                {
                    "strategy": card.strategy,
                    "signal_epoch": card.signal_epoch,
                    "reason": why,
                    "code_version": CODE_VERSION,
                },
            )
            outcome.refuse(card.strategy, why or "the entry has gone", signal_utc=card.signal_utc)
            continue
        row_id = store.reserve_auto_trade(
            strategy=card.strategy,
            families=card.families,
            side=card.side,
            signal_epoch=card.signal_epoch,
            signal_utc=card.signal_utc,
            entry=card.entry,
            sl=card.sl,
            tp=order_tp,
            volume=sizing.volume,
            stop_pips=card.stop_pips,
            tp_r=order_tp_r,
            risk_amount=sizing.risk_amount,
            equity=equity,
            risk_pct=settings.auto_risk_pct,
            digits=card.digits,
            expires_epoch=card.signal_epoch + (max_hold_bars + 1) * timeframe_s,
            magic=settings.auto_magic,
            code_version=CODE_VERSION,
            open_ts=_utc(now_epoch) or "",
            symbol=symbol,
        )
        if row_id is None:
            continue  # reserved by an earlier cycle between the read above and here
        result = trader.placed(
            symbol=symbol,
            side=card.side,
            volume=sizing.volume,
            sl=card.sl,
            tp=order_tp,
            comment=f"MTI {card.strategy}"[:31],
            magic=settings.auto_magic,
        )
        status = STATUS_PLACED if result.ok else STATUS_REJECTED
        store.settle_auto_trade(row_id, status=status, ticket=result.ticket, retcode=result.retcode, note=result.comment)
        store.append_event(
            "auto_placed" if result.ok else "auto_rejected",
            {
                "id": row_id,
                "strategy": card.strategy,
                "families": list(card.families),
                "validated": validated_card,
                "side": card.side,
                "signal_epoch": card.signal_epoch,
                "volume": sizing.volume,
                "entry": card.entry,
                "sl": card.sl,
                "tp": order_tp,
                "tp_r": round(order_tp_r, 3),
                "tp_kind": tp_kind,
                "tp_note": tp_note,
                "risk_amount": sizing.risk_amount,
                "ticket": result.ticket,
                "retcode": result.retcode,
                "comment": result.comment,
                "code_version": CODE_VERSION,
            },
        )
        row = {
            "strategy": card.strategy,
            "families": list(card.families),
            "validated": validated_card,
            "side": card.side,
            "signal_utc": card.signal_utc,
            "entry": card.entry,
            "sl": card.sl,
            "tp": order_tp,
            "tp_kind": tp_kind,
            "volume": sizing.volume,
            "risk_amount": sizing.risk_amount,
            "ticket": result.ticket,
            "retcode": result.retcode,
        }
        if result.ok:
            budget -= 1
            outcome.placed.append(row)
            still_open.append(
                {
                    "id": row_id,
                    "strategy": card.strategy,
                    "families": list(card.families),
                    "side": card.side,
                    "signal_epoch": card.signal_epoch,
                    "signal_utc": card.signal_utc,
                    "ticket": result.ticket,
                    "volume": sizing.volume,
                    "entry": card.entry,
                    "sl": card.sl,
                    "tp": order_tp,
                    "stop_pips": card.stop_pips,
                    "tp_r": order_tp_r,
                    "expires_epoch": card.signal_epoch + (max_hold_bars + 1) * timeframe_s,
                }
            )
        else:
            outcome.refused.append({**row, "reason": f"the broker refused it: {result.comment}"})

    outcome.open_rows = still_open
    return outcome


def _setups_age_s(setups: Any, now_epoch: int) -> int | None:
    """How long ago the live setups view was written, or None when its stamp cannot be read."""
    if not isinstance(setups, dict):
        return None
    stamp = setups.get("generated_utc")
    if not isinstance(stamp, str):
        return None
    try:
        written = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    return int(now_epoch - written.timestamp())


def _size(trader: MT5Trader, settings: Settings, spec: Any, equity: float | None, card: Card) -> Sizing:
    """Position size for one card. Any unreadable input reads as "does not fit"."""
    if equity is None or equity <= 0:
        return Sizing(0.0, 0.0, 0.0, False, "the account equity is unreadable")
    if spec is None:
        return Sizing(0.0, 0.0, 0.0, False, "the symbol spec is unreadable")
    if int(getattr(spec, "trade_mode", 0) or 0) != 4:
        # 4 is FULL. LONG_ONLY/SHORT_ONLY/CLOSE_ONLY/DISABLED would refuse half the cards anyway,
        # and refusing here says so once instead of once per side.
        return Sizing(0.0, 0.0, 0.0, False, "the broker does not allow trading this symbol")
    return size_position(
        equity=equity,
        risk_pct=settings.auto_risk_pct,
        stop_distance=card.stop_distance,
        tick_size=float(getattr(spec, "trade_tick_size", 0.0) or 0.0),
        tick_value=float(getattr(spec, "trade_tick_value", 0.0) or 0.0),
        volume_min=float(getattr(spec, "volume_min", 0.01) or 0.01),
        volume_step=float(getattr(spec, "volume_step", 0.01) or 0.01),
        volume_max=float(getattr(spec, "volume_max", 100.0) or 100.0),
    )


def _fresh(trader: MT5Trader, card: Card, symbol: str) -> tuple[bool, str | None]:
    """Is the price still at the entry the card planned, or has the move already happened?

    A card is fresh by construction (`MAX_ENTRY_LAG_BARS`), but between the signal bar's open and
    the moment this cycle sees it the price can have run. Entering there is the late entry that
    makes a working strategy look broken, so it is skipped and named instead.
    """
    tick = trader.symbol_info_tick(symbol)
    if tick is None:
        return False, "the terminal returned no tick"
    market_price = float(tick.ask if card.side == "buy" else tick.bid)
    slip = (market_price - card.entry) if card.side == "buy" else (card.entry - market_price)
    if slip > MAX_ENTRY_SLIP_R * card.stop_distance:
        return False, f"price has already moved {slip / card.stop_distance:.2f}R past the entry"
    return True, None


# --- what the dashboard reads ------------------------------------------------------------


def artifact(
    outcome: Outcome,
    settings: Settings,
    *,
    market: dict[str, Any],
    now_epoch: int,
    symbol: str = "",
) -> dict[str, Any]:
    """The auto-trading view: the switch, the allow-list, the cap, and this cycle's decisions.

    This is the whole of what the dashboard needs, and it is written even when the switch is off -
    a panel that says why it is idle is the difference between "switched off" and "broken".

    A per-symbol step (`symbol` naming something other than the primary) writes a narrower view:
    its own market's rows and its own readings, its own allow-symbol, and no duplicate claim on
    the shared fields - the account, the switch and the cap stay as the primary's cycle wrote
    them, because those are one decision and one budget, not one per market. Its own readings
    and its own timestamp it does carry: a decision file that cannot say which bar it was read
    against, or how old it is, is one the health view cannot tell apart from one nobody wrote.
    """
    symbol = symbol or settings.symbol_root
    if symbol != settings.symbol_root:
        return {
            "symbol": symbol,
            "generated_utc": _utc(now_epoch),
            "open_count": len(outcome.open_rows),
            "open": outcome.open_rows,
            "placed": outcome.placed,
            "refused": outcome.refused,
            "closed": outcome.closed,
            "managed": outcome.managed,
            "errors": outcome.errors,
            "notes": outcome.notes,
            "allow": {**outcome.allow, "symbol": symbol},
            # This market's own readings, the ones its cycle gathered and its cards were judged
            # on - not the primary's, which is the whole point of a per-symbol file.
            "market": {
                "bar_age_s": market.get("bar_age_s"),
                "spread_pips": market.get("spread_pips"),
                "market_open": market.get("market_open"),
                "open_positions": market.get("open_positions"),
                "circuit_breaker_tripped": market.get("circuit_breaker_tripped"),
                "news_verifiable": market.get("news_verifiable"),
                "news_excluded": market.get("news_excluded"),
            },
        }
    switch = outcome.switch
    return {
        "generated_utc": _utc(now_epoch),
        "code_version": CODE_VERSION,
        "switch": switch.as_dict() if switch else None,
        "selection": outcome.selection.as_dict() if outcome.selection else None,
        "terminal": outcome.terminal,
        "registry": outcome.registry,
        "account": outcome.account,
        "allow": {**outcome.allow, "symbol": symbol},
        "open_count": len(outcome.open_rows),
        "open": outcome.open_rows,
        "placed": outcome.placed,
        "refused": outcome.refused,
        "rejected": outcome.rejected,
        "closed": outcome.closed,
        "managed": outcome.managed,
        "errors": outcome.errors,
        "notes": outcome.notes,
        "market": {
            "bar_age_s": market.get("bar_age_s"),
            "spread_pips": market.get("spread_pips"),
            "market_open": market.get("market_open"),
            "daily_loss_pct": market.get("daily_loss_pct"),
            "open_positions": market.get("open_positions"),
            "circuit_breaker_tripped": market.get("circuit_breaker_tripped"),
        },
        "settings": {
            "master": settings.auto_trade,
            "allow_real": settings.auto_allow_real,
            "symbol": symbol,
            "risk_pct": settings.auto_risk_pct,
            "max_positions": settings.auto_max_positions,
            "magic": settings.auto_magic,
        },
    }
