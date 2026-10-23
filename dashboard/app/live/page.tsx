import { AutoPanel } from "@/components/auto-panel";
import { ForecastPanel } from "@/components/forecast-panel";
import { ContextNav, WorkspaceFrame, tradingNavItems } from "@/components/shell/context-nav";
import { SymbolFilter } from "@/components/symbol-filter";
import { TimeframeFilter } from "@/components/timeframe-filter";
import { LiveFeedProvider } from "@/components/live-feed";
import { LivePoller } from "@/components/live-poller";
import { SetupsPanel } from "@/components/setups-panel";
import { readAuto, readSelection, readSwitch } from "@/lib/auto";
import { buildForecast } from "@/lib/forecast";
import { readLiveStatus } from "@/lib/live";
import { normalizeTimeframe, readSetups } from "@/lib/setups";
import { readTradesWithLabels } from "@/lib/trades";
import { listSymbols, normalizeSymbolParam, setupsFilter } from "@/lib/symbols";
import { filterTradesBySymbol } from "@/lib/trades";

// Reads the watcher status on every request so the page is never served from cache.
export const instant = false;

export const metadata = {
  title: "MT5 intel · live decisions",
};

/**
 * The trading intelligence workspace (master plan §11.1): a left context column
 * carrying the market and frame pickers and the watcher's own state, the decision
 * surface in the centre, and the ranked forecast on a right rail. Selection flows
 * through the URL (`?symbol=`, `?tf=`) so deep links keep working. The auto-trading
 * machinery stays folded below the daily view — arming is deliberate.
 */
export default async function LivePage({
  searchParams,
}: {
  searchParams: Promise<{ symbol?: string; tf?: string }>;
}) {
  // The `?symbol=` and `?tf=` choices are read here, once: everything below - the card view, the
  // forecast and the strip's readings - shows that one market's one frame, never a second symbol's
  // numbers beside it and never a faster frame under H1's authority.
  const params = await searchParams;
  const symbol = setupsFilter(normalizeSymbolParam(params.symbol));
  const timeframe = normalizeTimeframe(params.tf);
  // Reading the frame's own artifact: M30 and M15 come from `setups_<SYMBOL>_<TF>.json`, the
  // watched-symbol names the watcher writes them to.
  const [status, setups, auto, toggle, selection, journal, { watched }] = await Promise.all([
    readLiveStatus(),
    readSetups(symbol, timeframe),
    readAuto(),
    readSwitch(),
    readSelection(),
    readTradesWithLabels(),
    listSymbols(),
  ]);
  // The forecast scores a card against the same market's journal: a EURUSD card is never read
  // against an XAUUSD record. It is always the H1 card view it ranks, whatever frame the strip
  // below shows, because its ranking is over strategies the watcher can trade - and the polled
  // forecast reads the H1 artifact too, so the render and the tick stay consistent.
  const trades = filterTradesBySymbol(journal, symbol);
  const forecastSetups = timeframe === "H1" ? setups : await readSetups(symbol, "H1");

  return (
    <WorkspaceFrame nav={<ContextNav heading="Trading workspace" items={tradingNavItems("/live")} />}>
      {/* One provider for the workspace: its panels read one payload set, fetched on one tick. */}
      <LiveFeedProvider
        live={status}
        setups={setups}
        symbol={symbol}
        timeframe={timeframe}
        auto={{ status: auto, toggle, selection }}
        forecast={buildForecast(forecastSetups, trades)}
      >
        <LivePoller />
        <div className="live-grid">
          {/* Left: the page's own choices of market and frame, beside the state they filter. */}
          <aside className="panel live-context" aria-label="Market and watcher state">
            <p className="section-label">Market</p>
            <SymbolFilter
              basePath="/live"
              label="Market:"
              current={symbol ?? ""}
              symbols={[...watched]}
            />
            <TimeframeFilter basePath="/live" current={timeframe} symbol={symbol} />
            <p className="section-label">Watcher</p>
            <dl className="risk-list">
              <div>
                <dt>Connection</dt>
                <dd>
                  <span className={`pill ${status.connected ? "pill-pass" : "pill-warn"}`}>
                    {status.available ? (status.connected ? "MT5 linked" : "offline") : "not reporting"}
                  </span>
                </dd>
              </div>
              <div>
                <dt>Gate verdict</dt>
                <dd className="num">{status.gateVerdict ?? "—"}</dd>
              </div>
              <div>
                <dt>Market</dt>
                <dd className="num">{status.marketState ?? "—"}</dd>
              </div>
              <div>
                <dt>Spread</dt>
                <dd className="num">
                  {status.spreadPips != null ? `${status.spreadPips.toFixed(1)} pips` : "—"}
                </dd>
              </div>
              <div>
                <dt>Bar age</dt>
                <dd className="num">{status.barAgeS != null ? `${status.barAgeS} s` : "—"}</dd>
              </div>
            </dl>
          </aside>

          {/* Centre: the decision surface - every rule's current position. */}
          <div aria-label="Decision surface">
            <SetupsPanel />
          </div>

          {/* Right: the ranked forecast, kept out of the decision surface's way. */}
          <aside aria-label="Strategy forecast">
            <ForecastPanel />
          </aside>
        </div>

        {/* The machinery of auto-trading matters only while configuring or auditing it, so it is
            kept out of the daily view until asked for. */}
        <details className="panel auto-fold" style={{ marginTop: "1rem" }}>
          <summary>Auto-trading</summary>
          <AutoPanel />
        </details>
      </LiveFeedProvider>
    </WorkspaceFrame>
  );
}
