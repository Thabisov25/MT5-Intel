"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The main nav, with the current page marked.
 *
 * The reader of this console lives on two pages - Live to decide, Trades to record and review - so
 * those lead the strip and everything research-shaped is folded into one group beside them. That is
 * not a demotion: it is the difference between the pages you act on and the pages you consult.
 * `aria-current` is what a screen reader reads out, the rule is what the eye sees.
 */
const PRIMARY: { href: string; label: string }[] = [
  { href: "/live", label: "Live" },
  { href: "/trades", label: "Trades" },
  { href: "/", label: "Home" },
];

const RESEARCH: { href: string; label: string }[] = [
  { href: "/runs", label: "All runs" },
  { href: "/replays", label: "Replays" },
  { href: "/research", label: "Research" },
  { href: "/health", label: "Health" },
];

export function Nav() {
  const path = usePathname() ?? "/";
  const active = (href: string) =>
    href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
  return (
    <nav aria-label="Main">
      <ul>
        {PRIMARY.map(({ href, label }) => (
          <li key={href}>
            <Link href={href} aria-current={active(href) ? "page" : undefined} data-current={active(href) || undefined}>
              {label}
            </Link>
          </li>
        ))}
        <li className="nav-group">
          <details className="nav-details">
            <summary aria-label="Research pages">
              Research
              {/* Marked when any grouped page is the one being read, so the group never
                  hides where you are. */}
              {RESEARCH.some(({ href }) => active(href)) ? <span className="nav-dot" aria-hidden="true" /> : null}
            </summary>
            <ul>
              {RESEARCH.map(({ href, label }) => (
                <li key={href}>
                  <Link href={href} aria-current={active(href) ? "page" : undefined} data-current={active(href) || undefined}>
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </details>
        </li>
      </ul>
    </nav>
  );
}
