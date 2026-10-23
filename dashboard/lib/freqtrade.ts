import { readFile } from "fs/promises";
import path from "path";

/**
 * The freqtrade backtest results, as `tools/freqtrade_run_backtest.py` writes them.
 *
 * One entry per family, added by whichever run finished last - the panel shows what was
 * actually measured (with the buy parameters in force at the time), never what a defaults
 * table says should have been. `expectancy_r` is profit divided by each trade's own stop
 * distance, averaged the way the hyperopt loss scores it, so the panel and the search can
 * never disagree about what expectancy means here.
 */
export interface FreqtradeRun {
  strategy: string;
  timerange: string;
  trades: number;
  wins: number;
  losses: number;
  /** Fraction of decided trades that won, or null when nothing was decided. */
  win_rate: number | null;
  /** Expectancy in R per trade (pre-spread: freqtrade charges no cost here). */
  expectancy_r: number | null;
  net_profit_pct: number;
  max_drawdown_pct: number;
  /** The hyperopt buy parameters in force, when a best-params file named this strategy. */
  buy_params?: Record<string, number | string> | null;
  generated_utc: string;
}

export interface FreqtradeArtifact {
  runs: Record<string, FreqtradeRun>;
  generated_utc?: string;
}

export function freqtradePath(): string {
  return (
    process.env.MT5_FREQTRADE_RESULTS ??
    path.resolve(process.cwd(), "..", "dashboard", "freqtrade-results.json")
  );
}

/**
 * The latest artifact, or an empty one. A missing or unreadable file reads as "nothing has
 * been run yet" - the same tolerance every reader in this console has for an artifact that
 * another process may not have written.
 */
export async function readFreqtradeResults(): Promise<FreqtradeArtifact> {
  try {
    const text = await readFile(freqtradePath(), "utf8");
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { runs: {} };
    }
    const data = parsed as Record<string, unknown>;
    return {
      runs:
        typeof data.runs === "object" && data.runs !== null && !Array.isArray(data.runs)
          ? (data.runs as Record<string, FreqtradeRun>)
          : {},
      generated_utc: typeof data.generated_utc === "string" ? data.generated_utc : undefined,
    };
  } catch {
    return { runs: {} };
  }
}
