"use client";

import { useState } from "react";
import { Info, PanelHead } from "@/components/info";
import type { AutoRegistryRow, AutoSelection, TpMode } from "@/lib/auto";

/**
 * One strategy's settings, on that strategy's own page.
 *
 * Reached from the Live tab's strategy table by the row's settings gear. Every option a strategy
 * carries lives at `/strategies/<family>` - a take-profit mode for any strategy, and for
 * `confluence` also the side its entry takes. The save is the whole selection - strategies list,
 * acknowledgement, confluence mode, and the take-profit map with this page's single change folded
 * in - so one file keeps one writer shape and a save here cannot disagree with a save from the
 * Live tab's picker: changing the side to trade never quietly resets a take-profit mode.
 */
export function StrategySettingsForm({
  family,
  registryRow,
  selection,
}: {
  family: string;
  registryRow: AutoRegistryRow | null;
  selection: AutoSelection | null;
}) {
  const savedMap = selection?.tpMode ?? {};
  const savedKey = selectionKey(
    selection?.strategies,
    selection?.allowUnvalidated,
    selection?.confluenceMode,
    savedMap,
  );
  const [picked, setPicked] = useState<{ key: string; mode: TpMode } | null>(null);
  const [entrySet, setEntrySet] = useState<{ key: string; mode: "members" | "fade" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // What this page last wrote, so "unsaved changes" is not shown for the up-to-one-cycle window
  // before the watcher reports the selection back.
  const [written, setWritten] = useState<{ key: string; map: Record<string, TpMode> } | null>(null);
  const [entryWritten, setEntryWritten] = useState<{
    key: string;
    mode: "members" | "fade";
  } | null>(null);

  const savedMode: TpMode = savedMap[family] ?? "fixed";
  const baselineMode = written && written.key === savedKey ? (written.map[family] ?? "fixed") : savedMode;
  const mode = picked && picked.key === savedKey ? picked.mode : baselineMode;
  const tradeModeDirty = mode !== baselineMode;

  const savedEntry = selection?.confluenceMode ?? "members";
  const entryBaseline =
    entryWritten && entryWritten.key === savedKey ? entryWritten.mode : savedEntry;
  const entryMode = entrySet && entrySet.key === savedKey ? entrySet.mode : entryBaseline;
  const entryDirty = entryMode !== entryBaseline;

  const post = async (body: {
    tpMode: Record<string, TpMode>;
    confluenceMode: "members" | "fade";
  }) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch("/api/auto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategies: selection?.strategies ?? [],
          allowUnvalidated: selection?.allowUnvalidated ?? false,
          confluenceMode: body.confluenceMode,
          tpMode: body.tpMode,
        }),
      });
      const payload = (await res.json().catch(() => null)) as
        | { selection?: AutoSelection; error?: string }
        | null;
      if (!res.ok) {
        setError(payload?.error ?? `The settings could not be saved (${res.status}).`);
        return;
      }
      setMessage("Saved. The watcher re-reads this every cycle, so it is in force within one.");
      return payload?.selection ?? null;
    } catch {
      setError("The selection could not be reached.");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const saveTradeMode = async (next: TpMode) => {
    setPicked({ key: savedKey, mode: next });
    const map: Record<string, TpMode> = { ...savedMap };
    if (next === "fixed") delete map[family];
    else map[family] = next;
    const saved = await post({ tpMode: map, confluenceMode: entryMode });
    if (saved) {
      setWritten({ key: savedKey, map });
      setEntryWritten({ key: savedKey, mode: saved.confluenceMode ?? "members" });
    }
  };

  const saveEntryMode = async (next: "members" | "fade") => {
    setEntrySet({ key: savedKey, mode: next });
    const saved = await post({ tpMode: savedMap, confluenceMode: next });
    if (saved) {
      setEntryWritten({ key: savedKey, mode: saved.confluenceMode ?? "members" });
      setWritten({ key: savedKey, map: savedMap });
    }
  };

  const row = registryRow;
  return (
    <section className="panel">
      <PanelHead
        title="Settings"
        about={
          <p>
            A mode is applied when the strategy actually trades, not as a backtest change: switching
            to <strong>swing</strong> does not rewrite its research numbers, which is also why this
            page offers no numbers of its own.
          </p>
        }
      />
      <div className="setting-field">
        <label htmlFor="tp-mode">Take-profit mode</label>
        <Info label="Take-profit mode">
          <p>
            <strong>fixed</strong> is the strategy&apos;s own planned target, the multiple every
            research number on this console was recorded under. <strong>swing</strong> is the
            structure rule: a sell aims just above the previous lower-low the signal bar had already
            confirmed, a buy just below the previous higher-high, a fraction of the stop short of the
            level. When that level is unknown, or the market has already passed it, the order falls
            back to the fixed target and the journal says which rule chose the exit.
          </p>
        </Info>
        <select
          id="tp-mode"
          value={mode}
          disabled={busy}
          onChange={(event) => {
            const next = event.target.value === "swing" ? "swing" : "fixed";
            void saveTradeMode(next);
          }}
        >
          <option value="fixed">fixed — the strategy&rsquo;s own planned target</option>
          <option value="swing">swing — previous lower-low / higher-high + buffer</option>
        </select>
        <span className="muted" aria-live="polite">
          {message ?? (tradeModeDirty ? "Unsaved changes." : undefined)}
        </span>
      </div>
      {family === "confluence" ? (
        <>
          <hr className="rule" />
          <div className="setting-field" role="radiogroup" aria-label="Confluence entry side">
            <span className="group-label">Entry side</span>
            <Info label="Confluence entry side">
              <p>
                How the pair decides which way to enter on a candle two or more allowed strategies
                agree on. <strong>members</strong> enters the way the agreeing families themselves
                point; <strong>fade</strong> is the placement rule — the agreeing bar in the top
                third of its 96-bar range sells, the bottom third buys, the middle stands down, and
                where the fade flips the agreeing side, the group&rsquo;s tightest card is mirrored
                to the fade side at the same distance and R. It fires only when two or more ticked
                strategies signal on the same closed bar, so it needs at least two ticked strategies
                to be able to fire at all.
              </p>
            </Info>
            <label>
              <input
                type="radio"
                name="confluence-entry"
                checked={entryMode === "members"}
                disabled={busy}
                onChange={() => void saveEntryMode("members")}
              />{" "}
              members — the agreeing families&rsquo; own side
            </label>
            <label>
              <input
                type="radio"
                name="confluence-entry"
                checked={entryMode === "fade"}
                disabled={busy}
                onChange={() => void saveEntryMode("fade")}
              />{" "}
              fade — high sells / low buys, middle stands down
            </label>
            <span className="muted" aria-live="polite">
              {message ?? (entryDirty ? "Unsaved changes." : undefined)}
            </span>
          </div>
        </>
      ) : null}
      {error ? (
        <p className="muted" role="alert">
          <span className="pill pill-fail">selection error</span> {error}
        </p>
      ) : null}
      {row ? (
        <details>
          <summary>What this strategy is, and what its last trial measured</summary>
          <dl className="facts">
            <div>
              <dt>Standing</dt>
              <dd>
                {row.status === "historically validated" ? (
                  <span className="pill pill-pass">validated</span>
                ) : (
                  <span className="pill pill-warn">candidate</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Graded trades</dt>
              <dd>{row.n ?? "—"}</dd>
            </div>
            <div>
              <dt>Mean (R)</dt>
              <dd>{signed(row.meanR)}</dd>
            </div>
            <div>
              <dt>Edge vs placebo</dt>
              <dd>{signed(row.edgeR)}</dd>
            </div>
            <div>
              <dt>1.5× costs</dt>
              <dd>{signed(row.stressedR)}</dd>
            </div>
            <div>
              <dt>Folds +</dt>
              <dd>
                {row.foldsPositive === null || row.foldsPositive === undefined
                  ? "—"
                  : `${Math.round(row.foldsPositive * 100)}%`}
              </dd>
            </div>
          </dl>
        </details>
      ) : null}
    </section>
  );
}

function signed(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

/** The selection, as one comparable string, with the take-profit map folded in at the end. */
function selectionKey(
  strategies: string[] | undefined,
  allowUnvalidated: boolean | undefined,
  mode: string | undefined,
  map: Record<string, TpMode>,
): string {
  const modes = Object.keys(map)
    .sort()
    .map((name) => `${name}=${map[name]}`)
    .join(",");
  return `${[...(strategies ?? [])].sort().join(",")}|${allowUnvalidated ? 1 : 0}|${mode ?? "members"}|${modes}`;
}
