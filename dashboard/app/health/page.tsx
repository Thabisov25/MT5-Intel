import Link from "next/link";
import { ClusteringPanel } from "@/components/clustering-panel";
import { HealthRefresh } from "@/components/health-refresh";
import { PageHead, PanelHead } from "@/components/info";
import { ContextNav, WorkspaceFrame, systemNavItems } from "@/components/shell/context-nav";
import { Tabs } from "@/components/tabs";
import { formatBytes, LOG_CAP_BYTES, readHealth, toneClass, type PerSymbolWatch } from "@/lib/health";

// Reads live process and log state on every request; never serve it from cache.
export const instant = false;

export const metadata = {
  title: "MT5 intel · health",
};

const SAST = new Intl.DateTimeFormat("en-ZA", {
  timeZone: "Africa/Johannesburg",
  dateStyle: "medium",
  timeStyle: "short",
});

function formatSast(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : SAST.format(date);
}

function formatAge(ms: number | null): string {
  if (ms === null) return "unknown";
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} h ago`;
  return `${(seconds / 86400).toFixed(1)} d ago`;
}

function TonePill({ tone, children }: { tone: "ok" | "warn" | "bad"; children: React.ReactNode }) {
  return <span className={`pill ${toneClass(tone)}`}>{children}</span>;
}

function verdictSummary(verdicts: Record<string, number>): string {
  const parts = Object.entries(verdicts).map(([verdict, count]) => `${count} ${verdict}`);
  return parts.length === 0 ? "no verdicts recorded" : parts.join(" · ");
}

/** How many log files are listed before the rest fold away. The stale ones rarely change. */
const LOGS_SHOWN = 6;

type Health = Awaited<ReturnType<typeof readHealth>>;
type LogFile = Health["logs"][number];

function LogRow({ log }: { log: LogFile }) {
  const nearCap = log.bytes > LOG_CAP_BYTES;
  const empty = log.bytes === 0;
  return (
    <tr>
      <th scope="row">
        <code>{log.name}</code>
        {nearCap ? (
          <>
            {" "}
            <TonePill tone="warn">over cap</TonePill>
          </>
        ) : null}
      </th>
      <td className={nearCap ? "num detail" : "num"}>{empty ? "empty" : formatBytes(log.bytes)}</td>
      <td className="num">{log.backups}</td>
      <td className="detail">{formatAge(log.modifiedMs)}</td>
      <td>
        {log.tail.length === 0 ? (
          <span className="detail">—</span>
        ) : (
          <details>
            <summary>Show {log.tail.length} lines</summary>
            <pre>
              <code>{log.tail.join("\n")}</code>
            </pre>
          </details>
        )}
      </td>
    </tr>
  );
}

function LogTable({ logs }: { logs: LogFile[] }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Log files, newest write first</caption>
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col" className="num">
              Size
            </th>
            <th scope="col" className="num">
              Backups
            </th>
            <th scope="col">Last write</th>
            <th scope="col">Newest lines</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <LogRow key={log.name} log={log} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What the launcher started, and what its own pid file remembers starting. */
function StatePanel({ health }: { health: Health }) {
  return (
    <>
      <PanelHead
        title="Components"
        about={
          <>
            The launcher starts each component separately, and a component is only shown as running
            when its own evidence says so.
          </>
        }
      />
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Components the launcher starts</caption>
          <thead>
            <tr>
              <th scope="col">Component</th>
              <th scope="col">State</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {health.components.map((component) => (
              <tr key={component.name}>
                <th scope="row">{component.name}</th>
                <td>
                  <TonePill tone={component.tone}>{component.state}</TonePill>
                </td>
                <td className="detail">{component.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <PanelHead
        title="Launcher processes"
        level={3}
        about={
          health.processes.length > 0 ? (
            <>
              The launcher&rsquo;s own record, written {formatAge(health.pidsRecordedMs)}. A pid can be
              reused by the OS after a process exits, so treat an unexpected entry as a hint, not
              proof.
            </>
          ) : (
            <>
              No launcher session is recorded — <code>logs/launcher-pids.json</code> is absent. Either
              the console was started by hand, or it has been stopped since.
            </>
          )
        }
      />
      {health.processes.length === 0 ? (
        <p className="muted">Nothing recorded. Started by hand, or stopped since.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Processes the launcher tracked</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col" className="num">
                  PID
                </th>
                <th scope="col">Alive</th>
              </tr>
            </thead>
            <tbody>
              {health.processes.map((process) => (
                <tr key={`${process.name}-${process.pid}`}>
                  <th scope="row">{process.name}</th>
                  <td className="num">{process.pid}</td>
                  <td>
                    <TonePill tone={process.alive ? "ok" : "bad"}>
                      {process.alive ? "running" : "exited"}
                    </TonePill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** What the gate kept saying, by code and by hour. */
function GatePanel({ history }: { history: Health["gateHistory"] }) {
  if (!history.available) {
    return <p className="muted">{history.reason}</p>;
  }
  return (
    <>
      <PanelHead
        title="Reason codes"
        about={
          <>
            <p>
              A code shown as <em>never cleared</em> appeared in every cycle since it first
              appeared: either nothing measures it yet, or the condition is genuinely always true. A
              code missing from a later cycle does track something, and the reason list on the Live
              page cannot tell those two apart.
            </p>
            <p>
              &ldquo;Cycles present&rdquo; is how many of the cycles since that code first appeared
              contained it.
            </p>
          </>
        }
      />
      <p className="muted">
        {history.history.cycles.toLocaleString("en-US")} cycles in the last{" "}
        {history.history.windowDays} days · {formatSast(history.history.firstTs)} &rarr;{" "}
        {formatSast(history.history.lastTs)} · {verdictSummary(history.history.verdicts)} · summary
        written {formatSast(history.history.generatedAt)}
      </p>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Gate reason codes seen in this window</caption>
          <thead>
            <tr>
              <th scope="col">Code</th>
              <th scope="col">Status</th>
              <th scope="col" className="num">
                Cycles present
              </th>
              <th scope="col">Latest cycle</th>
              <th scope="col">Last cleared</th>
            </tr>
          </thead>
          <tbody>
            {history.history.codes.map((row) => (
              <tr key={row.code}>
                <th scope="row">
                  <code>{row.code}</code>
                </th>
                <td>
                  <TonePill tone={row.everCleared ? "ok" : "warn"}>
                    {row.everCleared ? "clears" : "never cleared"}
                  </TonePill>
                </td>
                <td className="num">
                  {row.cyclesPresent}
                  <span className="detail">/{row.cyclesSinceFirstSeen}</span>
                </td>
                <td>
                  <span className="detail">{row.inLatestCycle ? "present" : "absent"}</span>
                </td>
                <td className="detail">{row.lastClearedTs ? formatSast(row.lastClearedTs) : "-"}</td>
              </tr>
            ))}
            {history.history.codes.length === 0 ? (
              <tr>
                <td colSpan={5} className="detail">
                  No watch cycle has been journaled yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="muted">
        {history.history.neverSeen.length > 0 ? (
          <>
            {history.history.neverSeen.length} of {history.history.codesTotal} gate codes have not
            appeared in this window: <code>{history.history.neverSeen.join(", ")}</code>
          </>
        ) : (
          <>Every gate code has appeared in this window.</>
        )}
      </p>

      <div className="subhead-rule" />
      <ClusteringPanel history={history.history} />
    </>
  );
}

/**
 * Per-symbol markets: every watched symbol that is not the primary, and whether its own cycle is
 * actually being run.
 *
 * This panel exists because the failure this page once missed was exactly this shape: the primary
 * traded on every cycle while each extra market filed "no per-symbol market reading yet this
 * cycle" - and nothing else on the page said so. The components table above can only say that a
 * watcher is alive; a watcher can be alive and still only ever look at one market.
 */
function SymbolFeedTable({ feeds }: { feeds: PerSymbolWatch[] }) {
  return (
    <>
      <PanelHead
        title="Per-symbol markets"
        level={3}
        about={
          <>
            Every watched symbol after the primary, and whether its own cycle ran: the readings its
            cycle saw, and what its own auto step last said - placed, refused, or the reason it
            stood aside. A market whose cycle stopped is a market that will never trade again, and
            nothing else on the account would show it until the balance did.
          </>
        }
      />
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Per watched symbol, its reading and last decision</caption>
          <thead>
            <tr>
              <th scope="col">Symbol</th>
              <th scope="col">State</th>
              <th scope="col">Readings</th>
              <th scope="col">Last decision</th>
              <th scope="col">Age</th>
            </tr>
          </thead>
          <tbody>
            {feeds.map((feed) => (
              <tr key={feed.symbol}>
                <th scope="row">
                  <code>{feed.symbol}</code>
                </th>
                <td>
                  <TonePill tone={feed.tone}>{feed.state}</TonePill>
                </td>
                <td className="detail">{feed.readings}</td>
                <td className="detail">{feed.decision}</td>
                <td className="num detail">{formatAgeS(feed.decisionAgeS)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function formatAgeS(ageS: number | null): string {
  if (ageS == null) return "—";
  return formatAge(Date.now() - ageS * 1000);
}

/** The files the launcher writes and trims. */
function FilesPanel({ logs, dir }: { logs: LogFile[]; dir: string }) {
  return (
    <>
      <PanelHead
        title="Logs"
        about={
          <>
            The launcher caps <code>launcher.log</code> at {formatBytes(LOG_CAP_BYTES)} and keeps
            three backups. The dashboard logs are trimmed while the session runs as well as at
            launch, and the per-run logs are cleared on each launch. Folder: <code>{dir}</code>
          </>
        }
      />
      {logs.length === 0 ? (
        <p className="muted">No log files yet — the console has not been launched from the script.</p>
      ) : (
        <>
          <LogTable logs={logs.slice(0, LOGS_SHOWN)} />
          {logs.length > LOGS_SHOWN ? (
            <details className="more">
              <summary>Show {logs.length - LOGS_SHOWN} older log files</summary>
              <LogTable logs={logs.slice(LOGS_SHOWN)} />
            </details>
          ) : null}
        </>
      )}
    </>
  );
}

export default async function HealthPage() {
  const health = await readHealth();
  const latest = health.latestDiagnostic;
  const history = health.gateHistory;
  // Newest write first: the file that moved a minute ago is the one being asked about.
  const logsByRecency = [...health.logs].sort((a, b) => (b.modifiedMs ?? 0) - (a.modifiedMs ?? 0));

  const up = health.components.filter((c) => c.tone === "ok").length;
  const down = health.components.filter((c) => c.tone !== "ok");
  const neverCleared = history.available
    ? history.history.codes.filter((row) => !row.everCleared).length
    : 0;
  const overCap = logsByRecency.filter((log) => log.bytes > LOG_CAP_BYTES).length;

  return (
    <WorkspaceFrame nav={<ContextNav heading="System workspace" items={systemNavItems()} />}>
      <PageHead
        title="Health"
        about={
          <>
            Whether the console is actually running, what each part last said, and how big the logs
            are. Everything here is read from the files the launcher already writes, so it reports
            observed state rather than an assumption.
          </>
        }
      >
        <HealthRefresh />
      </PageHead>

      {/* The one thing that should never need a click: is it up, and what is failing. */}
      <section className="verdict" aria-labelledby="overall-heading">
        <div>
          <p className="kicker">Overall</p>
          <h2 id="overall-heading" className="verdict-status">
            <TonePill tone={health.overall}>
              {health.overall === "ok" ? "Healthy" : health.overall === "warn" ? "Degraded" : "Attention"}
            </TonePill>
            {down.length > 0 ? (
              <span className="muted"> {down.map((c) => `${c.name}: ${c.state}`).join(" · ")}</span>
            ) : null}
          </h2>
          <p className="muted">{health.overallNote}</p>
        </div>
        <dl className="counts">
          <div>
            <dt>Up</dt>
            <dd className="num">
              {up}/{health.components.length}
            </dd>
          </div>
          <div>
            <dt>Iterations</dt>
            <dd className="num">{health.iterations.length}</dd>
          </div>
          <div>
            <dt>Logs</dt>
            <dd className="num">{formatBytes(health.logBytesTotal)}</dd>
          </div>
        </dl>
        <p className="verdict-stamp">
          Read {formatSast(health.generatedAt)} SAST
          {latest ? (
            <>
              {" · latest diagnostic "}
              <strong>{latest.ok ? latest.report.overall : "unreadable"}</strong>{" "}
              {latest.ok ? (
                <Link href={`/runs/${latest.report.id}`}>open report</Link>
              ) : (
                <code>{latest.id}</code>
              )}
            </>
          ) : (
            <>
              {" · no diagnostic yet — run "}
              <code>{`.\\\\.venv\\\\Scripts\\\\python.exe -m mt5_intel`}</code>
            </>
          )}{" "}
          · <Link href="/runs">all {health.diagnosticCount} runs</Link>
        </p>
      </section>

      <Tabs
        ariaLabel="Health views"
        tabs={[
          {
            id: "state",
            label: "State",
            badge: (
              <span className={toneClass(health.symbolFeeds.every((f) => f.tone === "ok") ? "ok" : "warn")}>
                {health.symbolFeeds.length === 0
                  ? "no extras"
                  : `${health.symbolFeeds.filter((f) => f.tone !== "ok").length} drifting`
              }
              </span>
            ),
            content: (
              <>
                <StatePanel health={health} />
                {health.symbolFeeds.length > 0 ? <SymbolFeedTable feeds={health.symbolFeeds} /> : null}
              </>
            ),
          },
          {
            id: "gate",
            label: "Gate",
            badge: neverCleared > 0 ? `${neverCleared} never cleared` : "all clear at some point",
            content: <GatePanel history={history} />,
          },
          {
            id: "files",
            label: "Files",
            badge:
              overCap > 0 ? (
                <span className="pnl-down">{overCap} over cap</span>
              ) : (
                `${logsByRecency.length} logs`
              ),
            content: <FilesPanel logs={logsByRecency} dir={health.logsDir} />,
          },
        ]}
      />
    </WorkspaceFrame>
  );
}
