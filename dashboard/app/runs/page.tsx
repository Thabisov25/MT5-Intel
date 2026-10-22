import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { PageHead } from "@/components/info";
import { StatusPill } from "@/components/report-view";
import { listReports } from "@/lib/reports";

export default function RunsPage() {
  return (
    <>
      <PageHead
        title="All runs"
        about={
          <>
            Every diagnostic pass the console has recorded, newest first, up to 50. Each row counts
            the checks in that run by status; open one for the detail behind it.
          </>
        }
      />
      <Suspense fallback={<p className="muted" role="status">Reading reports…</p>}>
        <RunsTable />
      </Suspense>
    </>
  );
}

async function RunsTable() {
  await connection();
  const entries = await listReports();

  if (entries.length === 0) {
    return <p className="muted">No reports yet. Run the diagnostic to create the first one.</p>;
  }

  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Diagnostic runs</caption>
        <thead>
          <tr>
            <th scope="col">Run (UTC)</th>
            <th scope="col">Overall</th>
            <th scope="col" className="num">Pass</th>
            <th scope="col" className="num">Fail</th>
            <th scope="col" className="num">Not verified</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) =>
            entry.ok ? (
              <tr key={entry.report.id}>
                <th scope="row">
                  <Link href={`/runs/${entry.report.id}`}>{entry.report.generatedUtc}</Link>
                </th>
                <td>
                  <StatusPill status={entry.report.overall} />
                </td>
                <td className="num">{entry.report.checks.filter((c) => c.status === "PASS").length}</td>
                <td className="num">{entry.report.checks.filter((c) => c.status === "FAIL").length}</td>
                <td className="num">
                  {entry.report.checks.filter((c) => c.status === "NOT_VERIFIED").length}
                </td>
              </tr>
            ) : (
              <tr key={entry.id}>
                <th scope="row">{entry.id}</th>
                <td colSpan={4} className="detail">
                  Unreadable: {entry.error}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}
