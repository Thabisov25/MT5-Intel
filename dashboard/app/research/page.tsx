import { Suspense } from "react";
import { connection } from "next/server";
import { Info, PageHead } from "@/components/info";
import { StrategyCell, StrategyRuleboard, TradingManual } from "@/components/strategy-guide";
import {
  formatParams,
  formatR,
  listIterations,
  type IterationEntry,
  type IterationFamily,
  type IterationRun,
} from "@/lib/research";
import { formatStamp } from "@/lib/replays";
import { SCALP_IDS } from "@/lib/strategies";

export const metadata = {
  title: "MT5 intel · research",
};

export default function ResearchPage() {
  return (
    <>
      <PageHead
        title="Research"
        about={
          <>
            <p>
              Each run sweeps a family&rsquo;s parameters on a training window, then grades only the
              training winner on a disjoint test window. The runs are the evidence; the rules
              themselves are in the reference at the foot of this page.
            </p>
            <TradingManual />
          </>
        }
      />
      <Suspense fallback={<p className="muted" role="status">Reading iteration reports…</p>}>
        <Iterations />
      </Suspense>
      <p className="muted">
        Generate one:{" "}
        <code>{`.\\\\.venv\\\\Scripts\\\\python.exe -m mt5_intel.research.iterate M5`}</code>
      </p>
      <details className="fold">
        <summary>The rules, family by family</summary>
        <StrategyRuleboard />
      </details>
    </>
  );
}

async function Iterations() {
  await connection();
  const entries = await listIterations();

  if (entries.length === 0) {
    return (
      <section className="empty" aria-labelledby="no-iterations-heading">
        <h2 id="no-iterations-heading">No iteration runs yet</h2>
        <p>Run the harness to create the first one:</p>
        <pre><code>{`\\\\.venv\\\\Scripts\\\\python.exe -m mt5_intel.research.iterate M5`}</code></pre>
      </section>
    );
  }

  return (
    <>
      {entries.map((entry, index) =>
        entry.ok ? (
          <RunSection key={entry.run.id} run={entry.run} defaultOpen={index === 0} />
        ) : (
          <UnreadableSection key={entry.id} entry={entry} />
        ),
      )}
    </>
  );
}

function UnreadableSection({ entry }: { entry: Extract<IterationEntry, { ok: false }> }) {
  return (
    <section className="empty" aria-labelledby={`unreadable-${entry.id}`}>
      <h2 id={`unreadable-${entry.id}`}>Unreadable report</h2>
      <p className="muted">
        <code>{entry.id}</code>: {entry.error}
      </p>
    </section>
  );
}

function verdictPill(validated: boolean) {
  return (
    <span className={`pill ${validated ? "pill-pass" : "pill-fail"}`}>
      {validated ? "validated" : "rejected"}
    </span>
  );
}

function HurdleCells({ row }: { row: IterationFamily }) {
  if (row.hurdle === null) {
    return (
      <>
        <td className="num detail">—</td>
        <td className="num detail">—</td>
      </>
    );
  }
  const { rawSignals, keptSignals, keptShare, medianStopPips, floorPips } = row.hurdle;
  const share = keptShare === null ? "" : ` (${Math.round(keptShare * 100)}%)`;
  return (
    <>
      <td className="num">
        {keptSignals}
        <span className="detail">/{rawSignals}</span>
        <span className="detail">{share}</span>
      </td>
      <td className="num detail">
        {medianStopPips === null ? "— / " : `${medianStopPips} / `}
        {floorPips} pips
      </td>
    </>
  );
}

function FamilyTable({ run }: { run: IterationRun }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">
          Every family the run tested, with its training and held-out test results
        </caption>
        <thead>
          <tr>
            <th scope="col">Strategy</th>
            <th scope="col">Settings that won on train</th>
            <th scope="col" className="num">Train (R)</th>
            <th scope="col" className="num">Test (R)</th>
            <th scope="col" className="num">Gross test (R)</th>
            <th scope="col" className="num">Placebo (R)</th>
            <th scope="col" className="num">1.5&times; costs (R)</th>
            <th scope="col" className="num">Kept by cost rule</th>
            <th scope="col" className="num">Stop / floor</th>
            <th scope="col">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {run.families.map((row) => (
            <tr key={row.family}>
              <StrategyCell
                id={row.family}
                badge={SCALP_IDS.includes(row.family) ? "scalp template" : undefined}
              />
              <td className="detail">{formatParams(row.params)}</td>
              <td className="num">
                {formatR(row.trainMean)} <span className="detail">n={row.trainN}</span>
              </td>
              <td className="num">
                {formatR(row.testMean)} <span className="detail">n={row.testN}</span>
              </td>
              <td className="num detail">{formatR(row.grossTestMean)}</td>
              <td className="num">{formatR(row.placeboMean)}</td>
              <td className="num">{formatR(row.stressedMean)}</td>
              <HurdleCells row={row} />
              <td>{verdictPill(row.validated)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One run, folded to a single line until it is asked about. There are a dozen of these and each
 * one holds a twelve-column table: stacked open they were a nine-thousand-pixel page where the
 * newest run was the only one anybody wanted first.
 */
function RunSection({ run, defaultOpen = false }: { run: IterationRun; defaultOpen?: boolean }) {
  const passed = run.families.filter((row) => row.validated).map((row) => row.family);
  const costShare =
    run.cost === null
      ? null
      : `${Math.round((2 / run.cost.minStopSpreadMult) * 100)}% of risk`;
  return (
    <details className="run-block" open={defaultOpen}>
      <summary>
        <h2 id={`run-${run.id}`}>{run.timeframe}</h2>
        <span className="tag">{formatStamp(run.stamp)}</span>
        <span className="run-facts">
          <span className="muted">{run.families.length} strategies</span>
          <span className={passed.length > 0 ? "pill pill-pass" : "pill pill-fail"}>
            {passed.length > 0 ? passed.join(", ") : "no candidate"}
          </span>
          <Info label="this run">
          <p>
            Every family is swept over the training bars, and only the training winner is graded
            &mdash; on held-out bars it never saw. That is what makes the train and test columns
            comparable.
            {run.scalpingIncluded ? " This run includes the four scalp templates." : ""}
          </p>
          {run.cost !== null ? (
            <p>
              Cost rule: a stop closer than the run&rsquo;s multiple of the spread is either dropped
              or widened, so the round trip can never cost more than the cost budget. &ldquo;Kept by
              cost rule&rdquo; counts the signals that survived that screen, and is blank for the six
              earlier families,            which do not use it.
            </p>
          ) : null}
          </Info>
        </span>
      </summary>
      <div className="verdict">
        <div>
          <p className="kicker">
            {run.families.length} strategies swept &middot; {passed.length} validated &middot;{" "}
            {run.families.length - passed.length} rejected
          </p>
          <p className="verdict-status">{run.verdict}</p>
          <p className="muted">
            {run.barCount.toLocaleString("en-US")} bars ({run.firstUtc} &rarr; {run.lastUtc})
            &middot; graded on {run.testBars.toLocaleString("en-US")} held-out bars (
            {(run.testFraction * 100).toFixed(0)}%) &middot; longest hold {run.maxHoldBars} bars
            {run.scalpingIncluded ? " · includes the four scalp templates" : ""}
          </p>
        </div>
        {run.cost !== null ? (
          <dl className="rows">
            <div>
              <dt>Cost budget</dt>
              <dd>{costShare}</dd>
            </div>
            <div>
              <dt>Min stop</dt>
              <dd className="num">
                {run.cost.minStopSpreadMult}&times; spread &middot; {run.cost.floorPips} pips
              </dd>
            </div>
            <div>
              <dt>Median spread</dt>
              <dd className="num">{run.cost.spreadPipsMedian} pips</dd>
            </div>
          </dl>
        ) : (
          <dl className="rows">
            <div>
              <dt>Cost rule</dt>
              <dd>not applied to this run</dd>
            </div>
          </dl>
        )}
      </div>
      <FamilyTable run={run} />
    </details>
  );
}
