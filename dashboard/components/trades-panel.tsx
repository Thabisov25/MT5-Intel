"use client";

import { Fragment, useCallback, useState } from "react";
import { PanelHead } from "@/components/info";
import { useVisiblePoll } from "@/components/poll";
import { RULE_IDS, strategyName } from "@/lib/strategies";
import type { TradeRow, Trades } from "@/lib/trades";

/**
 * The trades the account actually took, and which strategy each one was following.
 *
 * Everything here is read from the terminal, so nothing has to be typed in: an open position
 * appears on its own, closed trades arrive with their own exit price and result, and each row
 * remembers which strategies had a live card when it was first seen. The one judgement this panel
 * asks of the reader is the one only they can make - which of those cards they were following -
 * and that answer is written to its own file, never over the journal.
 *
 * The watcher rebuilds the journal every 60 s while the market is open, so a view that stops
 * moving is a view the watcher has stopped rebuilding, not a quiet market.
 */
const REFRESH_MS = 15_000;
// Three of the watcher's own refresh windows, so a normal slow cycle does not read as stale but a
// stopped watcher does.
const VIEW_STALE_S = 600;

// The rules, not the whole catalogue: `combo` is in `STRATEGIES` to be read, and a trade cannot be
// filed under a family that never issues an order.
const STRATEGY_IDS = [...RULE_IDS].sort((a, b) => strategyName(a).localeCompare(strategyName(b)));

/**
 * A stamp's instant. The journal writes "YYYY-MM-DD HH:MM" on the UTC clock (or on the broker's,
 * which the page labels), and `Date.parse` would read that form as *local* time - silently
 * shifting every age by the viewer's own offset - so the shape decides which reading applies.
 */
function instantMs(value: string | null): number | null {
  if (!value) return null;
  const iso = value.includes("T")
    ? value
    : `${value.replace(" ", "T")}${value.length === 16 ? ":00" : ""}Z`;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/** "3 h 12 m" reads faster than a stamp when the question is "how long has this been on". */
function age(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} m`;
  if (seconds < 86_400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes ? `${hours} h ${minutes} m` : `${hours} h`;
  }
  const days = Math.floor(seconds / 86_400);
  const hours = Math.round((seconds % 86_400) / 3600);
  return hours ? `${days} d ${hours} h` : `${days} d`;
}

const price = (value: number | null, digits: number) =>
  value === null ? "—" : value.toFixed(Math.max(0, Math.min(8, digits)));

const money = (value: number) => `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;

function Money({ value, children }: { value: number; children?: React.ReactNode }) {
  const tone = value > 0 ? "pnl-up" : value < 0 ? "pnl-down" : undefined;
  return (
    <td className={`num ${tone ?? ""}`}>
      {money(value)}
      {children}
    </td>
  );
}

export function TradesPanel({
  initial,
  symbol,
}: {
  initial: Trades | null;
  /** The `?symbol=` choice the page rendered with, or null for the account-wide journal. */
  symbol: string | null;
}) {
  const [trades, setTrades] = useState<Trades | null>(initial);
  const [labels, setLabels] = useState<Record<string, string | null>>({});
  const [pending, setPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState<number | null>(null);

  const refresh = useCallback(() => {
    setNowMs(Date.now());
    // The slice is the server's job, not this panel's arithmetic: the URL carries the symbol, and
    // the payload's totals are recomputed for that slice, so rows and totals cannot disagree.
    const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    fetch(`/api/trades${query}`, { cache: "no-store" })
      .then((res) => (res.ok ? (res.json() as Promise<Trades | null>) : null))
      .then((next) => {
        if (next) setTrades(next);
      })
      .catch(() => {
        /* keep the last good view until the next tick */
      });
  }, [symbol]);

  // The journal is rebuilt by the watcher every minute while the market is open, and this is the
  // clock the floating result is read against. It is on the shared interval, so it also reads the
  // journal again the moment this tab is looked at rather than a throttled minute later.
  useVisiblePoll(refresh, REFRESH_MS);

  const labelOf = (row: TradeRow) =>
    Object.hasOwn(labels, String(row.ticket)) ? labels[String(row.ticket)] : row.label;

  const assign = (row: TradeRow, value: string | null) => {
    const key = String(row.ticket);
    setPending(row.ticket);
    setError(null);
    const previous = Object.hasOwn(labels, key) ? labels[key] : row.label;
    setLabels((current) => ({ ...current, [key]: value }));
    fetch("/api/trades/label", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket: row.ticket, strategy: value }),
    })
      .then(async (res) => {
        if (!res.ok) {
          const detail = (await res.json().catch(() => null)) as { error?: string } | null;
          throw new Error(detail?.error ?? `the console answered ${res.status}`);
        }
      })
      .catch((exc: unknown) => {
        // The write is the point of the control, so a failed one is put back rather than left
        // looking as though it landed.
        setLabels((current) => ({ ...current, [key]: previous }));
        setError(
          `Could not save the label: ${exc instanceof Error ? exc.message : "unknown error"}`,
        );
      })
      .finally(() => setPending(null));
  };

  if (!trades) {
    return (
      <section className="empty" aria-labelledby="trades-empty-heading">
        <h2 id="trades-empty-heading">The trade journal has not been written yet</h2>
        <p>
          It is rebuilt by the watcher from the terminal&rsquo;s own positions and deal history,
          every minute while the market is open. Start the watcher from the{" "}
          <code>mt5-intel</code> folder:
        </p>
        <pre>
          <code>{`.\\.venv\\Scripts\\python.exe -m mt5_intel.watch`}</code>
        </pre>
        <p className="muted">
          Nothing here places an order. This page only reads what the account already did, and adds
          which strategy you were following.
        </p>
      </section>
    );
  }

  const open = trades.trades.filter((row) => row.status === "open");
  const closed = trades.trades.filter((row) => row.status === "closed");
  const generatedMs = instantMs(trades.generatedUtc);
  const viewAgeS =
    generatedMs === null || nowMs === null
      ? null
      : Math.max(0, Math.round((nowMs - generatedMs) / 1000));
  const staleForS = viewAgeS !== null && viewAgeS > VIEW_STALE_S ? viewAgeS : null;
  const clockLabel = trades.clock === "utc" ? "UTC" : "the broker's clock";

  // What each strategy has actually made, over the trades that have been labelled with it. An
  // unlabelled trade is not attributed to anyone: guessing would put a result on a rule that may
  // never have been followed.
  const byStrategy = new Map<string, { trades: number; closed: number; wins: number; losses: number; net: number }>();
  for (const row of trades.trades) {
    const key = labelOf(row) ?? "";
    const entry = byStrategy.get(key) ?? { trades: 0, closed: 0, wins: 0, losses: 0, net: 0 };
    entry.trades += 1;
    if (row.status === "closed") {
      entry.closed += 1;
      if (row.net > 0) entry.wins += 1;
      else if (row.net < 0) entry.losses += 1;
    }
    entry.net += row.net;
    byStrategy.set(key, entry);
  }
  const ranked = [...byStrategy.entries()].sort((a, b) => b[1].net - a[1].net);

  return (
    <>
      <div className="verdict verdict-strip">
        <dl className="counts">
          <div>
            <dt>Open ({trades.openCount})</dt>
            <dd className={trades.openNet > 0 ? "pnl-up" : trades.openNet < 0 ? "pnl-down" : undefined}>
              {money(trades.openNet)}
            </dd>
          </div>
          <div>
            <dt>Taken ({trades.closedCount})</dt>
            <dd className={trades.closedNet > 0 ? "pnl-up" : trades.closedNet < 0 ? "pnl-down" : undefined}>
              {money(trades.closedNet)}
            </dd>
          </div>
          <div>
            <dt>Win rate</dt>
            <dd className="num">
              {trades.winRate === null ? "—" : `${Math.round(trades.winRate * 100)}%`}
            </dd>
          </div>
        </dl>
        <p className="muted verdict-meta">
          {trades.currency ?? ""} {clockLabel} · rebuilt {trades.generatedUtc}
          {viewAgeS !== null ? ` (${age(viewAgeS)} ago)` : ""}
          {staleForS !== null ? (
            <>
              {" "}
              <span className="pill pill-warn">not rebuilt for {age(staleForS)}</span>
            </>
          ) : null}
        </p>
      </div>

      {error !== null ? (
        <p className="muted" role="alert">
          {error}
        </p>
      ) : null}

      {/* Only when there is something in it: an empty panel that says "none" twice is the kind of
          thing that trains a reader to skip the top of a page. */}
      {open.length === 0 ? null : (
      <section className="panel" aria-labelledby="open-trades-heading">
        <PanelHead
          id="open-trades-heading"
          title="Open now"
          aside={<span className="muted">{`${open.length} open`}</span>}
          about={
            <p>
              Positions the account is still in, marked to the terminal&rsquo;s own floating result —
              profit, plus the swap and commission the deals have already booked. &ldquo;Live when
              taken&rdquo; is the strategies that had a card running when this position was first
              seen; pick the one you were following and it stays with the trade after it closes.
            </p>
          }
        />
        {
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Following</th>
                  <th scope="col">Side</th>
                  <th scope="col" className="num">Entry</th>
                  <th scope="col" className="num">Now</th>
                  <th scope="col" className="num">P / L</th>
                  <th scope="col">Opened</th>
                </tr>
              </thead>
              <tbody>
                {open.map((row) => {
                  const openedAge = openedSeconds(row, nowMs);
                  return (
                    <tr key={row.ticket}>
                      <td className="trade-label">
                        <StrategySelect
                          value={labelOf(row) ?? null}
                          auto={row.labelNote === "auto-trade"}
                          busy={pending === row.ticket}
                          onChange={(value) => assign(row, value)}
                        />
                      </td>
                      <td>{row.side ?? "—"}</td>
                      <td className="num mono">{price(row.entry, row.digits)}</td>
                      <td className="num mono">{price(row.priceNow, row.digits)}</td>
                      <Money value={row.net} />
                      <td>
                        {row.openedUtc ?? "—"}
                        {openedAge !== null ? <span className="muted"> · {age(openedAge)}</span> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        }
      </section>
      )}

      <section className="panel" aria-labelledby="closed-trades-heading">
        <PanelHead
          id="closed-trades-heading"
          title="Trades taken"
          aside={<span className="muted">{trades.lookbackDays} days back</span>}
          about={
            <p>
              Every closed position the terminal still holds, newest first - results come from the
              deals themselves. Hover a row to see which strategies were live when it was taken.
            </p>
          }
        />
        {closed.length === 0 ? (
          <p className="muted">
            No closed trades in the last {trades.lookbackDays} days. A trade appears here the
            moment its position leaves the terminal.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Closed trades, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Following</th>
                  <th scope="col">Side</th>
                  <th scope="col" className="num">Entry</th>
                  <th scope="col" className="num">Exit</th>
                  <th scope="col" className="num">Net</th>
                  <th scope="col">Closed</th>
                </tr>
              </thead>
              <tbody>
                {closed.map((row) => (
                  <tr key={row.ticket} title={row.context.map((c) => strategyName(c.family)).join(", ")}>
                    <td className="trade-label">
                      <StrategySelect
                        value={labelOf(row) ?? null}
                        auto={row.labelNote === "auto-trade"}
                        busy={pending === row.ticket}
                        onChange={(value) => assign(row, value)}
                      />
                    </td>
                    <td>{row.side ?? "—"}</td>
                    <td className="num mono">{price(row.entry, row.digits)}</td>
                    <td className="num mono">{price(row.exit, row.digits)}</td>
                    <Money value={row.net} />
                    <td className="nowrap">{shortStamp(row.closedUtc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="by-strategy-heading">
        <PanelHead
          id="by-strategy-heading"
          title="By strategy"
          aside={
            <span className="muted">
              {ranked.filter(([key]) => key !== "").length} labelled
              {byStrategy.has("") ? " · some unlabelled" : ""}
            </span>
          }
          about={
            <p>
              What each strategy made on the trades you labelled with it. Unlabelled trades count
              toward nothing; the scorecards below each row list that strategy&rsquo;s trades.
            </p>
          }
        />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Strategy</th>
                <th scope="col" className="num">Closed</th>
                <th scope="col">Win rate</th>
                <th scope="col" className="num">Net</th>
                <th scope="col" className="num">Per trade</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map(([key, row]) => {
                const decided = row.wins + row.losses;
                if (key === "" && ranked.length > 1) return null;
                const mine =
                  key === ""
                    ? []
                    : trades.trades.filter((t) => labelOf(t) === key && t.status === "closed");
                const pct = decided === 0 ? null : (row.wins / decided) * 100;
                return (
                <Fragment key={key || "unlabelled"}>
                <tr>
                  <td>
                    {key === "" ? (
                      <span className="muted">not recorded</span>
                    ) : (
                      <span className="strategy-name">{strategyName(key)}</span>
                    )}
                  </td>
                  <td className="num">{row.closed}</td>
                  <td className="winrate">
                    {pct === null ? (
                      <span className="muted">—</span>
                    ) : (
                      <>
                        <span
                          className="winbar"
                          aria-hidden="true"
                          style={{ ["--pct" as string]: `${pct}%` }}
                        >
                          <span className="winbar-fill" />
                        </span>
                        <span className="num">{Math.round(pct)}% ({row.wins}W/{row.losses}L)</span>
                      </>
                    )}
                  </td>
                  <Money value={row.net} />
                  <td className="num">
                    {row.closed === 0 ? "—" : money(row.net / row.closed)}
                  </td>
                </tr>
                {mine.length > 0 ? (
                  <tr className="scorecard-row">
                    <td colSpan={5}>
                      <details className="scorecard">
                        <summary>scorecard · {mine.length} closed trades</summary>
                        <div className="table-wrap">
                          <table>
                            <caption className="sr-only">
                              {`Closed trades labelled ${strategyName(key)}, newest first`}
                            </caption>
                            <thead>
                              <tr>
                                <th scope="col">Closed</th>
                                <th scope="col">Side</th>
                                <th scope="col" className="num">Entry</th>
                                <th scope="col" className="num">Exit</th>
                                <th scope="col" className="num">Net</th>
                                <th scope="col">Run</th>
                              </tr>
                            </thead>
                            <tbody>
                              {[...mine]
                                .reverse()
                                .slice(0, 12)
                                .map((t) => {
                                  const idx = mine.indexOf(t);
                                  const prior = mine
                                    .slice(0, Math.max(0, idx))
                                    .reduce((sum, r) => sum + r.net, 0);
                                  return (
                                    <tr key={t.ticket}>
                                      <td className="nowrap">{shortStamp(t.closedUtc)}</td>
                                      <td>{t.side ?? "—"}</td>
                                      <td className="num">{price(t.entry, t.digits)}</td>
                                      <td className="num">{price(t.exit, t.digits)}</td>
                                      <Money value={t.net} />
                                      <td className="num">{money(prior + t.net)}</td>
                                    </tr>
                                  );
                                })}
                            </tbody>
                          </table>
                        </div>
                      </details>
                    </td>
                  </tr>
                ) : null}
                </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

    </>
  );
}

/**
 * "2026-10-13 14:09" spends five characters repeating the day the table is already inside. The
 * year comes back only when it is not this one, so a January trade is not read as this year's.
 */
function shortStamp(value: string | null | undefined): string {
  if (!value) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2})/.exec(value);
  if (!match) return value;
  const [, year, month, day, time] = match;
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const label = `${Number(day)} ${names[Number(month) - 1]}`;
  return `${year === String(new Date().getFullYear()) ? label : `${label} ${year}`} ${time}`;
}


function StrategySelect({
  value,
  auto,
  busy,
  onChange,
}: {
  value: string | null;
  auto?: boolean;
  busy: boolean;
  onChange: (value: string | null) => void;
}) {
  return (
    <>
      <select
        value={value ?? ""}
        disabled={busy}
        aria-label="Strategy followed"
        onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
      >
        <option value="">— not recorded —</option>
        {STRATEGY_IDS.map((id) => (
          <option key={id} value={id}>
            {strategyName(id)}
          </option>
        ))}
      </select>
      {value !== null ? <span className="tag">{value}</span> : null}
      {auto ? <span className="pill pill-info" style={{ marginLeft: "0.25rem" }}>auto</span> : null}
    </>
  );
}

/** How long an open trade has been on, counted to the reader's clock rather than baked in. */
function openedSeconds(row: TradeRow, nowMs: number | null): number | null {
  if (nowMs === null) return null;
  const openedMs = instantMs(row.openedUtc);
  return openedMs === null ? null : Math.max(0, Math.round((nowMs - openedMs) / 1000));
}
