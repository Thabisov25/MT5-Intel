"use client";

import { Info } from "@/components/info";
import { FeedClock } from "@/components/live-feed";
import { useLiveFeed } from "@/components/live-feed-context";
import type { LiveStatus } from "@/lib/live";

const fmt = (value: number | null | undefined, digits = 1, unit = "") =>
  value === null || value === undefined ? "n/a" : `${value.toFixed(digits)}${unit}`;

// Shown while the watcher has never reported, or while its heartbeat is past its TTL. It is
// rendered by LivePoller, which keeps polling underneath, so the strip is replaced by the live view
// on its own. That matters at launch: the launcher starts the watcher only after the dashboard is
// already serving, so the first paint of this page is usually this state.
function WatcherDown({ status, ttl }: { status: LiveStatus; ttl: number }) {
  const reason =
    status.reason ??
    `The last heartbeat is ${status.heartbeatAgeS ?? "?"} s old, past its ${ttl} s TTL, so the watcher may have stopped.`;
  return (
    <section className="empty" aria-labelledby="watcher-heading" aria-live="polite">
      <h2 id="watcher-heading">Watcher not reporting</h2>
      <p>{reason}</p>
      <pre>
        <code>{`\\.venv\\Scripts\\python.exe -m mt5_intel.watch`}</code>
      </pre>
      <p className="muted">Start it from the mt5-intel folder. This panel keeps checking and fills in by itself.</p>
    </section>
  );
}

/**
 * The watch loop's own reading of itself: whether it is running, what the gate said, and the bar
 * and spread it is looking at.
 *
 * It polls nothing itself. The strip, the switch and the per-rule table are the same payload set
 * fetched on the same tick, so this panel can only ever show a heartbeat that the rest of the page
 * is looking at too - which is the thing that was wrong when each block kept its own timer.
 */
export function LivePoller() {
  const { live: status, symbol } = useLiveFeed();

  const ttl = status.heartbeatTtlS ?? 180;
  const down =
    !status.available ||
    typeof status.heartbeatAgeS !== "number" ||
    status.heartbeatAgeS > ttl + 60;

  // Poll before deciding what to show, so a tab opened during the watcher's first cycle recovers on
  // its own instead of sitting on the empty state until a manual reload.
  if (down) return <WatcherDown status={status} ttl={ttl} />;

  // A named symbol reads its own readings - the bar and spread its cycle saw, its own gate - from
  // the status's per-symbol map. The heartbeat stays the watcher's: one process is alive or it is
  // not, and no view of one market should imply a second process. With no map entry yet (the
  // symbol was just added, or this view predates it), the primary's readings are the fallback,
  // labelled honestly by what they are: the watcher's last cycle reading.
  const view = symbol ? (status.symbols?.[symbol] ?? null) : null;
  const noView = symbol !== null && (view === null || !view.available);
  // Readings are per symbol when a symbol is named: with no per-symbol reading yet, the strip
  // shows n/a rather than quietly quoting the primary market's spread or bar under EURUSD's name.
  const spread = symbol === null ? status.spreadPips : view?.spreadPips ?? null;
  const barAge = symbol === null ? status.barAgeS : view?.barAgeS ?? null;
  const idle = status.mode === "idle" || status.marketState === "closed";
  const verdict = (view && view.available ? view.gateVerdict : status.gateVerdict) || "unknown";
  const passed = verdict === "CARD";
  const gateReasons = view?.available ? (view.gateReasons ?? []) : status.gateReasons;

  return (
    <section aria-labelledby="live-heading">
      <h2 id="live-heading" className="sr-only">
        Watch loop
      </h2>
      {/* One strip for one question: is it running, is the market sane, and did the gate let
          anything through. These were three panels and a paragraph. */}
      <div className="verdict">
        <div>
          <p className="kicker">Watch loop</p>
          <h3 className="verdict-status">
            <span className={idle ? "pill pill-info" : "pill pill-pass"}>{idle ? "Idle" : "Live"}</span>
          </h3>
          <p className="muted">
            Updated {status.ts ?? "unknown"} · heartbeat {fmt(status.heartbeatAgeS ?? null, 0, " s")} ago ·{" "}
            <FeedClock />
          </p>
        </div>
        <dl className="counts">
          <div>
            <dt>MT5</dt>
            <dd>{status.connected ? "connected" : "disconnected"}</dd>
          </div>
          <div>
            <dt>Symbol</dt>
            <dd className="num">{symbol ?? status.symbol ?? "n/a"}</dd>
          </div>
          <div>
            <dt>Spread</dt>
            <dd className="num">{fmt(spread ?? null, 1, " pips")}</dd>
          </div>
          <div>
            <dt>Bar age</dt>
            <dd className="num">{fmt(barAge ?? null, 0, " s")}</dd>
          </div>
        </dl>
        <p className="gate-line">
          <span className="kicker">Gate</span>
          <span className={passed ? "pill pill-pass" : "pill pill-warn"}>{verdict}</span>
          <Info label="the recommendation gate">
            <p>
              Every cycle runs the same checks in order — data freshness, spread, session, news,
              circuit breaker, daily loss, position count, a validated strategy, and whether the risk
              fits — and records every reason code it found rather than stopping at the first.
            </p>
            <p>
              A code here is why the last cycle did not produce a card. <code>SKIP</code> is normal
              operation, not a fault: nothing is generated unless the whole list passes. Health breaks
              the same codes down by hour and session, so a code that only looks frequent because its
              hour is busy can be told apart from a real cluster.
            </p>
          </Info>          {noView ? (
            <span className="muted">
              this symbol gets its own card view but has not been gated in its own cycle yet
            </span>
          ) : gateReasons?.length ? (
            gateReasons.map((reason) => (
              <span className="tag" key={reason}>
                {reason}
              </span>
            ))
          ) : (
            <span className="muted">every check passed this cycle</span>
          )}
          <span className="muted">cycle {status.recId ?? "n/a"}</span>
        </p>
      </div>
    </section>
  );
}
