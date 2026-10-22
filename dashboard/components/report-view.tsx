import { PanelHead } from "@/components/info";
import type { Account, Check, HistoryTimeframe, Report, Risk, SpreadByHour, Status } from "@/lib/reports";

const STATUS_LABEL: Record<Status, string> = {
  PASS: "Pass",
  WARN: "Warn",
  FAIL: "Fail",
  NOT_VERIFIED: "Not verified",
  INFO: "Info",
};

const SAST = new Intl.DateTimeFormat("en-ZA", {
  timeZone: "Africa/Johannesburg",
  dateStyle: "medium",
  timeStyle: "short",
});

function formatSast(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : SAST.format(date);
}

function formatNumber(value: number | null, digits = 2): string {
  return value === null ? "n/a" : value.toFixed(digits);
}

export function StatusPill({ status }: { status: Status }) {
  return <span className={`pill pill-${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span>;
}

function Verdict({ report }: { report: Report }) {
  const counts = report.checks.reduce<Partial<Record<Status, number>>>((acc, check) => {
    acc[check.status] = (acc[check.status] ?? 0) + 1;
    return acc;
  }, {});
  const failing = report.checks.filter((check) => check.status === "FAIL");
  return (
    <section className="verdict" aria-labelledby="verdict-heading">
      <div>
        <p className="kicker">Overall</p>
        <h2 id="verdict-heading" className="verdict-status">
          <StatusPill status={report.overall} />
        </h2>
        <p className="muted">
          Generated {formatSast(report.generatedUtc)} SAST · UTC {report.generatedUtc}
        </p>
      </div>
      <dl className="counts">
        {(Object.keys(STATUS_LABEL) as Status[]).map((status) => (
          <div key={status}>
            <dt>{STATUS_LABEL[status]}</dt>
            <dd className="num">{counts[status] ?? 0}</dd>
          </div>
        ))}
      </dl>
      {failing.length > 0 ? (
        <ul className="blockers" aria-label="Failing checks">
          {failing.map((check) => (
            <li key={`${check.area}-${check.name}`}>
              <strong>
                {check.area} · {check.name}
              </strong>{" "}
              {check.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function AccountBlock({ account, report }: { account: Account | null; report: Report }) {
  if (!account) {
    return <p className="muted">No account data in this report.</p>;
  }
  const rows: [string, string][] = [
    ["Login", account.loginMasked],
    ["Server", account.server],
    ["Company", account.company],
    ["Currency", account.currency],
    ["Mode", account.mode],
    ["Leverage", `1:${account.leverage}`],
    ["Balance", `${formatNumber(account.balance)} ${account.currency}`],
    ["Equity", `${formatNumber(account.equity)} ${account.currency}`],
    [
      "USD/ZAR",
      report.usdZar ? `${formatNumber(report.usdZar.rate, 4)} (${report.usdZar.source})` : "n/a",
    ],
  ];
  return (
    <dl className="facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd className="num">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ChecksTable({ checks }: { checks: Check[] }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Diagnostic checks</caption>
        <thead>
          <tr>
            <th scope="col">Area</th>
            <th scope="col">Check</th>
            <th scope="col">Status</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((check) => (
            <tr key={`${check.area}-${check.name}`}>
              <th scope="row">{check.area}</th>
              <td>{check.name}</td>
              <td>
                <StatusPill status={check.status} />
              </td>
              <td className="detail">{check.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RiskTable({ risk }: { risk: Risk | null }) {
  if (!risk) {
    return <p className="muted">Risk table not available. The symbol or tick specs were not verified.</p>;
  }
  return (
    <>
      <dl className="facts compact">
        <div>
          <dt>Reference equity</dt>
          <dd className="num">R{formatNumber(risk.referenceEquityZar)}</dd>
        </div>
        <div>
          <dt>Risk per trade ({risk.riskPct}%)</dt>
          <dd className="num">
            R{formatNumber(risk.riskBudgetZar)} · {formatNumber(risk.riskBudgetUsc)} USC
          </dd>
        </div>
        <div>
          <dt>Pip value, min lot</dt>
          <dd className="num">R{formatNumber(risk.pipValuePerMinLotZar, 4)}</dd>
        </div>
        <div>
          <dt>Widest stop at min lot</dt>
          <dd className="num">
            {risk.maxStopPipsAtMinLot === null ? "n/a" : `${formatNumber(risk.maxStopPipsAtMinLot, 1)} pips`}
          </dd>
        </div>
      </dl>
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Risk fit by stop distance</caption>
          <thead>
            <tr>
              <th scope="col" className="num">Stop (pips)</th>
              <th scope="col" className="num">Risk per min lot (R)</th>
              <th scope="col" className="num">Max volume</th>
              <th scope="col" className="num">Actual risk (R)</th>
              <th scope="col">Fits budget</th>
            </tr>
          </thead>
          <tbody>
            {risk.table.map((row) => (
              <tr key={row.stopPips}>
                <th scope="row" className="num">{formatNumber(row.stopPips, 0)}</th>
                <td className="num">{formatNumber(row.riskPerMinLotZar)}</td>
                <td className="num">{formatNumber(row.maxVolume, 2)}</td>
                <td className="num">{formatNumber(row.actualRiskZar)}</td>
                <td>{row.fits ? "Yes" : "No, unsuitable"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function HistoryTable({ history }: { history: Record<string, HistoryTimeframe> }) {
  const entries = Object.entries(history);
  if (entries.length === 0) {
    return <p className="muted">No history was read. See the history checks above for the terminal error.</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">History depth by timeframe</caption>
        <thead>
          <tr>
            <th scope="col">Timeframe</th>
            <th scope="col" className="num">Bars</th>
            <th scope="col" className="num">Span (days)</th>
            <th scope="col">First (UTC)</th>
            <th scope="col">Last (UTC)</th>
            <th scope="col" className="num">Unexpected gaps</th>
            <th scope="col" className="num">Longest gap (h)</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([timeframe, row]) => (
            <tr key={timeframe}>
              <th scope="row">{timeframe}</th>
              <td className="num">{row.bars.toLocaleString("en-ZA")}</td>
              <td className="num">{formatNumber(row.spanDays, 1)}</td>
              <td>{row.firstUtc}</td>
              <td>{row.lastUtc}</td>
              <td className="num">
                {row.gapsUnexpected}
                {row.utcClassified ? "" : " (server clock)"}
              </td>
              <td className="num">{formatNumber(row.longestUnexpectedGapH, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SpreadChart({ spread }: { spread: SpreadByHour | null }) {
  if (!spread) {
    return <p className="muted">No spread data in this report.</p>;
  }
  const maxMedian = Math.max(0, ...spread.hours.map((h) => h.medianPips ?? 0));
  return (
    <>
      <p className="muted">
        {spread.note} Hours are {spread.utcClassified ? "UTC" : "server clock (offset unknown)"}, with
        SAST shown alongside.
      </p>
      <table className="spread">
        <caption className="sr-only">Median spread by hour</caption>
        <thead>
          <tr>
            <th scope="col">UTC</th>
            <th scope="col">SAST</th>
            <th scope="col" className="bar-col">Median spread (pips)</th>
            <th scope="col" className="num">Median</th>
            <th scope="col" className="num">P90</th>
            <th scope="col" className="num">Samples</th>
          </tr>
        </thead>
        <tbody>
          {spread.hours.map((hour) => {
            const width = hour.medianPips && maxMedian > 0 ? (hour.medianPips / maxMedian) * 100 : 0;
            return (
              <tr key={hour.hourUtc}>
                <th scope="row" className="num">{String(hour.hourUtc).padStart(2, "0")}:00</th>
                <td className="num">{String(hour.hourSast).padStart(2, "0")}:00</td>
                <td className="bar-col">
                  <span className="bar" style={{ width: `${width}%` }} aria-hidden="true" />
                </td>
                <td className="num">{formatNumber(hour.medianPips)}</td>
                <td className="num">{formatNumber(hour.p90Pips)}</td>
                <td className="num">{hour.samples}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

export function ReportView({ report }: { report: Report }) {
  return (
    <div className="report">
      <Verdict report={report} />

      {/* One panel per question, each on its own row: the tables are wide, and a short panel
          stretched to the height of a long one beside it reads as missing data. */}
      <div className="grid-main">
        <section aria-labelledby="checks-heading" className="panel span-full">
          <PanelHead
            title="Checks"
            id="checks-heading"
            about={
              <>
                Every check the diagnostic ran, in order. A FAIL here is what the verdict strip at
                the top lists; NOT VERIFIED means the check could not be evaluated at all, which is
                not the same as passing.
              </>
            }
          />
          <ChecksTable checks={report.checks} />
        </section>

        <section aria-labelledby="account-heading" className="panel span-full">
          <PanelHead
            title="Account and risk"
            id="account-heading"
            about={
              <>
                <p>
                  Read from the terminal when the run was taken, with the login masked. The risk
                  budget is what one trade at {report.risk ? `${report.risk.riskPct}%` : "the set fraction"} of
                  reference equity works out to, and the fit table below says which stop distances
                  fit inside it at the smallest lot.
                </p>
                <p>A stop with no fitting row is one the account cannot take at minimum size.</p>
              </>
            }
          />
          <AccountBlock account={report.account} report={report} />
          <h3 className="subhead">Risk fit</h3>
          <RiskTable risk={report.risk} />
        </section>

        <section aria-labelledby="history-heading" className="panel span-full">
          <PanelHead
            title="History depth"
            id="history-heading"
            about={
              <>
                How far back the terminal can serve each timeframe, and where the gaps are. A run
                whose window reaches past the first bar here is graded on less data than it asked
                for.
              </>
            }
          />
          <HistoryTable history={report.history} />
        </section>

        <section aria-labelledby="spread-heading" className="panel span-full">
          <PanelHead
            title="Spread by hour"
            id="spread-heading"
            about={
              <>
                The spread the account is actually charged, hour by hour. Every cost in this system
                is charged in R against these values: a wider spread is a smaller edge, and the
                quiet hours are when a stop has to be widest to survive the round trip.
              </>
            }
          />
          <SpreadChart spread={report.spreadByHour} />
        </section>
      </div>
    </div>
  );
}
