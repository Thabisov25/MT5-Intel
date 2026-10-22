import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHead } from "@/components/info";
import { StrategySettingsForm } from "@/components/strategy-settings-form";
import type { AutoRegistryRow } from "@/lib/auto";
import { readAuto, readSelection } from "@/lib/auto";
import { strategyInfo } from "@/lib/strategies";

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
}: {
  params: Promise<{ family: string }>;
}) {
  const { family } = await params;
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
    <>
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
    </>
  );
}
