"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

/**
 * Explanations that are available but not in the way.
 *
 * Every panel in this console had grown a paragraph of rationale above its data, and read as a wall
 * once you already knew it. This puts that copy behind an "i" instead: hover on a pointer, tap or
 * Enter on anything else, Escape or a click anywhere to close. Nothing here is hover-only - the
 * pointer path and the keyboard path both exist - and the note is a sibling of the control rather
 * than a `title`, so it is readable, selectable and screen-reader reachable.
 *
 * The panel opens downward, absolutely positioned. So do not put one inside a `.table-wrap`, which
 * scrolls horizontally and would clip it: give the table's explanation to the heading above it.
 */
export function Info({
  label,
  children,
  align = "start",
}: {
  label: string;
  children: ReactNode;
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  // A hover-opened note is not *pinned*: moving the pointer away closes it. A click pins it, so a
  // click that lands on a note the pointer already opened keeps it rather than closing what the
  // person was reading. Escape and outside clicks clear both.
  const [pinned, setPinned] = useState(false);
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        setPinned(false);
      }
    };
    const onDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) {
        setOpen(false);
        setPinned(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const click = () => {
    if (!open) {
      setOpen(true);
      setPinned(true);
    } else if (pinned) {
      setOpen(false);
      setPinned(false);
    } else {
      setPinned(true);
    }
  };

  return (
    <span
      ref={root}
      className={align === "end" ? "info info-end" : "info"}
      // `pointerType` is what keeps a tap from being read as a hover: on a phone the emulated
      // pointerenter would otherwise open and the following tap would immediately close it.
      onPointerEnter={(event) => {
        if (event.pointerType === "mouse") setOpen(true);
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === "mouse" && !pinned) setOpen(false);
      }}
      onBlur={(event) => {
        // Focus leaving the control (tabbing on) closes an unpinned note.
        if (!pinned && !event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
      }}
    >
      <button
        type="button"
        className="info-button"
        aria-label={`About ${label}`}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={(event) => {
          // An info control is often inside a `summary` or a link. Without this, opening the note
          // would also fold or navigate the thing it is describing.
          event.preventDefault();
          click();
        }}
      >
        <span aria-hidden="true">i</span>
      </button>
      {open ? (
        <span className="info-body" id={id} role="note">
          {children}
        </span>
      ) : null}
    </span>
  );
}

/**
 * A page's own name, with its explanation behind the info control.
 *
 * Pages used to open with a heading and a paragraph about what the page was for. Read once, that
 * paragraph is furniture with a cost: it pushes the data down on every visit. It lives in the info
 * now, which is where the question "what is this page again?" actually gets asked.
 */
export function PageHead({
  title,
  about,
  children,
}: {
  title: string;
  about?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-title">
        <h1>{title}</h1>
        {about ? <Info label={title}>{about}</Info> : null}
      </div>
      {children}
    </header>
  );
}

/**
 * A panel's heading with the same treatment, plus room for a control on the right.
 *
 * `<h2>Title <Info/></h2>` reads badly - the control inherits the heading's serif face and size -
 * so headings that carry an explanation are built from this instead.
 */
export function PanelHead({
  title,
  about,
  level = 2,
  aside,
  id,
}: {
  title: string;
  about?: ReactNode;
  level?: 2 | 3;
  aside?: ReactNode;
  id?: string;
}) {
  const Heading = level === 3 ? "h3" : "h2";
  return (
    <div className="panel-head">
      <Heading id={id}>{title}</Heading>
      {about ? <Info label={title}>{about}</Info> : null}
      {aside ? <span className="panel-aside">{aside}</span> : null}
    </div>
  );
}
