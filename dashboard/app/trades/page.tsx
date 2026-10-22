import { SymbolFilter } from "@/components/symbol-filter";
import { PageHead } from "@/components/info";
import { TradesPanel } from "@/components/trades-panel";
import { listSymbols, normalizeSymbolParam } from "@/lib/symbols";
import { filterTradesBySymbol, readTradesWithLabels } from "@/lib/trades";

// Reads the watcher's journal on every request so the page is never served from cache.
export const instant = false;

export const metadata = {
  title: "MT5 intel · trades",
};

export default async function TradesPage({
  searchParams,
}: {
  searchParams: Promise<{ symbol?: string }>;
}) {
  // The journal is account-wide - positions on symbols the watcher has never built a card view
  // for land in it too - so the tables are one symbol's slice when the `?symbol=` tab names one,
  // and "ALL" (the Trades-only tab) keeps the account view. The totals come recomputed with the
  // rows, so a EURUSD table's win rate is EURUSD's.
  const symbol = normalizeSymbolParam((await searchParams).symbol);
  const [journal, { watched, traded }] = await Promise.all([readTradesWithLabels(), listSymbols()]);
  const trades = filterTradesBySymbol(journal, symbol);
  // The tabs come from the journal's own symbols, watched first: a hand-taken XAUUSD position is
  // as real a row as an auto-traded USDJPY one, and both deserve a tab of their own.
  const tabs: string[] = [];
  for (const name of [...watched, ...traded]) {
    if (name && !tabs.includes(name)) tabs.push(name);
  }

  return (
    <>
      <PageHead
        title="Trades"
        about={
          <>
            <p>
              What the account actually did, read from the terminal: the positions still open and the
              trades already taken, each with the strategies that were live when it was first seen.
              Say which of them you were following and it stays with the trade.
            </p>
            <p>
              The watcher rebuilds this journal every minute while the market is open, so a closed
              trade can appear up to a minute after it closed. Trades are held by the broker, not by
              this console: nothing here is deleted, and clearing a strategy only clears which rule
              you said you followed.
            </p>
            <p>Nothing here places, changes or closes an order.</p>
          </>
        }
      />
      <SymbolFilter
        basePath="/trades"
        label="Filter by symbol:"
        current={`${symbol ?? ""}`}
        symbols={["ALL", ...tabs]}
        allowAll
      />
      <TradesPanel initial={trades} symbol={symbol} />
    </>
  );
}
