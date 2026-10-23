import { PageHead } from "@/components/info";
import { ContextNav, WorkspaceFrame, researchNavItems } from "@/components/shell/context-nav";
import { FreqtradePanel } from "@/components/freqtrade-panel";
import { StrategyRuleboard, TradingManual } from "@/components/strategy-guide";

export const metadata = {
  title: "MT5 intel · research",
};

/**
 * The research page is now two questions, in the order they get asked.
 *
 * 1. What does the freqtrade engine actually measure? - the comparison band, one row per family,
 *    runnable from the console.
 * 2. What is each rule? - the reference, one line per family, opened on demand.
 *
 * The train/held-out sweep tables that used to sit between them read the old researcher's
 * iteration reports. That harness grades a different set of families with a different cost model
 * and no relationship to what the engine or the live watcher does, so the card was removed rather
 * than kept as a second, contradictory account of the same rules. `tools/freqtrade_sweep.py` is
 * the search that replaced it, and its per-family params are what the run buttons above apply.
 */
export default function ResearchPage() {
  return (
    <WorkspaceFrame nav={<ContextNav heading="Research workspace" items={researchNavItems("/research")} />}>
      <PageHead
        title="Research"
        about={
          <>
            <p>
              One family of rules, one engine. Run a family to re-export its bars from MT5 when
              they are stale and backtest it; read the reference below to see what the rule waits
              for and where its stop and target go. Rejection is the default outcome, and the
              reference says plainly which families have earned anything else.
            </p>
            <TradingManual />
          </>
        }
      />
      <FreqtradePanel />
      <div style={{ marginTop: "1rem" }}>
        <StrategyRuleboard />
      </div>
    </WorkspaceFrame>
  );
}
