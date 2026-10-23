import Link from "next/link";
import { connection } from "next/server";
import { ContextNav, WorkspaceFrame } from "@/components/shell/context-nav";
import { SymbolFilter } from "@/components/symbol-filter";
import { buildForecast, type ForecastRow } from "@/lib/forecast";
import { readAuto } from "@/lib/auto";
import { readLiveStatus } from "@/lib/live";
import { listReports } from "@/lib/reports";
import { readSetups, type SetupRow } from "@/lib/setups";
import { filterTradesBySymbol, readTradesWithLabels, type TradeRow } from "@/lib/trades";
import { strategyName } from "@/lib/strategies";
import { listSymbols, normalizeSymbolParam, setupsFilter } from "@/lib/symbols";

// The brief reads several files on every request; serving a cached one would be a brief about
// the morning pretending to be one about now.
export const instant = false;

/** Daily-loss ceiling of the standing risk rules (1% per trade, 3% per day). */
const DAILY_LOSS_LIMIT_PCT = 3;

function money(value: number | null | undefined, currency: string | null): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toLocaleString("en-ZA", { maximumFractionDigits: 2 })} ${currency ?? ""}`.trim();
}

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

/**
 * The command centre (master plan §11.0): an operational overview strip, a primary focus
 * column that ranks what to watch, a secondary analytical column for risk and research
 * state, and an activity band. Panel weight encodes importance; nothing here is invented —
 * every number is a field of the watcher's artifacts.
 */
export default async function BriefPage({
  searchParams,
}: {
  searchParams: Promise<{ symbol?: string }>;
}) {
  await connection();
  // The same `?symbol=` rule as Live: one named market's forecast and one market's account
  // numbers, never a second symbol's rows beside them.
  const symbol = setupsFilter(normalizeSymbolParam((await searchParams).symbol));
  const [setups, journal, status, reports, auto] = await Promise.all([
    readSetups(symbol),
    readTradesWithLabels(),
    readLiveStatus(),
    listReports(),
    readAuto(),
  ]);
  const trades = filterTradesBySymbol(journal, symbol);
  const forecast = buildForecast(setups, trades);
  const ranked = (forecast?.rows ?? []).slice(0, 6);
  const opportunities = (setups?.setups ?? []).filter((row) => row.status === "open").slice(0, 5);
  const recentClosed: TradeRow[] = (trades?.trades ?? [])
    .filter((row) => row.status === "closed")
    .slice(-5)
    .reverse();
  const [latestReport] = reports;
  const { watched, traded } = await listSymbols();
  const symbolNames: string[] = [];
  for (const name of [...watched, ...traded]) {
    if (name && !symbolNames.includes(name)) symbolNames.push(name);
  }

  const dailyLossPct = auto.available ? (auto.market?.dailyLossPct ?? null) : null;
  const openPositions = auto.available ? (auto.market?.openPositions ?? null) : (trades?.openCount ?? null);
  const maxPositions = auto.available ? (auto.settings?.maxPositions ?? null) : null;
  const riskPct = auto.available ? (auto.settings?.riskPct ?? null) : null;

  return (
    <WorkspaceFrame
      nav={
        <ContextNav
          heading="Trading workspace"
          items={[
            { href: "/", label: "Today’s brief", current: true },
            { href: "/live", label: "Live decisions" },
            { href: "/trades", label: "Trading journal" },
            { href: "/terminal", label: "Trading terminal" },
          ]}
        />
      }
    >
      <SymbolFilter
        basePath="/"
        label="Market:"
        current={symbol ?? ""}
        symbols={symbolNames}
      />

      {/* ── A. Overview strip: account and system state on one line of sight ─────── */}
      <section aria-label="Account and system overview" className="command-overview">
        <dl className="metric-strip">
          <div className="metric">
            <dt>Equity</dt>
            <dd>
              {auto.account?.equity != null
                ? auto.account.equity.toLocaleString("en-ZA", { maximumFractionDigits: 2 })
                : "—"}
              <span className="metric-sub">{auto.account?.currency ?? ""} · account {auto.account?.login ?? "—"}</span>
            </dd>
          </div>
          <div className={`metric ${trades && trades.openNet > 0 ? "is-positive" : trades && trades.openNet < 0 ? "is-negative" : ""}`}>
            <dt>Open P/L</dt>
            <dd>
              {trades ? money(trades.openNet, trades.currency) : "—"}
              <span className="metric-sub">{trades ? `${trades.openCount} open` : "journal unavailable"}</span>
            </dd>
          </div>
          <div className={`metric ${trades && trades.closedNet >= 0 ? "is-positive" : "is-negative"}`}>
            <dt>Closed (last {trades?.lookbackDays ?? "—"} d)</dt>
            <dd>
              {trades ? money(trades.closedNet, trades.currency) : "—"}
              <span className="metric-sub">{trades ? `${trades.closedCount} trades · win rate ${pct(trades.winRate)}` : ""}</span>
            </dd>
          </div>
          <div className={`metric ${dailyLossPct !== null && dailyLossPct >= DAILY_LOSS_LIMIT_PCT ? "is-negative" : dailyLossPct !== null && dailyLossPct >= DAILY_LOSS_LIMIT_PCT / 2 ? "is-warning" : ""}`}>
            <dt>Daily loss</dt>
            <dd>
              {dailyLossPct !== null ? `${dailyLossPct.toFixed(2)}%` : "—"}
              <span className="metric-sub">of the {DAILY_LOSS_LIMIT_PCT}% daily limit</span>
            </dd>
          </div>
          <div className={`metric ${status.available && status.watcherAlive ? "is-positive" : "is-warning"}`}>
            <dt>Watcher</dt>
            <dd style={{ fontSize: "1.0625rem" }}>
              {status.available
                ? status.watcherAlive
                  ? status.connected
                    ? "Live"
                    : "MT5 offline"
                  : "Down"
                : "Not reporting"}
              <span className="metric-sub">
                gate {status.gateVerdict ?? "—"} · {status.marketState ?? "market ?"}
              </span>
            </dd>
          </div>
        </dl>
      </section>

      {/* ── B. Primary focus: what to watch right now ───────────────────────────── */}
      <section className="panel" aria-labelledby="brief-forecast">
        <p className="section-label" id="brief-forecast">
          Strategy forecast
        </p>
        {!forecast ? (
          <p className="muted">
            The watcher has not written its live views yet, so nothing is ranked. It fills in by
            itself once <Link href="/live">Live</Link> has data.
          </p>
        ) : (
          <ol className="leaderboard">
            {ranked.map((row) => (
              <LeaderboardRow key={row.family} row={row} />
            ))}
          </ol>
        )}
        <p className="muted" style={{ marginTop: "0.875rem" }}>
          Full ranking and the decision controls: <Link href="/live">Live decisions</Link>. Nothing
          here is advice — the gate decides what may trade.
        </p>
      </section>

      {/* ── C. Secondary analytical: risk state ─────────────────────────────────── */}
      <section className="panel" aria-labelledby="brief-risk">
        <p className="section-label" id="brief-risk">
          Risk state
        </p>
        <dl className="risk-list">
          <div>
            <dt>Positions</dt>
            <dd>
              <span className="num">
                {openPositions ?? "—"}
                {maxPositions !== null ? ` / ${maxPositions}` : ""}
              </span>
              <div className="risk-bar" aria-hidden="true">
                <span
                  className="risk-bar-fill"
                  style={{
                    width: `${Math.min(100, maxPositions ? ((openPositions ?? 0) / maxPositions) * 100 : 0)}%`,
                  }}
                />
              </div>
            </dd>
          </div>
          <div>
            <dt>Risk per trade</dt>
            <dd className="num">{riskPct !== null ? `${riskPct}%` : "—"}</dd>
          </div>
          <div>
            <dt>Daily loss used</dt>
            <dd className="num">
              {dailyLossPct !== null ? `${dailyLossPct.toFixed(2)}% / ${DAILY_LOSS_LIMIT_PCT}%` : "—"}
            </dd>
          </div>
          <div>
            <dt>Circuit breaker</dt>
            <dd>
              {auto.available && auto.market?.circuitBreakerTripped !== null && auto.market?.circuitBreakerTripped !== undefined ? (
                <span className={`pill ${auto.market.circuitBreakerTripped ? "pill-fail" : "pill-pass"}`}>
                  {auto.market.circuitBreakerTripped ? "TRIPPED" : "clear"}
                </span>
              ) : (
                <span className="muted">unavailable</span>
              )}
            </dd>
          </div>
          <div>
            <dt>Auto-trading</dt>
            <dd>
              {auto.available && auto.switch ? (
                <span className={`pill ${auto.switch.enabled ? "pill-warn" : "pill-not_verified"}`}>
                  {auto.switch.enabled ? "ARMED" : "off"}
                </span>
              ) : (
                <span className="muted">unavailable</span>
              )}
            </dd>
          </div>
        </dl>
        <p className="muted" style={{ marginTop: "0.875rem" }}>
          Manual execution only — arming is deliberate and double-gated on{" "}
          <Link href="/live">Live</Link>.
        </p>
      </section>

      {/* ── B′. Primary focus: live opportunities ───────────────────────────────── */}
      <section className="panel" aria-labelledby="brief-opportunities">
        <p className="section-label" id="brief-opportunities">
          Active opportunities{setups ? ` · ${setups.symbol} ${setups.timeframe}` : ""}
        </p>
        {opportunities.length === 0 ? (
          <p className="muted">No open setups on this market&rsquo;s card view right now.</p>
        ) : (
          <ul className="opportunity-list">
            {opportunities.map((row) => (
              <OpportunityRow key={`${row.family}-${row.signalEpoch ?? row.signalUtc}`} row={row} digits={setups?.digits ?? 3} />
            ))}
          </ul>
        )}
      </section>

      {/* ── C′. Secondary analytical: research & diagnostics ────────────────────── */}
      <section className="panel" aria-labelledby="brief-research">
        <p className="section-label" id="brief-research">
          Research &amp; diagnostics
        </p>
        {latestReport && latestReport.ok ? (
          <ul className="activity-list">
            <li>
              <span className={`pill ${latestReport.report.overall === "PASS" ? "pill-pass" : latestReport.report.overall === "FAIL" ? "pill-fail" : "pill-warn"}`}>
                {latestReport.report.overall}
              </span>
              <Link href={`/runs/${latestReport.report.id}`}>run {latestReport.report.id}</Link>
              <span className="muted num">{latestReport.report.generatedUtc}</span>
            </li>
          </ul>
        ) : (
          <p className="muted">No diagnostic runs yet. They are listed under All runs.</p>
        )}
        <ul className="link-chips">
          <li>
            <Link className="btn" href="/research">
              Strategy tests
            </Link>
          </li>
          <li>
            <Link className="btn" href="/replays">
              Graded replays
            </Link>
          </li>
          <li>
            <Link className="btn" href="/runs">
              All runs
            </Link>
          </li>
          <li>
            <Link className="btn" href="/health">
              Gate health
            </Link>
          </li>
        </ul>
      </section>

      {/* ── D. Activity band: what just happened ───────────────────────────────── */}
      <section className="command-band" aria-label="Recent activity">
        <div className="panel">
          <p className="section-label">Recent journal activity</p>
          {recentClosed.length === 0 ? (
            <p className="muted">No closed trades in this window yet.</p>
          ) : (
            <ul className="activity-list">
              {recentClosed.map((row) => (
                <li key={row.ticket}>
                  <span className={`pill ${row.net >= 0 ? "pill-pass" : "pill-fail"}`}>
                    {row.side ?? "—"}
                  </span>
                  <span className="activity-name">
                    {row.symbol} · {row.label ? strategyName(row.label) : (row.comment ?? "unlabelled")}
                  </span>
                  <span className={`num ${row.net >= 0 ? "pnl-up" : "pnl-down"}`}>
                    {money(row.net, trades?.currency ?? null)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            Full journal and labelling: <Link href="/trades">Trading journal</Link>.
          </p>
        </div>

        <div className="panel">
          <p className="section-label">Newest diagnostic runs</p>
          {reports.filter((entry) => entry.ok).length === 0 ? (
            <p className="muted">No reports yet.</p>
          ) : (
            <ul className="activity-list">
              {reports.slice(0, 5).map((entry) =>
                entry.ok ? (
                  <li key={entry.report.id}>
                    <span
                      className={`pill ${entry.report.overall === "PASS" ? "pill-pass" : entry.report.overall === "FAIL" ? "pill-fail" : "pill-warn"}`}
                    >
                      {entry.report.overall}
                    </span>
                    <Link href={`/runs/${entry.report.id}`} className="activity-name">
                      run {entry.report.id}
                    </Link>
                    <span className="muted num">{entry.report.generatedUtc}</span>
                  </li>
                ) : (
                  <li key={entry.id}>
                    <span className="pill pill-fail">unreadable</span>
                    <span className="activity-name">{entry.id}</span>
                  </li>
                ),
              )}
            </ul>
          )}
        </div>
      </section>
    </WorkspaceFrame>
  );
}

function tierWidth(tier: ForecastRow["tier"]): string {
  return tier === "favour" ? "100%" : tier === "watch" ? "62%" : tier === "struggle" ? "32%" : "12%";
}

function LeaderboardRow({ row }: { row: ForecastRow }) {
  const headline =
    row.status === "open"
      ? `running · ${row.rNow != null ? `${row.rNow > 0 ? "+" : ""}${row.rNow.toFixed(2)}R` : ""}`
      : row.agreement?.partners.length
        ? `agrees with ${row.agreement.partners.length} other ${row.agreement.partners.length === 1 ? "strategy" : "strategies"}`
        : row.record && row.record.decided >= 3
          ? `${Math.round((row.record.winRate ?? 0) * 100)}% of ${row.record.decided} · ${row.record.net >= 0 ? "+" : ""}${row.record.net.toFixed(0)}`
          : "no record yet";
  return (
    <li>
      <span className={`leader-bar leader-${row.tier}`} aria-hidden="true">
        <span style={{ width: tierWidth(row.tier) }} />
      </span>
      <span className="leader-name">
        {strategyName(row.family)}
        {row.side ? <span className="tag">{row.side}</span> : null}
      </span>
      <span className={`pill forecast-pill-${row.tier}`}>
        {row.tier === "favour" ? "favoured" : row.tier === "struggle" ? "poor" : row.tier === "quiet" ? "—" : "watch"}
      </span>
      <span className="muted num">{headline}</span>
    </li>
  );
}

function OpportunityRow({ row, digits }: { row: SetupRow; digits: number }) {
  const fmt = (value: number | null) => (value === null ? "—" : value.toFixed(digits));
  return (
    <li>
      <span className={`pill ${row.side === "buy" ? "pill-pass" : "pill-fail"}`}>{row.side ?? "—"}</span>
      <span className="activity-name">
        {strategyName(row.family)}
        {row.params ? <span className="muted"> · {row.params}</span> : null}
      </span>
      <span className="muted num">
        {fmt(row.entry)} / {fmt(row.sl)} / {fmt(row.tp)}
      </span>
      <span className={`num ${row.rNow != null && row.rNow >= 0 ? "pnl-up" : "pnl-down"}`}>
        {row.rNow != null ? `${row.rNow > 0 ? "+" : ""}${row.rNow.toFixed(2)}R` : "—"}
      </span>
    </li>
  );
}
