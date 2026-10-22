# Indicator families, and making a candle pattern tolerant without making it noise

Scope: two new families (moving-average crossover, RSI mean reversion) and one cross-cutting
change — the rules that used to demand an exact single-bar event now carry an explicit
**confirmation window**, and the MA crossover carries an explicit **hysteresis band**. Every
number below is a measurement from this repository's own terminal, produced by the same harness
as the earlier notes (`replay`, `iterate`); sources are cited where a setting came from a source.

Related: [docs/SCALPING_RESEARCH.md](SCALPING_RESEARCH.md) (the four intraday templates, same
cost model), [docs/RESEARCH_PLAN_MOMENTUM_CARRY.md](RESEARCH_PLAN_MOMENTUM_CARRY.md) (the living
changelog and the gap list).

## 1. The question this answers

The design concern was that a rule which depends on a *fixed, rigid* candle movement will miss
valid setups: the same event happens, but it takes one candle longer to show itself than the
textbook definition allows. The opposite failure is loosening the rule until it fires on noise.

Section 2 audits every family against that, section 3 states what was changed, and section 4 is
the evidence.

## 2. Audit: rigid vs tolerant, family by family

| Family | The recognition condition | Tolerance | Verdict |
|---|---|---|---|
| `sweep_reclaim` | sweep of a swing level **and** reclaim on the **same bar** | swept depth is a range (`atr_mult`), the level is a line | **rigid** — the writer of the setup bar had to also complete it |
| `compression_breakout` | close outside a box whose width is at a low quantile | box is a range, squeeze is a quantile | fair — a breakout *is* a single-bar close event |
| `trend_pullback` | any bar dipping `k x ATR` back to the fast EMA | pullback depth is a range | fair (arguably loose: no confirmation at all) |
| `range_extreme_mr` | close in the top/bottom 5% of a 96-bar range **and** RSI past 65/35 | percentile band + indicator band | tolerant — already a range, not a point |
| `daily_momentum` | sign flip of a trailing return | none | fair — a sign flip is inherently one bar |
| `carry_trend` | carry sign agrees with close-vs-MA | one-bar `agreement begins` event | fair |
| `sr_flip` | close beyond a **zone** of width `0.5 x ATR`, then a retest within 4-6 bars | zone (not a line) + retest window | tolerant |
| `orb_london` | close beyond the range edge by `0.25 x ATR`, within the session | ATR buffer + a 24-bar session window | tolerant |
| `pullback_scalp` | pullback bar, then **the very next bar only** confirms | none on the confirmation | **rigid** — one bar was the whole window |
| `asian_fade` | false break **and** close back inside on the **same bar** | excursion depth is a range | **rigid** — same-bar completion required |

Three families failed the audit: `sweep_reclaim`, `pullback_scalp` and `asian_fade`. Two of them
demand that one candle both start and finish the pattern; the third allows exactly one bar for the
rejection candle to appear.

## 3. What changed

**Confirmation windows.** `sweep_reclaim`, `pullback_scalp` and `asian_fade` each gained a
`confirm_bars` parameter. The default is `1`, which is *exactly* the old rule (verified by
snapshotting every signal index, side, stop and target before and after the change on three
fixed synthetic series — zero differences), so nothing already published moved. Above 1 the
setup is armed by the first half of the pattern, the level that was swept is frozen, and any bar
inside the window may complete it. The setup expires when the window runs out; a pending setup
owns its bars, so a fresh one cannot quietly replace it.

**A hysteresis band.** `ma_crossover` takes `buffer_atr`. At `0.0` (the default) it is the exact
textbook cross. Above 0 the separation between the averages must clear `buffer_atr x ATR(14)`
before the state may flip, and the state is held inside the band. This is the same idea as the
confirmation window: recognise "the fast line is *meaningfully* above the slow line" rather than
"the two lines swapped on this tick".

**Windows are swept, never assumed.** Every tolerance is a dimension in `research/sweep.py`
(`confirm_bars` in 1/3 for the three templates, `buffer_atr` in 0.0/0.1 for the crossover), so the
train/test harness measures whether relaxing the rule actually helps instead of assuming it does.
The literature is clear that this is the right way round: Bulkowski's candle definitions use
relative geometry but his *performance* statistics are reported separately from the shape rules,
and the standard warnings about parameter count and data snooping (Ernie Chan; Bailey et al.'s
probability-of-backtest-overfitting) apply directly to a window length.

## 4. The two new families

### 4.1 Moving-average crossover (`ma_crossover`)

Sources: Investopedia's golden-cross page describes 50/200 as the commonly used pair and warns
that longer periods are smoother but lag; OANDA and StockCharts describe 20/50 as the
*intermediate* pair and 5/10 as short; OANDA notes signals are usually treated as valid on a
**close**, which StockCharts repeats for its crossover examples. No source establishes a
"best" pair, and the FX evidence is unflattering: Schulmeister's yen/dollar study of 1,024 MA and
momentum models found all profitable 1976-1999 and the profitability gone in 2000-2004, and
Coakley/Marzano/Nankervis report that after data-snooping adjustment virtually no traditional
rule is significant in 2006-2015. So the family carries the three cited pairs as a small,
predeclared set rather than a search over many:

| Setting | Where it comes from |
|---|---|
| 20/50 SMA (default) | the intermediate pair OANDA/StockCharts describe |
| 50/200 SMA | the golden cross (long-horizon; a daily-scale pair) |
| 9/21 EMA | the fast practitioner variant, tested as a comparison |
| `buffer_atr` 0.0 / 0.1 | no source publishes a canonical buffer, so it is tested, not assumed |

Stop is `1.5 x ATR(14)` and the target is `2R`, the harness's convention; the sources publish no
crossover-specific stop, and StockCharts' own example (stop below the MA or the recent swing low)
does not translate into a number.

### 4.2 RSI mean reversion (`rsi_mean_reversion`)

Sources: Wilder's RSI(14) with 30/70 is the historically grounded baseline (StockCharts and
Investopedia both state 14 and 70/30 as the default); 20/80 is the more selective variant. The
Connors fast reading is RSI(2) below 10 / above 90, with the more extreme <5 / >95 tested in his
own work (StockCharts' RSI(2) page). On entry timing the sources are explicit that an extreme
reading is an *alert*: OANDA and Investopedia both warn that RSI can stay beyond 70/30 for a long
time in a trend and that a reading alone is early.

That is why the default here is **not** the naive version:

| `entry` | Meaning |
|---|---|
| `"immediate"` | fade the bar whose RSI closes beyond the level — the literal "buy below 30 / sell above 70" |
| `"confirm"` (default), `confirm_bars=3` | the extreme arms the setup; enter on the first bar within three whose RSI closes back through the level |

Both are in the grid, so the harness compares them head to head rather than trusting either.

## 5. Results on real history

### 5.1 Reference settings behave as advertised (smoke test, full sample)

Cards generated and graded the way a replay does, no as-of clock, max hold 120 bars. The point is
that the implementations do what their definitions say — the signs are the research finding.

| Rule | H1 (30,000 bars, 1,760 days, 1.90 pip spread) | M15 (30,000 bars, 439 days, 2.20 pip spread) |
|---|---|---|
| MA cross 20/50 SMA, exact | 624 cards, **-0.076R** | 673, **-0.333R** |
| MA cross 50/200 SMA (golden cross) | 192, **-0.155R** | 181, **-0.477R** |
| MA cross 9/21 EMA | 1,234, **-0.067R** | 1,411, **-0.380R** |
| MA cross 20/50 + 0.10 ATR band | 582, **-0.060R** | 607, **-0.358R** |
| RSI 14/30-70, fade the extreme | 3,986, **-0.175R** | 2,886, **-0.172R** |
| RSI 14/30-70, wait for the turn (3 bars) | 973, **-0.126R** | 809, **-0.203R** |
| RSI 14/20-80, wait for the turn | 210, **-0.215R** | 152, **-0.219R** |
| RSI 2/10-90 (Connors), fade the extreme | 7,818, **-0.174R** | 7,571, **-0.285R** |
| `sweep_reclaim`, same bar | 1,477, -0.122R | 1,527, -0.335R |
| `sweep_reclaim`, 3-bar reclaim window | 2,544, -0.131R | 2,673, -0.306R |
| `pullback_scalp`, next bar only | 2,520, -0.080R | 500, -0.192R |
| `pullback_scalp`, 3-bar confirm window | 2,331, **-0.050R** | 512, -0.230R |
| `asian_fade`, same bar | 149, -0.193R | 1, -1.190R |
| `asian_fade`, 3-bar reclaim window | 330, -0.176R | 4, -0.160R |

Every family generates signals, in the expected direction, with sensible TP/SL splits — and every
one is negative. The confirmation window is doing what it was asked to do (the `sweep_reclaim` and
`asian_fade` counts rise by ~1.4-2x, because previously-missed late completions are now seen), but
widening what a rule recognises does not create an edge that was never there.

### 5.2 The rigorous harness (sweep on train, graded on a disjoint test window)

`python -m mt5_intel.research.iterate <TF>` — train-best by train mean, then on the held-out test
window the candidate must be positive, beat its side-flipped placebo and survive 1.5x costs.

| Run | Family | Train-best | Train | Test | Gross test | Placebo | 1.5x costs | Verdict |
|---|---|---|---|---|---|---|---|---|
| H1 | `ma_crossover` | 20/50 EMA, exact cross | +0.011 (n=362) | **-0.065** (n=176) | +0.062 | -0.182 | -0.065 | rejected |
| H1 | `rsi_mean_reversion` | 14/30-70, confirm 1 | -0.100 (n=689) | -0.136 (n=284) | -0.031 | -0.242 | -0.125 | rejected |
| H1 | `sweep_reclaim` | 3-bar window, not chosen | +0.004 (n=955) | -0.211 (n=427) | -0.103 | +0.106 | -0.198 | rejected |
| H1 | `pullback_scalp` | **3-bar window wins on train** | +0.011 (n=2368) | -0.024 (n=848) | +0.076 | -0.134 | -0.021 | rejected |
| M15 | `ma_crossover` | 9/21 EMA + 0.1 ATR band | -0.270 (n=682) | -0.406 (n=303) | -0.030 | -0.465 | -0.356 | rejected |
| M15 | `rsi_mean_reversion` | 14/30-70, immediate | -0.170 (n=2063) | -0.132 (n=823) | +0.166 | -0.342 | -0.085 | rejected |
| M15 | `sweep_reclaim` | **3-bar window wins on train** | -0.216 (n=1568) | -0.348 (n=569) | -0.035 | -0.312 | -0.304 | rejected |
| M15 | `pullback_scalp` | **3-bar window wins on train** | -0.143 (n=3074) | -0.188 (n=1298) | -0.020 | -0.084 | -0.179 | rejected |
| D1 | `ma_crossover` | 20/50 SMA, exact cross | +0.196 (n=99) | +0.055 (n=39) | +0.077 | **+0.095** | +0.055 | rejected (placebo) |
| D1 | `rsi_mean_reversion` | 14/30-70, confirm 1 | +0.089 (n=123) | **+0.099** (n=53) | +0.121 | -0.003 | +0.099 | **VALIDATED** |

(all values mean R; max hold 120 bars on H1/M15 and 60 on D1)

Two findings worth stating plainly:

1. **The tolerance wins the training window in three of the six intraday runs that carry the
   dimension** (`pullback_scalp` on both H1 and M15, `sweep_reclaim` on M15), and still fails out of
   sample. That is the honest answer to "does the window help?": it helps the rule *see* more
   setups, and it does not create edge. Making a rule less rigid is not the same as making it good.
2. **`ma_crossover` on D1 is the case the gate exists for.** Its held-out mean is positive
   (+0.055R) and it looks like a discovery until the side-flipped placebo beats it (+0.095R): the
   crossover was not adding directional information, and the gate refused it.

### 5.3 The one validation, and why it is not being promoted

`rsi_mean_reversion` passed every gate on D1 at the default 70/30 split: test **+0.099R** on n=53,
gross +0.121R, placebo -0.003R, and it survives 1.5x costs. Because the repository's own history
says a single split is not evidence (GAP-5 was raised for exactly this on `carry_trend`), the same
run was repeated at other splits:

| Split | Train-best | Test | Verdict |
|---|---|---|---|
| 70/30 (default) | 14/30-70, confirm 1 | +0.099R (n=53) | validated |
| 60/40 | 14/30-70, confirm 1 | +0.030R (n=73) | validated (barely) |
| 50/50 | RSI(2) 5/95, confirm 3 | **-0.034R** (n=222) | rejected |

So the D1 reading is **split-dependent**, and the family stays a research candidate. It is not
written to the strategy registry (only the walk-forward CLI does that) and the live gate is
unaffected.

## 6. What this does and does not establish

- It establishes that the two indicator families are implemented to their published definitions
  (Wilder's smoothing is checked against an independent loop and against the saturation cases;
  the crossover is checked against its own textbook semantics), that they are causal and
  deterministic, and that the confirmation windows are behaviour-preserving at their default.
- It establishes that the tolerance widens recognition, measurably, without being swept so wide
  that the rules fire freely: the windows are bounded (1-3 bars), the band is ATR-scaled, and
  every setting is graded out of sample.
- It does **not** establish a tradeable edge in either new family. One D1 split validates one of
  them and does not replicate; everything else is rejected.
- It does not change the live system. Nothing here is registered, and the watcher's gate still
  sees only the D1 `carry_trend` record.

### Open gaps

| Gap | Why it matters | Status |
|---|---|---|
| The D1 RSI reading is split-dependent | a validated label on one split is not evidence | open (GAP-16) |
| `pullback_scalp`'s local SMA pads its first `slow-1` bars with the first computed value, and its EMA is seeded at `series[0]` | the bias line is fake during warm-up, so the rule can signal before its 50-bar average exists; found while building the invariant test | open (GAP-17) |
| Confirmation windows were swept at only 1 and 3 bars | a finer grid would say more, but each extra cell adds a trial to the multiple-testing count | open (GAP-18) |
| The window is a fixed bar count, not volatility-scaled | a quiet market and a busy one get the same three bars | open (GAP-18) |
