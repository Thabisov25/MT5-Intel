# MT5 Trading Intelligence — implementation plan

Status: Phase 1 (foundation) built and live-verified; Phase 2 (research engine) scaffolded;
Phases 3–4 (recommendation gate, demo evaluation) wired but explicitly **not validated** — the
gate default-denies until research produces strategies that pass validation. Nothing in this
repo places orders unless auto-trading is armed on purpose (D-019, D-039). The gate's reason
codes are the system's way of saying
"insufficient evidence", which the blueprint treats as a valid output, not a failure.

Owner: you · Technical partner: Buffy · Last updated: 2026-10-10

## Scope (fixed requirements)

- Broker: HFM MT5 on your Windows PC. Instrument: USD/JPY, buy and sell.
- The system **recommends; you trade**. Auto-trading (D-039) can place and close a position for a
  validated card, but only with two switches on, only on a demo account unless a third setting
  allows a live one, and only through the one module allowed to send an order.
- Risk limits: 1% per trade, 3% daily loss, max two simultaneous positions. The risk governor
  cannot be bypassed by strategies and never rounds risk up (D-017, D-023).
- Read-only MT5 access in depth: investor password, an allow-listed adapter (only reads are
  exposed), and a test that fails the build if an order call appears anywhere in `src/` outside the
  one module allowed to trade (D-020, D-039).
- Every card carries exact SL and TP; the watcher alerts when it cannot verify positions (D-013).
- The gate evaluates every check, records every reason code, and **default-denies** when a check
  cannot be verified (D-027): stale data, unverifiable news, unvalidated strategy, risk misfit.
- Journal records every recommendation (including SKIPs), your decision, and hypothetical
  outcomes for rejected cards (D-028).
- All maths in account currency (this account: ZAR; Cent accounts would be USC). Rand values come
  from the live USD/ZAR rate read from MT5 when available.
- Sessions are defined on Tokyo, London and New York local clocks; internally UTC (D-011, D-024).
  Implemented in `src/mt5_intel/sessions.py` (local windows resolved through the IANA database, so
  the UTC hours move with DST rather than being pinned).
- USD/JPY guards: fast-move circuit breaker, spread guard, rollover exclusion, news exclusion
  starting at ±30 minutes around major USD/JPY events (D-031, D-032).

## Current build status

| Blueprint phase | What exists | Honest state |
|---|---|---|
| 1.1 Windows app + MT5 connection | `python -m mt5_intel` diagnostic, adapter | **Verified live** (account, symbol, USD/ZAR, 80k bars) |
| 1.2 Historical data + local DB | SQLite `data/mt5.db` (events, recommendations, decisions, dedupe) | Built; schema migrates cleanly |
| 1.3 Dashboard + notifications | Next.js dashboard + `notify.py` (Windows toast, email, dedupe) | Built; toast verified. Health page breaks the gate reason codes down by UTC hour and by trading session, as a rate per bucket. |
| 2. Research engine | `research/` backtester + placebo, 4 families on real HFM H1 data (2,347 days), statuses registry | **Ran trial T-002: no family validated. All remain research candidates. Gate default-denies (correct).** |
| 3. Risk + recommendation gate | `gate.py` all-checks gate with reason codes; journal | Built, default-denies (correct) |
| 3.1 Gate history reporting | `gatehistory.py` summary over an hourly rollup (`gate_rollup.py`) with 90-day retention | Built; refresh reads one hour of cycles instead of the window |
| 4. Demo evaluation / statuses | Strategies carry validation status; no strategy has passed | Awaiting research evidence |

## Trial log (feeds deflated Sharpe; every run recorded, D-026)

| Trial | Date | Window | Result (net, out-of-sample) | Decision |
|---|---|---|---|---|
| T-002 | 2026-10-10 | H1, 40,000 bars (2,347 days) HFM real data | sweep_reclaim −0.145R (edge vs placebo −0.073R); compression_breakout −0.186R (+0.037R); trend_pullback −0.158R (−0.037R); range_extreme_mr −0.604R (−0.960R). Zero folds-positive for 3 of 4 families. | No family promoted. Gate stays default-deny. Next: parameter plateau check on compression_breakout (only family that beat placebo) and M15 timeframe before any conclusion. |
| T-003 | 2026-10-11 | H1 + M15, 30,000 bars each, 12 families | New MA-crossover and RSI mean-reversion families plus a `confirm_bars` window on the three rigid templates. H1: ma_crossover test −0.065R (gross +0.062R); rsi_mean_reversion −0.136R; pullback_scalp best-on-train is the 3-bar window, test −0.024R. M15: ma_crossover −0.406R; rsi_mean_reversion −0.132R (gross +0.166R); sweep_reclaim and pullback_scalp both prefer the 3-bar window on train and both fail on test. 6 families rejected, carry_trend re-validated. | Nothing promoted. The tolerance widens recognition ~1.4–2x without creating edge. See [docs/INDICATOR_FAMILIES.md](INDICATOR_FAMILIES.md). |
| T-004 | 2026-10-11 | D1, 6,000 bars, three train/test splits | ma_crossover positive on test (+0.055R) but beaten by its own placebo (+0.095R) → rejected. rsi_mean_reversion (14/30-70, confirm 1) passed every gate at 70/30 (test +0.099R, gross +0.121R, placebo −0.003R, survives 1.5x costs) but is split-dependent (60/40 +0.030R; 50/50 −0.034R). | The RSI family stays a research candidate — not promoted, not registered. Gate unaffected. |
| T-005 | 2026-10-12 | H1, 6,000 bars, all 12 families | **Two correctness fixes, not parameter changes (D-035, D-036).** Grading counted every re-fire of a still-open position as its own trade (D-035), and the range-extreme fade's RSI read future bars (D-036). Under the corrected count on H1: trend_pullback 1,096 signals → 192 trades (82% were re-fires), daily_momentum 223 → 49, carry_trend 119 → 29, sweep_reclaim 316 → 112, pullback_scalp 392 → 186, ma_crossover 130 → 120, orb_london and asian_fade unchanged. Expectancy moved on the honest count: sweep_reclaim −0.116R → +0.210R; daily_momentum −0.117R → +0.096R; carry_trend +0.304R → +0.150R; trend_pullback −0.112R → −0.191R. The fade rule moved twice — D-035 alone took it to −0.597R on 146 trades, and the causal RSI in D-036 then added signals and lifted it to −0.175R on 183 of 554 signals (67% of its signals are re-fires). | Supersedes the n and expectancy recorded in T-002–T-004: those counted one position several times, and the repeats concentrated on the bars a condition persisted, which is what pushed four families' mean R across zero. Nothing is promoted — carry_trend is still positive on the corrected count and still short of the gate, and the rest remain negative. Every family is re-run before any promotion. |
| T-006 | 2026-10-12 | M15 + H1, 80,000 bars each (1,176 and 4,708 days), all 12 families | First full gate run on the corrected harness (D-035, D-036) with a cost gate that actually applies 1.5× (D-037). 24 evaluations (12 families × 2 timeframes), **none promoted**. Closest: H1 `daily_momentum` n=712, mean +0.058R, edge vs placebo +0.176R, 1.5× costs +0.027R, 80% folds positive — fails only the lower 90% CI (−0.024R), so the edge is not distinguishable from zero. Next closest M15 `carry_trend`: n=446, mean +0.001R (t=0.02), edge +0.164R, 1.5× costs −0.052R. Everything else fails several criteria at once; all four scalp templates fail on both timeframes (best H1 `sr_flip` −0.098R). | Gate stays default-deny. `daily_momentum` on H1 is the only family worth a further trial, and only as a candidate: one criterion short is still a fail. |
| T-007 | 2026-10-21 | USDJPY H1 + M15, 20,000 bars each, `confluence` agreeing groups (303 and 217), both grading modes (`tools/confluence_fade_replay.py`) | The composite was regraded two ways on the same agreeing bars: the members' side it has always taken, and a `fade` rule (top third of the 96-bar range sells, bottom third buys, middle stands down, tightest card mirrored), both with fixed barriers and with the watcher's own stop management replayed bar by bar (breakeven lock at 1R → +0.1R, trail from 2R). H1: members' side +0.045R fixed / −0.017R managed; fade −0.199R fixed / −0.192R managed — most fade losers never reached +1R, so the lock never armed. M15: both negative; the fade loses less (−0.23R vs −0.36R fixed). | The user's placement rule does not beat the members' side on the timeframe this watcher trades, but it is their call: both modes are implemented behind a selection-file field (`confluence_mode`, default `members`, dashboard toggle), the setups view now carries each card's own-bar placement (`range_pos`/`fresh_high`/`fresh_low`, `setups.placement_at`), and the Live panel shows both readings. The selection is the account holder's; the measurement stays recorded here. |

## Decision log

Decisions D-001–D-034 are recorded in the blueprint. New ones are recorded here.

- **D-035 — one position at a time.** A rule may only be taken when it is flat. Every family in
  `research/strategies.py` is stateless: while its condition holds it re-fires on *every* closed
  bar. `range_extreme_reversion` sells any bar that closes in the top 5% of its 96-bar range with
  RSI > 65, so on 2026-10-12 it fired eight times between 08:00 and 16:00 — eight shorts into the
  same rally, entries ratcheting up 158.451 → 158.702 — and each was graded as a separate trade,
  stored as a separate replay card, and shown as a fresh "just entered" row. Measured over 6,000
  H1 bars, 53–82% of most families' signals were re-fires of a position already open.
  `backtest.one_at_a_time` now decides which signals are trades, and that same decision is what
  `simulate` (edges, placebo, walk-forward), the replay cards and the Live tab all read, so they
  cannot disagree about which trades exist; the placebo controls are filtered the same way.
  Two consequences to keep in view: every graded number recorded before this decision is
  superseded (T-002–T-004), and the Live tab now reports the position **as taken** — first entry,
  true age, true R — with a count of the re-fires it stood aside for, which is the number that
  used to be hidden by resetting the row to a new entry each bar.

- **D-036 — no rule may read a bar that has not happened.** `range_extreme_reversion` built its
  RSI with `np.convolve(..., mode="same")`, which *centres* the window: a bar's reading averaged
  roughly the next seven bars, so the rule decided some bars using bars that came after them. The
  causality rule the project already states (features from closed bars only) was enforced for the
  entry convention and the swing confirmation lag, but not for this indicator, and look-ahead is
  invisible in the topline R — it made the fade rule look far worse than it is (−0.597R on the
  filtered count, against −0.175R once the reading is honest). `_causal_mean` is now the single
  trailing-window helper, used by the ATR (bit-identical output, so no earlier result moves) and
  by that RSI; the ATR path was checked to produce identical values before and after. Two tests
  hold the line: appending a bar must leave every earlier reading bit-for-bit unchanged, and every
  family must judge the same bars when run over a prefix of the series as over the whole series.

- **D-037 — the cost-stress gate must charge the stressed cost.** `evaluate_family` widens the
  spreads by a `spread_multiplier` (default 1.0) for its stressed run, but two things stopped it
  biting: the trial command never passed the factor, so every report's "Stressed 1.5x" column was
  an unstressed rerun of the same numbers (plain and stressed means agreed to three decimals for
  all twelve families), and `simulate` read the cost median off `bars` rather than the spreads it
  was handed, so even an explicit 1.5× moved only a short's fill by half a spread while the cost
  charged in R stayed put. `cost_from_spreads` is now the one place the cost in R is computed —
  the median of whatever spread array it is handed, over the stop distance — `simulate` charges it
  against the spreads it was given, and the trial command passes `COST_STRESS = 1.5`. Consequence: the "survives 1.5× costs"
  claim in T-002–T-004 was never measured, and on the corrected gate the column moves materially —
  M15 `ma_crossover` −0.311R becomes −0.441R, M15 `compression_breakout` −0.329R becomes −0.546R.
  A second-order gap remains, deliberately: a stressed run re-grades the trades the *unstressed*
  fills picked, so it does not re-derive the one-position chain from stressed entry prices.

- **D-038 — a rule has to mean what it says on every timeframe, and on the clock it names.** An
  audit of the twelve families against their own docstrings found four places where the code was
  something other than the written rule. (1) **The session hours were read off the broker's
  clock.** `_utc_hours` did `(bar.time // 3600) % 24` and called it UTC, but bar stamps are the
  server's clock (D-011): on this terminal (UTC+3) London's "07:00" opening range was really at
  04:00 UTC, in the Tokyo/London gap, and the "Asian 00:00-06:00" range was 21:00-03:00 UTC. The
  module's own docstring said the reconciliation "must [happen] before any card is actionable";
  the cards shipped anyway. `Bars` now carries `utc_offset_s`, `_utc_hours` subtracts it, and a
  session rule that cannot get the offset raises `SessionClockError` (reported as an error card,
  never a silent empty) instead of trading three hours early. The same class of bug sat in
  `carry_trend`, which looks its policy-rate row up by calendar month: with a UTC+3 stamp every
  evening after 21:00 UTC reads as the *next* day, so a policy change dated the first of a month
  was applied a few hours early. It now looks up the UTC date. No measurable effect on this
  window (338 signals, +0.125R before and after) - recorded because the next month-end that does
  flip a signal should not also require re-discovering this. (2) **The windows were bar counts
  written for M5.** `orb_london(range_bars=6)` was "the first 30 minutes of 07:00 UTC" in the
  docs and a *six-hour* box on H1; `asian_fade`'s `end + 24` was "~2h" in its docstring and a
  full day on H1; `sr_flip`'s retest window was 30 minutes on M5 and 6 hours on H1. Windows that
  name a duration are now durations, converted with the series' own measured bar interval; an
  opening range shorter than one bar is refused rather than widened, which is why `orb_london`
  produces nothing on H1 (measured: 393 raw signals and -0.206R before, 0 after). (3) **Two
  RSIs.** `range_extreme_mr` built its reading from a simple mean of gains and losses while the
  panel and `rsi_mean_reversion` used Wilder's: 6 points apart at the median on H1, 14 at the
  90th percentile, and on 2,772 of 20,000 bars the two disagreed about the very 65 line this rule
  trades. It now reads `_wilder_rsi`, the project's one RSI. (4) **The staleness guard read
  `and`.** `sweep_and_reclaim` was meant to skip a bar whose swing level is older than 400 bars,
  but only skipped when *both* sides were stale, so an ancient high could anchor a trade off a
  fresh low. Latent on H1 (no bar in 20,000 has one side stale), fixed because the next quiet
  stretch or longer timeframe would find it. The side-flip "placebo" docstring was also corrected:
  it is a deterministic mirror, not the random-level control D-008 asks for, and `seed` is inert.

  **What this invalidates:** every recorded number for `orb_london` and `asian_fade` on M15 and H1
  (T-003, T-006) was measured with windows that did not mean what they said - the M15 "opening
  range" was 90 minutes, the M15 "2h" fade window was six hours. `range_extreme_mr`'s numbers move
  slightly under the single RSI (H1: -0.178R → -0.174R, n=654). The other eight families' H1
  numbers are unaffected, and the fixes change no parameter of any family. Re-run
  `python tools/audit_families.py H1 M5` for the per-family signal counts, the UTC hour each one
  actually trades, its stop against the quoted spread, and the exit mix; `python
  tools/_golden_m5.py before|after` pins the M5 behaviour the templates were written for.

- **D-039 — execution is a separate module behind two switches, and the read-only scan names it.**
  Auto-trading was asked for after the fact (unattended trades left through a turn, and entries seen
  late and taken at a worse price), and it is the one change this project had refused outright:
  "places no orders" was a promise enforced by a source scan over all of `src/`, not a note. So the
  promise was **re-scoped, not dropped**. `ReadOnlyMT5` is unchanged, the scan still fails on any
  order vocabulary anywhere in `src/`, and `mt5_intel/execution.py` is its single named exception —
  with a second test asserting that module really does contain the calls (a rename that emptied it
  would otherwise make the scan pass on a codebase that cannot trade) and a third pinning
  `MT5Trader`'s public surface to exactly `account_info`, `symbol_info`, `positions_get`,
  `symbol_info_tick`, `placed`, `closed`. Four decisions inside it, each chosen to be wrong in the
  safe direction: the `.env` master and the dashboard switch are both required, and the dashboard's
  is a file the watcher re-reads every cycle, so off takes effect on the next cycle; a REAL account
  is refused unless `AUTO_TRADE_ALLOW_REAL` is *also* set; a strategy trades only if it is listed
  **and** carries a `historically validated` row for H1, with `confluence` (two or more allowed
  cards agreeing on one bar) requiring every family that formed it; and the journal reserves
  `(strategy, signal bar, side)` with a UNIQUE key *before* the order is sent, so a re-fire, a
  restart or a crash cannot double a position. Position size is floored to the lot step and a
  minimum lot that would exceed the risk budget is a refusal, not a rounded-up trade; the card's
  stop and target ride on the order so the broker closes it with the watcher down, and the watcher
  adds only the card's own hold limit. Cards are entered fresh or not at all: a card older than one
  bar, or one whose price has already moved a quarter of the stop past the planned entry, is
  skipped and says which. **Not verified:** only the fake terminal has run this path. It has never
  sent a live order, and the live account it is pointed at is the REAL one this repo's own account
  check already reports, so the guard refuses it by default.

- **D-040 — a confluence signal stops everything opposite on its symbol.** The account holder
  set this next to the two switches: when the composite is actable (the bar it would trade),
  every opposing position on that symbol is closed at market — journal-tracked or opened by
  hand, because the terminal is what is searched, not the journal — and only that symbol;
  another watched symbol's opposing positions belong to their own cycle's decision. It fires
  once per signal, journaled on (symbol, signal bar, side) so a 30-second re-read cannot
  re-close what the holder re-opened by hand; it runs only on an armed cycle; and it exists
  only where `confluence` is itself on the allow-list, which is what builds a composite card at
  all. A bar where the composite cancels itself (agreeing groups on both sides of one bar, a
  `  members`-mode shape) closes nothing. Deliberately *not* a gate on entries: it is a close, and
  the budget recomputes after it, so the composite's own entry cannot be refused by the very
  positions its signal just stopped. **Not verified live:** the
  same reading as D-039 — only the fake terminal has run it.

## Phase 2 acceptance gate (before any card can be actionable)

A strategy family may only feed an actionable card when:

1. Its event study beats the random-level placebo, net of measured costs, in ≥2 time blocks.
2. Purged walk-forward expectancy stays > 0 with the lower confidence bound above 0.
3. Cost stress (1.5×) leaves expectancy positive.
4. Calibration is demonstrated on a held-out block, or the card shows SKIP with LOW_EVIDENCE.

Until then every cycle's outcome is a journaled `SKIP: NO_VALIDATED_STRATEGY` — which is the
system working as designed.

## Phase 3 — recommendation gate flow (implemented in `watcher/gate.py`)

```text
mt5 connected? → data fresh? → spread ok? → session open? → news verifiable/not excluded?
→ circuit breaker not tripped? → daily loss ok? → position count ok? →
validated strategy exists? → risk fits budget at min lot (never round up)? → CARD
any failure → append event with all reason codes evaluated, notify once (deduped)
```

## Phase 4 — demo evaluation

Statuses: `research candidate → historically validated → demo tested → ready for review`.
A strategy may only move up a stage with recorded evidence; deterioration pauses it;
replacements require your approval (D-030). Passing a stage never means "profitable".

## Controls

- **Execution is opt-in and pinned:** `ReadOnlyMT5` exposes no order functions and the source scan
  enforces that across `src/` except `execution.py`, whose trader surface is asserted to be exactly
  the six methods that opening and closing a trade needs (D-020, D-039).
- **Credentials:** kept in `.env` (gitignored). Reports and events never contain them.
- **Reproducibility:** every event stores inputs snapshot + code version.
- **Causality:** features are computed from closed bars only; swing confirmation lags respected.
- **Notifications:** deduped per (kind, symbol, reason signature) with a cooldown; email failures
  are logged and never crash the cycle.
- **Recovery:** on reconnect the cycle verifies account/symbol/freshness before normal operation,
  and the daily-loss basis resets at the broker day boundary.
- **Startup:** `python -m mt5_intel.watch` runs continuously; a Windows scheduled task or startup
  shortcut can launch it on boot, waiting for MT5 to become available.
- **Resource use:** SQLite + file-status JSON; no external services.
