import type { GateHistory } from "@/lib/gate-history";
import { Info } from "@/components/info";
import {
  busiestHours,
  codeProfiles,
  formatHour,
  formatHourRuns,
  formatMultiple,
  formatPercent,
  MIN_CYCLES_FOR_PEAK,
  type CodeProfile,
  type HourCell,
} from "@/lib/clustering";

/**
 * Where the reason codes fall in the day.
 *
 * The strip is a visual summary and carries no information of its own: every fact in it also
 * appears as text in the same row, because a colour-shaded bar chart is unreadable to a screen
 * reader and hard to read for anyone who cannot separate the shades.
 */

function stripTooltip(cell: HourCell, code: string): string {
  if (cell.cycles === 0) return `${formatHour(cell.hour)} UTC — no cycles`;
  const body = `${formatHour(cell.hour)} UTC — ${code} in ${cell.present} of ${cell.cycles} cycles (${formatPercent(cell.rate)})`;
  return cell.cycles < MIN_CYCLES_FOR_PEAK ? `${body} · thin sample` : body;
}

function CodeRow({ profile }: { profile: CodeProfile }) {
  const rates = profile.hours.map((cell) => cell.rate).filter((value): value is number => value !== null);
  const rowMax = Math.max(0.0001, ...rates);
  return (
    <tr>
      <th scope="row">
        <code>{profile.code}</code>
      </th>
      <td>
        <span className="cluster-strip" aria-hidden="true">
          {profile.hours.map((cell) => {
            // A one-cycle hour reaches 100% as easily as a 32-cycle hour does, so thin samples are
            // capped and tinted: the bar must not let a single cycle outrank a well-sampled hour.
            const thin = cell.cycles > 0 && cell.cycles < MIN_CYCLES_FOR_PEAK;
            const share = cell.rate === null ? 0 : cell.rate / rowMax;
            return (
              <span
                key={cell.hour}
                className={`cluster-bar${cell.cycles === 0 ? " empty-hour" : thin ? " thin" : ""}`}
                style={{
                  height: `${0.15 + share * (thin ? 0.25 : 0.85)}rem`,
                  opacity: thin ? 0.55 : cell.cycles === 0 ? 1 : 0.35 + 0.65 * share,
                }}
                title={stripTooltip(cell, profile.code)}
              />
            );
          })}
        </span>
      </td>
      <td className="num">
        {profile.peak ? formatHour(profile.peak.hour) : <span className="detail">—</span>}
      </td>
      <td className="num">
        {profile.peak ? (
          <>
            {formatPercent(profile.peak.rate)}{" "}
            <span className="detail">
              {profile.peak.present}/{profile.peak.cycles}
            </span>
          </>
        ) : (
          <span className="detail">—</span>
        )}
      </td>
      <td className="num">{formatMultiple(profile.peakVsOverall)}</td>
      <td className="num">
        {profile.present}
        <span className="detail">/{profile.cycles}</span>
      </td>
    </tr>
  );
}

export function ClusteringPanel({ history }: { history: GateHistory }) {
  if (history.byHour.length === 0) {
    return (
      <p className="muted">
        This artifact has no time breakdown yet — it was written by a watcher that predates it. The
        next refresh, within five minutes, adds the hour and session views.
      </p>
    );
  }

  const profiles = codeProfiles(history);
  const busiest = busiestHours(history);
  const quiet = profiles.filter((profile) => profile.present === 0);
  // Appeared, but in no hour busy enough to quote a rate for. Their strip cells still show where,
  // so the positions are spelled out here rather than left to a tooltip.
  const tooRare = profiles
    .filter((profile) => profile.present > 0 && profile.peak === null)
    .map((profile) => ({
      code: profile.code,
      present: profile.present,
      hours: profile.hours.filter((cell) => cell.present > 0).map((cell) => cell.hour),
    }));

  return (
    <>
      <div className="panel-head">
        <h3>Hour profile (UTC 00&ndash;23)</h3>
        <Info label="the hour profile">
          <p>
            Each strip is UTC hour 00 &rarr; 23 (SAST is a fixed UTC+2, so 00:00 UTC is 02:00 SAST).
            Bars are scaled by the code&rsquo;s <em>rate</em> in that hour &mdash; cycles containing it
            over cycles journaled &mdash; not by its count, because an hour with more cycles contains
            more of everything. &ldquo;vs overall&rdquo; is the peak rate divided by the code&rsquo;s
            rate across the whole window: 1.0&times; means the code is spread evenly through the day
            and does not cluster.
          </p>
          <p>
            A peak needs at least {MIN_CYCLES_FOR_PEAK} cycles in the hour to be quoted, so a single
            busy minute cannot look like a pattern; those thin hours are drawn short and pale, and
            their tooltip says so. Hours with no journaled cycle are drawn as a flat tick.
          </p>
          {history.rollup ? (
            <p>
              Built from {history.rollup.hoursKept.toLocaleString("en-US")} rolled hour
              {history.rollup.hoursKept === 1 ? "" : "s"} rather than every cycle: the last refresh
              read {history.rollup.rowsRead.toLocaleString("en-US")} journal row
              {history.rollup.rowsRead === 1 ? "" : "s"}, folded {history.rollup.hoursWritten} hour
              {history.rollup.hoursWritten === 1 ? "" : "s"}
              {history.rollup.prunedHours > 0 ? (
                <>
                  {" "}
                  and dropped {history.rollup.prunedHours} hour
                  {history.rollup.prunedHours === 1 ? "" : "s"} past the window
                </>
              ) : null}
              .
            </p>
          ) : null}
        </Info>
      </div>
      {busiest.length > 0 ? (
        <p className="muted">
          Busiest hours so far:{" "}
          {busiest
            .map(
              (bucket) =>
                `${formatHour(bucket.hour)} (${bucket.cycles} ${bucket.cycles === 1 ? "cycle" : "cycles"})`,
            )
            .join(", ")}
          .
        </p>
      ) : null}
      {history.rollup && history.rollup.unplacedCycles > 0 ? (
        <p className="muted">
          <strong>
            {history.rollup.unplacedCycles} cycle
            {history.rollup.unplacedCycles === 1 ? "" : "s"} could not be placed in an hour
          </strong>{" "}
          &mdash; see <code>logs</code> and the journal for the error; that is a clock or ordering
          bug, not a market condition.
        </p>
      ) : null}

      <div className="table-wrap">
        <table className="cluster">
          <caption className="sr-only">
            Hour profile for each gate code, counted from the watcher&rsquo;s journal. Every figure in
            a row is also in the tooltip of the strip cell it came from.
          </caption>
          <thead>
            <tr>
              <th scope="col">Code</th>
              <th scope="col">
                Hour profile (UTC 00&ndash;23)
                <span className="cluster-axis" aria-hidden="true">
                  <span>00</span>
                  <span>06</span>
                  <span>12</span>
                  <span>18</span>
                  <span>23</span>
                </span>
              </th>
              <th scope="col" className="num">
                Peak hour
              </th>
              <th scope="col" className="num">
                Rate there
              </th>
              <th scope="col" className="num">
                vs overall
              </th>
              <th scope="col" className="num">
                Overall
              </th>
            </tr>
          </thead>
          <tbody>
            {profiles
              .filter((profile) => profile.present > 0)
              .map((profile) => (
                <CodeRow key={profile.code} profile={profile} />
              ))}
            {profiles.every((profile) => profile.present === 0) ? (
              <tr>
                <td colSpan={6} className="detail">
                  No reason code has been journaled yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {tooRare.length > 0 ? (
        <p className="muted">
          No hour is busy enough to quote a rate for:{" "}
          {tooRare.map((item, index) => (
            <span key={item.code}>
              {index > 0 ? "; " : ""}
              <code>{item.code}</code> ({item.present} cycle{item.present === 1 ? "" : "s"} at{" "}
              {formatHourRuns(item.hours)} UTC)
            </span>
          ))}
          . Their strip cells above still show where they fell.
        </p>
      ) : null}

      <div className="panel-head">
        <h3>By session</h3>
        <Info label="the session breakdown">
          Sessions are the conventional local business window of each centre (D-011), so the UTC
          hours shift by an hour twice a year. Overlaps are their own row, so the cycles column sums
          to the total rather than counting a cycle twice.
        </Info>
      </div>
      {!history.sessionsAvailable ? (
        <p className="muted">
          Sessions could not be resolved, so no session breakdown is shown:{" "}
          {history.sessionsNote ?? "the time-zone database is unavailable"}. The hour profile above
          does not depend on it.
        </p>
      ) : history.bySession.length === 0 ? (
        <p className="muted">No journaled cycle has a resolvable session yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Codes seen in each session</caption>
            <thead>
              <tr>
                <th scope="col">Session</th>
                <th scope="col">UTC hours seen</th>
                <th scope="col" className="num">
                  Cycles
                </th>
                <th scope="col">Codes seen, strongest rate first</th>
              </tr>
            </thead>
            <tbody>
              {history.bySession.map((bucket) => {
                const rows = profiles
                  .flatMap((profile) =>
                    profile.sessions
                      .filter((cell) => cell.session === bucket.session)
                      .map((cell) => ({ code: profile.code, cell })),
                  )
                  .sort((a, b) => (b.cell.rate ?? 0) - (a.cell.rate ?? 0) || a.code.localeCompare(b.code));
                return (
                  <tr key={bucket.session}>
                    <th scope="row">{bucket.session}</th>
                    <td className="detail">{formatHourRuns(bucket.hoursSeen)}</td>
                    <td className="num">{bucket.cycles}</td>
                    <td>
                      {rows.length === 0 ? (
                        <span className="detail">no codes</span>
                      ) : (
                        <ul className="cluster-codes">
                          {rows.map(({ code, cell }) => (
                            <li key={code}>
                              <code>{code}</code>{" "}
                              <span className="detail">
                                {cell.present}/{cell.cycles} ({formatPercent(cell.rate)})
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {history.unparsedTs > 0 ? (
        <p className="muted">
          {history.unparsedTs} journaled cycle{history.unparsedTs === 1 ? "" : "s"} could not be
          placed in an hour or a session because its timestamp did not parse. They are counted in the
          per-code totals above but appear in no time bucket.
        </p>
      ) : null}
      {quiet.length > 0 ? (
        <p className="muted">
          {quiet.length} of {profiles.length} gate codes have not appeared in this window, so they
          have no profile: <code>{quiet.map((profile) => profile.code).join(", ")}</code>
        </p>
      ) : null}
    </>
  );
}
