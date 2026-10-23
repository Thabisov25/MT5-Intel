"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickChart,
  ChartCandlestick,
  ChevronsLeft,
  ChevronsRight,
  Maximize2,
  Minimize2,
  RotateCcw,
  Search,
  Star,
} from "lucide-react";
import { TerminalChart, type ChartMarker, type ChartPriceLine } from "@/components/terminal/chart";
import { useVisiblePoll } from "@/components/poll";
import type { AutoStatus } from "@/lib/auto";
import type { DecisionRow } from "@/lib/decisions";
import type { LiveStatus } from "@/lib/live";
import type { MarketBars, MarketWatchSnapshot } from "@/lib/market";
import type { Setups, SetupRow, Timeframe } from "@/lib/setups";
import type { TradeRow, Trades } from "@/lib/trades";
import { strategyName } from "@/lib/strategies";

/**
 * The MT5 web terminal (master plan §12): toolbar, Market Watch, chart, intelligence
 * panel and activity dock as one integrated workspace. Every value is real — bars and
 * ticks come from the read-only MT5 export, account and positions from the watcher's
 * journal, signals from the watcher's card views. Missing data renders as an explicit
 * unavailable state, never as a placeholder price.
 */

const TIMEFRAME_ORDER: Timeframe[] = ["H1", "M30", "M15"];

const LAYOUT_KEY = "mt5-terminal-layout";
const FAVOURITES_KEY = "mt5-terminal-favourites";
const POLL_MS = 20_000;

interface TerminalData {
  bars: MarketBars;
  watch: MarketWatchSnapshot;
  setups: Setups | null;
  journal: Trades | null;
  live: LiveStatus;
  auto: AutoStatus;
}

interface LayoutPrefs {
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  chartStyle: "candles" | "line";
}

const DEFAULT_LAYOUT: LayoutPrefs = {
  leftCollapsed: false,
  rightCollapsed: false,
  chartStyle: "candles",
};

function fmtPrice(value: number | null, digits: number | null): string {
  return value === null ? "—" : value.toFixed(digits ?? 2);
}

function fmtSigned(value: number | null, suffix = ""): string {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}${suffix}`;
}

export function Terminal({
  initial,
  symbols,
  initialSymbol,
  initialTimeframe,
}: {
  initial: TerminalData;
  symbols: string[];
  initialSymbol: string;
  initialTimeframe: Timeframe;
}) {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [timeframe, setTimeframe] = useState<Timeframe>(initialTimeframe);
  const [layout, setLayout] = useState<LayoutPrefs>(DEFAULT_LAYOUT);
  const [fullscreen, setFullscreen] = useState(false);
  const [query, setQuery] = useState("");
  const [favourites, setFavourites] = useState<Record<string, boolean>>({});
  const [dockTab, setDockTab] = useState<"positions" | "history" | "decisions" | "system">("positions");
  const [selectedFamily, setSelectedFamily] = useState<string | null>(null);
  const [data, setData] = useState<TerminalData>(initial);
  const [decisions, setDecisions] = useState<(DecisionRow & { recId?: number })[]>([]);
  const [note, setNote] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // Workspace preferences survive a reload (master plan §7.3). The restore is scheduled
  // after hydration instead of run inline in the effect body.
  useEffect(() => {
    const id = window.setTimeout(() => {
      try {
        const raw = window.localStorage.getItem(LAYOUT_KEY);
        if (raw) setLayout({ ...DEFAULT_LAYOUT, ...(JSON.parse(raw) as Partial<LayoutPrefs>) });
        const favs = window.localStorage.getItem(FAVOURITES_KEY);
        if (favs) setFavourites(JSON.parse(favs) as Record<string, boolean>);
      } catch {
        /* preferences are optional */
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, []);

  const saveLayout = useCallback((next: Partial<LayoutPrefs>) => {
    setLayout((current) => {
      const merged = { ...current, ...next };
      try {
        window.localStorage.setItem(LAYOUT_KEY, JSON.stringify(merged));
      } catch {
        /* non-fatal */
      }
      return merged;
    });
  }, []);

  const toggleFavourite = useCallback((name: string) => {
    setFavourites((current) => {
      const next = { ...current, [name]: !current[name] };
      try {
        window.localStorage.setItem(FAVOURITES_KEY, JSON.stringify(next));
      } catch {
        /* non-fatal */
      }
      return next;
    });
  }, []);

  const recId = useMemo(() => {
    if (data.live.symbol === symbol) return data.live.recId ?? null;
    return data.live.symbols?.[symbol]?.recId ?? null;
  }, [data.live, symbol]);

  /** The bars are stamped in the BROKER's clock (the terminal's own), while the journal's
   * timestamps are UTC. A trade drawn raw would sit hours left of the candle it belongs to.
   * The offset comes from the watcher's own measurement of the server clock (`live.status`
   * carries it per symbol); when the watcher has none, a guess read off the export itself:
   * the last row of the bars artifact is the current forming bar, so its open minus the
   * current wall-clock bucket approximates it — honestly zero (no correction) when no
   * source can be trusted rather than a fabricated shift. */
  const [barClockOffsetS, setBarClockOffsetS] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const [marketRes, setupsRes, liveRes, tradesRes, autoRes] = await Promise.all([
        fetch(`/api/market?symbol=${encodeURIComponent(symbol)}&tf=${timeframe}`),
        fetch(`/api/setups?symbol=${encodeURIComponent(symbol)}&tf=${timeframe}`),
        fetch("/api/live"),
        fetch("/api/trades"),
        fetch("/api/auto"),
      ]);
      const market = (await marketRes.json()) as { bars: MarketBars; watch: MarketWatchSnapshot };
      const setups = (await setupsRes.json()) as Setups | null;
      const live = (await liveRes.json()) as LiveStatus;
      const journal = (await tradesRes.json()) as Trades | null;
      const autoPayload = (await autoRes.json()) as { status: AutoStatus };
      setData({ bars: market.bars, watch: market.watch, setups, journal, live, auto: autoPayload.status });
      if (market.bars.available && market.bars.bars.length >= 2) {
        // The watcher's own reading is authoritative for this market; the artifact-based
        // guess only fills in when the watcher has no reading (server clock unreadable).
        const measured = live.symbols?.[symbol]?.offsetS ?? live.marketOffsetS ?? null;
        if (measured !== null) {
          setBarClockOffsetS(measured);
        } else {
          const interval = Math.max(0, market.bars.bars[1].time - market.bars.bars[0].time);
          if (interval > 0) {
            const utcBucket = Math.floor(Date.now() / 1000 / interval) * interval;
            const offset = market.bars.bars[market.bars.bars.length - 1].time - utcBucket;
            // A whole day off means the export is stale or the clocks disagree in a way an
            // arithmetic guess must not paper over: draw nothing rather than guess.
            setBarClockOffsetS(Math.abs(offset) > 2 * 86_400 ? 0 : offset);
          }
        }
      }
      // The dock shows decisions across recommendations — the watcher rolls the
      // recommendation id every cycle, but the trader's decisions history does not.
      const decisionsRes = await fetch("/api/decisions");
      if (decisionsRes.ok) {
        const payload = (await decisionsRes.json()) as {
          decisions: (DecisionRow & { recId?: number })[];
        };
        setDecisions(payload.decisions);
      }
    } catch {
      /* keep the last payload on a failed tick; the staleness chip reports it */
    }
  }, [symbol, timeframe]);

  // One visibility-aware poller for the whole workspace.
  useVisiblePoll(refresh, POLL_MS);

  // Switching market or frame re-reads immediately; scheduled as a task so the effect
  // body itself never sets state (the fetch resolves asynchronously anyway).
  useEffect(() => {
    const id = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(id);
  }, [refresh]);

  // Terminal keyboard shortcuts (master plan §7.3): frames 1-3, f = fullscreen,
  // b = market search, Escape leaves fullscreen.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName)) return;
      if (event.key === "1") setTimeframe("H1");
      else if (event.key === "2") setTimeframe("M30");
      else if (event.key === "3") setTimeframe("M15");
      else if (event.key === "f") setFullscreen((value) => !value);
      else if (event.key === "b") {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (event.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const quote = data.watch.quotes[symbol];
  const digits = data.bars.digits ?? quote?.digits ?? null;
  const openSetups = (data.setups?.setups ?? []).filter((row) => row.status === "open");
  const activeSetup: SetupRow | null =
    openSetups.find((row) => row.family === selectedFamily) ?? openSetups[0] ?? null;
  const validationRow = data.auto.registry?.find((row) => row.family === activeSetup?.family) ?? null;

  const chartMarkers: ChartMarker[] = useMemo(() => {
    const markers: ChartMarker[] = [];
    for (const row of data.setups?.setups ?? []) {
      if (row.signalEpoch === null || row.side === null) continue;
      if (row.status !== "open" && row.status !== "target" && row.status !== "stop") continue;
      markers.push({
        time: row.signalEpoch,
        side: row.side,
        text: `${row.family.replace(/_/g, " ")} ${row.side}`,
        kind: "signal",
      });
    }
    for (const row of data.journal?.trades ?? []) {
      if (row.status !== "open" || row.openedUtc === null || row.side === null) continue;
      if (row.symbol !== symbol) continue;
      const epoch = Date.parse(`${row.openedUtc.replace(" ", "T")}Z`) / 1000;
      if (Number.isNaN(epoch)) continue;
      markers.push({
        // UTC trade time → the broker bar clock the chart's axis is stamped in, so the
        // arrow sits on the candle the trade was actually taken in.
        time: Math.round(epoch) + barClockOffsetS,
        side: row.side,
        text: `#${row.ticket} ${row.side}`,
        kind: "position",
      });
    }
    return markers;
  }, [data.setups, data.journal, symbol, barClockOffsetS]);

  const chartLines: ChartPriceLine[] = useMemo(() => {
    const lines: ChartPriceLine[] = [];
    if (quote?.last !== null && quote?.last !== undefined) {
      lines.push({ price: quote.last, title: "last", color: "#2DD4BF" });
    } else if (data.bars.bars.length > 0) {
      lines.push({
        price: data.bars.bars[data.bars.bars.length - 1].close,
        title: "close",
        color: "#2DD4BF",
      });
    }
    if (activeSetup) {
      if (activeSetup.entry !== null) lines.push({ price: activeSetup.entry, title: "entry", color: "#FBBF24" });
      if (activeSetup.sl !== null) lines.push({ price: activeSetup.sl, title: "SL", color: "#FB7185" });
      if (activeSetup.tp !== null) lines.push({ price: activeSetup.tp, title: "TP", color: "#34D399" });
    }
    return lines;
  }, [quote, data.bars, activeSetup]);

  const watchSymbols = useMemo(() => {
    const names = [...symbols];
    for (const name of Object.keys(data.watch.quotes)) {
      if (!names.includes(name)) names.push(name);
    }
    const filtered = names.filter((name) => name.toLowerCase().includes(query.trim().toLowerCase()));
    return filtered.sort((a, b) => Number(!!favourites[b]) - Number(!!favourites[a]) || a.localeCompare(b));
  }, [symbols, data.watch.quotes, query, favourites]);

  const lastBar = data.bars.bars[data.bars.bars.length - 1] ?? null;
  const previousBar = data.bars.bars[data.bars.bars.length - 2] ?? null;

  const recordDecision = async (decision: "accepted" | "rejected") => {
    if (recId === null) return;
    setFeedback(null);
    try {
      const res = await fetch("/api/decisions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recId, decision, note: note.trim() || null }),
      });
      if (res.ok) {
        const recentRes = await fetch("/api/decisions");
        if (recentRes.ok) {
          const payload = (await recentRes.json()) as {
            decisions: (DecisionRow & { recId?: number })[];
          };
          setDecisions(payload.decisions);
        }
        setNote("");
        setFeedback(
          decision === "accepted"
            ? "Recorded as accepted in the decision journal. No order is placed."
            : "Recorded as rejected in the decision journal.",
        );
      } else {
        const payload = (await res.json()) as { error?: string };
        setFeedback(payload.error ?? "Could not record the decision.");
      }
    } catch {
      setFeedback("Could not reach the decision journal.");
    }
  };

  return (
    <div className="terminal">
      {/* ── Top terminal toolbar ─────────────────────────────────────────────── */}
      <div className="terminal-toolbar" role="toolbar" aria-label="Terminal controls">
        <div className="tool-group">
          <label className="sr-only" htmlFor="terminal-symbol">
            Active symbol
          </label>
          <select
            id="terminal-symbol"
            className="tool-btn"
            value={symbol}
            onChange={(event) => {
              setSymbol(event.target.value);
              setSelectedFamily(null);
            }}
          >
            {[...new Set([...symbols, ...Object.keys(data.watch.quotes)])].map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>

        <div className="tool-sep" role="presentation" />

        <div className="tool-group" role="group" aria-label="Timeframe">
          {TIMEFRAME_ORDER.map((tf) => (
            <button
              key={tf}
              type="button"
              className="tool-btn"
              aria-pressed={timeframe === tf}
              onClick={() => setTimeframe(tf)}
              title={`Timeframe ${tf} (key ${TIMEFRAME_ORDER.indexOf(tf) + 1})`}
            >
              {tf}
            </button>
          ))}
        </div>

        <div className="tool-sep" role="presentation" />

        <div className="tool-group" role="group" aria-label="Chart style">
          <button
            type="button"
            className="tool-btn"
            aria-pressed={layout.chartStyle === "candles"}
            onClick={() => saveLayout({ chartStyle: "candles" })}
            title="Candlesticks"
          >
            <ChartCandlestick size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="tool-btn"
            aria-pressed={layout.chartStyle === "line"}
            onClick={() => saveLayout({ chartStyle: "line" })}
            title="Line"
          >
            <CandlestickChart size={15} aria-hidden="true" />
          </button>
        </div>

        <div className="tool-sep" role="presentation" />

        <div className="tool-group">
          <button
            type="button"
            className="tool-btn"
            aria-pressed={layout.leftCollapsed}
            onClick={() => saveLayout({ leftCollapsed: !layout.leftCollapsed })}
            title="Collapse or restore Market Watch"
          >
            <ChevronsLeft size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="tool-btn"
            aria-pressed={layout.rightCollapsed}
            onClick={() => saveLayout({ rightCollapsed: !layout.rightCollapsed })}
            title="Collapse or restore the intelligence panel"
          >
            <ChevronsRight size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="tool-btn"
            aria-pressed={fullscreen}
            onClick={() => setFullscreen((value) => !value)}
            title="Full-screen chart (key f)"
          >
            {fullscreen ? <Minimize2 size={15} aria-hidden="true" /> : <Maximize2 size={15} aria-hidden="true" />}
          </button>
          <button
            type="button"
            className="tool-btn"
            onClick={() => {
              try {
                window.localStorage.removeItem(LAYOUT_KEY);
                window.localStorage.removeItem(FAVOURITES_KEY);
              } catch {
                /* non-fatal */
              }
              setLayout(DEFAULT_LAYOUT);
              setFavourites({});
            }}
            title="Reset workspace layout"
          >
            <RotateCcw size={15} aria-hidden="true" />
            Reset
          </button>
        </div>

        <div className="topbar-status" style={{ marginLeft: "auto" }}>
          <span className="chip">
            <span
              className={`status-dot ${data.live.watcherAlive ? (data.live.connected ? "ok" : "warn") : "bad"}`}
              aria-hidden="true"
            />
            {data.live.watcherAlive ? (data.live.connected ? "Live" : "MT5 offline") : "Watcher down"}
          </span>
          <span className={`chip ${data.bars.ageS !== null && data.bars.ageS > 300 ? "chip-demo" : ""}`}>
            bars {data.bars.ageS !== null ? `${data.bars.ageS} s ago` : "unavailable"}
          </span>
        </div>
      </div>

      {/* ── Workspace: market · chart · intelligence ─────────────────────────── */}
      <div
        className="terminal-body"
        data-left={layout.leftCollapsed ? "collapsed" : "open"}
        data-right={layout.rightCollapsed ? "collapsed" : "open"}
      >
        {/* Left: Market Watch */}
        <section className="terminal-panel terminal-market" aria-label="Market Watch">
          <div className="terminal-panel-head">
            <Search size={13} aria-hidden="true" />
            <label className="sr-only" htmlFor="watch-search">
              Search instruments
            </label>
            <input
              id="watch-search"
              ref={searchRef}
              type="search"
              placeholder="Search instruments (b)"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              style={{
                flex: 1,
                background: "transparent",
                border: "0",
                color: "var(--text-primary)",
                font: "inherit",
                textTransform: "none",
                letterSpacing: 0,
              }}
            />
          </div>
          <div className="terminal-panel-body">
            {watchSymbols.length === 0 ? (
              <p className="muted" style={{ padding: "0.875rem" }}>
                No instruments match.
              </p>
            ) : (
              watchSymbols.map((name) => {
                const row = data.watch.quotes[name];
                return (
                  <div key={name} style={{ display: "flex", alignItems: "center" }}>
                    <button
                      type="button"
                      className="watch-row"
                      aria-current={name === symbol ? "true" : undefined}
                      onClick={() => {
                        setSymbol(name);
                        setSelectedFamily(null);
                      }}
                    >
                      <span className="watch-symbol">{name}</span>
                      <span className="watch-price">
                        {row?.available
                          ? `${fmtPrice(row.bid, row.digits)} / ${fmtPrice(row.ask, row.digits)}`
                          : "unavailable"}
                      </span>
                      <span className="watch-spread">
                        {row?.spreadPips !== null && row?.spreadPips !== undefined
                          ? `${row.spreadPips.toFixed(1)}p`
                          : "—"}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="tool-btn"
                      aria-label={`${favourites[name] ? "Unfavourite" : "Favourite"} ${name}`}
                      onClick={() => toggleFavourite(name)}
                    >
                      <Star
                        size={13}
                        aria-hidden="true"
                        fill={favourites[name] ? "#FBBF24" : "transparent"}
                        color={favourites[name] ? "#FBBF24" : "currentColor"}
                      />
                    </button>
                  </div>
                );
              })
            )}
            <p className="muted" style={{ padding: "0.75rem 0.875rem", fontSize: "0.6875rem" }}>
              Bid/ask are real ticks from the MT5 export ({data.watch.ageS !== null ? `${data.watch.ageS} s ago` : "unavailable"}).
            </p>
          </div>
        </section>

        {/* Centre: chart */}
        <section className="terminal-panel terminal-chart-panel" aria-label="Chart">
          <div className="terminal-panel-head">
            <strong style={{ color: "var(--text-primary)" }}>
              {symbol} · {timeframe}
            </strong>
            <span style={{ textTransform: "none", letterSpacing: 0 }}>
              {lastBar
                ? `O ${fmtPrice(lastBar.open, digits)} H ${fmtPrice(lastBar.high, digits)} L ${fmtPrice(lastBar.low, digits)} C ${fmtPrice(lastBar.close, digits)}`
                : "no bars"}
            </span>
            {lastBar && previousBar ? (
              <span
                className={`pill ${lastBar.close >= previousBar.close ? "pill-pass" : "pill-fail"}`}
                style={{ marginLeft: "auto" }}
              >
                {fmtSigned(((lastBar.close - previousBar.close) / (previousBar.close || 1)) * 100, "%")}
              </span>
            ) : null}
          </div>
          <div className={`chart-host${fullscreen ? " is-fullscreen" : ""}`}>
            {data.bars.available ? (
              <TerminalChart
                bars={data.bars.bars}
                digits={digits}
                style={layout.chartStyle}
                markers={chartMarkers}
                priceLines={chartLines}
              />
            ) : (
              <div className="chart-empty">
                <div>
                  <p>
                    <strong>No real bars available for {symbol} {timeframe}.</strong>
                  </p>
                  <p className="muted">{data.bars.reason ?? "The market export is missing."}</p>
                  <p className="muted">
                    Real candles are exported from the MT5 terminal with{" "}
                    <code>python -m mt5_intel.market_export</code>. Nothing here is simulated.
                  </p>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Right: intelligence panel */}
        <section className="terminal-panel terminal-intel" aria-label="Trading intelligence">
          <div className="terminal-panel-head">Intelligence · {symbol}</div>
          <div className="terminal-panel-body">
            {!activeSetup ? (
              <div className="intel-block">
                <p className="muted">
                  No open signal on this market&rsquo;s card view right now. The panel fills in
                  when the watcher reports one.
                </p>
              </div>
            ) : (
              <>
                <div className="intel-block">
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <span className={`pill ${activeSetup.side === "buy" ? "pill-pass" : "pill-fail"}`}>
                      {activeSetup.side?.toUpperCase() ?? "—"}
                    </span>
                    <strong>{strategyName(activeSetup.family)}</strong>
                  </div>
                  <p className="muted" style={{ margin: "0.5rem 0 0", fontSize: "0.75rem" }}>
                    Opportunity {activeSetup.signalUtc ?? "—"} · {activeSetup.barsSinceSignal ?? "—"} bars
                    ago · status {activeSetup.status}
                  </p>
                  <dl className="intel-prices">
                    <div>
                      <dt>Entry</dt>
                      <dd>{fmtPrice(activeSetup.entry, digits)}</dd>
                    </div>
                    <div>
                      <dt>Stop loss</dt>
                      <dd>{fmtPrice(activeSetup.sl, digits)}</dd>
                    </div>
                    <div>
                      <dt>Take profit</dt>
                      <dd>{fmtPrice(activeSetup.tp, digits)}</dd>
                    </div>
                  </dl>
                  <dl className="risk-list">
                    <div>
                      <dt>Risk : reward</dt>
                      <dd className="num">
                        {activeSetup.tpR !== null ? `1 : ${activeSetup.tpR.toFixed(1)}` : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>Stop size</dt>
                      <dd className="num">
                        {activeSetup.stopPips !== null ? `${activeSetup.stopPips.toFixed(1)} pips` : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>Open R</dt>
                      <dd className={`num ${activeSetup.rNow != null && activeSetup.rNow >= 0 ? "pnl-up" : "pnl-down"}`}>
                        {fmtSigned(activeSetup.rNow, "R")}
                      </dd>
                    </div>
                    <div>
                      <dt>Validation</dt>
                      <dd>
                        {validationRow ? (
                          <span className={`pill ${validationRow.status.includes("validated") ? "pill-pass" : "pill-warn"}`}>
                            {validationRow.status}
                          </span>
                        ) : (
                          <span className="muted">no research row</span>
                        )}
                      </dd>
                    </div>
                  </dl>
                </div>

                {/* Risk warnings: the gate's own reasons and the account's risk state. */}
                <div className="intel-block">
                  <p className="section-label">Risk</p>
                  <ul className="activity-list">
                    {(data.live.gateReasons ?? []).length > 0 ? (
                      data.live.gateReasons!.map((reason) => (
                        <li key={reason}>
                          <span className="pill pill-warn">{reason}</span>
                        </li>
                      ))
                    ) : (
                      <li className="muted">No gate warnings on the current cycle.</li>
                    )}
                    <li>
                      <span className="muted">
                        Gate verdict <strong>{data.live.gateVerdict ?? "—"}</strong> · daily loss{" "}
                        {data.auto.market?.dailyLossPct != null
                          ? `${data.auto.market.dailyLossPct.toFixed(2)}% of 3%`
                          : "unavailable"}{" "}
                        · open {data.auto.market?.openPositions ?? data.journal?.openCount ?? "—"}/
                        {data.auto.settings?.maxPositions ?? "—"} positions
                      </span>
                    </li>
                  </ul>
                </div>

                {/* Manual decision controls — journal only, never an order. */}
                <div className="intel-block">
                  <p className="section-label">Manual decision</p>
                  <label className="sr-only" htmlFor="decision-note">
                    Note
                  </label>
                  <input
                    id="decision-note"
                    type="text"
                    placeholder="Note (optional)"
                    value={note}
                    maxLength={500}
                    onChange={(event) => setNote(event.target.value)}
                    style={{
                      width: "100%",
                      marginBottom: "0.5rem",
                      padding: "0.4375rem 0.625rem",
                      borderRadius: "10px",
                      border: "1px solid var(--border-strong)",
                      background: "rgba(255,255,255,0.04)",
                      color: "var(--text-primary)",
                      font: "inherit",
                    }}
                  />
                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={recId === null}
                      onClick={() => void recordDecision("accepted")}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="btn"
                      disabled={recId === null}
                      onClick={() => void recordDecision("rejected")}
                    >
                      Reject
                    </button>
                  </div>
                  <p className="muted" style={{ margin: "0.5rem 0 0", fontSize: "0.6875rem" }}>
                    {recId === null
                      ? "No recommendation id on this cycle yet, so there is nothing to record against."
                      : "Records your decision in the journal (recommendation #" +
                        recId +
                        "). Approval never places an order — execution is manual."}
                  </p>
                  {feedback ? (
                    <p style={{ margin: "0.5rem 0 0", fontSize: "0.75rem", color: "var(--accent-highlight)" }}>
                      {feedback}
                    </p>
                  ) : null}
                </div>
              </>
            )}
          </div>
        </section>
      </div>

      {/* ── Bottom activity dock ────────────────────────────────────────────── */}
      <section className="terminal-dock" aria-label="Activity">
        <div className="dock-tabs" role="tablist" aria-label="Activity views">
          {(
            [
              ["positions", "Open positions"],
              ["history", "Trading history"],
              ["decisions", "Decisions"],
              ["system", "System"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              className="dock-tab"
              aria-selected={dockTab === id}
              onClick={() => setDockTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="dock-body">
          {dockTab === "positions" ? (
            <TradeTable rows={(data.journal?.trades ?? []).filter((row) => row.status === "open")} digits={digits} currency={data.journal?.currency ?? null} empty="No open positions." />
          ) : dockTab === "history" ? (
            <TradeTable
              rows={(data.journal?.trades ?? []).filter((row) => row.status === "closed").slice(0, 50)}
              digits={digits}
              currency={data.journal?.currency ?? null}
              empty="No closed trades in the journal's window."
            />
          ) : dockTab === "decisions" ? (
            decisions.length === 0 ? (
              <p className="muted" style={{ padding: "0.875rem" }}>
                No decisions recorded against the current recommendation yet.
              </p>
            ) : (
              <div className="table-wrap" style={{ border: 0, borderRadius: 0, background: "transparent" }}>
                <table>
                  <thead>
                    <tr>
                      <th scope="col">When (UTC)</th>
                      <th scope="col">Recommendation</th>
                      <th scope="col">Decision</th>
                      <th scope="col">Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decisions.map((row, index) => (
                      <tr key={`${row.ts}-${index}`}>
                        <td className="num" style={{ textAlign: "left" }}>
                          {row.ts}
                        </td>
                        <td className="num" style={{ textAlign: "left" }}>
                          {row.recId != null ? `#${row.recId}` : "—"}
                        </td>
                        <td>
                          <span className={`pill ${row.decision === "accepted" ? "pill-pass" : "pill-fail"}`}>
                            {row.decision}
                          </span>
                        </td>
                        <td>{row.note ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <dl className="risk-list" style={{ padding: "0.875rem" }}>
              <div>
                <dt>Watcher heartbeat</dt>
                <dd className="num">{data.live.ts ?? "—"}</dd>
              </div>
              <div>
                <dt>Mode</dt>
                <dd className="num">{data.live.mode ?? "—"}</dd>
              </div>
              <div>
                <dt>Market</dt>
                <dd className="num">{data.live.marketState ?? "—"}</dd>
              </div>
              <div>
                <dt>Auto-trading</dt>
                <dd className="num">
                  {data.auto.available
                    ? data.auto.switch?.enabled
                      ? "ARMED"
                      : "off"
                    : "unavailable"}
                </dd>
              </div>
              {data.auto.errors.length > 0 ? (
                <div>
                  <dt>Errors</dt>
                  <dd>{data.auto.errors.join(" · ")}</dd>
                </div>
              ) : null}
            </dl>
          )}
        </div>
      </section>
    </div>
  );
}

function TradeTable({
  rows,
  digits,
  currency,
  empty,
}: {
  rows: TradeRow[];
  digits: number | null;
  currency: string | null;
  empty: string;
}) {
  if (rows.length === 0) {
    return (
      <p className="muted" style={{ padding: "0.875rem" }}>
        {empty}
      </p>
    );
  }
  return (
    <div className="table-wrap" style={{ border: 0, borderRadius: 0, background: "transparent" }}>
      <table>
        <thead>
          <tr>
            <th scope="col">Ticket</th>
            <th scope="col">Symbol</th>
            <th scope="col">Side</th>
            <th scope="col" className="num">
              Volume
            </th>
            <th scope="col" className="num">
              Entry
            </th>
            <th scope="col" className="num">
              SL
            </th>
            <th scope="col" className="num">
              TP
            </th>
            <th scope="col" className="num">
              Now
            </th>
            <th scope="col" className="num">
              Net ({currency ?? ""})
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.ticket}>
              <th scope="row" className="num" style={{ textAlign: "left" }}>
                {row.ticket}
              </th>
              <td>{row.symbol}</td>
              <td>
                <span className={`pill ${row.side === "buy" ? "pill-pass" : "pill-fail"}`}>{row.side ?? "—"}</span>
              </td>
              <td className="num">{row.volume.toFixed(2)}</td>
              <td className="num">{fmtPrice(row.entry, row.digits ?? digits)}</td>
              <td className="num">{fmtPrice(row.sl, row.digits ?? digits)}</td>
              <td className="num">{fmtPrice(row.tp, row.digits ?? digits)}</td>
              <td className="num">{fmtPrice(row.priceNow, row.digits ?? digits)}</td>
              <td className={`num ${row.net >= 0 ? "pnl-up" : "pnl-down"}`}>{fmtSigned(row.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
