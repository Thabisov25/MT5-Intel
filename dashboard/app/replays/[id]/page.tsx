import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Info, PanelHead } from "@/components/info";
import { ReplayCardsTable, ReplayFamiliesTable } from "@/components/replay-view";
import { ReplayManual } from "@/components/strategy-guide";
import { formatStamp, getReplay } from "@/lib/replays";

// Per-run page reads the report file at request time.
export const instant = false;

export default async function ReplayRunPage({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const entry = await getReplay(id);
  if (!entry) notFound();

  if (!entry.ok) {
    return (
      <section className="empty" aria-labelledby="unreadable-replay-heading">
        <h1 id="unreadable-replay-heading">This replay could not be read</h1>
        <p>
          <code>{entry.id}</code>: {entry.error}
        </p>
        <p>
          <Link href="/replays">Back to all replays</Link>
        </p>
      </section>
    );
  }

  const run = entry.run;
  const cards = run.results.reduce((acc, r) => acc + r.n, 0);
  const net = run.results.reduce((acc, r) => acc + (r.netSumR ?? 0), 0);

  return (
    <>
      <header className="page-head">
        <p className="muted">
          <Link href="/replays">All replays</Link> &middot; run {run.id}
        </p>
        <div className="page-title">
          <h1>
            {run.timeframe} replay at {run.asofUtc}
          </h1>
          <Info label="replays">
            <ReplayManual />
          </Info>
        </div>
      </header>
      <section className="verdict" aria-label="Run result">
        <div>
          <p className="kicker">
            {cards} card{cards === 1 ? "" : "s"} across {run.results.length} strategies
          </p>
          <p className="verdict-status">
            Net if every card had been taken:{" "}
            <strong className="num">
              {net > 0 ? "+" : ""}
              {net.toFixed(1)}R
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
          <div>
            <dt>Generated</dt>
            <dd>{formatStamp(run.generatedUtc)}</dd>
          </div>
        </dl>
      </section>
      <PanelHead
        title="Results by strategy"
        about={
          <>
            <p>
              Every strategy the run let issue cards, including the ones that issued none. A
              negative mean says doing nothing was better at this clock.
            </p>
            <p>
              Mean and total R are net of cost: one spread going in, one coming out, charged against
              the stop distance. Small card counts make an average noisy &mdash; a rule is judged on
              the held-out test windows on the research page, not here.
            </p>
          </>
        }
      />
      <ReplayFamiliesTable results={run.results} showRules />
      <PanelHead
        title="The trades themselves"
        about={
          <>
            Every card is one trade. The stop and target are fixed before entry, and the stop
            distance is the R unit: &minus;1R is a stop-out, and the target column is how many R it
            was aiming for. Up to 12 cards are shown from each strategy, so no rule dominates the
            sample.
          </>
        }
      />
      <ReplayCardsTable run={run} />
    </>
  );
}
