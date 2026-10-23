import Link from "next/link";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { ReportView } from "@/components/report-view";
import { ContextNav, WorkspaceFrame, researchNavItems } from "@/components/shell/context-nav";
import { getReport } from "@/lib/reports";

// This route awaits its id before streaming, so an unknown id can return a real 404 status.
export const instant = false;

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  // Looked up before any streaming starts, so a missing or malformed id returns a real 404.
  const entry = await getReport(id);
  if (!entry) notFound();

  if (!entry.ok) {
    return (
      <section className="empty" aria-labelledby="unreadable-heading">
        <h1 id="unreadable-heading">This report could not be read</h1>
        <p>
          <code>{entry.id}</code>: {entry.error}
        </p>
        <p>
          <Link href="/runs">Back to all runs</Link>
        </p>
      </section>
    );
  }

  return (
    <WorkspaceFrame nav={<ContextNav heading="Research workspace" items={researchNavItems("/runs")} />}>
      <header className="page-head">
        <p className="muted">
          <Link href="/runs">All runs</Link> · run {entry.report.id}
        </p>
        <h1>Diagnostic run</h1>
      </header>
      <ReportView report={entry.report} />
    </WorkspaceFrame>
  );
}
