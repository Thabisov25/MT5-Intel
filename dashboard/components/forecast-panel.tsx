"use client";

import { PanelHead } from "@/components/info";
import { useLiveFeed } from "@/components/live-feed-context";
import { strategyName } from "@/lib/strategies";
import type { Forecast, ForecastRow, ForecastTier } from "@/lib/forecast";
import type { Agreement } from "@/lib/confluence";
import { agreementsOf, orderLine } from "@/lib/confluence";
import type { Setups } from "@/lib/setups";

/**
 * The one answer the page exists for, and nothing else:
 *
 * a single card for the entry decision (side, entry, stop, target, and whether anything is
 * allowed), then every other strategy as one quiet line each. The numbers a reader acts on are
 * big; the sentences that justify them are behind the rows and behind the information control.
 * Nothing here is advice - the gate decides what may trade, and the card shows exactly where
 * that stands.
 */
const TIER_PILL: Record<ForecastTier, string> = {
  favour: "pill pill-pass",
  watch: "pill pill-info",
  struggle: "pill pill-fail",
  quiet: "pill",
};

const TIER_SHORT: Record<ForecastTier, string> = {
  favour: "favoured",
  watch: "watch",
  struggle: "poor record",
  quiet: "—",
};

/** The strongest pair of rules pointing one way on one candle right now, if any. */
function topAgreement(setups: Setups | null): Agreement | null {
  if (!setups) return null;
  return agreementsOf(setups).filter((a) => a.fresh)[0] ?? null;
}

function oneReason(row: ForecastRow): string {
  const record = row.record;
  if (row.agreement)
    return `agrees with ${row.agreement.partners.map((p) => strategyName(p)).join(", ")}`;
  if (row.status === "open") return "running now";
  if (record && record.decided >= 3 && record.winRate !== null)
    return `${Math.round(record.winRate * 100)}% of ${record.decided} · ${record.net >= 0 ? "+" : ""}${record.net.toFixed(0)}`;
  if (record) return record.run || "no closed trades yet";
  return "no record yet";
}

function DecisionCard({ forecast }: { forecast: Forecast }) {
  const { setups } = useLiveFeed();
  const agreement = topAgreement(setups);
  const top = forecast.rows.find((row) => row.tier !== "quiet") ?? forecast.rows[0];
  if (!top) return null;

  const isAgreement = agreement !== null;
  const name = isAgreement
    ? agreement.families.map((f) => strategyName(f)).join(" + ")
    : strategyName(top.family);
  const side = isAgreement ? agreement.side : top.side;
  const running = !isAgreement && top.status === "open";

  return (
    <div className="decision">
      <p className="decision-label muted">
        {isAgreement ? "Two rules agree on one candle" : running ? "Running now" : TIER_SHORT[top.tier]}
      </p>
      <div className="decision-main">
        <h2 className="decision-name">{name}</h2>
        {side ? (
          <span className={`decision-side ${side}`}>{side === "buy" ? "Buy" : "Sell"}</span>
        ) : null}
      </div>
      {isAgreement && setups ? (
        <dl className="decision-prices">
          <div>
            <dt>Entry</dt>
            <dd>{agreement.entry.toFixed(setups.digits)}</dd>
          </div>
          <div>
            <dt>Stop</dt>
            <dd>{agreement.stop.toFixed(setups.digits)}</dd>
          </div>
          <div>
            <dt>Target</dt>
            <dd>{agreement.target.toFixed(setups.digits)}</dd>
          </div>
        </dl>
      ) : null}
      {isAgreement && setups ? (
        <p className="decision-order muted">
          {orderLine(agreement, setups.digits)}
        </p>
      ) : null}
      {!isAgreement ? <p className="decision-reason muted">{oneReason(top)}</p> : null}
      {running ? (
        <p className="decision-note muted">
          A rule re-enters only while flat, so this one&rsquo;s next entry waits until the running
          card closes.
        </p>
      ) : null}
    </div>
  );
}

function Ranked({ forecast }: { forecast: Forecast }) {
  const hero = forecast.rows.find((row) => row.tier !== "quiet") ?? forecast.rows[0];
  const rest = forecast.rows.filter((row) => row.family !== hero?.family);
  return (
    <ul className="rank">
      {rest.map((row) => {
        const headline =
          row.status === "open"
            ? `${row.side ?? ""} ${row.rNow != null ? `${row.rNow > 0 ? "+" : ""}${row.rNow.toFixed(2)}R` : ""}`
            : oneReason(row);
        return (
          <li key={row.family} title={row.reasons.join(" · ")}>
            <span className={TIER_PILL[row.tier]}>{TIER_SHORT[row.tier]}</span>
            <span className="rank-name">{strategyName(row.family)}</span>
            <span className="muted num">{headline}</span>
          </li>
        );
      })}
    </ul>
  );
}

function ReSignals() {
  const { setups } = useLiveFeed();
  if (!setups) return null;
  const refires = setups.setups
    .filter((row) => row.status === "open" && row.latestSignal !== null)
    .sort((a, b) => (b.latestSignal?.signalEpoch ?? 0) - (a.latestSignal?.signalEpoch ?? 0));
  if (refires.length === 0) return null;
  return (
    <ul className="refires">
      {refires.map((row) => (
        <li key={row.family}>
          <strong>{strategyName(row.family)}</strong> re-signaled on the newest bar
          <span className="muted"> - enterable again once its card exits</span>
        </li>
      ))}
    </ul>
  );
}

function Guard() {
  const { auto, live } = useLiveFeed();
  const status = auto.status;
  const market = status?.market;
  const pieces: string[] = [];
  if (live.gateVerdict) {
    pieces.push(`gate ${live.gateVerdict.toLowerCase()}`);
    if (live.gateVerdict !== "TRADING") pieces.push(...(live.gateReasons ?? []));
  }
  if (market?.openPositions != null && status?.settings?.maxPositions != null)
    pieces.push(`slots ${market.openPositions}/${status.settings.maxPositions}`);
  if (market?.circuitBreakerTripped === true) pieces.push("circuit breaker");
  if (status?.errors.length) pieces.push("watcher error");
  return (
    <p className="guard muted" role="note">
      {pieces.length ? pieces.join(" · ") : "no guard readings yet"}
    </p>
  );
}

export function ForecastPanel() {
  const { forecast } = useLiveFeed();
  return (
    <section className="panel" aria-labelledby="forecast-heading">
      <PanelHead id="forecast-heading" title="What to take now" />
      {!forecast ? (
        <p className="muted">Waiting for the watcher&rsquo;s views - this fills in on its own.</p>
      ) : (
        <>
          <DecisionCard forecast={forecast} />
          <ReSignals />
          <Guard />
          {forecast.rows.length > 1 ? (
            <details className="fold-compact">
              <summary>Other strategies ({forecast.rows.length - 1})</summary>
              <Ranked forecast={forecast} />
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}
