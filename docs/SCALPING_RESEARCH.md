# Scalping research — recent sources, the templates they support, and what our tests found

Scope: intraday USD/JPY scalping (M5–M15 native, holds of minutes to one day), covering
support/resistance flips, retracement continuation, opening-range breakouts and session-range
fades. Everything below is either a quoted source claim (with URL) or a measurement from this
repository's own terminal. Sources were gathered fresh (October 2026) and the rule sets come
from **eight independent sources**, not one.

## 1. What the sources actually specify

| Setup | Source | URL | What it pins down | What it leaves open |
|---|---|---|---|---|
| S/R flip | FXOpen, break-and-retest | https://fxopen.com/blog/en/how-can-you-use-a-break-and-retest-strategy-in-trading/ | A candle must **close decisively beyond** the level (a wick is not enough); enter after a rejection candle at the retest; stop "a few pips" beyond the retest extreme; target = next structural level **or** fixed 1:2 / 1:3 | swing lookback; what "decisive" means in pips; retest tolerance; how many pips are "a few" |
| S/R flip | MultiBank, S/R guide | https://multibankfx.com/en/blog/trading-101/support-and-resistance-complete-trading-guide | Zone not line; level valid after ≥ 2 meaningful reactions; stop **0.5–1.0 ATR** beyond the zone | "meaningful" is undefined |
| S/R flip | Dukascopy, support and resistance | https://www.dukascopy.com/swiss/english/marketwatch/articles/support-and-resistance/ | Level = prior swing high/low, prior highs/lows, round numbers, pivots | no M5/M15-specific numbers |
| Pullback | ACY Securities lesson (syndicated) | https://liquidityfinder.com/news/pullback-trading-strategy-using-moving-averages-fibonacci-61797 | Trend = **50 SMA**, value line = **20 EMA**; 38.2–50% = shallow pullback (20 EMA), 61.8–78.6% = deep (50 SMA); triggers = engulfing / rejection close / sweep-and-react; stop below the entry wick or swing low; target previous high/low or 1.272/1.618 extension | objective swing anchors; what a "strong close" is |
| Pullback | Investopedia, Fibonacci mistakes | https://www.investopedia.com/articles/forex/11/fibonacci-rules.asp | Levels 23.6/38.2/50/61.8/78.6; **short-timeframe Fibs are less reliable** and need confirmation | nothing substitution-worthy; this is a warning |
| ORB | FXOpen, opening-range breakout | https://www.fxopen.com/blog/en/opening-range-breakout-strategy/ | Opening range = first **5, 15 or 30 minutes** after a session open (5 = fast/false-prone, 30 = fewer signals); trigger = candle **closes** beyond the edge; target **2:1 or 3:1**; stop = **either** just beyond the broken boundary **or** beyond the opposite side (the page contradicts itself) | which of the two stops; how far beyond the broken edge |
| ORB | FXOpen, 3-session system (2024) | https://fxopen.com/blog/en/forex-3-session-trading-system/ | London break of the **Asian range** by pending stops at both edges, unfilled side cancelled, **stop at the opposing range edge** | the exact Asian window; target |
| ORB | NYCServers, London breakout | https://newyorkcityservers.com/blog/london-breakout-strategy | Asian window **midnight–07:00 GMT**, **3–5 pip** entry buffers, opposite-edge stop (or +5 pips), 1× and 1.5–2× range targets, no re-entry after a stop-out | presented as one vendor's rule set, not a standard |
| Session fade | FXNX, Tokyo "Asian range trap" | https://fxnx.com/en/blog/tokyo-session-strategy-asian-range-breakouts | Enter opposite **only after a candle closes back inside**; stop just beyond the false-break wick; **first target = the other side of the range** | no numeric buffer; calls it "powerful" without a performance series |
| Session fade | FXSSI, session strategies | https://fxssi.com/forex-strategies-by-session | Asian session 21:00–08:00 UTC; alternative "master candle" H1 fade on M15 with 5-pip break, 1:1 stop/target | contains no plain session-high/low fade |
| Session fade | ForexFactory thread (older, 2008) | https://www.forexfactory.com/thread/80175-daily-scalping-the-asian-high-and-asian-low | Trades **breakouts** at the Asian high/low with an hourly 55 EMA filter, 30-pip stop, 15-pip first target | old, user-generated, opposite direction to the fade above |
| Volatility | FxBacktest session/hour ranges (2015–2025, hourly bars) | https://fxbacktest.app/guide/forex-trading-sessions/ | USD/JPY mean hourly range: Tokyo 17.2 pips, London 17.4, London–NY overlap 24.0, NY 14.6, late NY 12.2 | hourly, not M5/M15 bar ATR |
| Volatility | FxBacktest volatility by hour | https://fxbacktest.app/research/forex-volatility-by-hour/ | Peak 14:00 UTC = 25.4 pips mean hourly range; quiet 21:00 UTC = 10.7 | different sample window from the table above |
| Volatility | OffbeatForex, daily range table | https://offbeatforex.com/forex-average-daily-range-table/ | USD/JPY average daily range: 2023 = 123 pips, 2024 = 157.5, 2025 = 133 | daily, not intraday |
| Costs | Afterprime, USD/JPY page | https://afterprime.com/forex/usdjpy | Displays **0.47 pips** all-in and $4.75 per standard lot round turn | broker's own figure, not an audited session average |
| Costs | Pepperstone, USD/JPY guide | https://pepperstone.com/en/insights/guides/forex/how-to-trade-usdjpy | Claims USD/JPY averages **0.1–0.3 pips** with most brokers at peak hours | educational claim; not an execution study |
| Costs | FXNX, USD/JPY 1-minute guide | https://fxnx.com/en/blog/usd-jpy-1-min-strategy-your-forex-goldmine | Only trade when the spread is **below 0.8 pips**; setup uses a **5–8 pip stop**; news slippage damages it | practitioner guidance, no audited net results |
| Costs | OANDA historical spreads / Dukascopy average spreads | https://www.oanda.com/us-en/trading/historical-spreads/ · https://www.dukascopy.com/swiss/english/marketwatch/average-spreads/ | Tools exist for 15-minute-interval and session spread distributions | neither exposed USD/JPY values in a retrievable form |
| Evidence | Holmberg, Lönnbark & Lundström, ORB study | https://ideas.repec.org/p/hhs/umnees/0845.html | Finds significantly positive ORB returns vs a fair game | older study, not USD/JPY M5–M15, not cost-adjusted for retail |

**Bottom line from the search:** no source offers a fully deterministic, cost-tested scalping
recipe for USD/JPY M5–M15. The sources agree on *concepts* (zones not lines, close-beyond
confirmation, retest entries, session ranges) and contradict each other on the parameters.

### The disagreements, explicitly

1. **S/R swing lookback** — no source gives one; "valid after two reactions" is the closest.
2. **Breakout confirmation** — pending orders at the edge (FXOpen 2024) vs a candle close beyond
   (FXOpen ORB) vs close-then-retest (FXSSI) are three different entry models.
3. **Stops** — "a few pips" past the extreme (FXOpen), 0.5–1.0 ATR beyond the zone (MultiBank),
   the opposing range edge (FXOpen 2024), or aligned to the wick (FXNX). Materially different risk.
4. **Targets** — next level, fixed R, range multiples, the range midpoint, or the far edge.
5. **Asian window** — 00:00–04:00, 00:00–08:00, 21:00–06:00 and 21:00–08:00 all appear.
6. **Evidence** — the only empirical profitability result found is the ORB paper; nothing recent
   and cost-adjusted for USD/JPY M5–M15 was retrievable.

## 2. The four templates we implemented

`src/mt5_intel/research/strategies_scalp.py`. Each rule is a transparent adaptation; where a
source gave no number we chose one and said so in the docstring.

| Family | Level / value line | Trigger | Stop | Target |
|---|---|---|---|---|
| `sr_flip` | fractal swing zone, width 0.5 × ATR(14) | close beyond the zone, then within 4–6 bars a bar trades back into the zone and closes beyond the edge again | 0.5 ATR past the retest bar's extreme (MultiBank's 0.5–1.0 ATR; FXOpen's "few pips") | 2R or 3R (FXOpen's cited fixed multiples) |
| `pullback_scalp` | fast EMA(20) value line, slow SMA(50) bias (ACY) | bar trades 0.5–1.0 ATR into the fast EMA without closing beyond the slow line, then a bar within the confirmation window (`confirm_bars`, 1 by default = the next bar only, 3 when swept) closes beyond the pullback bar's extreme | 0.25 ATR past the pullback bar's extreme | 1.5R or 2.0R (source cites previous high/low or 1.272/1.618 extensions) |
| `orb_london` | high/low of the first 3 or 6 bars of 07:00 UTC (15 or 30 minutes on M5) | a bar closes 0.25 ATR beyond the range edge; one signal per side per day | **both source conventions tested**: 1 ATR (FXOpen "just beyond the broken edge") and the opposing range edge (FXOpen 2024) | 2R or 3R |
| `asian_fade` | high/low of the 00:00–06:00 UTC window (one cited variant of four) | a bar trades 0.5 × ATR beyond an edge and closes back inside; by default both happen on one bar, and with the 3-bar reclaim window the break arms the setup and a later bar may close back inside the same frozen range | 0.25 ATR past the excursion extreme (FXNX wick rule, no published buffer) | range midpoint **or** the far edge (FXNX's stated first target) |

Two design decisions worth stating:

- **Our session windows are UTC fixed offsets.** The live system classifies bars by exchange-local
  clocks (D-011); this research layer does not, so a card from these families is not actionable
  until that is reconciled. Noted in the module docstring.
- **Entries are the next bar's open after a closed-bar signal** (D-012), and shorts fill on the
  ask. No look-ahead is available to any family.

## 3. The cost rule, and this account's measured constraint

The one cost rule the research supports is a **stop that is a multiple of the quoted spread**:
with `stop >= m x spread` the round-trip cost (one spread in, one out) is `2/m` of the risk.
Default `m = 10` → costs ≤ 20% of risk. The harness applies it per signal bar in one of two
modes, and both are swept against each other rather than assumed:

- `filter` — drop signals whose structural stop is too close (the conservative screen);
- `widen` — keep the signal and push the stop out to the floor (risk-based sizing absorbs it);
- `off` — raw signals, used only to measure how much the rule removes.

Measured with this repository's terminal (40,000 M5 bars and 30,000 M15 bars of USD/JPY):

| Measurement | Value |
|---|---|
| Median quoted spread (M5 and M15, per the terminal's own bar snapshots) | **2.20 pips** |
| Spread by UTC hour (diagnostic's 24-hour table) | flat 2.20 at every hour; 6.50 at 00:00 UTC, 2.90 at 23:00 |
| Median M5 bar ATR(14) | **3.6 pips** (quiet 2.3 at 02:00 UTC, busy 5.5 at 17:00) |
| Median M15 bar ATR(14) | **8.0 pips** (quiet 5.9 at 01:00 UTC, busy 11.1 at 18:00) |
| Round-trip cost vs one M5 ATR stop | 4.4 / 3.6 = **122% of risk** |
| Round-trip cost vs one M15 ATR stop | 4.4 / 8.0 = **55% of risk** |
| Stop needed to hold cost at 20% of risk | **22 pips** (6.1 M5 ATRs, 2.8 M15 ATRs) |
| Stop needed to hold cost at 67% of risk (`m = 3`) | 6.6 pips |

This is a *fixed-spread retail account*: there is no cheap hour to filter into, so the only lever
is a wider stop or a different timeframe. Every result below is therefore also run with a loosened
budget (`m = 3`, i.e. tolerating 67% of risk in cost) to test whether the templates fail because of
the budget or because of the signal.

## 4. Results on real history

Harness: `python -m mt5_intel.research.iterate <TF> --bars N --max-hold H [--cost-mult m]`.
Each family is swept on the train window; only the train-best candidate is graded on a disjoint
test window and must pass all five gates (positive train, positive test, n >= 30, beat the side-flip
placebo, survive 1.5x costs). `gross` re-scores the identical trades with a zero spread, which
separates "no edge" from "edge smaller than the spread".

### 4.1 Scalping templates, per timeframe and cost budget

| Run | Template | Best on train | Raw -> kept | Median stop vs floor | Net test | Gross test | Verdict |
|---|---|---|---|---|---|---|---|
| M5, m=10 | `sr_flip` | swing_k=3, retest 6, tp 3R, filter | 2217 -> 26 (1%) | 4.16 vs 22.0 pips | +0.436R (n=12) | +0.583R | rejected (train -0.072R, n<30) |
| M5, m=10 | `pullback_scalp` | 8/21 EMA, 1.0 ATR, tp 2R, filter | 6438 -> 161 (2%) | 5.54 vs 22.0 pips | -0.345R (n=77) | -0.276R | rejected |
| M5, m=10 | `orb_london` | 30 min, 0.25 ATR trigger, stop 1 ATR, tp 3R | 322 -> 1 (0%) | 2.92 vs 22.0 pips | n=0 | n/a | rejected |
| M5, m=10 | `asian_fade` | fade 0.5 ATR, far target, widen | 33 -> 0 (0%) | 1.81 vs 22.0 pips | -0.971R (n=11) | -0.776R | rejected |
| M5, m=3 | `sr_flip` | swing_k=2, retest 4, tp 2R, filter | 2395 -> 510 (21%) | 3.94 vs 6.6 pips | -0.442R (n=248) | - | rejected |
| M5, m=3 | `pullback_scalp` | 8/21 EMA, 1.0 ATR, tp 2R, filter | 6438 -> 2399 (37%) | 5.54 vs 6.6 pips | -0.364R (n=1101) | - | rejected |
| M5, m=3 | `orb_london` | 30 min, 0.25 ATR, stop = opposite edge, tp 2R | 322 -> 114 (35%) | 5.25 vs 6.6 pips | -0.335R (n=58) | - | rejected |
| M5, m=3 | `asian_fade` | fade 0.25 ATR, far target, filter | 67 -> 8 (12%) | 1.81 vs 6.6 pips | -1.550R (n=3) | - | rejected |
| M15, m=10 | `sr_flip` | swing_k=3, retest 4, tp 3R, widen | 1525 -> 130 (8%) | 9.58 vs 22.0 pips | -0.289R (n=439) | **-0.115R** | rejected |
| M15, m=10 | `pullback_scalp` | 8/21 EMA, 1.0 ATR, tp 2R, widen | 4861 -> 815 (17%) | 12.46 vs 22.0 pips | -0.182R (n=1454) | **-0.001R** | rejected |
| M15, m=10 | `orb_london` | 30 min, 0.25 ATR, stop = opposite edge, tp 2R | 374 -> 83 (22%) | 13.40 vs 22.0 pips | -0.408R (n=12) | -0.250R | rejected |
| M15, m=10 | `asian_fade` | fade 0.25 ATR, mid target, filter | 366 -> 4 (1%) | 5.25 vs 22.0 pips | n=0 | n/a | rejected |

**No scalping template validated on either timeframe at either budget.** M15 is the more
informative failure because it has the largest samples: `pullback_scalp` produced 4,861 raw signals
of which the sweep kept 815 on test, and those 1,454 finished trades return **-0.001R gross** - a
literal zero before any cost is charged, and -0.182R after. `sr_flip` on M15 is the same story with
a smaller sample: -0.115R gross on n=439.

The one positive scalping reading anywhere is `sr_flip` on M5 test (net +0.436R, gross +0.583R),
and it is not evidence: n=12 trades, and its train window was negative (-0.072R). The gate refuses it.

### 4.2 The six earlier families, for contrast (M15, m=10)

| Family | Net test | Gross test | Verdict |
|---|---|---|---|
| `sweep_reclaim` | -0.459R (n=321) | -0.112R | rejected |
| `compression_breakout` | -0.528R (n=114) | +0.026R | rejected (edge ~ 0, costs decide) |
| `trend_pullback` | -0.324R (n=656) | +0.006R | rejected (edge ~ 0, costs decide) |
| `range_extreme_mr` | -0.640R (n=628) | -0.277R | rejected |
| `daily_momentum` | -0.125R (n=410) | +0.122R | rejected (gross edge, cost-negative) |
| `carry_trend` | +0.082R (n=273) | +0.255R | **VALIDATED** (train +0.132R, placebo -0.306R, survives 1.5x costs) |

The pattern is consistent: the families with a real gross edge are the slow, wide-stop ones
(`carry_trend` needs 4 ATR, `daily_momentum` 3 ATR), and they are the only ones where removing
costs changes the sign of the result. Every fast, tight-stop family sits at gross ~ 0 or below, so
no stop or target adjustment can rescue it.

### 4.3 Replay with an as-of clock

`python -m mt5_intel.research.replay M5 --bars 40000 --max-hold 288 --asof "2026-08-01"`
(40,000 M5 bars, 193 days, 10 families; cards are generated only from bars at or before the clock
and graded on what came next):

| Family | Cards | Mean (R) | TP | SL |
|---|---|---|---|---|
| `sr_flip` | 12 | -0.455 | 2 | 8 |
| `pullback_scalp` | 38 | -0.063 | 16 | 21 |
| `orb_london` | 1 | +1.830 | 1 | 0 |
| `asian_fade` | 0 | - | - | - |

The scalping card counts are tiny **because the cost rule removed almost everything**: on a 2.2-pip
spread, `sr_flip` generates 2,217 raw signals over the window and only 26 clear the floor. That is
itself the finding - an M5 scalper on this account is almost always idle, and the trades that do
survive the screen are a coin flip in the 12-38 card range.

### 4.4 Confirmation windows on the three rigid templates

The audit in [docs/INDICATOR_FAMILIES.md](INDICATOR_FAMILIES.md) found three templates demanding
an exact single-bar event: `sweep_reclaim` (sweep and reclaim on one bar), `asian_fade` (false break
and close back inside on one bar) and `pullback_scalp` (confirmation on the very next bar only).
Each now takes a `confirm_bars` window, defaulting to 1 so every number in the tables above stays
reproducible, and swept at 1 against 3.

Measured on the same windows as section 4.2 (H1 30,000 bars, M15 30,000 bars; max hold 120):

| Template | Timeframe | Same bar / next bar only | 3-bar window |
|---|---|---|---|
| `sweep_reclaim` | H1 | 1,477 cards, -0.122R | 2,544, -0.131R |
| `sweep_reclaim` | M15 | 1,527, -0.335R | 2,673, -0.306R |
| `pullback_scalp` | H1 | 2,520, -0.080R | 2,331, **-0.050R** |
| `pullback_scalp` | M15 | 500, -0.192R | 512, -0.230R |
| `asian_fade` | H1 | 149, -0.193R | 330, -0.176R |
| `asian_fade` | M15 | 1, -1.190R | 4, -0.160R |

The window does what it was added for: `sweep_reclaim` and `asian_fade` recognise roughly 1.4-2x
as many setups, because late completions that the one-bar rule silently skipped are now seen. It
does not change any verdict. In the out-of-sample harness the 3-bar window wins the *training*
window for `pullback_scalp` on both H1 and M15 and for `sweep_reclaim` on M15, and each still fails
on the held-out test window (`pullback_scalp` H1: train +0.011R, test -0.024R). Widening what a rule
can recognise is not the same as giving it an edge.

## 5. Interpretation

1. **The templates fail for two independent reasons, and costs are only one of them.** The cost
   floor is real and severe (98-100% of raw M5 signals removed at m=10), but the decisive number is
   `pullback_scalp`'s -0.001R gross on 1,454 trades: the entries carry no directional information.
   Widening stops, moving to M15, or doubling the tolerated cost share does not change a zero.
2. **Costs are nevertheless the binding constraint on anything fast here.** A 2.20-pip spread on a
   3.6-pip M5 ATR means a 1-ATR stop pays 122% of its risk in round-trip cost. A viable intraday rule
   on this account needs stops of roughly 22 pips (6 M5 ATRs) - which is no longer scalping, and is
   why `carry_trend` (4 ATR stop) is the only survivor.
3. **No adjustment tested rescues the four templates.** Both cost modes were swept against each
   other, both ORB stop conventions, both fade targets, 15- and 30-minute opening ranges, and 8/21
   and 20/50 EMA/SMA pullback variants. Every grid winner failed out-of-sample.
4. **Nothing here is wired into the live system.** These families live in the research layer; the
   watcher's strategy gate reads only the registry, and the iteration harness does not write to it
   (only the walk-forward CLI does). `carry_trend`'s D1 record from the earlier work remains the
   single registered strategy, and the live H1 watcher deliberately ignores it (timeframe mismatch,
   see `watch.py`).

### What would change the answer

- **A different cost structure.** At the 0.47-pip all-in cost one broker publishes, round-trip is
  0.94 pips, so a 5-pip stop pays 19% of risk and the 10x rule admits stops from ~9 pips. The
  templates would then be a question of *edge* rather than *budget* - which the gross column above
  already answers negatively for M5-M15.
- **Wider-stop intraday rules on M15-M30** with stops >= 2.5 M15 ATRs. The `daily_momentum` reading
  (gross +0.122R, net -0.125R at 3 ATR) is a cost-budget hypothesis to test, not a result.
- **A rule with an actual gross edge.** Nothing in this set has one, so further tuning of these
  four templates is not warranted.

## 6. Open gaps

| Gap | Why it matters | Status |
|---|---|---|
| Session windows are fixed UTC, not exchange-local (D-011) | cards from these families are not actionable until reconciled | open |
| No M5/M15 bar-ATR-by-hour data in the diagnostic report | measured ad hoc for this note (section 3); belongs in the diagnostic | open |
| Real broker swap/rollover costs for the carry side | `carry_trend` uses a policy-rate proxy, not swap charges | open (carried from the momentum/carry plan) |
| `carry_trend` validated on M15 as well as D1 | on M15 it is MA100/200 (25h/50h) - a different strategy sharing a name; needs its own spec if it is to be used | open |
| No slippage model beyond spread | news-time slippage is one of the few things the sources do warn about | open |
| Confirmation windows on the three rigid templates were swept at only 1 and 3 bars | a finer grid would say more, but every extra cell is another trial in the multiple-testing count | open (see [INDICATOR_FAMILIES.md](INDICATOR_FAMILIES.md)) |
| `pullback_scalp`'s local SMA pads its warm-up bars with the first computed value (and its EMA is seeded at `series[0]`) | the bias line is artificial for the first `slow-1` bars, so the template can signal before its 50-bar average exists | open |
