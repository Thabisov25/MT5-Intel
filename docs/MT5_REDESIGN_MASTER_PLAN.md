# MT5 REDESIGN — MASTER PLAN

Persistent, authoritative plan for the complete frontend reconstruction of the MT5 intel
application and the new MT5 web terminal. **This document is the source of truth.** Read it
before every development session, after every context reset, and before announcing completion.
Progress is tracked separately in `docs/MT5_REDESIGN_PROGRESS.md`.

Status legend: `NOT STARTED` · `IN PROGRESS` · `IMPLEMENTED — NOT VERIFIED` · `VERIFIED` · `BLOCKED`

---

## 1. Project objectives and non-negotiable requirements

1. **Complete frontend reconstruction.** The existing Next.js dashboard is the source of
   business logic, data connections and user requirements — **not** the design template. The
   final product must read as a newly designed premium trading workstation (deep black,
   emerald/teal accents, frosted-glass surfaces, rounded geometry, compact professional data
   presentation).
2. **Structural, not cosmetic, change.** Recolouring, re-rounding, re-shadowing or re-fonting the
   existing cards is explicitly NOT redesign work. Page composition, information hierarchy,
   navigation, component arrangement and interaction patterns must change.
3. **New MT5 web terminal** (`/terminal`): multi-panel workspace (Market Watch, chart,
   intelligence panel, activity dock) driven by the real backend artifacts. No fabricated
   prices, candles, signals, balances or confidence values. Read-only trading surfaces.
4. **Functional preservation.** Every existing route, API, workflow (manual Approve/Reject of
   signals, journal labelling, auto-trading arming switches, caps, research/replay/report
   browsing) continues to work. Risk rules stay authoritative in the Python backend:
   USD/JPY primary, 1% risk/trade, 3% daily loss limit, max 2 open positions, news avoidance,
   manual execution only.
5. **Persistent planning.** This file plus `MT5_REDESIGN_PROGRESS.md` must allow any agent to
   resume the work without conversation context.
6. **No fake functionality.** Non-functional controls must not be presented as working. Missing
   backend support ⇒ truthful unavailable state + documented limitation.

---

## 2. Existing application architecture (audit, 2026-10-23)

### 2.1 Runtime topology

```
MetaTrader 5 terminal (Windows, HFM demo, ZAR account 49869754)
   ▲ MetaTrader5 python package (read-only allow-list: src/mt5_intel/mt5_adapter.py)
Python watcher (src/mt5_intel/watch.py, loop; gate.py, execution.py, trades.py, news.py…)
   │ writes atomic JSON artifacts → dashboard/           (read by the Next.js server)
   │ writes SQLite journal       → data/mt5.db           (events, recommendations, decisions,
   │                                                      strategies, equity_days, gate_rollup)
   │ reads control files         ← dashboard/            (auto-switch.json, auto-strategies.json,
   │                                                      auto-caps.json — dashboard writes them)
Next.js 16.4 dashboard (dashboard/, React 19, TS, ESLint 9; NO UI kit, NO chart lib, NO icon lib)
   lib/* = server-side data layer reading the JSON artifacts + SQLite-derived reports
   app/api/* = JSON endpoints for client polling (live, setups, trades, auto, forecast…)
```

Key constraint: the Next.js frontend can NOT talk to the MT5 executable directly. All broker
data reaches the frontend through the watcher's artifacts. Anything the artifacts do not carry
must be added on the Python side (an additive export), never simulated in the frontend.

### 2.2 Stack facts

- Next.js **16.4.0** App Router, React **19.3**, TypeScript 5, `eslint-config-next` 16.4.
- Styling: one hand-written stylesheet `dashboard/app/globals.css` (~1650 lines), semantic class
  names (`.panel`, `.pill`, `.facts`, `.rank`, `.table-wrap`, `.num`, `.muted`, …). No Tailwind.
- Scripts: `npm run dev | build | start | lint`.
- Python: `pyproject.toml`, package `mt5_intel` under `src/`, tests under `tests/`
  (pytest), tooling under `tools/`.
- JSON artifacts at `dashboard/` root: `live-status.json`, `trades.json`, `setups.json`,
  `setups_<SYMBOL>.json`, `setups_<SYMBOL>_<TF>.json` (H1/M30/M15), `auto.json`,
  `auto-switch.json`, `auto-strategies.json`, `auto-caps.json`, `gate-history.json`,
  `freqtrade-results.json`, `setups_EURUSD*.json` etc.
- `data/trade-labels.json` holds journal labels (strategy + note per ticket).

---

## 3. Complete route and page inventory

| Route | File | Purpose | Current layout | Status |
|---|---|---|---|---|
| `/` | `app/page.tsx` | "Today's brief": forecast ranking, account facts, research links | 3 stacked `.panel` sections | NOT STARTED |
| `/live` | `app/live/page.tsx` | Live watcher: symbol tabs, timeframe tabs, ForecastPanel + SetupsPanel + auto-trading `<details>` | filter strips + stacked panels | NOT STARTED |
| `/trades` | `app/trades/page.tsx` | Journal: open/closed positions, per-trade strategy labelling, per-symbol cap stepper | symbol tabs + SymbolCapControl + TradesPanel | NOT STARTED |
| `/runs` | `app/runs/page.tsx` | All diagnostic runs, newest first (max 50) | single table | NOT STARTED |
| `/runs/[id]` | `app/runs/[id]/page.tsx` | One diagnostic run: checks by status (ReportView/StatusPill) | report view | NOT STARTED |
| `/replays` | `app/replays/page.tsx` | Replay runs list | list/table | NOT STARTED |
| `/replays/[id]` | `app/replays/[id]/page.tsx` | Replay detail: families table, cards, graded entries | ReplayFamiliesTable + ReplayRunBlock + ReplayCardsTable | NOT STARTED |
| `/research` | `app/research/page.tsx` | Strategy iteration sweeps (train/test), freqtrade results, strategy ruleboard | PageHead + FreqtradePanel + Iterations table + `<details>` ruleboard | NOT STARTED |
| `/health` | `app/health/page.tsx` | Watcher/gate health: processes, logs, per-symbol feeds, 90-day clustering | Tabs + tables + ClusteringPanel | NOT STARTED |
| `/strategies/[family]` | `app/strategies/[family]/page.tsx` | Strategy detail + StrategySettingsForm | detail page | NOT STARTED |
| `/terminal` | **NEW** | MT5-style multi-panel trading terminal | NEW | NOT STARTED |

### 3.1 API inventory (must keep working, unchanged contracts)

| Endpoint | File | Role |
|---|---|---|
| `GET /api/live` | `app/api/live/route.ts` | live-status payload for LivePoller |
| `GET /api/setups` | `app/api/setups/route.ts` | setups artifact for symbol/timeframe |
| `GET /api/symbols` | `app/api/symbols/route.ts` | watched + traded symbol lists |
| `GET /api/trades` | `app/api/trades/route.ts` | journal payload |
| `POST /api/trades/label` | `app/api/trades/label/route.ts` | write strategy label/note for a ticket |
| `GET/POST /api/auto` | `app/api/auto/route.ts` | auto-trading status + switch/selection/caps writes |
| `GET /api/forecast` | `app/api/forecast/route.ts` | forecast ranking payload |
| `GET /api/reports/latest` | `app/api/reports/latest/route.ts` | newest diagnostic report |
| `GET /api/freqtrade` | `app/api/freqtrade/route.ts` | freqtrade results payload |
| **NEW** `GET /api/market` | `app/api/market/route.ts` | real OHLC bars + tick snapshot for the terminal chart/Market Watch |

---

## 4. Existing component inventory

Data/panels (all keep their business logic; presentation reworked per §11):
`auto-panel.tsx` (790 ln — auto-trading arm/switch/selection/caps/decisions),
`setups-panel.tsx` (620 ln — per-strategy cards, Approve/Reject-free read view + confluence),
`trades-panel.tsx` (522 ln — journal tables, labelling), `report-view.tsx` (StatusPill,
ReportView), `replay-view.tsx` (replay tables), `clustering-panel.tsx` (gate history clusters),
`forecast-panel.tsx` (live forecast), `freqtrade-panel.tsx`, `symbol-cap.tsx` (cap stepper),
`strategy-settings-form.tsx`, `strategy-guide.tsx` (TradingManual/ReplayManual/StrategyCell/
StrategyRuleboard), `live-feed.tsx` + `live-poller.tsx` + `poll.tsx` (client polling fabric),
`symbol-filter.tsx`, `timeframe-filter.tsx`, `tabs.tsx`, `info.tsx` (Info/PageHead/PanelHead),
`nav.tsx` (old header nav — replaced by the new shell), `health-refresh.tsx`,
`latest-run-watcher.tsx`.

Data layer `dashboard/lib/` (DO NOT rewrite; extend only additively): `auto.ts`, `clustering.ts`,
`combos.ts`, `confluence.ts`, `forecast.ts`, `freqtrade.ts`, `gate-history.ts`, `health.ts`,
`live.ts`, `replays.ts`, `reports.ts`, `research.ts`, `setups.ts`, `strategies.ts`, `symbols.ts`,
`trades.ts` (+ new `market.ts`).

---

## 5. Existing features and functionality inventory (must survive)

1. Today's brief (forecast tiers, account facts, research links).
2. Live watcher view: per-symbol and per-timeframe artifact browsing, live polling every 10 s,
   forecast ranking, per-strategy setup cards with entry/SL/TP/R, confluence agreements,
   combination study.
3. Manual signal decision workflow (Approve/Reject recorded as decisions; **never** places an
   order) and auto-trading control surface (two-switch arming, strategy selection, per-symbol
   caps, decision log, unvalidated acknowledgement, real-account refusal switch).
4. Trade journal: open/closed positions, per-ticket strategy labels + notes, per-symbol filter,
   account totals, win rate; per-symbol auto position-cap stepper.
5. Diagnostic runs: list + per-run check detail (PASS/FAIL/NOT_VERIFIED).
6. Graded replays: run lists, family tables, card tables, entry rows.
7. Research: parameter-sweep iterations (train/test split), cost hurdles, freqtrade results,
   strategy ruleboard + per-strategy settings form.
8. Health: process/log monitoring, per-symbol feed freshness, 90-day gate clustering
   (hour/session buckets).
9. Strategy detail pages with editable settings.
10. Symbol + timeframe filter state in URL (`?symbol=`, `?tf=`) — deep links must keep working.

## 6. Backend and MT5 integration inventory

- `mt5_adapter.py` — `ReadOnlyMT5` allow-list: connect/shutdown/last_error/account_info/
  positions_get/copy_rates*/symbol_info*/history_deals — **no order methods exposed**.
- `watch.py` — main loop; writes `live-status.json` (per-symbol `symbols{}` block with spread,
  bar age, gate verdict, positions), `trades.json`, `setups*.json`, `gate-history.json`;
  reads the dashboard's control files each cycle; atomic writes.
- `execution.py` — auto-trading decision engine (caps file `auto-caps.json`, risk 1%, max 2
  positions, daily-loss circuit breaker, magic number, validated-only selection).
- `gate.py`, `riskfit.py`, `news.py`, `sessions.py` — gate verdict codes, risk fit, economic
  news exclusion, session windows.
- `store.py` + `data/mt5.db` — append-only journal (events, recommendations, decisions,
  strategies, equity_days, gate_rollup).
- `research/` — backtest, iterate, `export_mt5.py` (CSV chart export into the terminal's
  MQL5/Files for the custom indicator), `setups.py`.
- `install_indicator.py`, `tools/mt5/Mt5IntelSignals.mq5` — MT5 chart indicator export.
- `tools/freqtrade_*` — freqtrade backtest/sweep/export/sync integration (`user_data/`).
- **Gap for the terminal:** no OHLC bar history or bid/ask tick snapshot reaches the dashboard
  today. Solution (additive, §12): `src/mt5_intel/market_export.py` exports real bars + ticks
  to `dashboard/market/*.json`; new `lib/market.ts` + `/api/market` read them.

---

## 7. Current visual and usability problems

1. **Website, not workstation.** Header + horizontal text nav + stacked full-width panels; wide
   screens leave large empty margins, no workspace concept.
2. **Uniform card rhythm.** Every section is the same `.panel` block; importance is not encoded
   in size or position.
3. **No dark trading aesthetic.** Light/plain styling, no depth system, no glass surfaces, no
   brand identity (black / emerald / teal).
4. **Header nav is cramped**; research pages hide behind a `<details>` dropdown; no active
   workspace indication, no connection/account status in view.
5. **Data density low** — tables and facts blocks use generous website spacing; a trader cannot
   see account + market + signal state at once.
6. **No charting at all** — the most important trading surface (price) is absent.
7. **Fragmented live surfaces** — Live/Trades/Brief each re-explain account state differently.
8. **No terminal workspace** for Market Watch + chart + intelligence + positions together.

---

## 8. Proposed new information architecture

Workspaces (mapped onto the **existing** routes — no duplicate pages):

| Workspace | Contextual nav entries | Routes |
|---|---|---|
| **Terminal** (new default landing target in the rail) | Market · Chart · Intelligence · Positions | `/terminal` |
| **Trading** | Live decisions · Journal · Brief | `/live`, `/trades`, `/` |
| **Research** | Iterations · Replays · Runs · Strategy rules | `/research`, `/replays`, `/runs`, `/strategies/[family]` |
| **System** | Health · Auto-trading | `/health`, `/live#auto` (auto-trading fold) |

Global shell = **icon nav rail** (left, expandable) + **contextual secondary column** (inside
workspace pages where useful) + **compact topbar** (workspace title, account + currency, demo
badge, connection + freshness, links to settings-relevant surfaces) + **main workspace**.

---

## 9. New navigation and application shell specifications

- `components/shell/nav-rail.tsx` (client): fixed slim glass rail; icon buttons with tooltips,
  expandable to show labels; active state = emerald indicator + elevated glass; keyboard
  accessible (`aria-current`, focus rings); sections: Terminal, Trading (Brief/Live/Journal),
  Research (Research/Replays/Runs), System (Health). Mobile (<900px): rail collapses to a
  bottom bar.
- `components/shell/topbar.tsx`: workspace title + breadcrumb, symbol context where relevant,
  account chip (login + ZAR from `trades.json` account fields — real values), DEMO badge
  (from account trade mode; label "demo"), connection dot (live-status `connected` +
  `watcher_alive`), data freshness (heartbeat age, stale > 180 s ⇒ warning state), clock (UTC).
  No invented notifications; when a notification feature does not exist, no bell is shown.
- `components/shell/context-nav.tsx`: secondary nav list per workspace (§8), used by workspace
  pages as a left column or a tab strip depending on page plan.
- `app/layout.tsx`: skip-link + rail + topbar + `main` (each page supplies its own workspace
  grid). Smooth panel transitions, `prefers-reduced-motion` respected.
- Routing preserved exactly; client transitions via `next/link` (no full reloads).

---

## 10. Complete design system

Tokens (in `globals.css` `:root`, extended with semantic states):

```css
--background-main: #060B0B;      --background-secondary: #0B1414;
--background-elevated: #101C1C;
--surface-glass: rgba(15,26,26,.78);  --surface-hover: rgba(28,48,45,.85);
--accent-primary: #10B981;       --accent-secondary: #14B8A6;
--accent-highlight: #2DD4BF;
--text-primary: #F1F5F4;         --text-secondary: #94A3A3;   --text-muted: #647575;
--border-subtle: rgba(255,255,255,.07);
--positive: #34D399;  --negative: #FB7185;  --warning: #FBBF24;
```

Extended: `--focus-ring`, `--accent-primary-hover/dim`, `--chart-bull/--chart-bear`,
`--shadow-panel`, `--shadow-float`, `--radius-container: 20px`, `--radius-card: 16px`,
`--radius-control: 12px`, `--radius-menu: 14px`, `--radius-dialog: 20px`, spacing scale
(4/8/12/16/24/32/48), font scale (11/12/13/15/18/22/28).

Typography: **Geist** (bundled via `geist` npm package) with fallback `Inter, system-ui`;
`font-variant-numeric: tabular-nums` on all prices/financial values (`.num`). Levels: app
heading 22px/600, section heading 15px/600, primary metric 28px/650, secondary metric 15px,
table header 11px uppercase tracked, table value 13px, label 12px, status pill 11px.

Glassmorphism: applied to nav rail, topbar, floating panels/dialogs/menus, terminal overlay
panels and selected dashboard widgets — `background: var(--surface-glass); backdrop-filter:
blur(18px) saturate(140%); border: 1px solid var(--border-subtle); box-shadow:
var(--shadow-float)`. Never stacked on every nested element.

Icons: `lucide-react` (single consistent set). Motion: 150–220 ms ease transitions on hover,
panel and menu changes; `@media (prefers-reduced-motion: reduce)` disables them; no price
flash animations beyond a brief background tint on tick change.

Charts: **TradingView lightweight-charts** (candlesticks, crosshair, zoom/pan, price line).
If installation is unavailable, fall back to an in-house canvas candle renderer and record the
limitation — never fake candles.

---

## 11. Page-by-page redesign requirements

Each entry: **Old structure → New structure → Main structural differences → Preserved
functionality → Verification.**

### 11.0 `/` — Command centre (Phase 2)
- **Old:** three stacked panels (Strategy forecast / Account / Research) + page header.
- **New:** four-zone asymmetric command grid: (A) top overview strip — account equity-style
  facts + watcher/gate/news status chips; (B) primary focus column (wide) — strategy forecast
  as a ranked leaderboard with tier bars + active opportunities from setups; (C) secondary
  analytical column — risk state (daily loss vs 3% limit, open positions vs cap), latest
  research outcome, gate health summary; (D) activity band — latest journal trades and newest
  diagnostic runs as compact rows.
- **Differences:** hierarchy encoded by panel size; one-glance risk/status strip replaces
  prose; activity timeline replaces link lists.
- **Preserved:** `buildForecast`, `readTradesWithLabels`, `readLiveStatus`, `listReports`,
  `?symbol=` filtering.
- **Verification:** build + browser render at 1920/1440/768/390; data matches artifacts.

### 11.1 `/live` — Trading intelligence workspace (Phase 3)
- **Old:** filter strips + ForecastPanel + SetupsPanel + auto `<details>`.
- **New:** three-column workspace: left context column (symbol + timeframe selectors as
  vertical picker lists, live status block), centre = decision surface (selected setup/strategy
  analysis with entry/SL/TP/RR, evidence drawer, Approve/Reject controls), right = ranked
  forecast + confluence agreements. Auto-trading becomes its own clearly-labelled drawer with
  the arming switches intact.
- **Differences:** selection-driven detail instead of one long card list; decision controls
  grouped per selection; filters moved into the context column.
- **Preserved:** LiveFeedProvider/LivePoller polling, forecast, confluence, Approve/Reject
  (decision-only), auto panel behaviour, `?symbol=`/`?tf=` URLs.
- **Verification:** poll tick updates; approve/reject writes decision without any order.

### 11.2 `/trades` — Journal workspace (Phase 3)
- **Old:** symbol tabs + cap stepper + two wide tables.
- **New:** master–detail review layout: left = trade list (compact rows, filters, open/closed
  segmentation), right = selected trade detail (entry/SL/TP/exit, R, context, strategy labels,
  notes). Summary rail with win rate, net, open exposure. Cap stepper becomes a control in the
  summary rail with the exact current semantics.
- **Differences:** master–detail instead of long tables; totals rail; label editing in the
  detail pane.
- **Preserved:** `readTradesWithLabels`, `filterTradesBySymbol`, `writeLabel` flow,
  SymbolCapControl semantics, ALL tab behaviour.

### 11.3 `/research` — Research workspace (Phase 3)
- **Old:** PageHead + FreqtradePanel + flat iterations table + folded ruleboard.
- **New:** left = research nav/filters (family, train/test, cost hurdle), centre = iteration
  results table with degradation indicators + freqtrade results as a comparison band, right =
  selected run evidence (params, R, costs, OOS results, rejection reasons). Ruleboard becomes
  a tab of the workspace, not a buried `<details>`.
- **Differences:** three-column analytical layout, selection-driven evidence pane, explicit
  validation-status column.
- **Preserved:** `listIterations`, `formatR/formatParams`, FreqtradePanel data, ruleboard
  content, generate-command hint.

### 11.4 `/replays` + `/replays/[id]` — Replay/backtest workspace (Phase 3)
- **Old:** list page + stacked family/cards/entry tables.
- **New:** run selector on the left, performance overview band (graded outcomes distribution
  TP/SL/time-exit), cumulative-R visualisation from recorded grades where available, individual
  simulated trade inspector below. Simulated vs broker-recorded clearly badged.
- **Preserved:** `listReplays`, `getReplay`, replay tables' data and grade semantics.

### 11.5 `/runs` + `/runs/[id]` — Diagnostics (Phase 3)
- **Old:** single table; report detail stack.
- **New:** runs as a dense timeline list with status chips and pass/fail/not-verified bars;
  detail page with check-group side nav + grouped check cards.
- **Preserved:** `listReports`, `ReportView`/`StatusPill` data, deep links `/runs/[id]`.

### 11.6 `/health` — System health (Phase 3)
- **Old:** Tabs + tables + ClusteringPanel.
- **New:** health command layout: top status grid (watcher, gate, market, news, files), log
  table with severity tinting, per-symbol feed freshness matrix, clustering visualisation band.
- **Preserved:** `readHealth`, `readSymbolFeeds`, clustering data, auto-refresh.

### 11.7 `/strategies/[family]` — Strategy detail (Phase 3)
- **Old:** detail + settings form.
- **New:** strategy dossier: rule summary, evidence/research linkage, settings editor in a
  clearly-labelled form panel with save feedback.
- **Preserved:** `StrategySettingsForm` behaviour and settings persistence.

### 11.8 Journal/notes features inside Trades (see 11.2) — preserved verbatim in behaviour.

---

## 12. New MT5 terminal architecture (Phase 4–5)

Route: `/terminal` (single route, panel state in `?panel=`/local preferences).

Layout (desktop):
1. **Top terminal toolbar** — active symbol selector, timeframe (H1/M30/M15), chart style
   (candles/line), chart tools (crosshair, zoom-to-fit, fullscreen), connection status,
   workspace controls (collapse panels, reset layout).
2. **Left Market Panel** — Market Watch: real per-symbol rows (last price, bid/ask when the
   tick export exists, spread pips, gate verdict, freshness), search, favourites
   (localStorage), selection drives chart + intelligence panel.
3. **Central chart** — lightweight-charts candlesticks from real exported bars; current price
   line; OHLC readout; timeframe switching re-reads the artifact for that frame; position
   markers (from real journal entries) and signal markers (from real setups) overlaid.
   Full-screen mode. **No fabricated candles** — missing artifact ⇒ explicit empty state.
4. **Right intelligence panel** — active signal for the selected symbol: direction, strategy
   family, opportunity time, suggested entry, SL, TP, RR, validation status (research
   `strategies` table / gate reasons), risk warnings (news, daily loss, positions), evidence
   links. Unavailable values render "— (unavailable)". Manual decision controls reuse the
   existing decision workflow; approval never executes an order.
5. **Bottom activity dock** — tabs: Open positions · Trading history · Journal/events · System
   log summary. Real `trades.json` data; read-only.

Panel interactions: collapse/expand left/right/bottom, chart fullscreen, bottom tab switching,
keyboard shortcuts (`1..3` timeframe, `f` fullscreen, `b` focus symbol search), saved
workspace preferences in `localStorage` (`mt5-terminal-layout`) with Reset layout. Features
blocked by real limitations are documented in the UI ("unavailable: no tick export yet").

Backend integration (Phase 5):
- NEW `src/mt5_intel/market_export.py` — CLI + optional loop; uses `ReadOnlyMT5.copy_rates_from_pos`
  and `symbol_info_tick` to write atomic `dashboard/market/bars_<SYMBOL>_<TF>.json`
  (`{generated_utc, symbol, timeframe, digits, bars:[{time,open,high,low,close,volume}]}`) and
  `dashboard/market/watch.json` (per-symbol bid/ask/last/spread/digits/flags). Read-only; safe
  alongside the watcher.
- NEW `dashboard/lib/market.ts` — read/parse those artifacts with staleness detection.
- NEW `dashboard/app/api/market/route.ts` — `?symbol=&tf=` JSON for client refresh.
- Terminal polling reuses the `poll.tsx` visibility-aware pattern (15–30 s), and every panel
  degrades to a truthful stale/unavailable state when artifacts are old or missing.

---

## 13. Backend integration requirements

1. Do not modify watcher loop semantics; `market_export.py` is additive and read-only.
2. Artifact write style must match `watch.py` conventions (atomic temp+replace, ISO UTC
   timestamps, explicit `generated_utc` for staleness).
3. API routes keep existing contracts untouched; `/api/market` is purely additive.
4. Control-file flows (`auto-switch.json`, `auto-strategies.json`, `auto-caps.json`,
   `data/trade-labels.json`) keep their exact schemas — the dashboard writes them and the
   watcher reads them.
5. Risk authority remains the Python gate/execution modules. The frontend displays gate
   verdicts and risk numbers; it never recomputes them as authority, never auto-executes.

---

## 14. Functional preservation requirements

All items in §5 must work after each phase: routes resolve; polling updates; Approve/Reject
records decisions only; auto-trading arming semantics unchanged (two switches + real-account
refusal + validated-only); cap stepper semantics unchanged; labelling persists; symbol/
timeframe deep links work; research/replay/report browsing intact; settings save; health
auto-refresh intact; `npm run lint` and `next build` pass; Python tests stay green.

---

## 15. Step-by-step implementation phases

- **PHASE 0 — Audit & documentation** (this file + progress file). Acceptance: every page and
  feature inventoried with a redesign plan. → Status: see progress file.
- **PHASE 1 — Design architecture**: tokens + typography + glass system in `globals.css`;
  `nav-rail`, `topbar`, `context-nav`, `app/layout.tsx` shell; responsive foundations; legacy
  class names restyled to the tokens so existing panels inherit the new identity.
  Acceptance: structurally new shell rendering on every route.
- **PHASE 2 — Command centre** (`/`): asymmetric four-zone grid per §11.0. Acceptance: new
  composition with preserved data.
- **PHASE 3 — Existing workspaces**: `/live`, `/trades`, `/research`, `/replays(+[id])`,
  `/runs(+[id])`, `/health`, `/strategies/[family]` per §11. Acceptance: structural change +
  functionality per page.
- **PHASE 4 — MT5 terminal UI** per §12. Acceptance: all five regions exist with truthful
  states.
- **PHASE 5 — Terminal integration**: `market_export.py`, `lib/market.ts`, `/api/market`,
  wiring + staleness handling. Acceptance: chart shows real exported bars; missing data shows
  unavailable state.
- **PHASE 6 — QA**: lint + typecheck + `next build`; browser verification at 1920/1440/1366/
  768/390; interaction checks; regression pass on §14; Python tests.
- **PHASE 7 — Final audit**: reread both docs, acceptance matrix (§20), final report.

## 16. Dependencies and implementation order

Design tokens (P1) → shell (P1) → page reconstructions (P2→P3) in any order after P1 →
terminal UI (P4) depends on P1 primitives → terminal integration (P5) depends on P4 chart
component and Python export → QA (P6) depends on all. External packages to install in
`dashboard/`: `geist`, `lucide-react`, `lightweight-charts` (all small, MIT/Apache).

## 17. Testing and visual verification criteria

- `npm run lint`, `npx tsc --noEmit`, `npm run build` in `dashboard/` after each phase.
- `pytest` in repo root for Python changes.
- Browser inspection (Playwright/webapp-testing skill or preview tooling) at 1920×1080,
  1440×900, 1366×768, 768, 390 widths; screenshots saved under `screenshots/`.
- Old-vs-new comparison per §10.2 of the mandate: structural, navigation, hierarchy,
  component, visual identity, interaction, functionality.
- Data truthfulness spot-checks: every displayed number must be traceable to an artifact field.

## 18. Identified technical risks

1. **No candle/tick history artifact exists** — mitigated by `market_export.py` (P5); until it
   runs, the chart shows an honest empty state.
2. **Heavy client components** (auto-panel etc.) rely on legacy CSS classes — mitigated by
   keeping class contracts while restyling, then restructuring per page.
3. **Next.js 16 specifics** (`connection()`, async `searchParams`, `instant = false`) must be
   preserved to avoid caching regressions.
4. **npm install on this host** — registry reachable (verified); pin versions via lockfile.
5. **Watcher running live** — Python additions must be additive/read-only; never touch
   `data/mt5.db` destructively or restart the watcher without need.
6. **Scope** — 10 routes + terminal; phases tracked in the progress file to allow clean
   resumption.

## 19. Outstanding issues and blockers

- (updated during work — see progress file for live status)
- Baseline screenshots of the old UI were not captured before changes; mandate allows
  documenting this limitation.

## 20. Final acceptance checklist

- [ ] Master plan + progress file exist and match reality.
- [ ] New app shell (rail + topbar + contextual nav) on all routes.
- [ ] All §3 routes structurally redesigned (not recoloured), with per-page verification records.
- [ ] `/terminal` exists with toolbar / market watch / chart / intelligence / activity dock.
- [ ] Chart uses real exported bars only; no fabricated market data anywhere.
- [ ] Account/positions/history panels use real journal data (ZAR, HFM demo).
- [ ] Manual-only execution preserved; Approve/Reject never orders; auto switches unchanged.
- [ ] USD/JPY primary instrument honoured in defaults; no cross-symbol data mixing.
- [ ] `lint` + `tsc` + `next build` pass; Python tests pass; browser checks at 5 widths done.
- [ ] Final acceptance matrix (§20 of the mandate) filled with evidence; limitations stated
      honestly in the final report.
