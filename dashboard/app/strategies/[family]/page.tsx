import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHead } from "@/components/info";
import { ContextNav, WorkspaceFrame, researchNavItems } from "@/components/shell/context-nav";
import { ReplayPanel } from "@/components/replay-panel";
import { StrategySettingsForm } from "@/components/strategy-settings-form";
import { SymbolFilter } from "@/components/symbol-filter";
import type { AutoRegistryRow } from "@/lib/auto";
import { readAuto, readSelection } from "@/lib/auto";
import { isReplayFamily } from "@/lib/replay";
import { normalizeSymbolParam } from "@/lib/symbols";
import { strategyInfo } from "@/lib/strategies";
import { REPLAY_SYMBOLS } from "@/lib/replay-constants";

/**
 * One strategy's own settings page.
 *
 * Reached by the settings gear on the Live tab's strategy table, it shows that strategy's identity - what
 * the rule waits for, where its stop and target go, what its last trial measured - and the
 * settings that belong to it, currently the take-profit mode. The name is looked up before
 * anything renders, and a name no registry offers and the catalogue does not know is a real 404:
 * a settings page for a strategy that cannot trade would read as if it could.
 */
export const instant = false;

export async function generateMetadata({ params }: { params: Promise<{ family: string }> }) {
  const { family } = await params;
  return { title: `MT5 intel · ${family} settings` };
}

export default async function StrategySettingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ family: string }>;
  searchParams: Promise<{ symbol?: string }>;
}) {
  const { family } = await params;
  // The market is the URL's, the way the journal's is: a page that says it is about EURUSD is
  // actually about EURUSD, and the view can be bookmarked or passed on.
  const chosen = normalizeSymbolParam((await searchParams).symbol);
  const symbol = chosen && chosen !== "ALL" ? chosen : "USDJPY";
  // The same bound the selection file and the API apply, so a crafted path is refused here rather
  // than read as a strategy name.
  if (!/^[A-Za-z0-9_]{1,40}$/.test(family)) notFound();

  const [status, selection] = await Promise.all([readAuto(), readSelection()]);
  const registry = status.registry ?? [];
  const row: AutoRegistryRow | null = registry.find((entry) => entry.family === family) ?? null;
  // The composite is not a family and has no registry row, but it carries settings of its own, so
  // it is the one name allowed through without one.
  const known = row !== null || family === "confluence";
  if (!known) notFound();

  const info = strategyInfo(family);
  const onList = selection?.strategies.includes(family) ?? false;
  return (
    <WorkspaceFrame nav={<ContextNav heading="Research workspace" items={researchNavItems("/strategies")} />}>
      <PageHead
        title={info.name}
        about={
          <p>
            <span className="tag">{family}</span>{" "}
            <Link href="/live">Back to the Live tab&rsquo;s strategy table</Link> · this strategy{" "}
            {onList ? "may trade (it is ticked on the Live picker)" : "is not on the trade list"}.
          </p>
        }
      />
      {/* The replay grades this family's rule over the recent window and draws each trade
          as a box on real bars - the visual half of "how did it actually play out". The
          composite is not a family and has nothing to replay.

          One rule, one market at a time: the filter is the journal's own, so a page that is
          about EURUSD says so in its URL and in its chart. */}
      {isReplayFamily(family) ? (
        <>
          <SymbolFilter
            basePath={`/strategies/${family}`}
            label="Filter by symbol:"
            current={symbol}
            symbols={[...REPLAY_SYMBOLS]}
          />
          <ReplayPanel family={family} digits={null} symbol={symbol} />
        </>
      ) : null}
      <StrategySettingsForm family={family} registryRow={row} selection={selection} />
      <section className="panel">
        <h2>What the rule does</h2>
        <dl className="facts">
          <div>
            <dt>Waits for</dt>
            <dd>{info.waits}</dd>
          </div>
          <div>
            <dt>Entry</dt>
            <dd>{info.entry}</dd>
          </div>
          <div>
            <dt>Stop loss</dt>
            <dd>{info.stop}</dd>
          </div>
          <div>
            <dt>Take profit (fixed)</dt>
            <dd>{info.target}</dd>
          </div>
          <div>
            <dt>Timeframes</dt>
            <dd>{info.timeframes}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{info.origin}</dd>
          </div>
          <div>
            <dt>Measured status</dt>
            <dd>{info.evidence}</dd>
          </div>
        </dl>
      </section>
    </WorkspaceFrame>
  );
}
