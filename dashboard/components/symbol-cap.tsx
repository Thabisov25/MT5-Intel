"use client";

import { useCallback, useEffect, useState } from "react";
import type { AutoCaps } from "@/lib/auto";

/**
 * The per-symbol position cap, set from the Trades page, as one small strip.
 *
 * The watcher reads its engine cap from the .env (`AUTO_TRADE_MAX_POSITIONS`); this control
 * special-cases one market on top of that — cap 0 stops a symbol trading at all, 9 lets it take
 * everything the gate would otherwise allow, and "account-wide" is the absence of an override,
 * not a number of its own. It is used once in a while, so it is dressed like the symbol strip
 * above it rather than given a card of its own; the value is written to `auto-caps.json`, which
 * the watcher re-reads every cycle, so a change is in force within one cycle, not after a restart.
 */

// The same span the watcher clamps to (CAP_MIN/CAP_MAX in the engine), so the two ends of the
// file agree about what a valid cap is.
const CAP_MIN = 0;
const CAP_MAX = 9;

interface AutoReply {
  caps?: AutoCaps | null;
  status?: {
    settings?: { maxPositions?: number };
  } | null;
}

export function SymbolCapControl({ symbol }: { symbol: string | null }) {
  const [caps, setCaps] = useState<AutoCaps | null>(null);
  const [globalCap, setGlobalCap] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const read = useCallback(() => {
    fetch("/api/auto", { cache: "no-store" })
      .then((res) => (res.ok ? (res.json() as Promise<AutoReply>) : null))
      .then((data) => {
        if (!data) return;
        setCaps(data.caps ?? null);
        setGlobalCap(data.status?.settings?.maxPositions ?? null);
      })
      .catch(() => {
        /* keep the last good view until the next fetch */
      });
  }, []);

  useEffect(read, [read]);

  const write = useCallback(
    async (next: { symbols: Record<string, number>; default?: number }) => {
      setBusy(true);
      setError(null);
      try {
        const res = await fetch("/api/auto", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: "caps", caps: next.symbols, default: next.default ?? null }),
        });
        const payload = (await res.json().catch(() => null)) as { error?: string } | null;
        if (!res.ok) {
          setError(payload?.error ?? `not saved (${res.status})`);
          return;
        }
        read();
      } catch {
        setError("not saved — call did not reach the console");
      } finally {
        setBusy(false);
      }
    },
    [read],
  );

  // On the ALL tab the control is the file's own account-wide replacement for the .env cap; on a
  // symbol tab it is that market's override. "Inherit" is the absence of a number, and both are
  // written by rewriting the whole map from what was last read — the route replaces the file.
  const isAccountView = !symbol || symbol === "ALL";
  const override = !isAccountView && symbol ? (caps?.symbols[symbol] ?? null) : null;
  const fileDefault = caps?.default ?? null;
  const effective = override ?? (isAccountView ? fileDefault ?? globalCap : fileDefault ?? globalCap);

  const step = (delta: number) => {
    const base = isAccountView
      ? fileDefault ?? globalCap ?? 2
      : override ?? fileDefault ?? globalCap ?? 2;
    const next = Math.max(CAP_MIN, Math.min(CAP_MAX, base + delta));
    if (next === base || busy) return;
    if (isAccountView) {
      void write({ symbols: caps?.symbols ?? {}, default: next });
    } else {
      const symbols = { ...(caps?.symbols ?? {}) };
      if (symbol) symbols[symbol] = next;
      void write({ symbols, default: fileDefault ?? undefined });
    }
  };

  const clearOverride = () => {
    if (busy) return;
    if (isAccountView) {
      void write({ symbols: caps?.symbols ?? {}, default: undefined });
    } else {
      const symbols = { ...(caps?.symbols ?? {}) };
      if (symbol) delete symbols[symbol];
      void write({ symbols, default: fileDefault ?? undefined });
    }
  };

  const subject = isAccountView ? "account-wide cap" : `${symbol} cap`;

  return (
    <nav aria-label="Position cap" className="symbol-filter cap-strip">
      <p className="picker-label">
        {busy ? "saving cap…" : error !== null ? <span className="pill pill-fail">{error}</span> : "Auto-trade cap:"}
      </p>
      <p className="cap-inline" aria-live="polite">
        <button
          type="button"
          className="cap-btn"
          disabled={busy || effective === null || effective <= CAP_MIN}
          onClick={() => step(-1)}
          aria-label={`Lower the ${subject}`}
        >
          −
        </button>
        <strong className="cap-value" title={override !== null ? "this tab's own override" : "account-wide"}>
          {effective === null ? "—" : effective}
        </strong>
        <button
          type="button"
          className="cap-btn"
          disabled={busy || effective === null || effective >= CAP_MAX}
          onClick={() => step(1)}
          aria-label={`Raise the ${subject}`}
        >
          +
        </button>
        {override !== null && !busy ? (
          <button
            type="button"
            className="cap-btn cap-btn-quiet"
            onClick={clearOverride}
            aria-label={isAccountView ? "Follow the .env cap" : `Follow the account-wide cap for ${symbol}`}
          >
            {isAccountView ? "use .env" : "account-wide"}
          </button>
        ) : null}
        <span className="cap-note">of the .env&rsquo;s {globalCap ?? "—"}</span>
      </p>
    </nav>
  );
}
