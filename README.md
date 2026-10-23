# mt5-intel: MT5 research console (read-only unless you arm it)

Scope from the design doc (§18.8 and §19.6): connect to the HFM MT5 terminal, confirm demo mode and
the USC (Cent) account, identify the USD/JPY symbol and its specs, check the USD/ZAR rate, build the
risk-fit table, check tick liveness, measure history depth and gaps, and measure spread by hour.
**Nothing is traded unless you deliberately arm it.** Auto-trading is off by default, needs two
separate switches, refuses a live account unless a third setting says otherwise, and acts only on
strategies you have chosen - which by default means only ones research has validated, with an
explicit acknowledgement needed to trade a strategy that has no measured edge yet. Everything else
reads: the research adapter exposes no way to place anything, and a test scans all of `src/` for
order calls, allowing them in exactly one named module — so a second order path cannot appear by
accident.

## Auto-trading

A live card that is on an allowed strategy, still fresh, and past every check in the recommendation
gate can be placed as a real position without you watching. It is there for two failures: a trade
you left open through a turn, and an entry you saw too late and took at a worse price. Off by
default, and three things have to agree before an order exists.

```ini
# .env - the hard master. Off (unset) means off: the dashboard switch cannot arm anything.
AUTO_TRADE=true
# The fallback selection: what may trade when nothing has been saved on the Live tab. A saved
# selection (see below) wins over this line. `confluence` is the pair pattern: two or more *allowed*
# cards entering the same bar the same way, traded with the tightest of their stops.
AUTO_TRADE_STRATEGIES=daily_momentum,confluence
AUTO_TRADE_MAX_POSITIONS=2
AUTO_TRADE_RISK_PCT=1
# A second, separate switch. Without it a REAL account is refused outright.
AUTO_TRADE_ALLOW_REAL=false
# The acknowledgement: trade a strategy research has NOT validated. Every position it opens is
# recorded as unvalidated. Off, and a name without a `historically validated` row is refused.
AUTO_TRADE_ALLOW_UNVALIDATED=false
```

With `AUTO_TRADE=true`, the **Live** tab grows an Auto-trading panel whose button is the second
act: the switch is a file the watcher re-reads every cycle, so turning it off stops new entries
within one cycle rather than at the next restart. Only that button, and only from the console
itself — the route behind it refuses a request that is not same-origin.

### Choosing the strategies

The panel lists **every family in the research registry** for the watcher's timeframe, with the
standing research gave it and the numbers its last trial measured — mean R, the edge over the
side-flipped placebo, the same at 1.5x costs, and the share of walk-forward folds that were
positive. Tick what may trade and press **Save selection**; it is written to
`dashboard/auto-strategies.json`, which the watcher re-reads every cycle, so the list changes
without a restart and ticking something off stops new entries within one cycle (it never closes
what is already open — the broker holds those stops). A saved selection replaces the .env list for
the *strategies* only: it can narrow that list, never widen the two switches above it.

A family without a `historically validated` row is still tickable, but only with the
**acknowledgement** ticked — *trade a ticked strategy even though research has not validated it*.
Without it the watcher refuses the name and says so on the panel; with it, every position it opens
carries `validated: false` in the journal and the row is marked `unvalidated` on the panel. That is
not a formality. It is the difference between "this system traded a rule with a measured edge" and
"this system traded a rule because you asked it to", and there is no state in between.

> **Nothing is validated today.** The last full trial (`reports/research-*.md`) promoted no family
> on H1 or M15, so with the acknowledgement off the allow-list is empty and nothing trades — which is
> the honest outcome, not a bug. The closest on H1 is `daily_momentum`: mean +0.044R over 714
> signals, +0.155R against the placebo, still +0.014R at 1.5x costs and 80% of folds positive — it
> fails only on the lower confidence bound, which has to clear zero. `carry_trend` sits at +0.003R
> with no edge over the placebo. Re-run the trial with
> `.\.venv\Scripts\python.exe -m mt5_intel.research.cli` to refresh the registry the panel reads.

### The terminal's own switch

MT5 refuses every order with retcode 10027 while its **Algo Trading** button is off, and that is a
different switch from anything here: the account can permit trading while the terminal does not
send it. The watcher checks it before it will place anything, refuses with that reason, and the
panel says so — including while the trading switch is off, so it is visible before arming rather
than after. Turn it on in MT5's toolbar (it is also what `terminal_info().trade_allowed` reports).

What it does with a card: sizes the position so the card's own stop costs the configured share of
equity, and sends a market order with that stop and the card's target **attached to the request**,
so the broker closes the trade even if the watcher is not running. Position size is floored to the
broker's lot step and never rounded up; when even the minimum lot would risk more than the budget,
the card is refused rather than traded smaller-but-wrong.

What it deliberately will not do:

- **Enter late.** A card is traded only while it is fresh — a newly appeared card, at the price the
  card planned. A card that has been running for hours, or whose price has already moved a quarter
  of the stop away from the planned entry, is skipped and says which of the two it was.
- **Double up.** A signal is reserved in the journal as `(strategy, signal bar, side)` *before* the
  order is sent, so a re-fire, a restart or a crash in between cannot place it twice.
- **Trade anything unvalidated by accident.** A name without a `historically validated` row for
  the watcher's timeframe is refused unless the acknowledgement above says otherwise, and then it
  is traded *as* unvalidated rather than passed off as validated. `confluence` is only tradeable
  when every family that formed the pair is itself allowed, it needs two allowed families to be
  able to fire at all, and it takes the *tightest* of the agreeing stops.
- **Trade against a stale view.** The cards come from the live setups view, which the watcher
  rebuilds every 5 minutes. If that view is missing, unstamped or older than 15 minutes, no new
  entries are taken — an hour-old card can still read "just entered" while the market has long left
  the price it planned to enter at.
- **Ignore the gate.** Every market check the cycle gate runs applies per card, plus that card's own
  validated strategy and whether its stop fits the risk budget. Note that the gate's position limit
  counts **every** position on the account, manual ones included; `AUTO_TRADE_MAX_POSITIONS` caps
  only what this system opened, so a busy account can still hit the gate's own limit first.

Everything is journaled: each claim in `auto_trades` (with its ticket and retcode), and each
refusal as an `auto_refused` event with the reason. The panel shows the open positions, what this
cycle took, what it refused and why, and the account it is looking at.

> **This account is a demo.** It reports `trade_mode DEMO` (`HFMarketsSA-Demo`, ZAR), so the
> live-account guard is not being exercised and `AUTO_TRADE_ALLOW_REAL` is not needed. It is there
> for the day a live login is pointed at this: with it unset, a REAL account is refused outright and
> the panel says so. Set it only when you mean it.


## See the strategies on the MT5 chart

The tabs are numbers. `Mt5IntelSignals` is the same thing drawn on the chart, so you can look at
where a rule entered and judge it yourself. It is an **indicator**: it draws objects and cannot
place, change or close an order. The indicator itself places nothing, whatever the Python side is
armed to do.

It deliberately does not recompute the strategies. The families live in Python and stay the only
place they are defined; the indicator just draws what that console exported. An MQL5 second
implementation would be free to drift, and then a chart that disagreed with the console could not
tell you which of the two was wrong - which is the question it exists to answer.

```powershell
# 1. Write the three files to the terminal's MQL5\Files (the watcher also does this every 5 min)
.\.venv\Scripts\python.exe -m mt5_intel.research.export_mt5 H1

# 2. Install the indicator: copy it into the terminal's MQL5\Indicators, compile it there,
#    and re-export the chart files (so one run leaves build and files in step)
.\.venv\Scripts\python.exe -m mt5_intel.install_indicator
```

3. In MT5: **Navigator** (Ctrl+N) → **Indicators** → `Mt5IntelSignals` → drag it onto a
   **USDJPY H1** chart. If it is not listed yet, right-click the Indicators tree → *Refresh*.
   The install command above is how the terminal's copy stays in step with
   [Mt5IntelSignals.mq5](tools/mt5/Mt5IntelSignals.mq5) - it copies, compiles and re-exports the
   chart files (`--no-export` installs the build only). By hand the install is the same two moves
   it makes for you - copy the file into `MQL5\Indicators`, press F7 in MetaEditor
   (`MetaEditor64.exe /compile:"<path>"`), then restart MT5.
   **After updating the indicator you have to restart MT5** (or remove and re-attach it); the
   running terminal keeps the build it loaded, so the files can be new while the chart is old.
   The chart says which build it runs: the key's title line prints the indicator's version.

### What is on the chart

- **The moving averages themselves**, as ordinary indicator lines: EMA(50) and EMA(200) - the
  trend the pullback and momentum rules read - and SMA(20)/SMA(50) - the pair `ma_crossover`
  trades. Their colour, width and style are in the usual **Indicator Properties → Colors** tab
  like any other indicator, and they appear in the Data Window. `InpShowLines`, `InpLineEma` and
  `InpLineSma` switch them off.
- **The 96-bar box** (two faint lines) - the range `range_extreme_mr` fades. Seeing price pinned at
  the top line while a rule sells is the whole explanation for a trade that otherwise looks wrong.
- **A key naming every colour and tag** - and nothing else. Each family gets a colour swatch, its
  name, the short tag its arrows carry (`REM` = `range_extreme_mr`), what it is in right now (side
  and entry, or `flat`), how many trades are running, how many finished, and their mean R. So
  "which arrow is this?" is answered by looking at the same line the arrow's colour is on. The
  backdrop numbers this panel used to carry - the trend and EMA separation, the SMAs, ATR(14),
  RSI(14), the box and where price sits in it, fresh high/low, the move over 24/96/240 bars - are
  **not** repeated on the chart: they are the same `market_context` the Live tab quotes, and
  printing them twice makes the chart harder to read, not the console more trustworthy. Anything
  that does appear above the key is a fault - a file from another timeframe, an average that could
  not be placed, an arrow with no bar to sit on - and none of it appears in a healthy run.
- **The arrows.** One per trade taken, at its entry bar, coloured by family. **Thick** arrows are
  still running and get a tag, an entry line and dotted SL/TP lines; recent arrows (last
  `InpLabelBars`, default 60) are tagged too, older ones stay colour-only to keep the chart
  readable. Hover any arrow for the family, side, entry, stop, target, result in R, and how many
  times the rule re-fired while that position was open.
- **The arrows that agree, in green.** When two or more *live* cards entered the same bar in the
  same direction, their arrows are painted in the agreement colour (lime on a dark chart, green on
  a light one) instead of their family's, and their tags carry `xN` - so a rule that had company is
  still recognisable without the colour. This is the one thing a chart can show that a single row
  cannot: two rules reading one bar the same way. Only running cards take part (a finished trade is
  not live alongside anything) and only the same side counts (a buy and a sell on one bar are a
  disagreement). The count is computed in the exporter, not in the indicator - the same reason the
  trades are - so the rule stays testable. `InpAgreeHighlight` switches it off, `InpAgreeColor`
  overrides the colour.
- **An alert when a watched rule takes a position.** `InpNotifyRules` names the rules
  (`trend_pullback` by default, `""` for none) and the message is the arrow's own reading -
  `TP is buying  -  USDJPY H1  entry ..., stop ..., target ...` - the tag that arrow carries
  (`TP` = `trend_pullback`) and then the side. It is the terminal's own `Alert` (a popup, and a line
  in the Experts log), pushed to the phone as well while `InpNotifyPush` is on; a push the terminal
  will not accept says so in the log, naming the setting that is missing. Attaching the indicator
  never rings for the cards already in the file - the first pass only records what is there - so an
  alert means a position opened while this chart was being watched. It arrives with the arrow:
  both read the same file, so what you are told and what you can see cannot be two moments.

Rows whose bar is not in the loaded history are **counted in the panel** rather than dropped, so a
missing arrow is explained instead of silent - scroll the chart back, or raise "Max bars in chart"
in Tools → Options → Charts. The same goes for the average lines: the panel says so when not one
bar of them could be placed on the chart.

Useful inputs: `InpFamilies` (e.g. `range_extreme_mr,sr_flip` - see one rule at a time, with
`InpLabelAll` to tag every arrow it drew), `InpOnlyRunning` (just what is on now), `InpMaxTrades`
(how many rows to draw, default 40), `InpShowClosed`, `InpShowBarriers`, `InpShowPanel`,
`InpReloadSeconds` (it re-reads the files on that timer), `InpAgreeHighlight` and `InpAgreeColor`
(the arrows of live cards that agree), and `InpNotifyRules` / `InpNotifyPush` (which rules it says
out loud, and whether that also goes to the phone).

The three files, all written from one read of the same history:
`mt5intel_H1.csv` (the trades), `mt5intel_H1_lines.csv` (the two EMAs and two SMAs, bar by bar) and
`mt5intel_H1_context.csv` (the 96-bar box, and the symbol and timeframe the other two are read
against). The averages are exported rather than recomputed
with MT5's own `iMA()` on purpose: MT5 seeds an EMA off whatever history the chart happens to hold,
so the line and the number printed next to it could sit a hair apart with no way to tell which one
the rules used.

To check the files against the terminal's own bars (needs a logged-in terminal):

```powershell
.\.venv\Scripts\python.exe tools\check_chart_export.py
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Fill in `.env`:

- `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`.
- Leave `MT5_UTC_OFFSET_HOURS` empty to estimate the broker clock during market hours, or set `2` or `3`.
- Leave every `AUTO_TRADE*` setting out unless you want it trading (see **Auto-trading** above).

> **Which password you put in `.env` decides whether this can trade at all.** An investor
> (read-only) password cannot trade — MT5 rejects any order sent with it, so the diagnostic and
> research runs are perfectly happy with it and auto-trading can never work. The account's normal
> password is what arming needs, and it gives this process the ability to place orders. That is the
> real decision here, and `.env` is where it is made. Everything else on this page works either way.

## Run it (double-click)

Double-click **`MT5 Intel.lnk`** (or `Start MT5 Intel.bat`). One window opens, brings up the whole
console - dashboard, the MT5 watch loop and one diagnostic pass - then opens the browser. Close that
window (or Ctrl+C) to stop everything; **`Stop MT5 Intel.bat`** is a backstop if a window is lost.

The launcher checks and heals what usually goes wrong: a missing `.venv` or `node_modules` gets
installed, a busy port moves to the next free one, and a corrupt `.next` cache is cleared and the
dashboard restarted. It never touches `.env` values. Everything it does is appended to
`logs/launcher.log`. Once the dashboard is up it warms every tab, so the first click is not a cold
compile. `logs/launcher.log` is rotated when it passes 2 MB (3 backups kept) and the per-run logs
are reset each launch, so `logs/` cannot grow without bound. The dashboard's own output is capped
the same way **while it runs**: the launcher reads the dashboard's streams itself, so it can trim
`dashboard.log` and `dashboard.err.log` mid-session instead of only at launch. Set
`MT5_LOG_CAP_BYTES` (a byte count) to change the 2 MB cap.

Useful switches (`Start MT5 Intel.bat -Repair`, `-NoWatcher`, `-NoBrowser`, `-Port 4000`):

- `-Repair` - heal everything at once: stop the console, clear the `.next` cache, reinstall the
  Python and Node deps, and start fresh on a free port.
- `-NoWatcher` - skip the MT5 watch loop (start the dashboard only).
- `-NoDiagnostic` - skip the startup diagnostic pass.
- `-NoBrowser` - do not open a browser automatically.
- `-Reinstall` - force a reinstall of the Python deps and `node_modules`.

## The tabs

- **Latest** / **All runs** - the newest diagnostic report, and every run kept.
- **Live** - the MT5 watch loop's heartbeat, the recommendation gate's last verdict, and what
  every strategy is in **right now**: the position each rule holds (side, entry, stop, target,
  risk), whether it is still live or already closed, how far price has moved in R, and how many
  times the rule re-fired while that position was open. It reads *positions*, not signals: these
  rules have no memory of being in a trade, so while a condition holds they re-fire every bar,
  and 53-82% of most families' signals were repeats of a position already open (D-035). The
  trend families only signal at the start of a move, so this is the view that shows a live trade
  days into a trend - a graded replay cannot, because it only contains finished trades. Where a
  card is open, the row shows the trade as it was taken: first entry, real age, real R. **Two arrows
  on one candle** get their own card rather than being dismissed as a clash: a buy and a sell on the
  same bar is a repeatable event, so the page names the exact combination (one letter per rule plus
  the side, e.g. `G↓ L↑` for pullback_scalp selling while trend_pullback buys), prices each member's
  own order, says where the candle sat in its 96-bar range, and reports what the exported history
  measured about that combination. The obvious reading of it - buy when the arrows sit at a low,
  sell when they sit at a high - was tested against that history and called the wrong side more
  often than the right one (13 of 34), so the card states the record instead of a direction, and
  nothing trades a combination: the watcher only forms a pair when the rules agree on a side.
  Per-rule letters live in `dashboard/lib/combos.ts`, which is also where the measured record is
  transcribed (`python tools/combo_table.py` regenerates it from the exported chart history). It is
  rebuilt by the watcher while the market is open (`dashboard/setups.json`) and can be run by
  hand on any timeframe: `python -m mt5_intel.research.setups M15`.
  The tab's three blocks are one reading rather than three timers: the heartbeat strip, the switch
  and the per-rule table all come from a single check every 10 s (`components/live-feed.tsx`), so a
  heartbeat that has moved is never shown beside a table that has not, and the strip carries a clock
  of its own ("checked 3 s ago") because the watcher's cycle and its three artifacts are on separate
  timers. Returning to the tab is a check of its own, which is what makes a page left open overnight
  current the moment it is looked at instead of a throttled minute later. The switch reads the
  runtime toggle file, not the resolved state the watcher writes back a cycle later, so arming it
  shows that it took on the click rather than after the next cycle.
- **Trades** (`/trades`) - what the account actually did, read from the terminal rather than typed
  in: the positions still open, marked to the terminal's own floating result, and the trades already
  taken with their entry, exit and net (profit plus swap and commission, in the account's own
  currency - the number the terminal would show, not a second estimate of it). Each trade is
  stamped when it was taken and when it closed, and remembers which strategies had a live card on
  the Live tab when it was first seen, so a trade taken while sweep-and-reclaim was live still says
  so after it has closed. Which of those rules you were following is the one thing only you can
  say, and the page sets it per trade; that answer lives in `data/trade-labels.json` and is never
  written over the journal. Closed trades come from the deal history (180 days back, entry and exit
  volume-weighted so a partial fill is counted once), which is why a trade you have forgotten about
  still appears. It is rebuilt by the watcher every minute while the market is open
  (`dashboard/trades.json`), and the page itself places, modifies and closes nothing.
- **Replays** - graded what-if trades. A run whose clock is more than two days behind the newest
  bar is labelled as such: everything in it is a past moment, so an old clock otherwise reads as
  "no strategy is picking this up" when nothing in the run was looking there. Use the Live tab for
  the current view.
- **Research** - what each strategy does and how it was graded on held-out history.
- **Health** (`/health`) - whether the console is actually running: each component's state
  (dashboard up, watch loop live/idle/stale, diagnostic pass), the processes the launcher tracked,
  the latest diagnosis, recent runs, and the size of every log in `logs/` (so an over-cap log is
  visible at a glance). It also breaks the gate reason codes down by UTC hour and by trading
  session, as a rate per bucket rather than a count, so a code that only looks frequent because
  its hour is busy cannot pass for a cluster. That breakdown is built from an hourly rollup of the
  journal, so the refresh reads one hour of cycles plus the rolled hours (at most 90 days' worth)
  instead of every cycle in the window. It re-reads and refreshes every 10 seconds, through a
  transition (the render never holds the tab) and again the moment the tab is looked at.

To rebuild the icon or shortcut: `python tools/launcher/make_icon.py`, then
`powershell -ExecutionPolicy Bypass -File tools/launcher/make_shortcut.ps1` (add `-Desktop` to put a
copy on the desktop).

## Run (command line)

```powershell
.\.venv\Scripts\python.exe -m mt5_intel
```

The continuous watch loop (what the launcher starts for the Live tab):

```powershell
.\.venv\Scripts\python.exe -m mt5_intel.watch
```

The dashboard on its own:

```powershell
cd dashboard
npm run dev -- -p 52577
```

Exit codes: `0` PASS or WARN, `1` FAIL or NOT_VERIFIED, `2` setup or connection error.
Reports are written to `reports/diagnostic-<UTC stamp>.md` and `.json`. Credentials are never written.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy
```

The tests run against a fake terminal (`tests/fakes.py`), so they do not need a logged-in MT5 session.

## Known limits

- Staleness is judged by a 30 s tick-watch while the market is open. Quiet minutes can produce a WARN.
- UTC conversion needs the broker offset. While the market is closed, it is unknown unless `MT5_UTC_OFFSET_HOURS` is set.
- Weekend and holiday handling is a heuristic (FX closed Friday 21:00 to Sunday 21:00 UTC; holidays not modelled).
- Trading sessions are defined on each centre's **local** clock (Tokyo 09:00-18:00 JST, London and
  New York 08:00-17:00 local), so their UTC hours shift by an hour twice a year. That needs the IANA
  database, which `tzdata` supplies on Windows. If it is missing, the session breakdown says so
  instead of inventing fixed UTC windows; the hour breakdown still works.
- The spread-by-hour figures come from bar snapshots, not the full tick cost path.
- History requests are capped at 80,000 bars per call (live probe: 100,000 is rejected by the terminal). So M1 depth is about 78 days. Full depth needs paged requests, which are not built yet.
- The account checks follow what the terminal reports. The current login is a ZAR demo (`trade_mode DEMO`), so the Cent-account checks report WARN rather than the FAIL they reported on the previous live login.
- The gate summary keeps a 90-day window: rolled hours older than that are deleted, so the Health
  page's counts cover the window, not all history. The journal itself is not pruned.
