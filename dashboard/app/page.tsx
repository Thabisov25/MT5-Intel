import Link from "next/link";
import { connection } from "next/server";
import { Info } from "@/components/info";
import { buildForecast } from "@/lib/forecast";
import { readLiveStatus } from "@/lib/live";
import { listReports } from "@/lib/reports";
import { readSetups } from "@/lib/setups";
import { readTradesWithLabels } from "@/lib/trades";
import { strategyName } from "@/lib/strategies";
import { normalizeSymbolParam, setupsFilter } from "@/lib/symbols";
import { filterTradesBySymbol } from "@/lib/trades";

// The brief reads three files on every request; serving a cached one would be a brief about the
// morning pretending to be one about now.
export const instant = false;

export default async function BriefPage({
  searchParams,
}: {
  searchParams: Promise<{ symbol?: string }>;
}) {
  await connection();
  // The same `?symbol=` rule as Live: one named market's forecast and one market's account
  // numbers, never a second symbol's rows beside them.
  const symbol = setupsFilter(normalizeSymbolParam((await searchParams).symbol));
  const [setups, journal, status, reports] = await Promise.all([
    readSetups(symbol),
    readTradesWithLabels(),
    readLiveStatus(),
    listReports(),
  ]);
  const trades = filterTradesBySymbol(journal, symbol);
  const forecast = buildForecast(setups, trades);
  const top = (forecast?.rows ?? []).slice(0, 5);
  const [latest] = reports;

  return (
    <>
      <header className="page-head">
        <div className="page-title">
          <h1>Today&rsquo;s brief</h1>
          <Info label="this brief">
            One glance before you open the terminal: which strategies are favoured right now, how the
            account is standing, and whether the watcher and the gate are healthy. Full detail lives
            on <Link href="/live">Live</Link> (deciding) and <Link href="/trades">Trades</Link>{" "}
            (recording and reviewing).
          </Info>
        </div>
      </header>

      <section className="panel" aria-labelledby="brief-strategies">
        <h2 id="brief-strategies">Strategy forecast</h2>
        {!forecast ? (
          <p className="muted">
            The watcher has not written its live views yet, so nothing is ranked. It fills in by
            itself once <Link href="/live">Live</Link> has data.
          </p>
        ) : (
          <ul className="rank">
            {top.map((row) => {
              const headline =
                row.status === "open"
                  ? `running · ${row.rNow != null ? `${row.rNow > 0 ? "+" : ""}${row.rNow.toFixed(2)}R` : ""}`
                  : row.agreement?.partners.length
                    ? `agrees with ${row.agreement.partners.length} other ${row.agreement.partners.length === 1 ? "strategy" : "strategies"}`
                    : row.record && row.record.decided >= 3
                      ? `${Math.round((row.record.winRate ?? 0) * 100)}% of ${row.record.decided} · ${row.record.net >= 0 ? "+" : ""}${row.record.net.toFixed(0)}`
                      : "no record yet";
              return (
                <li key={row.family}>
                  <span className={`pill forecast-pill-${row.tier}`}>
                    {row.tier === "favour" ? "favoured" : row.tier === "struggle" ? "poor" : row.tier === "quiet" ? "—" : "watch"}
                  </span>
                  <span className="rank-name">{strategyName(row.family)}</span>
                  {row.side ? <span className="tag">{row.side}</span> : null}
                  <span className="muted num">{headline}</span>
                </li>
              );
            })}
          </ul>
        )}
        <p className="muted">
          Full ranking: <Link href="/live">Live</Link>. Nothing here is advice - the gate decides
          what may trade.
        </p>
      </section>

      <section className="panel" aria-labelledby="brief-account">
        <h2 id="brief-account">Account</h2>
        {!trades ? (
          <p className="muted">The watcher has not written the trade journal yet.</p>
        ) : (
          <dl className="facts">
            <div>
              <dt>Open</dt>
              <dd className="num">
                {trades.openCount} · {trades.openNet >= 0 ? "+" : ""}
                {trades.openNet.toFixed(2)} {trades.currency ?? ""}
              </dd>
            </div>
            <div>
              <dt>Closed (last {trades.lookbackDays} d)</dt>
              <dd className="num">
                {trades.closedCount} · {trades.closedNet >= 0 ? "+" : ""}
                {trades.closedNet.toFixed(2)} {trades.currency ?? ""}
              </dd>
            </div>
            <div>
              <dt>Win rate</dt>
              <dd className="num">
                {trades.winRate === null ? "—" : `${Math.round(trades.winRate * 100)}%`}
              </dd>
            </div>
            <div>
              <dt>Watcher</dt>
              <dd>
                {status.available
                  ? `${status.connected ? "connected" : "disconnected"} · gate ${status.gateVerdict ?? "?"}`
                  : "not reporting"}
              </dd>
            </div>
          </dl>
        )}
        <p className="muted">
          Per-strategy scorecards, labelling and the whole journal: <Link href="/trades">Trades</Link>.
        </p>
      </section>

      <section className="panel" aria-labelledby="brief-research">
        <h2 id="brief-research">Research</h2>
        {latest && latest.ok ? (
          <p>
            Newest diagnostic:{" "}
            <Link href={`/runs/${latest.report.id}`}>run {latest.report.id}</Link> ·{" "}
            <Link href="/replays">replays</Link> · <Link href="/research">strategy tests</Link> ·{" "}
            <Link href="/health">gate health</Link>
          </p>
        ) : (
          <p className="muted">
            No diagnostic runs yet. They are listed under <Link href="/runs">All runs</Link>.
          </p>
        )}
      </section>
    </>
  );
}
