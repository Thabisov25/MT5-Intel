import { ContextNav, WorkspaceFrame, tradingNavItems } from "@/components/shell/context-nav";
import { SymbolFilter } from "@/components/symbol-filter";
import { SymbolCapControl } from "@/components/symbol-cap";
import { TradesPanel } from "@/components/trades-panel";
import { listSymbols, normalizeSymbolParam } from "@/lib/symbols";
import { filterTradesBySymbol, readTradesWithLabels } from "@/lib/trades";

// Reads the watcher's journal on every request so the page is never served from cache.
export const instant = false;

export const metadata = {
  title: "MT5 intel · trading journal",
};

/**
 * The journal workspace (master plan §11.2): a summary rail carrying the slice's
 * account numbers first, then the market filter and the page's one live control (the
 * auto position cap), then the journal itself. The journal is account-wide — positions
 * on symbols the watcher has never built a card view for land in it too — so the tables
 * are one symbol's slice when `?symbol=` names one and ALL keeps the account view.
 */
export default async function TradesPage({
  searchParams,
}: {
  searchParams: Promise<{ symbol?: string }>;
}) {
  const symbol = normalizeSymbolParam((await searchParams).symbol);
  const [journal, { watched, traded }] = await Promise.all([readTradesWithLabels(), listSymbols()]);
  const trades = filterTradesBySymbol(journal, symbol);
  // The tabs come from the journal's own symbols, watched first: a hand-taken XAUUSD position is
  // as real a row as an auto-traded USDJPY one, and both deserve a tab of its own.
  const tabs: string[] = [];
  for (const name of [...watched, ...traded]) {
    if (name && !tabs.includes(name)) tabs.push(name);
  }

  return (
    <WorkspaceFrame nav={<ContextNav heading="Trading workspace" items={tradingNavItems("/trades")} />}>
      {/* The summary rail: the slice's own numbers, recomputed with the rows, so a EURUSD
          table's win rate is EURUSD's. */}
      <section aria-label="Journal summary" style={{ marginBottom: "1rem" }}>
        <dl className="metric-strip">
          <div className={`metric ${trades && trades.openNet >= 0 ? "is-positive" : "is-negative"}`}>
            <dt>Open</dt>
            <dd>
              {trades ? `${trades.openNet >= 0 ? "+" : ""}${trades.openNet.toFixed(2)}` : "—"}
              <span className="metric-sub">
                {trades ? `${trades.openCount} positions · ${trades.currency ?? ""}` : "journal unavailable"}
              </span>
            </dd>
          </div>
          <div className={`metric ${trades && trades.closedNet >= 0 ? "is-positive" : "is-negative"}`}>
            <dt>Closed (last {trades?.lookbackDays ?? "—"} d)</dt>
            <dd>
              {trades ? `${trades.closedNet >= 0 ? "+" : ""}${trades.closedNet.toFixed(2)}` : "—"}
              <span className="metric-sub">{trades ? `${trades.closedCount} trades` : ""}</span>
            </dd>
          </div>
          <div className="metric">
            <dt>Win rate</dt>
            <dd>
              {trades && trades.winRate !== null ? `${Math.round(trades.winRate * 100)}%` : "—"}
              <span className="metric-sub">
                {trades ? `${trades.wins}W / ${trades.losses}L` : ""}
              </span>
            </dd>
          </div>
          <div className="metric">
            <dt>Total net</dt>
            <dd>
              {trades ? `${trades.totalNet >= 0 ? "+" : ""}${trades.totalNet.toFixed(2)}` : "—"}
              <span className="metric-sub">
                {trades?.currency ?? ""} · account {trades?.account ?? "—"}
              </span>
            </dd>
          </div>
        </dl>
      </section>

      {/* The market filter and this page's one live control on one control bar: whichever tab
          is showing is the market the cap stepper names, and ALL edits the file's own
          account-wide replacement for the .env cap. */}
      <div className="journal-controls">
        <SymbolFilter
          basePath="/trades"
          label="Filter by symbol:"
          current={`${symbol ?? ""}`}
          symbols={["ALL", ...tabs]}
          allowAll
        />
        <SymbolCapControl symbol={symbol ?? "ALL"} />
      </div>

      <TradesPanel initial={trades} symbol={symbol} />
    </WorkspaceFrame>
  );
}
