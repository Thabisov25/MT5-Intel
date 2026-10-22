import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Info } from "@/components/info";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "MT5 intel · research console",
  description:
    "MT5 research console: diagnostics, out-of-sample strategy tests and graded replays. It reads unless auto-trading is deliberately armed, and then only validated strategies on a demo account by default.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <header className="site-header">
          <div className="brand-row">
            <p className="brand-name">MT5 intel</p>
            <Info label="MT5 intel">
              <p>
                Diagnostics, out-of-sample strategy tests and graded replays against the MetaTrader 5
                terminal, journalled as it goes.
              </p>
              <p>
                Every page here reads. Auto-trading is off until it is armed with two separate
                switches, refuses a live-money account unless a third setting allows it, and only
                acts on the strategies ticked on the Live tab.
              </p>
            </Info>
          </div>
          <Nav />
        </header>
        <main id="main" className="site-main" tabIndex={-1}>
          {children}
        </main>
      </body>
    </html>
  );
}
