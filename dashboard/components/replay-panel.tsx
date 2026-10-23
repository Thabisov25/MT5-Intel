"use client";

import { useCallback, useEffect, useState } from "react";
import { PanelHead } from "@/components/info";
import { ReplayChart } from "@/components/replay-chart";
// The client-safe module: `@/lib/replay` reads disk (`node:fs`) and cannot be bundled
// into a client component's chunk — the same split the freqtrade panel's types follow.
import { REPLAY_STALE_S, isReplaySymbol, type FamilyReplay } from "@/lib/replay-constants";

/**
 * How this family actually traded, drawn on real bars: every graded trade as a box from
 * entry to the barrier that resolved it (green TP, red stop, amber time-exit).
 *
 * The replay is cached next to the other artifacts and refreshed automatically when it is
 * older than the same window the freqtrade panel uses (6 h), so opening the page costs one
 * MT5 read at most - and usually none. The button re-runs it on demand: bars re-read from
 * the terminal, this family re-graded over them, artifact rewritten.
 *
 * The numbers under the chart are the artifact's own summary, not recomputed here: same
 * trades, same R, same costs as the Live tab and the research reports.
 */

const r = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(3)}R`;

function fmtAge(ageS: number | null): string {
  if (ageS === null) return "unknown age";
  if (ageS < 90) return `${ageS}s ago`;
  if (ageS < 5400) return `${Math.round(ageS / 60)}m ago`;
  return `${(ageS / 3600).toFixed(1)}h ago`;
}

export function ReplayPanel({
  family,
  digits,
  symbol = "USDJPY",
}: {
  family: string;
  digits: number | null;
  /** The market to replay. Chosen by the page's own symbol filter, which lives in the URL. */
  symbol?: string;
}) {
  // Guarded rather than trusted: the prop comes off the URL, and an unrecognised market would
  // ask the API for an artifact that cannot exist.
  const market = isReplaySymbol(symbol) ? symbol : "USDJPY";
  const [replay, setReplay] = useState<FamilyReplay | null>(null);
  const [ageS, setAgeS] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/replay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ family, symbol: market }),
      });
      const payload = (await res.json().catch(() => null)) as
        | { replay?: FamilyReplay | null; ageS?: number | null; error?: string }
        | null;
      if (!res.ok || !payload?.replay) {
        setError(payload?.error ?? `the console answered ${res.status}`);
        return;
      }
      setReplay(payload.replay);
      setAgeS(payload.ageS ?? 0);
    } catch {
      setError("the replay could not be started");
    } finally {
      setBusy(false);
    }
  }, [family, market]);

  // Cached first, refreshed only when stale. No mount-once ref: StrictMode and Fast
  // Refresh both re-run this effect after cancelling the first, and a ref that survives
  // the re-run would leave the panel stuck on its empty state forever. A duplicate GET
  // in dev is the price; production runs it once.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // The market is part of the key: switching tabs must read the other market's artifact,
        // and a stale chart of the previous market left on screen would be a lie about which
        // one is being looked at.
        setReplay(null);
        const res = await fetch(
          `/api/replay?family=${encodeURIComponent(family)}&symbol=${encodeURIComponent(market)}`,
          { cache: "no-store" },
        );
        const payload = (await res.json().catch(() => null)) as
          | { replay: FamilyReplay | null; ageS: number | null }
          | null;
        if (cancelled) return;
        if (payload?.replay) {
          setReplay(payload.replay);
          setAgeS(payload.ageS);
          if ((payload.ageS === null || payload.ageS > REPLAY_STALE_S) && !cancelled) await run();
        } else if (!cancelled) {
          // Nothing cached yet: the first visit builds its own replay.
          await run();
        }
      } catch {
        if (!cancelled) setError("the cached replay could not be read");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [family, market, run]);

  const summary = replay?.summary;
  return (
    <section className="panel" aria-labelledby="replay-heading">
      <PanelHead
        id="replay-heading"
        title="Replay on real bars"
        aside={
          <span className="muted">
            {replay
              ? `${replay.symbol} · ${replay.bars_counted} ${replay.timeframe} bars · graded ${fmtAge(ageS)}`
              : busy
                ? `replaying ${market}…`
                : `no replay of ${market} yet`}
          </span>
        }
        about={
          <p>
            Every trade this family&rsquo;s rule would have taken over the window, drawn on the
            bars it was graded against: <span style={{ color: "#10B981" }}>green</span> runs
            entry to target, <span style={{ color: "#FB7185" }}>red</span> entry to stop,{" "}
            <span style={{ color: "#FBBF24" }}>amber</span> a time exit or a trade still
            running. Entries are the next bar&rsquo;s open, stops first on a double touch, and
            R is net of the spread — the same grading the Live tab and the research tables
            use, never a second opinion. Cached like the other artifacts and re-read from the
            terminal when older than six hours.
          </p>
        }
      />
      {error !== null ? (
        <p className="pnl-down" role="alert">
          {error}
        </p>
      ) : null}
      {summary ? (
        <dl className="risk-list" style={{ padding: "0 0 0.5rem" }}>
          <div>
            <dt>Trades</dt>
            <dd className="num">
              {summary.n}
              {summary.open > 0 ? ` (${summary.open} still open)` : ""}
            </dd>
          </div>
          <div>
            <dt>Hit target</dt>
            <dd className="num pnl-up">{summary.tp}</dd>
          </div>
          <div>
            <dt>Hit stop</dt>
            <dd className="num pnl-down">{summary.sl}</dd>
          </div>
          <div>
            <dt>Time exit</dt>
            <dd className="num">{summary.time}</dd>
          </div>
          <div>
            <dt>Mean / total</dt>
            <dd className={`num ${(summary.net_r_mean ?? 0) >= 0 ? "pnl-up" : "pnl-down"}`}>
              {r(summary.net_r_mean)} / {r(summary.net_r_sum)}
            </dd>
          </div>
        </dl>
      ) : null}
      <div className="chart-host" style={{ minHeight: 360, height: 420 }}>
        {replay && replay.bars.length > 0 ? (
          <ReplayChart replay={replay} digits={digits} />
        ) : (
          <div className="chart-empty">
            <div>
              <p>
                <strong>{busy ? "Replaying this family…" : "No replay for this family yet."}</strong>
              </p>
              <p className="muted">
                {busy
                  ? "Reading bars from the terminal and grading every historical signal."
                  : "Run it to read the last bars from the terminal and grade this family's rule against them."}
              </p>
            </div>
          </div>
        )}
      </div>
      <p style={{ padding: "0.5rem 0.875rem 0.875rem" }}>
        <button
          type="button"
          className="pill pill-info cap-btn"
          disabled={busy}
          onClick={() => void run()}
        >
          {busy ? "replaying…" : replay ? "re-run replay" : "run replay"}
        </button>
      </p>
    </section>
  );
}
