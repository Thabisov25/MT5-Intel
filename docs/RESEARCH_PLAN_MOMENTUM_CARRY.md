# Plan: momentum + carry families with example-reproduction testing

Owner: you · Buffy · Updated 2026-10-10 (living document — extended when gaps appear)

## What the sources specify (verbatim requirements)

**TSMOM/price-momentum (daily-bar adaptation, spot-price proxy label):**
- Signal: sign of past N-day log return (N grid: 20, 60, 120, 252 trading days
  ≈ 1/3/6/12 months). MOP headline: 12-month lookback, monthly hold, long/short,
  inverse-vol scaling, 40% vol target (research convention, NOT account default).
- Our adaptions to declare: (a) daily bars not monthly; (b) our triple-barrier
  harness needs SL/TP → use ATR(20) stop, N-day-signal max-hold; hold-until-flip
  variant as alternative; (c) costs in R as elsewhere.
- Kim–Tse–Wald caution: vol-scaling materially changes TSMOM alpha → test sign-only
  simple version first (our R-multiple harness is effectively risk-normalized
  already: fixed fractional stop).

**Carry:**
- Direction: positive USD−JPY rate spread → LONG USDJPY. Zero threshold = sign rule.
- Rates problem: LIBOR ceased 2023, Euroyen TIBOR ceased Dec 2024 → policy-rate
  differential is the historical proxy; broker swaps are the real cost but not
  reconstructable across 12 years. Declare: carry = policy spread proxy.
- Dated checkpoints to reproduce (verified by researcher):
  - 2024-07-31 → 2024-08-05: USDJPY 150.38 → 143.95 (−4.3%): long-carry position
    must show an adverse move.
  - 2024-07-02 peak 161.96 → Aug 5 143.95: ≈ −11% stress window.
  - 2007-08-01 → 2007-08-16: 118.71 → 114.03 (−3.9%): 2007 carry unwind.
- Carry+momentum blend (candidate C2): position = carry sign when momentum sign
  agrees, else flat.

## Changelog (new todos as gaps appear)

- [GAP-1] Our 80k-bar window starts ~2013-05; 2007 checkpoint unreachable →
  fetch-symbol M1? No: 80k H1 limit is terminal. Add H4 (16388) → ~163k H4
  equivalent days. Resolution: run H4 history if terminal provides it; else accept
  2024-only checkpoint coverage and document.
- [GAP-2] Daily bars need calendar-day mapping: weekends/holidays → use H1 bars
  resampled to the UTC day boundary, or request D1 (16408) from terminal. Prefer D1
  native when available.
- [GAP-3] Carry rate series must be hardcoded from public records (Fed/BoJ policy
  rates by month) — build a small date→rate table in-repo with source comments.
- [GAP-4] Existing 4 families have no published dated examples (they are in-house
  hypotheses) → reproduce sweep_reclaim on the researched 2024 sessions as a
  self-consistency check of the harness itself, not of a published result; document.

## Test order (executed 2026-10-10)

1. ✅ Rate table + D1/H4 loader (adapter allow-list extended) + two new families.
2. ✅ **Checkpoint tests** (`python -m mt5_intel.research.checkpoints`, exit 0):
   all four H.10 dated levels reproduce within 0.09–0.42 yen (tol 1.5):
   2024-07-31 150.38, 2024-08-05 143.95, 2007-08-01 118.71, 2007-08-16 114.03.
   Both crash windows: carry direction +1 (long USDJPY) and the move is ADVERSE
   to the carry position, matching the sources. Rate-table gap found and fixed
   during reproduction (table extended 1990→present with Fed/BoJ row citations).
3. ✅ **GAP-4 self-consistency**: all six families signal-counted over Jun-Sep 2024
   on D1: zero signals each. Explainable (carry agreement began pre-window; 120-bar
   momentum sign held through the crash; intraday families sparse on D1), but a
   zero-across-the-board window is a weak check — see GAPS below.
4. ✅ **Rigorous OOS** (`iterate D1/H1/H4`, hardened gates: train>0 AND test>0 AND
   test n>=30 AND beats placebo AND 1.5x costs>0):
   - **carry_trend: VALIDATED on D1** — train +0.233R (n=178), test +0.433R (n=98),
     placebo −0.096R, stress +0.433R. First family ever to pass all gates.
     ⚠️ Superseded in part: this run used only 6,000 bars (2003+) and one trailing test window.
     On the full 14,384-bar history and across disjoint regimes the same family loses in 3 of 11
     regimes — see "Extended-history regime test of carry_trend" below.
   - daily_momentum: test +0.030R but placebo +0.036R — no edge, rejected.
   - sweep_reclaim: test +0.113R on D1 but train −0.101R (sign-flip luck); the
     hardened gate correctly rejects it now.
   - All others rejected on D1; **no family validates on H1 or H4** (carry_trend
     train −0.027R on H4 — the D1 validation does NOT replicate on H4; noted).
5. ✅ Plan updated with findings; full checks re-run.

## New gaps discovered (living doc — filled or tracked)

- [GAP-5] **carry_trend is D1-only evidence** (H4 does not replicate). Either the
  edge is daily-bar-specific or it is regime luck on one split. Next: split-
  stability test (repeat iterate with 2-3 different train/test fractions) before
  letting it into the strategy registry.
  **RESOLVED 2026-10-11: it is regime luck.** Split stability held on the shuffle, but the
  regime test on the full 1971-2026 history shows it losing in every variant in 1990-1995,
  2002-2007 and 2007-2012, and only the 100-day variants survive the pooled 53-year window.
  See "Extended-history regime test of carry_trend" below.
- [GAP-6] The sweep's daily_momentum grids use *bars* not calendar days; on D1
  252 bars ≈ 12 months (MOP), on H1 it's 10 days — the H1/H4 iterate runs are
  therefore not valid MOP tests. D1 is the honest timeframe for both new families.
- [GAP-7] Zero-signal Jun-Sep 2024 window for all six families weakens the
  self-consistency check; widen to a 24-month window with per-family minimum
  expected counts (defined per family) so the harness provably exercises itself.
- [GAP-8] The `iterate` validated-only replay used H1 bars by default; wire it to
  the family's native timeframe (D1 for validated families) so cards are priced
  on the timeframe the evidence came from.
- [GAP-9] carry_trend's D1 validation is recorded in the strategy registry, but the
  live watcher gates on H1 data only. Do NOT wire it into the live gate until the
  watcher can compute the D1 signal (daily resample + MA100) and the daily-rebalance
  cadence is decided; otherwise the live card would not match the validated rule.

## Registry decision (2026-10-10)

carry_trend (D1) recorded as 'historically validated' with full split evidence in
data/mt5.db strategies table. Live actionability: NOT wired (GAP-9). This is the
project's first family to clear the gates; the caveats (D1-only, H4 non-replication,
proxy rates, single instrument) travel with the record.

## Scalping pass (executed 2026-10-11)

Scope: four intraday scalping families (M5-M15 native, holds minutes to a day) built from
recent multi-source research, then tested with the same rigor as the momentum/carry work.
Full note with sources and every measured number: [docs/SCALPING_RESEARCH.md].

1. ✅ **Sources**: 8 independent, mostly 2023-2026 sources (FXOpen x2, MultiBank, Dukascopy,
   ACY/LiquidityFinder, Investopedia, FXNX, FXSSI, NYCServers) plus volatility data
   (FxBacktest, OffbeatForex) and cost data (Afterprime, Pepperstone, OANDA). No source
   publishes a cost-tested deterministic recipe; the parameter disagreements are catalogued.
2. ✅ **Four templates** in `research/strategies_scalp.py`: `sr_flip`, `pullback_scalp`,
   `orb_london`, `asian_fade`, each with the cost rule in three modes (filter / widen / off)
   and a CLI-tunable multiple (`--cost-mult`, default 10 = costs <= 20% of risk).
3. ✅ **Measured this account's constraint**: flat 2.20-pip spread at every UTC hour (terminal
   bar snapshots, cross-checked against the diagnostic's by-hour table), median M5 ATR 3.6 pips
   and M15 ATR 8.0 pips. A 1-ATR M5 stop pays 122% of its risk in round-trip cost; holding
   cost at 20% needs a 22-pip stop.
4. ✅ **Rigorous runs** (4 `iterate` runs: M5/M15 x cost-mult 10/3; sweep on train, train-best
   graded on a disjoint test window with placebo, 1.5x costs and n>=30):
   - **No scalping template validated on either timeframe at either budget.**
   - Decisive number: `pullback_scalp` on M15 returns **-0.001R gross** (zero spread) on n=1454
     trades - there is no edge to rescue - and `sr_flip` M15 is -0.115R gross on n=439.
   - `carry_trend` re-validated on M15 (train +0.132R, test +0.082R, placebo -0.306R,
     survives 1.5x costs) - but see GAP-13, it is a different rule at that timeframe.
5. ✅ **Replays with an as-of clock** on M5 and M15 (visible on /replays): scalping card counts
   are 0-38 per family because the cost rule removes 98-100% of raw M5 signals.
6. ✅ **Findings on the dashboard**: new /research page renders every iterate run (train, test,
   gross, placebo, 1.5x costs, kept-by-cost-rule, verdict) and /replays lists the scalping
   families alongside the original six.
7. ✅ **Bug found and fixed by the new tests**: the sweep silently fell back to bare defaults when
   a grid parameter did not match a family signature, so `sweep_reclaim`'s 12-candidate grid was
   really 12 identical default runs. Grids are now asserted against signatures in the test suite
   and the silent fallback is gone (a mismatch raises).
8. **No scalping family was written to the strategy registry**; the live gate is unaffected and
   still sees only the D1 `carry_trend` record.
9. ✅ **Two harness bugs found while making the results readable**, both fixed: `sweep_and_reclaim`
   took a `min_pool_age` parameter that no code path used (a dead `pass` branch), so half the
   `sweep_reclaim` grid was duplicate candidates; the parameter and the branch are gone and the
   grid no longer advertises it. And the replay report stored `cards[:2000]` in family order, so
   whichever family traded most often filled the cap and the scalping templates never appeared in
   the stored sample; cards are now capped per family (200 each) so every strategy is represented.
   Re-running both harnesses after the fixes reproduced every published number unchanged.

## New gaps from the scalping pass

- [GAP-10] Session windows in `strategies_scalp` are fixed UTC, not the live system's
  exchange-local classification (D-011). Not actionable until reconciled.
- [GAP-11] The diagnostic reports spread-by-hour but no bar-ATR-by-hour. It was measured ad hoc
  for the scalping note; it belongs in the diagnostic so the cost budget is always visible
  beside the spread.
- [GAP-12] No slippage model: `simulate` charges the quoted spread only. News-time slippage is
  the failure mode the sources warn about most, and it is unmodelled.
- [GAP-13] `carry_trend`'s M15 record shares a name with the D1 family but is a different rule
  (MA100/200 = 25h/50h at M15). If that timeframe is ever used, it needs its own spec and its own
  registry key.
- [GAP-14] One instrument (USD/JPY) and one account's cost structure. The gross-vs-net column
  makes the transfer question answerable on other symbols, but nothing else has been tested.
- [GAP-15] The `widen` cost mode can turn a 2-pip structural scalp into a 22-pip risk (6 M5 ATRs).
  That is survivable for risk sizing but changes what the strategy *is*; any future promotion needs
  a maximum-stop sanity bound.

## Indicator pass: MA crossover, RSI mean reversion, and confirmation windows (executed 2026-10-11)

Scope: two indicator families requested by name (moving-average crossover; RSI mean reversion),
plus an audit of the whole family for rules that demand an exact single-bar event and miss the same
pattern when it takes one candle longer. Full note with sources, the audit table and every measured
number: [docs/INDICATOR_FAMILIES.md].

1. ✅ **Audit**: three of ten families were rigid — `sweep_reclaim` and `asian_fade` required one bar
   to both start and finish the pattern, and `pullback_scalp` allowed exactly one bar for the
   rejection candle. `sr_flip` (zone + 4-6 bar retest) and `orb_london` (ATR trigger + session
   window) were already tolerant; `range_extreme_mr` was already a percentile band.
2. ✅ **Tolerance made explicit and swept, never assumed**: `confirm_bars` (1 = the original rule,
   3 = the swept window) on the three rigid templates, and `buffer_atr` (0.0 = the exact textbook
   cross, 0.1 = the swept hysteresis band) on the crossover. Defaults preserve the old behaviour
   exactly — verified by snapshotting every signal index, side, stop and target before and after on
   three fixed synthetic series (zero differences) — so every number already published stands.
3. ✅ **Two new families** in `research/strategies.py::FAMILIES` (so they run on every timeframe):
   `ma_crossover` (20/50 SMA default; 50/200 golden cross and 9/21 EMA swept) and
   `rsi_mean_reversion` (Wilder RSI(14) 30/70; the extreme arms the setup and the turn confirms it
   within 3 bars — `entry="confirm"` — with the literal fade-the-extreme reading and Connors'
   RSI(2) 10/90 and 5/95 swept alongside). Source-backed settings only; where no source publishes a
   number (the MA buffer, the stops) the parameter is tested rather than invented as a claim.
4. ✅ **Reference smoke test on real history** (replay-style cards, H1 30,000 bars / M15 30,000):
   both families behave exactly as their definitions say and are uniformly negative net of this
   account's spread (MA cross 20/50 H1 -0.076R / M15 -0.333R; RSI 14/30-70 immediate H1 -0.175R /
   M15 -0.172R; RSI confirm H1 -0.126R / M15 -0.203R).
5. ✅ **Rigorous OOS** (`iterate` H1, M15, D1): every new family rejected except one. On D1,
   `rsi_mean_reversion` (14/30-70, confirm 1) passed all gates — test +0.099R (n=53), gross +0.121R,
   placebo -0.003R, survives 1.5x costs — **but it is split-dependent** (60/40: +0.030R; 50/50
   switches to the RSI(2) variant and returns -0.034R), so it is NOT promoted and NOT registered.
   `ma_crossover` on D1 is the instructive rejection: test +0.055R, placebo +0.095R — positive, and
   correctly refused because it does not beat its own side-flip.
6. ✅ **The tolerance finding**: the 3-bar window wins the *training* window in three of the six
   intraday runs carrying the dimension (`pullback_scalp` H1 and M15, `sweep_reclaim` M15) and every
   one still fails out of sample. It widens recognition measurably (~1.4-2x the signals for
   `sweep_reclaim` and `asian_fade`) without creating edge.
7. ✅ Tests added (`tests/test_indicator_families.py`, 18 cases): Wilder RSI against an independent
   loop and against its saturation cases, the crossover against its own semantics (no whipsaw from
   a wide band, no signal before warm-up), the confirmation window widening what the rule can see,
   the window expiring when the setup never completes, and pinned counts proving the defaults are
   byte-for-byte the rigid originals. Full suite: 103 passed.
8. **Nothing was written to the strategy registry**; the live gate still sees only the D1
   `carry_trend` record. The two new families are research candidates.

## New gaps from the indicator pass

- [GAP-16] The D1 `rsi_mean_reversion` validation is split-dependent (validated at 70/30, marginal at
  60/40, rejected at 50/50). Same class of concern as GAP-5; do not promote on one split.
- [GAP-17] `pullback_scalp` computes its slow SMA with a helper that pads the first `slow-1` bars with
   the first computed value, and seeds its EMA at `series[0]`. The bias line is therefore artificial
   during warm-up and the template can signal before its 50-bar average exists. Found while building
   the invariant test; fixing it would change already-published counts, so it is recorded, not
   quietly changed.
- [GAP-18] Confirmation windows were swept at only 1 and 3 bars, and as a fixed bar count rather than
   a volatility-scaled one. A quieter or busier regime gets the same three bars. Finer grids need a
   matching multiple-testing accounting before they are worth running.- [GAP-19] `ma_crossover` and `rsi_mean_reversion` were never tested on the instrument's own
  session-local bars (D-011); like the scalping templates, the crossover is timeframe-general and
  the RSI family is session-agnostic, so this is lower risk here than for the session templates, but
  it is still unverified.

## Extended-history regime test of carry_trend (executed 2026-10-11)

Scope: re-test the one family that had ever cleared the gates, `carry_trend` on D1, on the
terminal's **full** history rather than the 6,000 bars every earlier D1 run used, and across
disjoint market regimes instead of one trailing test window. New harness:
`python -m mt5_intel.research.regimes D1 carry_trend` (`src/mt5_intel/research/regimes.py`),
report `reports/regimes-20261011-163519.md`.

1. ✅ **We were testing on a quarter of the data.** The terminal holds **14,384 D1 bars from
   1971-01-04** (H4: 50,396 bars from the same date; H1 is still capped at 80,000 from 2013-11).
   Every earlier "D1" run passed `--bars 6000` and so started in **2003-09** — meaning the 2007
   carry unwind reproduced in `checkpoints.py` sat inside the data but was never graded
   out-of-sample. That is fixed simply by not passing `--bars`.
2. ✅ **Rate table extended back to 1973** (GAP-3 follow-on). Rows 1973-04 .. 1990-03 added: the
   BoJ official discount rate (BoJ's own published change list) against the monthly average
   effective fed funds rate (FRED). The *level* is approximate — the two are not same-day,
   same-concept observations — but `carry_direction` reads only the sign, and the sign is not
   constant across history: **the BoJ discount rate sat above fed funds through most of
   1975-1977** (rows 1975-04 .. 1977-04), so the same rule asks for the opposite side of USDJPY
   there. Pinned by `tests/test_regimes.py`. Coverage before 1973-04 is not established from a
   primary source and is excluded rather than guessed.
3. ⚠️ **The D1 "validation" is not regime-robust.** Each of the 4 grid variants was graded in 11
   disjoint regimes (placebo + 1.5x costs + n >= 30, the same gates as promotion):

   | Regime | Carry sign | Variants positive | Verdict |
   |---|---|---|---|
   | 1973-1976 oil shock / BoJ above Fed | +- | 4/4 (n=10-11, gates: 0) | edge present |
   | 1977-1979 Fed tightening returns | -+ | 4/4 (n=2-6, gates: 0) | edge present |
   | 1980-1985 Volcker shock to Plaza | + | 2/4 | mixed |
   | 1985-1989 post-Plaza yen | + | 3/4 | mixed |
   | 1990-1995 bubble collapse | +0 | **0/4** | no edge |
   | 1996-2001 ZIRP and Fed easing | + | 4/4 (gates: 4) | edge present |
   | 2002-2007 carry build-up | + | **0/4** | no edge |
   | 2007-2012 GFC and carry unwind | + | **0/4** | no edge |
   | 2013-2019 Abenomics and NIRP | + | 3/4 (gates: 3) | mixed |
   | 2020-2021 COVID and reflation | + | 3/4 | mixed |
   | 2022-2026 hike cycle and 2024 unwind | + | 4/4 (n=15-19, gates: 0) | edge present |

   Three regimes are negative on **every** variant, and their **gross** (zero-cost) readings are
   negative too (-0.32R, -0.23R, -0.61R at ma100/stop4): costs are not the cause, there is simply
   no edge in those conditions. Two of the three — 2002-2007 and 2007-2012 — are the textbook
   carry regimes this rule exists for, and both sat inside the *train* window of the original 70/30
   split, so the earlier "validation" never graded them. The 2022-2026 window (which contains the
   2024 BoJ unwind) is positive on all four variants but on 15-19 trades, below the 30-trade
   floor, so nothing in it passes the gates.
4. ⚠️ **Pooled over the full 53 years, only the 100-day variants survive.** ma100/3.0: n=271,
   +0.278R, placebo +0.039R, lower 90% CI +0.135R -> passes; ma100/4.0 passes too. Both 200-day
   variants are **beaten by their own side-flipped placebo** (ma200/3.0: +0.017R against a placebo
   of +0.212R; ma200/4.0: +0.118R against +0.248R) and fail. The edge, if it is one, lives in the
   faster trend filter, not in the carry leg alone.
5. ⚠️ **The pooled gate and the regime view disagree, and the pooled gate is the forgiving one.**
   Re-running `iterate D1 --bars 14000` at 70/30, 60/40 and 50/50 still reports carry_trend
   VALIDATED on all three (test +0.529R n=92, +0.263R n=128, +0.179R n=181) — because a handful of
   strongly positive regimes (1973-79, 1996-2001) pay for the decade of losses in 2002-2012. Note
   also that all three test windows end at the present and overlap heavily, so "three different
   splits agreed" was weaker evidence than it looked; the 6000-bar 70/30, 60/40 and 50/50 runs in
   section 4 were not independent samples.
6. **Registry unchanged.** `carry_trend`/D1 is still recorded as 'historically validated' in
   `data/mt5.db`. This note is the evidence that the label needs a regime qualifier before anyone
   acts on it; the registry is not mutated by the new harness (it only writes its own report).

GAP-5 ("D1-only, single-split evidence") is **resolved as: not robust.** Split-stability held;
regime-stability does not.

## New gaps from the regime pass

- [GAP-20] The regime harness inherits `simulate`'s single global median spread, which is 5.00
  pips on the 1971-2026 series, so 1970s-80s trades are billed a modern spread and 2013+ trades
  are billed ~3x the real one. Every reading outside 2003+ is cost-approximate. Charging the
  per-bar quoted spread (already available as `bars.spread_pips()`) would fix it, and would change
  the 1973-79 rows most.
- [GAP-21] Regime power is low: 2-48 trades per variant per regime. "No edge" in a 5-year window
  is weak evidence, and "edge present" in 1973-79 rests on n <= 11. A pre-registered per-regime
  minimum n (or an explicit low-power flag) belongs in the report before these verdicts are
  quoted.
- [GAP-22] The 11 regime boundaries are hand-drawn monetary eras, which is both arbitrary and
  cherry-pickable. A data-driven changepoint segmentation of the rate spread and the trend would
  be harder to rationalize after the fact.
- [GAP-23] `carry_trend` emits an entry only when agreement *begins* and never emits anything when
  agreement *breaks* — despite its docstring saying the position "ends (reversed entry) when it
  breaks". The only exits are the triple barrier (stop, 2R target, `max_hold`). So the family is
  not the hold-until-disagreement rule the research plan describes, and the two readings must be
  tested separately before either is believed.
