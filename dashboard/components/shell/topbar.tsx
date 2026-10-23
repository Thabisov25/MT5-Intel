"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * The global topbar (master plan §9): current workspace, active MT5 account, demo
 * badge, broker/watcher connection state, data freshness and a UTC clock. Compact
 * information groups only — every value rendered here is read from the watcher's
 * artifacts by the server and passed in; nothing is invented.
 */

export interface TopbarData {
  account: {
    login: number | null;
    currency: string | null;
    tradeMode: string | null;
    equity: number | null;
  };
  connection: {
    connected: boolean;
    watcherAlive: boolean;
    heartbeat: string | null;
    gateVerdict: string | null;
    marketState: string | null;
    watcherSymbol: string | null;
    spreadPips: number | null;
  };
}

/** Route → workspace + page title for the shell chrome. */
function workspaceOf(path: string): { workspace: string; title: string } {
  if (path.startsWith("/terminal")) return { workspace: "Terminal", title: "Trading terminal" };
  if (path.startsWith("/live")) return { workspace: "Trading", title: "Live decisions" };
  if (path.startsWith("/trades")) return { workspace: "Trading", title: "Trading journal" };
  if (path === "/") return { workspace: "Trading", title: "Today’s brief" };
  if (path.startsWith("/research")) return { workspace: "Research", title: "Strategy research" };
  if (path.startsWith("/replays")) return { workspace: "Research", title: "Graded replays" };
  if (path.startsWith("/runs")) return { workspace: "Research", title: "Diagnostic runs" };
  if (path.startsWith("/strategies")) return { workspace: "Research", title: "Strategy dossier" };
  if (path.startsWith("/health")) return { workspace: "System", title: "System health" };
  return { workspace: "Console", title: "MT5 Intel" };
}

function heartbeatAgeMs(heartbeat: string | null, now: number): number | null {
  if (!heartbeat) return null;
  const ms = new Date(heartbeat).getTime();
  return Number.isNaN(ms) ? null : Math.max(0, now - ms);
}

/**
 * Money without `toLocaleString`: the server's ICU and the browser's disagree on
 * en-ZA grouping, which re-renders the whole tree (hydration mismatch). This format
 * is deterministic in both environments — same digits, same separators.
 */
function fixed(value: number): string {
  const [whole, decimals] = Math.abs(value).toFixed(2).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${value < 0 ? "−" : ""}${grouped}.${decimals}`;
}

export function Topbar({ data }: { data: TopbarData }) {
  const path = usePathname() ?? "/";
  const { workspace, title } = workspaceOf(path);
  const [now, setNow] = useState<number | null>(null);

  // The clock and the freshness counter tick client-side; the payload itself comes
  // from the server render and from the page's own polling. The first tick is scheduled
  // instead of run inline, so the effect body never sets state synchronously.
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, 1000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, []);

  const age = now === null ? null : heartbeatAgeMs(data.connection.heartbeat, now);
  const fresh = age !== null && age < 180_000;
  const stale = age !== null && age >= 180_000;
  const connectionTone = !data.connection.watcherAlive
    ? "bad"
    : !data.connection.connected
      ? "warn"
      : fresh
        ? "ok"
        : stale
          ? "warn"
          : "bad";

  const clock =
    now === null
      ? "—"
      : new Date(now).toLocaleTimeString("en-GB", {
          timeZone: "UTC",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });

  const freshness =
    age === null
      ? "no heartbeat"
      : age < 60_000
        ? `${Math.round(age / 1000)} s ago`
        : age < 3_600_000
          ? `${Math.round(age / 60_000)} min ago`
          : `${(age / 3_600_000).toFixed(1)} h ago`;

  return (
    <header className="topbar">
      <div className="topbar-title">
        <p className="kicker">{workspace} workspace</p>
        <h1>{title}</h1>
      </div>

      <div className="topbar-status">
        {data.account.login !== null ? (
          <span className="chip">
            Account <strong>{data.account.login}</strong>
            {data.account.currency ? <span>· {data.account.currency}</span> : null}
          </span>
        ) : (
          <span className="chip">Account unavailable</span>
        )}

        {data.account.equity !== null ? (
          <span className="chip">
            Equity <strong>{fixed(data.account.equity)}</strong>
          </span>
        ) : null}

        {/* Only shown when the backend really reports the mode; DEMO is called out. */}
        {data.account.tradeMode ? (
          <span className={`chip ${data.account.tradeMode === "DEMO" ? "chip-demo" : ""}`}>
            {data.account.tradeMode}
          </span>
        ) : null}

        <span className="chip" title={`Heartbeat ${freshness}`}>
          <span className={`status-dot ${connectionTone}`} aria-hidden="true" />
          {data.connection.watcherAlive ? "Watcher" : "Watcher down"}
          {data.connection.connected ? " · MT5 linked" : " · MT5 offline"}
        </span>

        {data.connection.gateVerdict ? (
          <span className="chip">
            Gate <strong>{data.connection.gateVerdict}</strong>
          </span>
        ) : null}

        {data.connection.watcherSymbol ? (
          <span className="chip">
            {data.connection.watcherSymbol}
            {data.connection.spreadPips !== null ? (
              <span> · {data.connection.spreadPips.toFixed(1)} pips</span>
            ) : null}
          </span>
        ) : null}

        <span className="chip" title={`Data freshness: ${freshness}`}>
          <strong suppressHydrationWarning>{clock}</strong> UTC
        </span>

        <Link className="chip" href="/health">
          Health
        </Link>
      </div>
    </header>
  );
}
