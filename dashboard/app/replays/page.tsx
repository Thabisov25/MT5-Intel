import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { PageHead } from "@/components/info";
import { ContextNav, WorkspaceFrame, researchNavItems } from "@/components/shell/context-nav";
import { ReplayRunBlock } from "@/components/replay-view";
import { ReplayManual } from "@/components/strategy-guide";
import { listReplays } from "@/lib/replays";
import { readLiveStatus } from "@/lib/live";

export const metadata = {
  title: "MT5 intel · replays",
};

export default function ReplaysPage() {
  return (
    <WorkspaceFrame nav={<ContextNav heading="Research workspace" items={researchNavItems("/replays")} />}>
      <PageHead title="Replays" about={<ReplayManual />} />
      <Suspense fallback={<p className="muted" role="status">Reading replays…</p>}>
        <ReplaysList />
      </Suspense>
      <p className="muted">
        Each run pretends an old moment is now, lets every strategy issue the cards it would have
        issued at that moment, and grades them against what the market did next. Generate one:{" "}
        <code>
          {`.\\.venv\\Scripts\\python.exe -m mt5_intel.research.replay M5 --asof "YYYY-MM-DD HH:MM"`}
        </code>{" "}
        &middot; the live per-strategy cards are on the <Link href="/live">Live</Link> tab.
      </p>
    </WorkspaceFrame>
  );
}

async function ReplaysList() {
  await connection();
  const [entries, live] = await Promise.all([listReplays(), readLiveStatus()]);
  // The live bar's epoch is what makes a run's clock readable as "old": without it, a replay
  // from months ago looks the same as one from this morning.
  const liveBarEpoch = live.available ? live.barEpoch ?? null : null;

  if (entries.length === 0) {
    return (
      <section className="empty" aria-labelledby="no-replays-heading">
        <h2 id="no-replays-heading">No replay runs yet</h2>
        <p>Run the replay module to create the first one:</p>
        <pre><code>{`\\.venv\\Scripts\\python.exe -m mt5_intel.research.replay M5`}</code></pre>
      </section>
    );
  }

  return (
    <>
      {entries.map((entry, index) =>
        entry.ok ? (
          <ReplayRunBlock
            key={entry.run.id}
            run={entry.run}
            liveBarEpoch={liveBarEpoch}
            defaultOpen={index === 0}
          />
        ) : (
          <section className="run-block" key={entry.id} aria-labelledby={`unreadable-${entry.id}`}>
            <h2 id={`unreadable-${entry.id}`}>Unreadable replay</h2>
            <p className="muted">
              <code>{entry.id}</code>: {entry.error}
            </p>
          </section>
        ),
      )}
      {entries.length >= 12 && (
        <p className="muted">Showing the newest 12 runs.</p>
      )}
    </>
  );
}
