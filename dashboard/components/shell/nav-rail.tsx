"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  Beaker,
  BookOpen,
  CandlestickChart,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  HeartPulse,
  Sunrise,
  type LucideIcon,
} from "lucide-react";

/**
 * The primary navigation rail (master plan §9): a slim glass rail with icon buttons,
 * tooltips, and an expansion mechanism that reveals labels. Destinations are the
 * actual existing routes; the new /terminal workspace leads because this is a
 * trading workstation first.
 */

interface RailItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Exact match only (for "/" and other index routes). */
  exact?: boolean;
}

interface RailGroup {
  items: RailItem[];
}

const GROUPS: RailGroup[] = [
  {
    items: [{ href: "/terminal", label: "Terminal", icon: CandlestickChart }],
  },
  {
    items: [
      { href: "/", label: "Brief", icon: Sunrise, exact: true },
      { href: "/live", label: "Live", icon: Activity },
      { href: "/trades", label: "Journal", icon: BookOpen },
    ],
  },
  {
    items: [
      { href: "/research", label: "Research", icon: Beaker },
      { href: "/replays", label: "Replays", icon: ClipboardCheck },
      { href: "/runs", label: "Runs", icon: ClipboardCheck },
    ],
  },
  {
    items: [{ href: "/health", label: "Health", icon: HeartPulse }],
  },
];

const STORAGE_KEY = "mt5-nav-rail";

export function NavRail() {
  const path = usePathname() ?? "/";
  const [expanded, setExpanded] = useState(false);

  // The rail is a workspace preference, restored between sessions (master plan §12).
  // The restore is scheduled rather than run inline: it must happen after hydration, and
  // a synchronous setState in the effect body would cascade a second render.
  useEffect(() => {
    const id = window.setTimeout(() => {
      try {
        setExpanded(window.localStorage.getItem(STORAGE_KEY) === "expanded");
      } catch {
        /* storage unavailable: the collapsed rail is a fine default */
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, []);

  const toggle = () => {
    setExpanded((value) => {
      const next = !value;
      try {
        window.localStorage.setItem(STORAGE_KEY, next ? "expanded" : "collapsed");
      } catch {
        /* non-fatal */
      }
      return next;
    });
  };

  const active = (item: RailItem) =>
    item.exact ? path === item.href : path === item.href || path.startsWith(`${item.href}/`);

  return (
    <nav className="nav-rail" aria-label="Workspaces" data-expanded={expanded || undefined}>
      <Link href="/" className="nav-rail-brand">
        <span className="brand-glyph" aria-hidden="true">
          M5
        </span>
        <span className="brand-word">MT5 Intel</span>
      </Link>

      {GROUPS.map((group, index) => (
        <div className="nav-rail-group" key={index}>
          {index > 0 ? <div className="nav-rail-sep" role="presentation" /> : null}
          {group.items.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="nav-rail-item"
                data-tip={item.label}
                aria-current={active(item) ? "page" : undefined}
              >
                <Icon size={18} aria-hidden="true" />
                <span className="nav-rail-label">{item.label}</span>
              </Link>
            );
          })}
        </div>
      ))}

      <button type="button" className="nav-rail-toggle" onClick={toggle} aria-label={expanded ? "Collapse navigation rail" : "Expand navigation rail"}>
        {expanded ? <ChevronLeft size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <span className="nav-rail-label">Collapse</span>
      </button>
    </nav>
  );
}
