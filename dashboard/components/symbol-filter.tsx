import Link from "next/link";

/**
 * The symbol filter, rendered on the page it filters.
 *
 * The choice is still the URL's (`?symbol=`), so a page that says it is about EURUSD is actually
 * about EURUSD - and a filtered view can be bookmarked or passed on, because the URL carries what
 * it shows. The labels are links rather than a stateful control: everything under the strip is
 * server-rendered already, so a move is one re-render, not client plumbing to keep in step.
 *
 * This is a page's own control, not header navigation: the header picker was removed when this
 * arrived on the pages, because a filter that lives in the chrome was the one place a reader
 * would not look when comparing two markets. `current` is the URL's own value; `null` means no
 * choice was made, which the Live and Trades pages read as their default (the primary market).
 */
export function SymbolFilter({
  basePath,
  current,
  symbols,
  allowAll = false,
  label,
}: {
  /** The page's own path, so the links swap only the symbol part of the query. */
  basePath: string;
  /** The URL's `?symbol=` value, normalized, or null when none was given. */
  current: string | null;
  /** The tabs to offer, already ordered; entries are symbol names or ALL. */
  symbols: string[];
  /** Whether the whole-account answer is offered (Trades only; a card page shows one market). */
  allowAll?: boolean;
  /** What the strip filters, read before the tabs ("Filter by symbol:"). */
  label: string;
}) {
  const hrefFor = (symbol: string | null) =>
    symbol === null
      ? basePath
      : `${basePath}?symbol=${encodeURIComponent(symbol)}`;
  // A page's no-choice state is what its default shows: on Trades that is the account-wide
  // journal (the ALL tab), on Live it is the primary market (the plain basePath). Either way the
  // strip marks the tab the reader is actually looking at rather than leaving none marked.
  const currentTab = current === "" ? (allowAll ? "ALL" : "") : current;
  const tab = (symbol: string | null, name: string) => (
    <li key={name}>
      <Link
        href={hrefFor(symbol)}
        aria-current={currentTab === (symbol ?? "") ? "true" : undefined}
        data-current={currentTab === (symbol ?? "") || undefined}
      >
        {name}
      </Link>
    </li>
  );
  return (
    <nav aria-label="Symbol filter" className="symbol-filter">
      <p className="picker-label">{label}</p>
      <ul>
        {allowAll ? tab("ALL", "All") : tab(null, "Default")}
        {symbols.map((symbol) => tab(symbol, symbol))}
      </ul>
    </nav>
  );
}
