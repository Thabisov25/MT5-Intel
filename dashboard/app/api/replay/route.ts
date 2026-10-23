import { execFile } from "child_process";
import { promisify } from "util";
import { NextResponse } from "next/server";
import {
  DEFAULT_REPLAY_SYMBOL,
  REPLAY_FAMILIES,
  REPLAY_SYMBOLS,
  isReplayFamily,
  isReplaySymbol,
  readFamilyReplay,
  replayAgeS,
} from "@/lib/replay";

const exec = promisify(execFile);

function pythonExe(): string {
  return (
    process.env.MT5_PYTHON ??
    // The Next server's cwd is dashboard/, so the project venv is one level up - the same
    // resolution every other reader here uses for its artifacts.
    `${process.cwd()}/../.venv/Scripts/python.exe`
  );
}

/**
 * Same-origin check, as on the freqtrade route: a POST here spawns a local process, so a
 * page from another origin must not be able to trigger it with a fetch.
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

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const family = query.get("family") ?? "";
  if (!isReplayFamily(family)) {
    return NextResponse.json(
      { error: `Unknown family. Known: ${REPLAY_FAMILIES.join(", ")}.` },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  // The market is optional and defaults to the primary, so a page that has never heard of
  // the filter still gets the chart it always got.
  const symbol = query.get("symbol") ?? DEFAULT_REPLAY_SYMBOL;
  if (!isReplaySymbol(symbol)) {
    return NextResponse.json(
      { error: `Unknown symbol. Known: ${REPLAY_SYMBOLS.join(", ")}.` },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  const replay = await readFamilyReplay(family, symbol);
  return NextResponse.json(
    { replay, ageS: replay ? replayAgeS(replay) : null },
    { headers: { "Cache-Control": "no-store" } },
  );
}

// One replay is: read ~2000 bars from the terminal + grade one family over them. A local
// disk job on a warm connection; the timeout only bounds a hung terminal.
const RUN_TIMEOUT_MS = 2 * 60 * 1000;

export async function POST(request: Request) {
  if (!isSameOrigin(request)) {
    return NextResponse.json(
      { error: "Replays can only be started from the console itself." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  let body: { family?: unknown; symbol?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Expected a JSON body." }, { status: 400 });
  }
  const family = body.family;
  if (typeof family !== "string" || !isReplayFamily(family)) {
    return NextResponse.json(
      { error: `Unknown family. Known: ${REPLAY_FAMILIES.join(", ")}.` },
      { status: 400 },
    );
  }
  // The symbol is a name from a short list, and it goes onto a command line, so it is checked
  // against the whitelist rather than passed through - the same rule the family gets.
  const symbol =
    typeof body.symbol === "string" && isReplaySymbol(body.symbol)
      ? body.symbol
      : DEFAULT_REPLAY_SYMBOL;
  try {
    const { stdout } = await exec(
      pythonExe(),
      [
        `${process.cwd()}/../tools/family_replay_export.py`,
        family,
        "--symbol",
        symbol,
      ],
      { timeout: RUN_TIMEOUT_MS },
    );
    const replay = await readFamilyReplay(family, symbol);
    if (!replay) {
      // The exporter printed something the artifact reader cannot see; surface its tail
      // rather than an empty chart with no explanation.
      const tail = stdout.trim().split(/\r?\n/).slice(-1).join(" ");
      return NextResponse.json(
        { error: tail.slice(0, 400) || "the replay produced no artifact" },
        { status: 500 },
      );
    }
    return NextResponse.json(
      { replay, ageS: 0 },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (exc) {
    const stderr = (exc as { stderr?: string }).stderr ?? "";
    const message = stderr.trim().split(/\r?\n/).slice(-2).join(" ") || "the replay did not finish";
    return NextResponse.json({ error: message.slice(0, 400) }, { status: 500 });
  }
}
