"use client";

import { startTransition, useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { useVisiblePoll } from "@/components/poll";

/** The run on screen is only ever as new as the last poll, so the poll is short. */
const POLL_MS = 10_000;

/**
 * Keeps the Latest page on the newest run.
 *
 * A diagnostic pass takes about a minute, while the console opens its tab the moment it starts
 * one, so the page a reader lands on is usually the *previous* run. That is invisible while the
 * account is unchanged and actively misleading right after a change: the console keeps showing
 * the account that was connected when the run it is displaying was made. Nothing in the page
 * changes on its own, so this watches the report folder and asks the server to re-render when
 * something newer is there.
 */
export function LatestRunWatcher({ currentId }: { currentId: string }) {
  const router = useRouter();
  const [newer, setNewer] = useState<string | null>(null);

  const check = useCallback(() => {
    fetch("/api/reports/latest", { cache: "no-store" })
      .then((res) => (res.ok ? (res.json() as Promise<{ id: string | null }>) : null))
      .then((next) => {
        if (!next?.id || next.id === currentId) return;
        setNewer(next.id);
        // Re-render from the server: the run on disk now is the one whose account, verdict and
        // numbers this page should be showing. Through a transition, because a report is the
        // largest page here and the reader should be able to keep scrolling the old one while the
        // new one renders.
        startTransition(() => router.refresh());
      })
      .catch(() => {
        /* keep the run that is on screen until the next tick */
      });
  }, [currentId, router]);

  // On the shared interval, so coming back to this tab checks at once: a run that finished while it
  // was in the background is a report the reader is looking for right now.
  useVisiblePoll(check, POLL_MS);

  // After the refresh the page carries the newer run, so this clears itself without extra state.
  if (!newer || newer === currentId) return null;
  return (
    <p className="muted" role="status" aria-live="polite">
      A newer diagnostic run ({newer}) is on disk — updating this page.
    </p>
  );
}
