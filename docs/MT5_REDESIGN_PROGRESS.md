# MT5 REDESIGN — PROGRESS

Companion to `docs/MT5_REDESIGN_MASTER_PLAN.md`. Status labels:
`NOT STARTED` · `IN PROGRESS` · `IMPLEMENTED — NOT VERIFIED` · `VERIFIED` · `BLOCKED`

**Current implementation phase:** PHASE 7 — Final audit (session 1 close-out)

---

## Session log

### Session 1 — 2026-10-23
- Full repository audit completed (routes, components, lib data layer, Python watcher, SQLite
  journal, JSON artifacts, live artifacts inspected). → VERIFIED (documented in master plan §2–6).
- Master plan created: `docs/MT5_REDESIGN_MASTER_PLAN.md`.
- Progress file created: this file.

---

## Phase status

| Phase | Status |
|---|---|
| PHASE 0 — Audit & documentation | VERIFIED |
| PHASE 1 — Design architecture (tokens + shell) | VERIFIED (shell renders on all routes; hydration error found + fixed 2026-10-23) |
| PHASE 2 — Command centre (`/`) | VERIFIED (browser: real artifact values on the overview strip) |
| PHASE 3 — Existing workspace reconstruction | IMPLEMENTED — NOT VERIFIED (all 9 routes restructured and rendering; deep panel-internal rewrites partially deferred — see matrix) |
| PHASE 4 — MT5 terminal UI | VERIFIED (browser: all five regions, chart with real candles + markers, decision flow) |
| PHASE 5 — Terminal backend integration | VERIFIED (real MT5 bars + ticks exported; decisions journal write tested) |
| PHASE 6 — QA (build/lint/browser) | VERIFIED (lint 0 problems, tsc clean, `next build` 25/25, pytest 382 passed, responsive 1920/1440/1366/768/390) |
| PHASE 7 — Final audit + acceptance matrix | IMPLEMENTED — NOT VERIFIED (matrix below; deep panel rewrites listed as remaining) |
| POST-COMPLETE FIX — chart zoom + markers | VERIFIED (2026-10-23, user-reported; evidence below) |
| SESSION — live candles wired + volume pane + context MAs + RSI pane | VERIFIED (2026-10-23, user-requested; evidence below) |
| SESSION — live position markers on the chart (clock-corrected) | VERIFIED (2026-10-23, user-requested; evidence below) |
| POST-COMPLETE FIX — line-style chart crash ("data value must be a number") | VERIFIED (2026-10-23, user-reported; evidence below) |
| SESSION — family replay chart on the strategy page + freqtrade press-only runs | VERIFIED (2026-10-23, user-requested; evidence below) |

---

## Task detail

### SESSION — live candles, volume pane, strategy context overlays (2026-10-23)

User reported the chart was not showing live candlesticks. Root cause: `market_export.py`
ran once manually, so `dashboard/market/*.json` was 3 h stale and growing.

- **Live bars wired into the watcher cycle — VERIFIED.** `watch.py::_export_market_artifacts`
  now refreshes every symbol-frame's bars + the tick snapshot on every open-market cycle (30 s),
  on the same read-only connection, failure journaled as `market_export_error` without touching
  the cycle (tests in `tests/test_export_market_artifacts.py`; suite 384 passed). The standalone
  `python -m mt5_intel.market_export --loop --seconds 30` is also running now, so the terminal
  is live immediately; the cycle keeps it live whether or not that process stays up.
- **Volume pane — VERIFIED.** A histogram series docked to the bottom band (scale margin 0.82)
  renders the export's real `tick_volume`, coloured by bar direction; bars without volume show
  nothing (never a fabricated tick count). Fed with the same setData/append discipline as price.
- **Strategy context overlays — VERIFIED.** The four lines the strategies themselves read
  (`research/setups.py` Context: EMA 50/200 trend, SMA 20/50 crossover) are drawn on the price
  pane (violet/amber/green/teal), computed client-side from the exported closes with the
  research code's exact `_sma/_ema` seeding. Cross-checked against `setups.json` context values
  at the same bar (Δ ≈ 1 bar's context shift: the watcher reads at the last *closed* bar; the
  chart line extends through the forming bar, i.e. the same convention as MT5's iMA).
- **RSI sub-pane — VERIFIED (user-approved).** Wilder's RSI-14 in its own short pane under the
  volume band (`addSeries(..., paneIndex=1)`, stretch factor 0.28), with dashed 30/70 level
  lines. Computed client-side with `research/strategies.py::_wilder_rsi`'s exact seeding and
  summarizing; cross-check with the watcher context at the same close: 38.39551 client vs
  38.396 watcher (formula exact; the visible line extends through the forming bar).
- Verification: 500 real bars ending at the current forming H1 bar (13:00 broker clock),
  artifact age 3–6 s per poll; zoom persistence re-verified across two full cycles with all
  series live (`unchanged: true`); no console errors; tsc + eslint clean.

### SESSION — live position markers on the chart (2026-10-23)

User took live manual trades and asked for them on the chart.

- **Found:** the journal already carries open positions and the terminal already polls them
  (amber `#ticket` arrows) — but (a) the bars artifacts sat stale (the running watcher process
  predates the export-wiring edit, so it writes no bars; the standalone export loop died with
  its shell), and (b) two real clock bugs made the arrows misleading where they did draw:
  position times are UTC while bars are broker-clocked, and the marker snap chose the nearest
  bar open, which flips a trade to the NEXT bar past :30 past the hour.
- **Stale bars — VERIFIED fixed.** `python -m mt5_intel.market_export --loop --seconds 30` is
  now running as a managed background process (PID 5432); artifacts fresh within ~30 s. Note:
  the watcher's own cycle wiring activates the next time the watcher process restarts; until
  then the exporter covers it.
- **Clock correction — VERIFIED.** The terminal now converts UTC trade times into the bar
  clock using the watcher's own per-symbol `offset_s` measurement (newly surfaced through
  `lib/live.ts`), falling back to a guess read off the export (last row is the forming bar)
  and honestly using zero when no source is trustworthy.
- **Snap — VERIFIED fixed.** The marker layer now prefers the bar CONTAINING a time over the
  nearest bar open, so a trade opened mid-hour draws on that hour's candle (verified: all
  EURUSD positions on their true bars, e.g. the 10:31 UTC trade on the broker 13:00 bar).
- Verified on EURUSD in the browser: four amber position arrows on their true candles,
  alongside signal markers, context MAs, RSI and volume; tsc + eslint clean.

### SESSION — family replay chart + freqtrade press-only runs (2026-10-23)

User asked (a) whether the freqtrade engine runs on latest or old data, (b) whether the
terminal's fresh chart data could drive a per-family page showing how a strategy played
out — green/red translucent boxes from entry to TP/SL — with recent data captured for
later improvement runs.

- **Data freshness (answered, no change needed):** freqtrade re-exports bars from MT5
  whenever `user_data/mt5_ohlcv/*.csv` are older than 6 h (checked before every run), so a
  press-only run always uses bars ≤6 h old, up to the last *closed* H1 bar. MT5 itself
  already holds ~830 days (the export pulls 20,000 bars), so no local capture loop is
  needed — the terminal is the store; the terminal chart's 500-bar window is a display
  choice, not a data limit.
- **Replay chart — VERIFIED.** New `tools/family_replay_export.py` replays ONE family over
  2,000 H1 bars (~3 months) and writes `dashboard/family-replay-<family>.json`: bars on
  UTC + one graded row per taken trade (entry/SL/TP/exit prices, hit, net R). Every number
  comes from the research core's own pinned trio — `one_at_a_time` (which signals became
  trades, D-035), `walk_trade` (where each ended, SL-first D-012), `closed_r` (net R with
  costs) — so a box on this chart is the same trade the Live tab and research tables show.
  7 new tests pin exporter↔core agreement (`tests/test_family_replay_export.py`); suite
  **395 passed**.
- **UI — VERIFIED (browser, live dev server).** `/strategies/<family>` now opens with
  "Replay on real bars": summary strip (n/TP/SL/time-exit/mean+total R) + chart. Boxes are
  drawn by a lightweight-charts primitive on the chart's own canvas behind the candles
  (`zOrder: 'bottom'`) — green entry→target, red entry→stop, amber time-exit or still-open
  — plus entry arrows labelled with each trade's R. Cached artifact, auto-refreshed when
  >6 h old, manual "re-run replay" button; same-origin + family-whitelist enforced on the
  POST (`/api/replay`). Pixel probe on trend_pullback: 2,522 green + 1,447 red translucent
  px, 0 amber (that family had no time exits); summary matched the exporter stdout exactly
  (63 trades, 25 TP / 38 SL, +0.88R). Screenshot `screenshots/replay-chart-boxes.png`.
- **Client/server split (found via Turbopack panic).** `lib/replay.ts` reads `node:fs` and
  cannot be bundled into a client chunk — the constants/types a "use client" panel needs
  moved to `lib/replay-constants.ts` (no fs import), re-exported from `lib/replay.ts` for
  server readers. Same for the panel's stale-guard: a mount-once ref breaks under
  StrictMode/Fast Refresh (cancelled first fetch + surviving ref = stuck empty state);
  the effect now relies on its `cancelled` flag alone.
- **Freqtrade press-only — VERIFIED.** All 8 families shared ONE params file
  (`user_data/strategies/MT5Family.json`) because they live in one module, and freqtrade's
  loader hard-fails unless its `strategy_name` matches the class being run — so after the
  sweep left the champion's params there, every other family's Run button died with
  "Invalid parameter file provided". New `stage_params()` in
  `tools/freqtrade_run_backtest.py` archives the current file under its owning family (as
  the sweep already did), then copies the requested family's archived params in place — or
  leaves the file absent, which is freqtrade's own recorded-defaults path. Verified all
  three paths live via the API: rotation (trend_pullback), archived restore (carry_trend,
  26 trades, +1.45R expectancy), defaults fallback (compression_breakout, no archive).
  The sweep now imports the runner's `PARAMS_FILE`/`ARCHIVE` so paths cannot drift.

### POST-COMPLETE FIX — line-style chart crash (2026-10-23)

User reported a runtime error the moment the terminal chart rendered in line style:
`Assertion failed: Line series item data value must be a number, got=undefined`.

- **Root cause — VERIFIED.** With `chartStyle: "line"` the chart builds a lightweight-charts
  `Line` series (which plots `{time, value}`), but `toBar()` in
  `dashboard/components/terminal/chart.tsx` unconditionally produced an OHLC row
  (`{time, open, high, low, close}`) for both the `setData()` reset and the `update()`
  append — so every point handed to the line series had `value: undefined` and the library
  asserted. Candle style was unaffected, which is why it only surfaced when switching styles
  (the style is persisted in `localStorage`, so it also crashed on every reload once chosen).
- **Fix — VERIFIED.** `toBar()` is now style-aware: `{time, value: close}` for line,
  the full OHLC quartet for candles. The volume histogram, MA overlays, RSI pane, markers and
  price lines were already style-agnostic and needed no change.
- Verification (browser, user's live dev server): candles → line → candles, both directions
  with zero console errors; the line series holds 500 real `{value, time}` points ending at
  the current forming close (156.85, matching the OHLC legend); the candle series still holds
  500 OHLC rows after switching back. `tsc --noEmit` clean.

### SESSION — auto-trade entries refused with retcode 10021 "No prices" (2026-10-23)

User asked why the last entries never reached the market and whether it could be fixed.

- **Root cause (from the journal, ids 11–12):** at 04:21 UTC on Oct 22, two USDJPY cards
  (compression_breakout sell, range_extreme_mr buy) passed every gate and sized fine, then the
  broker refused both orders with retcode 10021 "No prices" — a momentary feed gap at the
  server. A reservation is keyed on (strategy, signal bar, side) and a rejected reservation is
  spent forever, so both signals were traded nothing, while the same symbol filled normally at
  06:05 and 11:03 that day. The bars come from history and stay fresh while the tick stream is
  down, so `bar_age_s` alone could not see it.
- **Layer 1 — stale-price refusal before the reservation (VERIFIED).** `_fresh()` now reads the
  terminal's own price timestamp (`tick.time`, compared against the cycle clock shifted by the
  broker offset) and refuses an entry whose last price is older than `TICK_MAX_AGE_S` (60 s).
  This is a skip, not a rejection: nothing is reserved, so the signal survives and the next
  cycle trades it when the feed returns.
- **Layer 2 — bounded 10021 retry (VERIFIED).** A 10021 that still reaches the broker executed
  nothing, so `MT5Trader.placed()` re-reads the tick and re-sends the same request at the fresh
  price — the same intent again, not a second order (the discipline the 10030 filling-mode walk
  already follows). Bounded to `NO_PRICE_RETRIES` (2) with a delay; only 10021 retries: an
  Invalid stops (10016) would answer identically forever, and a raised error is an uncertain
  outcome where a resend could double a position.
- **Tests — 388 passed** (4 new: stale feed refuses without spending the signal; 10021 retried
  with a freshly read price and one reservation covering both requests; persistent 10021 is
  bounded and still settles rejected; every other refusal is asked exactly once).
- Also restarted the standalone market exporter (its earlier process died with its shell):
  dashboard bar artifacts fresh within ~30 s again.


---

## Task detail

### POST-COMPLETE FIX — terminal chart zoom persistence + marker rendering (2026-10-23)

User reported: (a) zoom resets to the fitted view a few seconds after zooming; (b) signal
markers (arrows) vanish at close zoom and are incomplete.

Both fixed in `dashboard/components/terminal/chart.tsx`:

- **Zoom reset — VERIFIED.** The bars effect ran `timeScale().fitContent()` on every 20 s poll
  (any new `bars` array identity). Now a full `setData` + `fitContent()` runs only when the data
  window genuinely changes (market/frame switch, series rebuild, rewritten export); a window
  that merely extends is appended with chronological `series.update()` calls — zoom untouched,
  right edge stays pinned at realtime, new data adds gracefully.
- **Markers vanishing — VERIFIED root cause two-part.** (1) `createSeriesMarkers()` was called
  on every refresh, stacking marker layers; the layer is now created once per series and
  refreshed with its `setMarkers()` (replace semantics). (2) The layer binary-searches its
  marker list, so the list must be ascending by time — the watcher's setups arrive newest-first,
  which is exactly why arrows were complete at wide zoom but missing at close zoom. Markers now
  sorted ascending. (3) Position markers used raw `openedUtc` seconds, which fall between bar
  opens and are silently dropped by the library; marker times are now snapped to the nearest
  exported bar open (dropped only if more than half a bar away from any bar).
- Verification (Playwright on the dev server): zoomed to the last 12 H1 bars, waited two full
  poll cycles — visible range unchanged both times (`1792710000 → 1792749600`); pixel probe of
  the canvas at a signal bar's arrow zone found the marker rendered where it was absent before
  the sort; screenshot `screenshots/terminal-zoomed-markers-fixed.png` shows all three arrows
  with labels at close zoom. tsc clean, lint 0 problems.
- Note: dev-only debug handles (`__chartApi`, `__series`, `__markers` on the chart host) were
  added for these checks; they are guarded by `NODE_ENV !== "production"` and absent from
  production builds.


### PHASE 0 — Audit & documentation
- Repository audit (routes, components, libs, Python, data artifacts) — **VERIFIED**
- `docs/MT5_REDESIGN_MASTER_PLAN.md` created (20 sections) — **VERIFIED**
- `docs/MT5_REDESIGN_PROGRESS.md` created — **VERIFIED**
- Baseline screenshots of old UI — **BLOCKED** (captured after changes were already underway;
  old layout documented textually in master plan §7 instead)

### PHASE 1 — Design architecture
- Design tokens + typography + glass system in `dashboard/app/globals.css` — VERIFIED
- `components/shell/nav-rail.tsx` (icon rail, expandable, accessible) — VERIFIED (browser)
- `components/shell/topbar.tsx` (workspace title, account chip, demo badge, connection +
  freshness, clock) — VERIFIED (browser; hydration mismatch in equity formatting found
  and fixed with a deterministic formatter)
- `components/shell/context-nav.tsx` (per-workspace secondary nav + WorkspaceFrame) — VERIFIED
- `app/layout.tsx` shell recomposition — VERIFIED (all routes 200)
- Legacy CSS class contract preserved for existing panels — VERIFIED (existing panels render)
- Dependencies installed: `geist`, `lucide-react`, `lightweight-charts` + `@types/node@^24`
  (for `node:sqlite`) — VERIFIED

### PHASE 2 — Command centre (`/`)
- Four-zone asymmetric command grid (overview strip, forecast leaderboard, risk/research
  analytical column, activity band) — VERIFIED (browser: equity 1 044 516,06 ZAR, open P/L,
  win rate 63%, daily loss 0.44%/3% all from real artifacts)
- `?symbol=` filter preserved — VERIFIED

### PHASE 3 — Existing workspaces
- `/live` three-column decision workspace — NOT STARTED
- `/trades` master–detail journal — NOT STARTED
- `/research` analytical three-column layout — NOT STARTED
- `/replays` + `/replays/[id]` replay workspace — NOT STARTED
- `/runs` + `/runs/[id]` diagnostics timeline — NOT STARTED
- `/health` system health command layout — NOT STARTED
- `/strategies/[family]` strategy dossier — NOT STARTED

### PHASE 4 — MT5 terminal UI (`/terminal`)
- Terminal shell + toolbar (symbol/timeframe/style/collapse/fullscreen/reset) — IMPLEMENTED — NOT VERIFIED
- Market Watch panel (real bid/ask/spread, search, favourites, truthful unavailable rows) — IMPLEMENTED — NOT VERIFIED
- Chart panel (lightweight-charts candles, OHLC legend, real signal + position markers,
  entry/SL/TP price lines, fullscreen) — IMPLEMENTED — NOT VERIFIED
- Intelligence panel (active signal, prices, RR, validation, gate warnings, manual decision
  controls) — IMPLEMENTED — NOT VERIFIED
- Bottom activity dock (open positions/history/decisions/system) — IMPLEMENTED — NOT VERIFIED
- Workspace preferences (collapse/reset/shortcuts 1/2/3/f/b/Esc) — IMPLEMENTED — NOT VERIFIED
- Browser verification: chart renders 500 real USDJPY H1 bars; M30 switch updates OHLC;
  XAUUSD shows unavailable; Approve writes journal row #17998 (accepted) with no order — VERIFIED

### PHASE 5 — Terminal backend integration
- `src/mt5_intel/market_export.py` (bars + tick snapshot export) — VERIFIED (ran against the
  live MT5 terminal: 7 artifacts, real USDJPY bid/ask 156.972/156.994)
- `dashboard/lib/market.ts` — VERIFIED
- `dashboard/app/api/market/route.ts` — VERIFIED (browser network: 200s)
- `dashboard/lib/decisions.ts` + `app/api/decisions/route.ts` (existing SQLite `decisions`
  journal via `node:sqlite`; approve/reject never orders) — VERIFIED (journal row written,
  dock lists it across recommendations)
- Staleness / unavailable states wired through the terminal — VERIFIED (bars age chip;
  XAUUSD unavailable row; chart empty state with export instructions)

### PHASE 6 — QA
- `npm run lint` / `tsc --noEmit` / `npm run build` — NOT STARTED
- Python `pytest` — NOT STARTED (no Python changes yet)
- Browser verification 1920 / 1440 / 1366 / 768 / 390 — NOT STARTED

---

## Files created (this project)
- `docs/MT5_REDESIGN_MASTER_PLAN.md`
- `docs/MT5_REDESIGN_PROGRESS.md`
- `dashboard/components/shell/nav-rail.tsx`
- `dashboard/components/shell/topbar.tsx`
- `dashboard/components/shell/context-nav.tsx`
- `dashboard/components/terminal/chart.tsx`
- `dashboard/components/terminal/terminal.tsx`
- `dashboard/app/terminal/page.tsx`
- `dashboard/lib/market.ts`
- `dashboard/lib/decisions.ts`
- `dashboard/app/api/market/route.ts`
- `dashboard/app/api/decisions/route.ts`
- `src/mt5_intel/market_export.py`
- `dashboard/market/*.json` (real exported bars + ticks, generated by market_export)

## Files significantly modified (this project)
- `dashboard/app/globals.css` (full design-system rewrite: dark emerald/teal tokens, glass,
  shell, terminal, command-centre classes; legacy class contract preserved)
- `dashboard/app/layout.tsx` (new shell: rail + topbar + workspace)
- `dashboard/app/page.tsx` (command centre rebuilt)
- `dashboard/package.json` (+geist, lucide-react, lightweight-charts, @types/node@^24)

## Components replaced
- (none yet)

## Pages completed
- (none yet)

## Integration work completed
- (none yet)

## Tests executed and results
- `npx tsc --noEmit` (dashboard): PASS (repeatedly, after each phase)
- Browser (Playwright preview): shell + command centre + terminal verified; Approve flow
  verified end-to-end; hydration mismatch found and fixed; no remaining console issues
- Python tests: NOT RUN yet (market_export.py is new; test file pending)

## Known problems
- Old-UI baseline screenshots not captured (limitation recorded in master plan §19).
- `market_export.py` must run (once or `--loop`) for live bars/ticks; terminal shows truthful
  stale/unavailable states otherwise. Not yet wired into the watcher loop (deliberate: additive
  and read-only first).

## Decisions made
1. Preserve `dashboard/lib/*` and all existing API contracts; redesign happens at page/shell
   component level plus additive `lib/market.ts` (master plan §13).
2. Charting library: TradingView `lightweight-charts` (not currently installed; mandate
   endorses it). Fonts: `geist` package (bundled, no build-time network dependency).
   Icons: `lucide-react` single set.
3. Market data truthfulness: add `market_export.py` (read-only, additive) rather than
   synthesising candles; until it runs, chart shows explicit empty state.
4. Legacy CSS class names are kept working (restyle, not delete) so untouched panels inherit
   the new design system while their pages are restructured around them.

## Final acceptance matrix (mandate §12.2)

| Route / feature | Required outcome | Status | Structural redesign | Functionality | Responsive | Outstanding problems |
|---|---|---|---|---|---|---|
| App shell (all routes) | Rail + topbar + contextual nav, workstation feel | REBUILT | YES | YES | YES (rail→bottom bar <900px) | — |
| `/` command centre | Asymmetric 4-zone operational overview | REBUILT | YES | YES (real artifact values verified) | YES (390, 1920) | intermediate widths spot-checked only |
| `/live` | 3-column decision workspace | RESTRUCTURED | YES (context column + decision surface + forecast rail + auto drawer) | YES (polling, forecast, auto panel preserved) | YES (1920) | SetupsPanel/ForecastPanel internals preserved (presentation re-skin only) |
| `/trades` | Journal workspace with summary rail | RESTRUCTURED | PARTIAL (summary rail + control bar + context nav added; TradesPanel internals preserved) | YES (labelling, cap stepper preserved) | YES (1920) | true master–detail trade inspector NOT built — listed as remaining work |
| `/research` | 3-column analytical layout | RESTRUCTURED | PARTIAL (comparison band + sweep/reference columns + ruleboard lifted out of fold) | YES | not individually checked | Iterations table internals preserved |
| `/replays` (+[id]) | Replay workspace | RESTRUCTURED (workspace + context nav + regrouped) | PARTIAL | YES | not individually checked | replay tables' internals preserved |
| `/runs` (+[id]) | Diagnostics timeline/detail | RESTRUCTURED (workspace + context nav) | PARTIAL | YES | not individually checked | check-group side nav not built |
| `/health` | System health command layout | RESTRUCTURED (workspace + context nav) | PARTIAL | YES (auto-refresh preserved) | not individually checked | internals preserved |
| `/strategies/[family]` | Strategy dossier | RESTRUCTURED (workspace + context nav) | PARTIAL | YES (settings form preserved) | not individually checked | — |
| `/terminal` | Full MT5 workspace (toolbar/watch/chart/intel/dock) | REBUILT NEW | YES | YES (real bars, ticks, signals, positions, decisions) | YES (1920/1440/1366/768/390) | bars refresh needs `market_export.py` running |
| Approve/Reject workflow | Journal decision, never an order | IMPLEMENTED + VERIFIED | n/a | YES (row #17998 written, no order placed) | n/a | — |
| Existing APIs | Contracts unchanged | PRESERVED | n/a | YES (all 200s) | n/a | +2 additive endpoints (`/api/market`, `/api/decisions`) |
| Auto-trading controls | Arming semantics unchanged | PRESERVED (AutoPanel untouched) | NO (fold repositioned only) | PRESERVED | n/a | panel still legacy presentation |
| Risk rules (1%/3%/2 pos/manual-only) | Backend authority intact | PRESERVED (Python untouched) | n/a | YES (displayed from backend) | n/a | — |

## Remaining work (explicit)
1. Deep panel-internal rewrites: TradesPanel master–detail inspector, SetupsPanel decision
   surface internals, AutoPanel modernisation, Iterations/ReportView/Health tables.
2. Wire `market_export.py --loop` into the watcher loop (or Start MT5 Intel.bat) so bars/ticks
   stay fresh without a manual run.
3. Per-width responsive checks for `/research`, `/runs`, `/replays`, `/health`.
4. Delete the now-unused `components/nav.tsx` (replaced by the shell rail).
5. News state (`live-status.json.news`) not yet surfaced in the terminal (interface gap).

## Known problems
- Old-UI baseline screenshots not captured (limitation recorded in master plan §19).
- `market_export.py` must run (once or `--loop`) for live bars/ticks; terminal shows truthful
  stale/unavailable states otherwise. Not yet wired into the watcher loop (deliberate: additive
  and read-only first).

---

# Session 2026-10-23 — the freqtrade engine now measures the live stop management

## The finding this session was built on

The freqtrade engine was grading a system that does not trade. The live watcher moves a stop up
after entry (`execution._manage_auto_stops`: lock `AUTO_LOCK_R`=0.1R past entry once a trade is
`AUTO_BREAKEVEN_R`=1R in profit, then trail `AUTO_TRAIL_DISTANCE_R`=1R behind price from
`AUTO_TRAIL_R`=2R). The backtester had none of it — `trailing_stop = False` and a
`custom_stoploss` that pinned the family's stop at its entry distance forever. So its losses
were not the losses the account would take.

## What changed

- `src/mt5_intel/config.py` — new `StopManage` + `load_stop_manage()`, and `load_settings` now
  builds its five stop-management fields from it. One reader, so the watcher and the backtest
  cannot be pointed at different numbers.
- `user_data/strategies/MT5Family.py` — `custom_stoploss` now applies the watcher's two rules on
  top of the family's own stop: same thresholds, same ratchet, same refusal to place a stop at or
  past the target. Reads `MT5INTEL_*` env vars (defaults = `config.py`'s own).
- `tools/freqtrade_run_backtest.py` — new `manage_env()` exports the settings into the freqtrade
  venv; `backtest()` uses it. The strategy cannot import `mt5_intel.config` (different venv, and
  `load_settings` demands broker credentials it has no use for).
- `tools/freqtrade_sweep.py` — hyperopt runs under the same management, so the search optimises
  the system that actually trades instead of one with fixed barriers.
- `tests/test_auto_manage.py` — 3 new tests: `manage_env` follows the .env; the strategy's source
  reads exactly the variables the runner writes (a typo on either side would silently disable
  management, and that would read as an edge that does not exist); `load_settings` takes its five
  fields from `load_stop_manage`.

## The measurement (USDJPY H1, `20250101-`, trend_pullback)

| | fixed barriers (before) | live management (now) |
|---|---|---|
| trades | 94 | 99 |
| exits: target / stop / managed | 38 / 43 / 12 | 24 / 35 / 39 |
| full −1R stop-outs | 43 | **35** |
| winners | 39, mean **+1.231R** | 43, mean **+0.811R** |
| expectancy | −0.088R | **−0.149R** |

**The lock does prevent losses — and on this family it costs more than it saves.** It removed 8
full stop-outs (≈ +8R) but turned 14 would-be +1.3R target hits into +0.1R scratches and 7 into
break-even (visible as the 0.1 and 0.0 spikes in the R histogram), which is ≈ −16R. Net: worse.

That is not a bug in the integration; it is the measurement working, and it is exactly the thing
that could not be tested before. The honest reading: a breakeven lock and a 1.3R target fight
each other, because the lock fires at +1R — close to the target — so it takes winners off the
table just before they would have paid. Whether the lock earns its place is a per-family question
with a number attached now, not an assumption.

## Research page cleanup (same session)

- Removed the "Parameter sweeps · train then held-out test" card and its six now-unused
  components. Those tables graded a different harness with a different cost model; the freqtrade
  sweep replaced it, and its per-family params are what the run buttons apply.
- `Reference · the rules, family by family` rebuilt: was a 3-column grid of cards each showing
  all six fields at once (unreadable when looking for one rule). Now one line per rule — name,
  id, what it waits for — grouped into Core families / Scalp templates / Not a rule on its own,
  expandable, closed by default. Groups derive from the catalogue, so a rule added later cannot
  go missing.
- Freqtrade panel: a family's name now links to `/strategies/[family]` — its replay chart,
  settings and rule identity. The table is the index of the dossiers rather than needing one.

`tsc` and `eslint` clean; 398 Python tests pass (3 new); the run button and the panel verified in
the browser on `http://localhost:52577`.

## Session continued — the replay is no longer one market

The replay chart was USDJPY-only: the exporter hardcoded `settings.symbol_root`, so a strategy
page answered "how did this rule actually play out" for exactly one market with no way to ask
about another. It now serves USDJPY and EURUSD, filtered the way the journal is.

- `tools/family_replay_export.py` — `--symbol` (choices `USDJPY` / `EURUSD`, default the
  watcher's primary). The clock offset is resolved **per market**, not inherited, so two
  markets on two servers each get their own correction.
- Artifacts are now `family-replay-<family>-<symbol>.json`. One file per family would mean the
  last replay to run silently overwrote the other market's answer, and the strategy page would
  draw EURUSD trades under a USDJPY chart with no error anywhere. Pinned by a test.
- `dashboard/lib/replay-constants.ts` — `REPLAY_SYMBOLS` / `isReplaySymbol`, the whitelist the
  exporter and both API branches enforce (the symbol reaches a command line, so it is checked,
  not passed through).
- `dashboard/lib/replay.ts`, `app/api/replay/route.ts` — symbol threaded through the reader,
  the GET query and the POST body.
- `dashboard/app/strategies/[family]/page.tsx` — the journal's own `SymbolFilter`, URL-driven
  and bookmarkable: `?symbol=EURUSD` is actually about EURUSD.
- `dashboard/components/replay-panel.tsx` — takes the market as a prop, clears the previous
  market's chart on switch rather than leaving it on screen, and names the market in its aside.
- Fixed: the freqtrade panel's "last artifact" header only read its timestamp on mount, so it
  still showed the old time immediately after a successful run. It now takes the stamp from the
  run that just landed.

### Verified

| | USDJPY | EURUSD |
|---|---|---|
| bars | 1,999 H1 | 1,999 H1 |
| price range | 152.872 – 163.973 (digits 3) | 1.11122 – 1.17106 (digits 5) |
| trades | 63 (25 TP / 38 SL) | 42 (19 TP / 22 SL, 1 open) |
| mean / total | +0.014R / +0.880R | **+0.162R / +6.650R** |

Both price ranges match MT5's own `symbol_info` (EURUSD bid 1.1194, USDJPY 156.864), so the
bars are the terminal's and the digits are the market's. In the browser: the filter marks the
active tab, switching re-reads the other market's artifact and redraws, and a pixel probe found
2,963 green + 2,482 red translucent box pixels on the EURUSD chart.

**The rule is not the same on both markets** — trend_pullback is +6.65R on EURUSD and +0.88R on
USDJPY over the same window. That is the whole reason the filter was worth adding: a one-market
chart made that invisible.

### Freqtrade run from the UI (before / after)

`screenshots/freqtrade-ui-before.png` and `freqtrade-ui-after.png`. Trend pullback was run from
the Research page's button and reproduced the CLI result exactly: 99 trades, 45% win,
−0.149R expectancy, −0.58% net, params `fast=50 · k_atr=2.9 · slow_gap=160 · stop_scale=1.4 ·
tp_r=1.3`, artifact 18:05 → 18:31. The numbers are identical by design — the UI path and the
CLI path are the same runner — so the visible difference is the row highlight and the header
timestamp now advancing with the run. That is the expected result, not a failed demonstration.

`screenshots/replay-eurusd.png` — the EURUSD replay chart with its filter tab active.

400 Python tests pass (2 new); `tsc` and `eslint` clean.
