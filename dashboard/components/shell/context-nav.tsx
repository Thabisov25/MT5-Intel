import Link from "next/link";

/**
 * The contextual secondary navigation (master plan §8–9): a per-workspace list that
 * changes with the selected workspace. Pages place it as the left column of their
 * workspace grid. Only entries backed by real routes are ever rendered — callers
 * pass the items.
 */

export interface ContextNavItem {
  href: string;
  label: string;
  hint?: string;
  /** Marks the item as the current page even when the route matcher is not exact. */
  current?: boolean;
}

export function ContextNav({
  heading,
  items,
}: {
  heading: string;
  items: ContextNavItem[];
}) {
  return (
    <nav className="context-nav" aria-label={heading}>
      <p className="context-heading">{heading}</p>
      {items.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={item.current ? "page" : undefined}
          title={item.hint}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

/** The contextual navigation of the Trading workspace, marked by the current route. */
export function tradingNavItems(current: "/" | "/live" | "/trades" | "/terminal"): ContextNavItem[] {
  return [
    { href: "/", label: "Today’s brief", current: current === "/" },
    { href: "/terminal", label: "Trading terminal", current: current === "/terminal" },
    { href: "/live", label: "Live decisions", current: current === "/live" },
    { href: "/trades", label: "Trading journal", current: current === "/trades" },
  ];
}

/** The contextual navigation of the Research workspace. */
export function researchNavItems(
  current: "/research" | "/replays" | "/runs" | "/strategies",
): ContextNavItem[] {
  return [
    { href: "/research", label: "Strategy tests", current: current === "/research" },
    { href: "/replays", label: "Graded replays", current: current === "/replays" },
    { href: "/runs", label: "Diagnostic runs", current: current === "/runs" },
    { href: "/strategies/confluence", label: "Strategy dossier", current: current === "/strategies" },
  ];
}

/** The contextual navigation of the System workspace. */
export function systemNavItems(): ContextNavItem[] {
  return [
    { href: "/health", label: "System health", current: true },
    { href: "/live", label: "Auto-trading controls", hint: "The auto-trading drawer on Live" },
    { href: "/runs", label: "Diagnostic runs" },
  ];
}

/**
 * Wraps a workspace page: contextual nav on the left (wide screens), the page's own
 * composition in the main column. Keeps every page inside one workspace grammar
 * without forcing pages into one generic layout.
 */
export function WorkspaceFrame({
  nav,
  children,
}: {
  nav?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={`workspace${nav ? " has-context" : ""}`}>
      {nav}
      <div className="workspace-main">{children}</div>
    </div>
  );
}
