"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PanelHead } from "@/components/info";
import { strategyName } from "@/lib/strategies";
import type { FreqtradeArtifact, FreqtradeRun } from "@/lib/freqtrade";

/**
 * Run freqtrade backtests from the console and read the results where the rest of the
 * evidence lives.
 *
 * Each row is one family of the live registry - the same rules the cards and research
 * tables are graded by, imported (not copied) by the freqtrade adapter. A run is the whole
 * pipeline: bars re-exported from MT5 when stale, converted, backtested in the freqtrade
 * venv. It takes seconds to a few minutes depending on how fresh the bars are, so the row
 * says "running…" until the API answers; nothing here is a queue, one run at a time.
 *
 * The numbers are pre-spread (freqtrade charges no cost in this offline setup; the real
 * cost is the spread, which the research pages charge in R) and long-side only - the
 * short side stays with the research replays until an exchange freqtrade supports offers
 * these pairs with shorts.
 *
 * A family's name links to its own page, so clicking a row is how you ask "what is this rule
 * and how did it actually play out" - the replay chart, the settings and the rule's identity
 * all live there rather than in a dossier of this table's own.
 */

const FAMILIES = [
  "sweep_reclaim",
  "carry_trend",
  "compression_breakout",
  "trend_pullback",
  "range_extreme_mr",
  "daily_momentum",
  "ma_crossover",
  "rsi_mean_reversion",
] as const;

const pct = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
const r = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(3)}R`;

export function FreqtradePanel() {
  const [artifact, setArtifact] = useState<FreqtradeArtifact>({ runs: {} });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/freqtrade", { cache: "no-store" })
      .then((res) => (res.ok ? (res.json() as Promise<FreqtradeArtifact>) : null))
      .then((next) => {
        if (next) setArtifact(next);
      })
      .catch(() => {
        /* the panel keeps its empty state; a run will repopulate it */
      });
  }, []);

  const run = useCallback(async (family: string) => {
    setBusy(family);
    setError(null);
    try {
      const res = await fetch("/api/freqtrade", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ family }),
      });
      const payload = (await res.json().catch(() => null)) as
        | { run?: FreqtradeRun; error?: string }
        | null;
      if (!res.ok || !payload?.run) {
        setError(payload?.error ?? `the console answered ${res.status}`);
        return;
      }
      // The run carries its own stamp, so the panel's "last artifact" header moves with the
      // row that just changed. Without it the header keeps the mount-time value and reads as
      // stale evidence immediately after a successful run.
      const fresh = payload.run!;
      setArtifact((current) => ({
        ...current,
        runs: { ...current.runs, [family]: fresh },
        generated_utc: fresh.generated_utc || current.generated_utc,
      }));
    } catch {
      setError("the run could not be started");
    } finally {
      setBusy(null);
    }
  }, []);

  // The row the numbers argue for: most expectancy in R, among families with a sample big
  // enough to mean anything. A candidate judged on four trades is not a candidate.
  const ranked = Object.entries(artifact.runs).filter(([, run]) => run.expectancy_r !== null);
  const credible = ranked.filter(([, run]) => run.trades >= 20);
  const best = credible.length
    ? credible.reduce((a, b) => (b[1].expectancy_r! > a[1].expectancy_r! ? b : a))
    : null;

  return (
    <section className="panel" aria-labelledby="freqtrade-heading">
      <PanelHead
        id="freqtrade-heading"
        title="Freqtrade engine (offline)"
        aside={
          <span className="muted">
            {artifact.generated_utc ? `last artifact ${artifact.generated_utc.slice(0, 16).replace("T", " ")}` : "nothing run yet"}
          </span>
        }
        about={
          <>
            <p>
              The same family rules the cards use, run through freqtrade&rsquo;s backtest engine
              against MT5-exported bars - freqtrade never touches the broker or the live watcher.
              Run one family to refresh it: bars re-export when stale, then the backtest runs in
              its own environment. Expectancy is per trade in R, scored against each
              trade&rsquo;s own stop distance.
            </p>
            <p>
              <strong>The live stop management is in force here too.</strong> The watcher locks a
              stop past entry once a trade is 1R in profit and trails it 1R behind price from 2R;
              the backtest applies the same two rules from the same settings, so these numbers
              describe the system that actually trades rather than one holding fixed barriers.
              Toggle it in the .env with <code>AUTO_MANAGE</code>.
            </p>
            <p>
              Two limits remain, both stated rather than hidden: numbers are
              <strong> pre-spread</strong> (the research pages charge the spread in R), and
              <strong> long-side only</strong> (no supported exchange lists these FX pairs with
              shorts inside freqtrade).
            </p>
          </>
        }
      />
      {error !== null ? (
        <p className="pnl-down" role="alert">
          {error}
        </p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Family</th>
              <th scope="col" className="num">Trades</th>
              <th scope="col" className="num">Win rate</th>
              <th scope="col" className="num">Expectancy</th>
              <th scope="col" className="num">Net</th>
              <th scope="col" className="num">Drawdown</th>
              <th scope="col">Params</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            {FAMILIES.map((family) => {
              const last = artifact.runs[family];
              const isBest = best?.[0] === family;
              return (
                <tr key={family} data-current={isBest || undefined}>
                  <td>
                    {/* The family's own page: its replay on real bars, its settings, and what the
                        rule does. The name is the way in, so the table doubles as the index of
                        the dossiers instead of needing one of its own. */}
                    <Link className="strategy-name strategy-link" href={`/strategies/${family}`}>
                      {strategyName(family)}
                    </Link>
                    {isBest ? <span className="pill pill-pass">best evidence</span> : null}
                  </td>
                  <td className="num">{last?.trades ?? "—"}</td>
                  <td className="num">
                    {last?.win_rate === null || last?.win_rate === undefined
                      ? "—"
                      : `${Math.round(last.win_rate * 100)}%`}
                  </td>
                  <td className={`num ${last && (last.expectancy_r ?? 0) > 0 ? "pnl-up" : (last?.expectancy_r ?? 0) < 0 ? "pnl-down" : ""}`}>
                    {r(last?.expectancy_r)}
                  </td>
                  <td className={`num ${last && last.net_profit_pct > 0 ? "pnl-up" : last ? "pnl-down" : ""}`}>
                    {pct(last?.net_profit_pct)}
                  </td>
                  <td className="num">{last ? pct(-last.max_drawdown_pct) : "—"}</td>
                  <td className="detail">
                    {last?.buy_params
                      ? Object.entries(last.buy_params)
                          .map(([key, value]) => `${key}=${value}`)
                          .join(" · ")
                      : last
                        ? "defaults"
                        : "—"}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="pill pill-info cap-btn"
                      disabled={busy !== null}
                      onClick={() => void run(family)}
                    >
                      {busy === family ? "running…" : "run"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
