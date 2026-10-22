import Link from "next/link";

import { TIMEFRAMES } from "@/lib/setups";

/**
 * The timeframe strip for the Live page, beside the symbol strip it filters with.
 *
 * A frame's cards come from its own artifact (`setups_<SYMBOL>_<TF>.json`, written by the
 * watcher), so choosing a frame here is a change of source file, not a re-render of one view:
 * the URL carries `?tf=` the way it carries `?symbol=`, and a choice can be linked or shared.
 * H1 stays first and reads as the default: it is the frame the watcher trades on and the view
 * every other page counts on; M30 and M15 are labelled observation frames where they appear
 * (the panel below carries the marking). Tabs render as links, because everything under them is
 * server-rendered already and one re-render beats client plumbing.
 */
export function TimeframeFilter({
  basePath,
  current,
  symbol,
}: {
  basePath: string;
  /** The URL's `?tf=` value, normalized, or null when it was not given. */
  current: string | null;
  /** The symbol the strip sits under, so a frame choice keeps the market choice. */
  symbol: string | null;
}) {
  const hrefFor = (tf: string) => {
    const market = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    const frame = tf === "H1" ? "" : `${market ? "&" : "?"}tf=${tf.toLowerCase()}`;
    return `${basePath}${market}${frame}`;
  };
  const currentFrame = current === "" ? "H1" : current;
  return (
    <nav aria-label="Timeframe filter" className="timeframe-filter symbol-filter">
      <p className="picker-label">Timeframe:</p>
      <ul>
        {TIMEFRAMES.map((tf) => (
          <li key={tf}>
            <Link
              href={hrefFor(tf)}
              aria-current={currentFrame === tf ? "true" : undefined}
              data-current={currentFrame === tf || undefined}
            >
              {tf}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
