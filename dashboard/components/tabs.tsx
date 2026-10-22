"use client";

import { useRef, useState } from "react";
import type { ReactNode } from "react";

/**
 * One question on screen at a time.
 *
 * These pages had grown into stacks: every panel visible at once, so the page was long enough that
 * the answer you wanted was never where you were looking. The panels themselves are fine - they are
 * just not all being asked about at the same moment.
 *
 * Each tab carries a badge, so the reason to open it (a failing component, a code that never
 * clears, a log over its cap) is legible without opening anything.
 *
 * Panel content is passed in as nodes rather than built here, so the panels stay server components
 * and keep reading files at request time.
 */
export type Tab = {
  id: string;
  label: string;
  /** Shown on the tab itself: the state of the thing behind it. */
  badge?: ReactNode;
  content: ReactNode;
};

export function Tabs({ tabs, ariaLabel }: { tabs: Tab[]; ariaLabel: string }) {
  const [activeId, setActiveId] = useState(tabs[0]?.id ?? "");
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({});
  const active = tabs.find((tab) => tab.id === activeId) ?? tabs[0];

  if (!active) return null;

  // Left/right arrows move between tabs, as a tablist is expected to: focus follows selection so
  // the next arrow press continues from where the eye is.
  const onKeyDown = (event: React.KeyboardEvent) => {
    const index = tabs.findIndex((tab) => tab.id === active.id);
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    const next = tabs[(index + step + tabs.length) % tabs.length];
    setActiveId(next.id);
    buttons.current[next.id]?.focus();
  };

  return (
    <div className="tabs-wrap">
      <div className="tabs" role="tablist" aria-label={ariaLabel}>
        {tabs.map((tab) => {
          const selected = tab.id === active.id;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                buttons.current[tab.id] = node;
              }}
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              className={selected ? "tab tab-on" : "tab"}
              onClick={() => setActiveId(tab.id)}
              onKeyDown={onKeyDown}
            >
              <span>{tab.label}</span>
              {tab.badge !== undefined ? <span className="tab-badge">{tab.badge}</span> : null}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={`panel-${active.id}`}
        aria-labelledby={`tab-${active.id}`}
        className="tab-panel"
        tabIndex={0}
      >
        {active.content}
      </div>
    </div>
  );
}
