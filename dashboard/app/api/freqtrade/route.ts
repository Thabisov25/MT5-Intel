import { execFile } from "child_process";
import { promisify } from "util";
import { NextResponse } from "next/server";
import { readFreqtradeResults, type FreqtradeRun } from "@/lib/freqtrade";

const exec = promisify(execFile);

// The families the strategy file exposes, keyed by the registry's ids. The same whitelist
// the runner enforces - this route spawns a process with a name from the request body, so
// the name is never passed through unchecked.
const FAMILIES = [
  "sweep_reclaim",
  "carry_trend",
  "compression_breakout",
  "trend_pullback",
  "range_extreme_mr",
  "daily_momentum",
  "ma_crossover",
  "rsi_mean_reversion",
] as const;

function pythonExe(): string {
  return (
    process.env.MT5_PYTHON ??
    // The Next server's cwd is dashboard/, so the project venv is one level up - the same
    // resolution every other reader here uses for its artifacts.
    `${process.cwd()}/../.venv/Scripts/python.exe`
  );
}

export async function GET() {
  const results = await readFreqtradeResults();
  return NextResponse.json(results, { headers: { "Cache-Control": "no-store" } });
}

/**
 * Same-origin check, as on the auto route: this spawns a local process, so a page from
 * another origin must not be able to trigger it with a fetch.
 */
function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

// A run is: bars (maybe re-exported from MT5) + conversion + backtest. The export is the
// long pole when the cache is cold, and the whole thing is a local disk job.
const RUN_TIMEOUT_MS = 10 * 60 * 1000;

export async function POST(request: Request) {
  if (!isSameOrigin(request)) {
    return NextResponse.json(
      { error: "Backtests can only be started from the console itself." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  let body: { family?: unknown; timerange?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Expected a JSON body." }, { status: 400 });
  }
  const family = body.family;
  if (typeof family !== "string" || !(FAMILIES as readonly string[]).includes(family)) {
    return NextResponse.json(
      { error: `Unknown family. Known: ${FAMILIES.join(", ")}.` },
      { status: 400 },
    );
  }
  // The timerange is only digits and a dash: it goes into a command line, so it is the one
  // other thing that must be verified rather than trusted.
  const timerange =
    typeof body.timerange === "string" && /^\d{0,8}-\d{0,8}$/.test(body.timerange)
      ? body.timerange
      : undefined;

  const args = [`${process.cwd()}/../tools/freqtrade_run_backtest.py`, family];
  if (timerange) args.push("--timerange", timerange);
  try {
    const { stdout } = await exec(pythonExe(), args, { timeout: RUN_TIMEOUT_MS });
    const lines = stdout.trim().split(/\r?\n/);
    const entry = JSON.parse(lines[lines.length - 1]) as FreqtradeRun;
    return NextResponse.json({ run: entry }, { headers: { "Cache-Control": "no-store" } });
  } catch (exc) {
    // The runner writes its failure to stderr; surface the tail of it rather than a code.
    const stderr = (exc as { stderr?: string }).stderr ?? "";
    const message = stderr.trim().split(/\r?\n/).slice(-2).join(" ") || "the run did not finish";
    return NextResponse.json({ error: message.slice(0, 400) }, { status: 500 });
  }
}
