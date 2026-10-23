import type { Metadata } from "next";
import { connection } from "next/server";
import { Suspense } from "react";
import type { ReactNode } from "react";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { NavRail } from "@/components/shell/nav-rail";
import { Topbar, type TopbarData } from "@/components/shell/topbar";
import { readAuto } from "@/lib/auto";
import { readLiveStatus } from "@/lib/live";
import "./globals.css";

export const metadata: Metadata = {
  title: "MT5 Intel · trading workstation",
  description:
    "MT5 trading workstation: a live terminal, diagnostics, out-of-sample strategy tests and graded replays against the MetaTrader 5 terminal. It reads unless auto-trading is deliberately armed, and then only validated strategies on a demo account by default.",
};

/**
 * The application shell (master plan §9): icon navigation rail + compact global
 * topbar + flexible workspace. The topbar's account and connection readings are the
 * watcher's own artifacts — read on every request, never invented.
 */
async function topbarData(): Promise<TopbarData> {
  // The shell reads live artifacts on every request — the account and connection chips
  // are now-state, never cached. Marking the request dynamic keeps prerendering from
  // freezing them into the build.
  await connection();
  let auto: Awaited<ReturnType<typeof readAuto>> | null = null;
  let live: Awaited<ReturnType<typeof readLiveStatus>> | null = null;
  try {
    auto = await readAuto();
  } catch {
    /* artifact absent: the topbar shows its unavailable states */
  }
  try {
    live = await readLiveStatus();
  } catch {
    /* artifact absent: the topbar shows its unavailable states */
  }
  return {
    account: {
      login: auto?.account?.login ?? null,
      currency: auto?.account?.currency ?? null,
      tradeMode: auto?.account?.tradeMode ?? null,
      equity: auto?.account?.equity ?? null,
    },
    connection: {
      connected: live?.connected ?? false,
      watcherAlive: live?.watcherAlive ?? false,
      heartbeat: live?.ts ?? null,
      gateVerdict: live?.gateVerdict ?? null,
      marketState: live?.marketState ?? null,
      watcherSymbol: live?.symbol ?? null,
      spreadPips: live?.spreadPips ?? null,
    },
  };
}

/**
 * The topbar's artifact reads are request-time data. Under `cacheComponents` they live
 * behind a Suspense boundary, so static shells (like the not-found page) can render
 * immediately and the live chips stream in over them.
 */
async function TopbarStream() {
  const data = await topbarData();
  return <Topbar data={data} />;
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <div className="app-shell">
          <NavRail />
          <div className="app-main">
            <Suspense fallback={<header className="topbar" aria-busy="true" />}>
              <TopbarStream />
            </Suspense>
            <main id="main" className="site-main" tabIndex={-1}>
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
