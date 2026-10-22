import Link from "next/link";
import type { ReplayEntry, ReplayRun, ReplaySummary } from "@/lib/replays";
import { formatEpoch } from "@/lib/replays";
import { StrategyCell } from "@/components/strategy-guide";

const DAY_S = 86_400;

/**
 * How far behind the live bar a run's clock is, when that is far enough to mislead.
 *
 * A replay answers what a rule would have done *then*. Left unlabelled, an old clock reads as
 * "none of the strategies are picking this up", which is the opposite of the truth: they are not
 * looking there at all. Two days is the tolerance - inside that, the run still covers the move.
 */
function clockLag(run: ReplayRun, liveBarEpoch: number | null): string | null {
  if (run.asofEpoch === null || liveBarEpoch === null) return null;
  const lagS = liveBarEpoch - run.asofEpoch;
  if (lagS <= 2 * DAY_S) return null;
  const days = Math.floor(lagS / DAY_S);
  const hours = Math.round((lagS % DAY_S) / 3600);
  return (
    `This run's clock is ${days} day${days === 1 ? "" : "s"}${hours ? ` ${hours} h` : ""} behind ` +
    `the newest bar (${formatEpoch(liveBarEpoch)}), so it cannot contain anything that happened since. ` +
    `For what each rule is in right now, see the Live tab.`
  );
}

export function gradePill(hit: "TP" | "SL" | "time-exit") {
  const cls = hit === "TP" ? "pill-pass" : hit === "SL" ? "pill-fail" : "pill-info";
  const label = hit === "TP" ? "target" : hit === "SL" ? "stop" : "time exit";
  return <span className={`pill ${cls}`}>{label}</span>;
}

function fmtR(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(3)}`;
}

function totals(results: ReplaySummary[]) {
  return results.reduce(
    (acc, r) => ({
      n: acc.n + r.n,
      tp: acc.tp + (r.hitTp ?? 0),
      sl: acc.sl + (r.hitSl ?? 0),
      net: acc.net + (r.netSumR ?? 0),
    }),
    { n: 0, tp: 0, sl: 0, net: 0 },
  );
}

/** One family per row: name, what it waits for, and how its cards finished. */
export function ReplayFamiliesTable({
  results,
  caption,
  showRules = false,
}: {
  results: ReplaySummary[];
  caption?: string;
  showRules?: boolean;
}) {
  return (
    <div className="table-wrap">
      <table>
        <caption className={caption ? undefined : "sr-only"}>
          {caption ?? "Results by strategy"}
        </caption>
        <thead>
          <tr>
            <th scope="col">Strategy</th>
            <th scope="col" className="num">Cards</th>
            <th scope="col" className="num">Mean (R)</th>
            <th scope="col" className="num">Total (R)</th>
            <th scope="col" className="num">Target hits</th>
            <th scope="col" className="num">Stop hits</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.label}>
              <StrategyCell id={r.label} showRules={showRules} />
              <td className="num">{r.n}</td>
              <td className="num">{fmtR(r.netRMean)}</td>
              <td className="num detail">
                {r.netSumR === undefined ? "—" : fmtR(r.netSumR)}
              </td>
              <td className="num">{r.hitTp ?? 0}</td>
              <td className="num">{r.hitSl ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * A replay run shown in full: the clock it pretended to be, the totals, and every
 * strategy's result. Deliberately not a link — the strategy mix belongs on this page.
 */
export function ReplayRunBlock({
  run,
  liveBarEpoch = null,
  defaultOpen = false,
}: {
  run: ReplayRun;
  liveBarEpoch?: number | null;
  defaultOpen?: boolean;
}) {
  const t = totals(run.results);
  const withCards = run.results.filter((r) => r.n > 0).length;
  const lag = clockLag(run, liveBarEpoch);
  return (
    <details className="run-block" open={defaultOpen}>
      <summary>
        <h2 id={`replay-${run.id}`}>{run.asofUtc}</h2>
        <span className="tag">{run.timeframe}</span>
        <span className="run-facts">
          <span className="muted">
            {t.n.toLocaleString("en-US")} cards &middot; {withCards}/{run.results.length} strategies
          </span>
          <span className={t.net > 0 ? "pill pill-pass" : "pill pill-fail"}>
            {t.net > 0 ? "+" : ""}
            {t.net.toFixed(1)}R
          </span>
          {lag ? <span className="tag">old clock</span> : null}
        </span>
      </summary>
      {lag ? (
        <p className="muted" role="note">
          <strong>Old clock.</strong> {lag}
        </p>
      ) : null}
      <div className="verdict">
        <div>
          <p className="kicker">
            {t.n} card{t.n === 1 ? "" : "s"} &middot; {t.tp} reached target &middot; {t.sl} hit the
            stop &middot; from {withCards} of {run.results.length} strategies
          </p>
          <p className="verdict-status">
            Net if every card had been taken:{" "}
            <strong className="num">
              {t.net > 0 ? "+" : ""}
              {t.net.toFixed(1)}R
            </strong>
          </p>
        </div>
        <dl className="rows">
          <div>
            <dt>Symbol</dt>
            <dd className="num">
              {run.symbol} {run.timeframe}
            </dd>
          </div>
          <div>
            <dt>Longest hold</dt>
            <dd className="num">{run.maxHoldBars} bars</dd>
          </div>
        </dl>
      </div>
      <ReplayFamiliesTable results={run.results} />
      <p>
        <Link href={`/replays/${run.id}`}>See the individual cards for this run</Link>
      </p>
    </details>
  );
}

/** Run-level row kept for the compact list of the newest runs. */
export function ReplayEntryRow({ entry }: { entry: ReplayEntry }) {
  if (!entry.ok) {
    return (
      <tr>
        <th scope="row">{entry.id}</th>
        <td colSpan={5} className="detail">
          Unreadable: {entry.error}
        </td>
      </tr>
    );
  }
  const t = totals(entry.run.results);
  return (
    <tr>
      <th scope="row">{entry.run.asofUtc}</th>
      <td className="num">{entry.run.timeframe}</td>
      <td className="num">{t.n}</td>
      <td className="num">{fmtR(t.net)}</td>
      <td className="detail">{entry.run.generatedUtc}</td>
    </tr>
  );
}

/** Card reports list every trade family by family; sample across families instead. */
function samplePerFamily(cards: ReplayRun["cards"], perFamily: number) {
  const seen = new Map<string, number>();
  const out: ReplayRun["cards"] = [];
  for (const card of cards) {
    const n = seen.get(card.family) ?? 0;
    if (n >= perFamily) continue;
    seen.set(card.family, n + 1);
    out.push(card);
  }
  return out;
}

export function ReplayCardsTable({ run, family }: { run: ReplayRun; family?: string }) {
  const rows = family ? run.cards.filter((c) => c.family === family) : run.cards;
  const shown = samplePerFamily(rows, 12);
  if (rows.length === 0) {
    return (
      <p className="muted">
        No cards for this run. That happens when the cost rule screens out every signal, which is
        itself a result.
      </p>
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">
          The trades themselves, sampled across strategies
        </caption>
        <thead>
          <tr>
            <th scope="col">Signal (UTC)</th>
            <th scope="col">Strategy</th>
            <th scope="col">Side</th>
            <th scope="col" className="num">Entry</th>
            <th scope="col" className="num">Stop</th>
            <th scope="col" className="num">Target</th>
            <th scope="col" className="num">Risk (pips)</th>
            <th scope="col" className="num">Target (R)</th>
            <th scope="col">Result</th>
            <th scope="col" className="num">Net (R)</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((card, i) => (
            <tr key={`${card.family}-${card.signalUtc}-${i}`}>
              <th scope="row">{card.signalUtc}</th>
              <StrategyCell id={card.family} showRules={false} as="td" />
              <td>{card.side}</td>
              <td className="num">{card.entry}</td>
              <td className="num detail">{card.sl}</td>
              <td className="num detail">{card.tp}</td>
              <td className="num">{card.stopPips}</td>
              <td className="num">{card.tpR}</td>
              <td>{gradePill(card.grade.hit)}</td>
              <td className="num">
                {card.grade.netR > 0 ? "+" : ""}
                {card.grade.netR.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > shown.length && (
        <p className="muted">
          Showing {shown.length} of {rows.length} cards, sampled across strategies.
        </p>
      )}
    </div>
  );
}
