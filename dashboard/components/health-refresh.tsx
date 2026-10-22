"use client";

import { startTransition, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useVisiblePoll } from "@/components/poll";

/**
 * Re-runs the server render on an interval, so the Health page reflects a process
 * that just died (or came back) without a manual reload.
 *
 * The refresh goes through a transition on purpose. This is the heaviest page in the console - it
 * reads the tail of every log, the report folder and a rollup of the gate journal - and a plain
 * `router.refresh()` holds the tab while the server renders it, which on a ten-second timer is a
 * console that appears to stop answering for a slice of every ten seconds. Inside a transition the
 * page that is already on screen stays interactive and is replaced when the new one arrives.
 *
 * It is also one of the pages most likely to be left open in a background tab, so it borrows the
 * shared interval, which refreshes the moment the tab is looked at again instead of waiting for its
 * throttled timer. The line below says so, rather than leaving the reader to guess why a stale
 * number did not move.
 */
export function HealthRefresh({ intervalMs = 10_000 }: { intervalMs?: number }) {
  const router = useRouter();

  const refresh = useCallback(() => {
    startTransition(() => router.refresh());
  }, [router]);

  useVisiblePoll(refresh, intervalMs);

  return (
    <p className="muted" role="status">
      Re-reading every {Math.round(intervalMs / 1000)} s, and once the moment you come back to this
      tab.
    </p>
  );
}
