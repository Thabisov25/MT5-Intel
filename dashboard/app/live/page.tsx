import { AutoPanel } from "@/components/auto-panel";
import { ForecastPanel } from "@/components/forecast-panel";
import { SymbolFilter } from "@/components/symbol-filter";
import { TimeframeFilter } from "@/components/timeframe-filter";
import { PageHead } from "@/components/info";
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
  title: "MT5 intel · live watcher",
};

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
    <>
      <PageHead
        title="Live"
        about={
          <p>
            One card for the entry decision, then every rule&rsquo;s current position. Rebuilt every
            10 s from the watcher&rsquo;s files; per-strategy scorecards are on{" "}
            <a href="/trades">Trades</a>.
          </p>
        }
      />
      {/* The page's own choices of market and frame, beside the information they filter. The
          market strip is one tab per watched symbol; the frame strip switches which artifact the
          card view reads, with H1 first and the observation frames after it. */}
      <SymbolFilter
        basePath="/live"
        label="Market:"
        current={symbol ?? ""}
        symbols={[...watched]}
      />
      <TimeframeFilter basePath="/live" current={timeframe} symbol={symbol} />
      {/* One provider for the blocks below: they read one payload set, fetched on one tick. */}
      <LiveFeedProvider
        live={status}
        setups={setups}
        symbol={symbol}
        timeframe={timeframe}
        auto={{ status: auto, toggle, selection }}
        forecast={buildForecast(forecastSetups, trades)}
      >
        <LivePoller />
        <ForecastPanel />
        <SetupsPanel />
        {/* The machinery of auto-trading matters only while configuring or auditing it, so it is
            kept out of the daily view until asked for. */}
        <details className="panel auto-fold">
          <summary>Auto-trading</summary>
          <AutoPanel />
        </details>
      </LiveFeedProvider>
    </>
  );
}
