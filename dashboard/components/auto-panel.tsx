"use client";

import { useOptimistic, useState, useTransition } from "react";
import Link from "next/link";
import { Info, PanelHead } from "@/components/info";
import { useLiveFeed } from "@/components/live-feed-context";
import type { AutoDecision, AutoStatus } from "@/lib/auto";
import type { ConfluenceMode } from "@/lib/confluence";

/**
 * The switch, and the account of what it has done.
 *
 * Two things this panel has to make obvious, because both are easy to get wrong when reading a
 * trading screen: whether it is *able* to trade at all (the .env master, which no button here can
 * change), and whether it is *currently* trading (the switch below, which this panel owns). "Off"
 * with one switch on and the other off looks identical unless the panel says which one is holding
 * it, so it names the one that is.
 */
// The watcher rewrites its view every cycle (30 s). Past a few of those the view is not the last
// word on what is open, and the panel says so instead of showing a frozen cycle as the current one.
const VIEW_STALE_S = 180;

function age(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} m`;
  if (seconds < 86_400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes ? `${hours} h ${minutes} m` : `${hours} h`;
  }
  return `${Math.floor(seconds / 86_400)} d`;
}

function Price({ value, digits }: { value: number | null | undefined; digits: number }) {
  return <>{value === null || value === undefined ? "—" : value.toFixed(digits)}</>;
}

/** A strategy list, its acknowledgement and its confluence mode, as one comparable string. Order is not meaningful. */
function selectionKey(
  strategies: string[] | undefined,
  allowUnvalidated: boolean | undefined,
  mode: string | undefined,
): string {
  return `${[...(strategies ?? [])].sort().join(",")}|${allowUnvalidated ? 1 : 0}|${mode ?? "members"}`;
}

/** A measured expectancy, or a dash. Signed, because the sign is the whole message. */
function R({ value }: { value: number | null | undefined }) {
  return <>{value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(3)}`}</>;
}

export function AutoPanel() {
  // The same payload set the strip and the per-rule table are reading. This panel polls nothing
  // itself, so the switch and the strip cannot disagree about which cycle they are describing.
  const { auto: view, setAuto, checkedAtMs } = useLiveFeed();
  const status = view.status;
  const [error, setError] = useState<string | null>(null);

  /**
   * The switch has to answer the click, not the round trip.
   *
   * It used to sit disabled with a "…" in it until the POST came back, which is the whole flight
   * time of the request; now the knob moves on the frame the click lands and the write follows.
   * `useOptimistic` holds that value for as long as the transition is pending and puts the old one
   * back if the write fails, so the control never rests on a state that was not saved.
   */
  const [optimisticArmed, setOptimisticArmed] = useOptimistic(view.toggle?.enabled ?? false);
  const [, startTransition] = useTransition();

  const change = (enabled: boolean) => {
    setError(null);
    startTransition(async () => {
      setOptimisticArmed(enabled);
      try {
        const res = await fetch("/api/auto", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        });
        const payload = (await res.json().catch(() => null)) as
          | { status?: AutoStatus; toggle?: { enabled: boolean; reason?: string; updatedUtc?: string }; selection?: typeof view.selection; error?: string }
          | null;
        if (!res.ok) {
          setError(payload?.error ?? `The switch could not be changed (${res.status}).`);
          return;
        }
        // The answer is folded into the shared view, so the strip and the table see the new switch
        // on this frame too rather than on the next tick.
        setAuto((current) => ({
          status: payload?.status ?? current.status,
          toggle: payload?.toggle ?? { enabled },
          selection: payload?.selection ?? current.selection,
        }));
      } catch {
        setError("The switch could not be reached.");
      }
    });
  };

  if (!status || !status.available) {
    return (
      <section className="panel" aria-labelledby="auto-heading">
        <h2 id="auto-heading">Auto-trading</h2>
        <p className="muted">
          {status?.reason ?? "The console has not read the watcher's auto-trading view."} The watcher
          writes this view on every cycle while the market is open, so it appears as soon as the
          watcher has run once with this build.
        </p>
      </section>
    );
  }

  const master = status.settings?.master ?? false;
  // The runtime toggle, not the artifact's resolved state.
  //
  // Two different things are called the switch: `auto-switch.json` holds the toggle this panel
  // owns, and `auto.json` holds what the watcher last resolved from the .env master and that file.
  // The knob used to be drawn from the second one, so arming it looked like it had not taken: the
  // artifact still carried the previous cycle's answer, and the knob snapped back to it. The wait
  // for the watcher to catch up is real, and is now said out loud instead of drawn as the state.
  const artifactArmed = status.switch?.enabled ?? null;
  const armed = view.toggle?.enabled ?? artifactArmed ?? false;
  const lagging = artifactArmed !== null && artifactArmed !== armed;
  const generatedMs = status.generatedUtc ? Date.parse(status.generatedUtc) : NaN;
  const viewAgeS =
    Number.isFinite(generatedMs) && checkedAtMs !== null
      ? Math.max(0, Math.round((checkedAtMs - generatedMs) / 1000))
      : null;
  const staleForS = viewAgeS !== null && viewAgeS > VIEW_STALE_S ? viewAgeS : null;
  const real = status.account?.tradeMode === "REAL";
  const allowReal = status.settings?.allowReal ?? false;
  const refusedReal = real && !allowReal;

  return (
    <section className="panel" aria-labelledby="auto-heading">
      <PanelHead
        id="auto-heading"
        title="Auto-trading"
        aside={
          <>
            {staleForS !== null ? (
              <>
                <span className="pill pill-warn">view {age(staleForS)} old</span>{" "}
              </>
            ) : null}
            {/* The state is the control: no second pill and no sentence repeating it. It is only
                disabled when the .env master makes it inert, never while a write is in flight - a
                control that stops answering until the server replies is the delay itself. */}
            <button
              type="button"
              role="switch"
              aria-checked={optimisticArmed}
              aria-label="Auto-trading switch"
              onClick={() => change(!optimisticArmed)}
              disabled={!master}
              className={optimisticArmed ? "switch switch-on" : "switch"}
            >
              <span className="switch-knob" aria-hidden="true" />
              {optimisticArmed ? "On" : master ? "Off" : "locked"}
            </button>
          </>
        }
        about={
          <>
            <p>
              With this on, a card that is <strong>live</strong>, on a ticked strategy, fresh, and
              past every check in the gate is placed as a real position sized to the risk below, with
              the card&rsquo;s own stop and target attached so MT5 closes it on its own.
            </p>
            <p>
              Three things have to agree before an order exists: <code>AUTO_TRADE</code> in the
              .env, the switch here, and the terminal&rsquo;s own Algo Trading button. A strategy
              research has not validated is refused unless the acknowledgement below is ticked, and
              then every position it opens is recorded as unvalidated.
            </p>
            <p>
              What it will not do: enter a card that is no longer at the price it planned, take the
              same signal twice, or trade from a setups view older than 15 minutes. Refusals are
              journaled with their reason, and the switch is a file the watcher re-reads every cycle
              — so turning it off stops new entries within one cycle, without closing what is open.
            </p>
          </>
        }
      />

      <p className="muted" aria-live="polite">
        {!master
          ? "The .env master (AUTO_TRADE) is off, so this switch cannot arm anything. Set it and restart the watcher."
          : lagging
            ? `Waiting on the watcher: it reads the switch file every cycle, and this view still describes the previous one, which had the switch ${artifactArmed ? "on" : "off"}. The file itself already reads ${armed ? "on" : "off"}, and that is the one orders depend on.`
            : status.switch?.reason ?? (optimisticArmed ? "Armed." : "The switch is off.")}
      </p>
      {error ? (
        <p className="muted" role="alert">
          <span className="pill pill-fail">switch error</span> {error}
        </p>
      ) : null}

      <dl className="facts">
        <div>
          <dt>Account</dt>
          <dd>
            {status.account?.login ?? "—"}{" "}
            <span className="muted">{status.account?.currency ?? ""}</span>
            {real ? (
              <>
                {" "}
                <span className="pill pill-fail">REAL</span>
              </>
            ) : (
              <>
                {" "}
                <span className="pill pill-info">{status.account?.tradeMode || "unknown"}</span>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Equity</dt>
          <dd className="num">
            {status.account?.equity === null || status.account?.equity === undefined
              ? "—"
              : status.account.equity.toFixed(2)}
          </dd>
        </div>
        <div>
          <dt>Risk per trade</dt>
          <dd className="num">{status.settings?.riskPct ?? 0}%</dd>
        </div>
        <div>
          <dt>Position cap</dt>
          <dd className="num nowrap">
            {status.open.length}/{status.settings?.maxPositions ?? 0}
            <span className="muted"> · {status.market?.openPositions ?? "?"} acct</span>
          </dd>
        </div>
        <div>
          <dt>Symbol</dt>
          <dd>
            {status.settings?.symbol || "—"}{" "}
            <span className="detail">magic {status.settings?.magic ?? 0}</span>
          </dd>
        </div>
        <div>
          <dt>View</dt>
          <dd className="num">{viewAgeS === null ? "—" : `${age(viewAgeS)} ago`}</dd>
        </div>
      </dl>

      {refusedReal ? (
        <p className="muted">
          <span className="pill pill-fail">live account refused</span> This is a REAL account and{" "}
          <code>AUTO_TRADE_ALLOW_REAL</code> is not set, so nothing will be traded even with the
          switch on.
        </p>
      ) : null}

      {status.terminal && status.terminal.tradeAllowed !== true ? (
        <p className="muted">
          <span className="pill pill-fail">terminal blocked</span> {status.terminal.reason}
        </p>
      ) : null}

      {status.open.length ? (
        <>
        <PanelHead
          level={3}
          title="Open auto positions"
          aside={`${status.open.length} open`}
          about={
            <p>
              Positions this system opened and has not yet seen close. The broker holds their stop
              and target, so a trade survives this process being stopped; the watcher adds only the
              card&rsquo;s own time limit on top.
            </p>
          }
        />
        <div className="table-wrap">
          <table className="table-dense">
            <caption className="sr-only">Auto positions still open</caption>
            <thead>
              <tr>
                <th scope="col">Strategy</th>
                <th scope="col">Side</th>
                <th scope="col" className="num">Ticket</th>
                <th scope="col" className="num">Lots</th>
                <th scope="col" className="num">Entry</th>
                <th scope="col" className="num">Stop</th>
                <th scope="col" className="num">Target</th>
                <th scope="col">Signal (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {status.open.map((row) => (
                <tr key={`${row.id ?? row.ticket}`}>
                  <td>
                    {row.strategy}
                    {row.families && row.families.length > 1 ? (
                      <span className="muted"> · {row.families.join(" + ")}</span>
                    ) : null}
                  </td>
                  <td>{row.side}</td>
                  <td className="num">{row.ticket ?? "—"}</td>
                  <td className="num">{row.volume ?? "—"}</td>
                  <td className="num">
                    <Price value={row.entry} digits={3} />
                  </td>
                  <td className="num detail">
                    <Price value={row.sl} digits={3} />
                  </td>
                  <td className="num detail">
                    <Price value={row.tp} digits={3} />
                  </td>
                  <td>{row.signalUtc ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      ) : (
        <p className="muted">No auto positions open.</p>
      )}

      {/* The broker's own refusals, which outlive the cycle that caused them. A refused signal is
          spent and never retried, so without this list an armed switch that had every entry refused
          by the broker looked exactly like one with nothing to do - and did. */}
      {status.rejected.length ? (
        <p className="blockers" aria-live="polite">
          <span className="pill pill-fail">broker refused</span>{" "}
          The last {status.rejected.length} entr{status.rejected.length === 1 ? "y" : "ies"} never
          reached the market: {status.rejected[0].strategy}, retcode{" "}
          {status.rejected[0].retcode ?? "?"} &ldquo;{status.rejected[0].brokerComment ?? "no comment"}
          &rdquo;. A signal is never retried once it is refused, so each of these bars was traded
          nothing.
        </p>
      ) : null}
      {status.notes.length ? (
        <p className="muted" aria-live="polite">
          {status.notes.join(" ")}
        </p>
      ) : null}
      {status.errors.length ? (
        <ul className="blockers">
          {status.errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}

      <PanelHead
        level={3}
        title="This cycle"
        aside={
          <>
            <span className="tag">placed {status.placed.length}</span>{" "}
            <span className="tag">refused {status.refused.length}</span>{" "}
            <span className="tag">closed {status.closed.length}</span>
          </>
        }
        about={
          <p>
            What the last cycle did, in the order it does it: positions the broker has already closed
            are reconciled first, then anything past the card&rsquo;s own time limit is closed, then
            the entries still fresh enough to take are placed. Every refusal carries its reason.
          </p>
        }
      />
      {status.placed.length || status.refused.length || status.closed.length ? (
        <>
          <DecisionList title="Placed" rows={status.placed} />
          <DecisionList title="Refused" rows={status.refused} />
          <DecisionList title="Closed" rows={status.closed} />
        </>
      ) : null}

      {/* The tick list is configuration, not state: it stays folded until it is being changed. */}
      <details className="fold">
        <summary>
          Strategies to trade{" "}
          <span className="tab-badge">
            {(status.selection?.strategies ?? []).length} of {status.registry?.length ?? 0} ticked
          </span>
          <Info label="Strategies to trade">
            <p>
              A strategy with a <strong>validated</strong> row on this timeframe is tradeable as it
              stands. Every other row is still a research candidate, so the numbers beside it are
              what its last trial measured &mdash; the mean in R, its edge over the side-flipped
              placebo, the same at 1.5&times; costs, and the share of walk-forward folds that were
              positive.
            </p>
            <p>
              <code>confluence</code> is the pair pattern: two ticked strategies that entered the
              same bar the same way. It needs two ticked strategies to be able to fire at all, and
              it has two modes (chosen beneath the list): <strong>members</strong> trades the pair
              with the tightest of their stops, on the agreeing side; <strong>fade</strong> lets
              the market&rsquo;s placement on the agreeing bar decide instead &mdash; high/higher-high
              sells, low/lower-low buys, and the middle takes nothing. Both modes&rsquo; measured
              records are in the strategy catalogue (Live tab &rarr; confluence).
            </p>
            <p>
              Saving needs no restart. The list is a file the watcher re-reads every cycle, so
              ticking one on takes effect on the next cycle and ticking one off stops new entries
              within one &mdash; it never closes what is already open, because the broker holds those
              stops.
            </p>
          </Info>
        </summary>
        <StrategyPicker
          status={status}
          onStatus={(next) => setAuto((current) => ({ ...current, status: next }))}
        />
      </details>
    </section>
  );
}

/**
 * Which strategies may trade, chosen here rather than in the .env.
 *
 * Three things this has to keep straight, because a screen that blurs them is worse than no screen:
 * what has been *saved*, what the *watcher* is actually trading (the two differ for up to a cycle),
 * and what is *validated*. The rows carry the last trial's numbers for exactly that reason - a
 * strategy with a negative expectancy is still selectable, and the person selecting it should be
 * looking at the number while they do.
 */
function StrategyPicker({
  status,
  onStatus,
}: {
  status: AutoStatus;
  onStatus: (next: AutoStatus) => void;
}) {
  const saved = status.selection;
  // The form follows the watcher until something is ticked, and the tick is held as `picked`. It is
  // used only while its key still matches what the watcher reports, so the list on screen is always
  // the watcher's answer, or an edit on top of it - never a stale copy of one of them. `source` is
  // deliberately not part of the key: it flips from `env` to `dashboard` the moment anything is
  // saved, and that must not be read as "the selection changed, drop what is being edited".
  const savedKey = selectionKey(saved?.strategies, saved?.allowUnvalidated, saved?.confluenceMode);
  const [picked, setPicked] = useState<{
    key: string;
    names: string[];
    ack: boolean;
    mode: ConfluenceMode;
  } | null>(null);
  // What this page last wrote, so "unsaved changes" is not shown for the up-to-one-cycle window
  // before the watcher reports the selection back.
  const [written, setWritten] = useState<{ names: string[]; ack: boolean; mode: ConfluenceMode } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resolved = {
    names: saved?.strategies ?? [],
    ack: saved?.allowUnvalidated ?? false,
    mode: saved?.confluenceMode ?? ("members" as ConfluenceMode),
  };
  const form =
    picked && picked.key === savedKey ? picked : { key: savedKey, ...resolved };
  const baseline =
    written && selectionKey(written.names, written.ack, written.mode) === savedKey
      ? resolved
      : written ?? resolved;

  const applyEdit = (names: string[], ack: boolean, mode: ConfluenceMode = form.mode) => {
    setMessage(null);
    setPicked({ key: savedKey, names, ack, mode });
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch("/api/auto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategies: form.names,
          allowUnvalidated: form.ack,
          confluenceMode: form.mode,
        }),
      });
      const payload = (await res.json().catch(() => null)) as
        | { status?: AutoStatus; error?: string }
        | null;
      if (!res.ok) {
        setError(payload?.error ?? `The selection could not be saved (${res.status}).`);
        return;
      }
      if (payload?.status) onStatus(payload.status);
      setWritten({ names: form.names, ack: form.ack, mode: form.mode });
      setMessage("Saved. The watcher re-reads this every cycle, so it is in force within one.");
    } catch {
      setError("The selection could not be reached.");
    } finally {
      setBusy(false);
    }
  };

  const registry = status.registry ?? [];
  const unvalidatedTicked = registry
    .filter((row) => row.status !== "historically validated")
    .map((row) => row.family)
    .filter((name) => form.names.includes(name));
  const dirty =
    selectionKey(form.names, form.ack, form.mode) !==
    selectionKey(baseline.names, baseline.ack, baseline.mode);
  const allowed = status.allow?.allowed ?? [];
  const tradedUnvalidated = status.allow?.unvalidated ?? [];

  return (
    <>
      {registry.length ? (
        <div className="table-wrap">
          <table className="table-dense">
            <caption className="sr-only">
              Strategies in the research registry, with the expectancy each last trial measured
            </caption>
            <thead>
              <tr>
                <th scope="col">Trade</th>
                <th scope="col">Strategy</th>
                <th scope="col">Standing</th>
                <th scope="col">
                  <span className="sr-only">Settings</span>
                </th>
                <th scope="col">Take profit</th>
                <th scope="col" className="num">
                  n
                </th>
                <th scope="col" className="num">
                  Mean (R)
                </th>
                <th scope="col" className="num">
                  Edge vs placebo
                </th>
                <th scope="col" className="num">
                  1.5× costs
                </th>
                <th scope="col" className="num">
                  Folds +
                </th>
              </tr>
            </thead>
            <tbody>
              {registry.map((row) => {
                const validated = row.status === "historically validated";
                const id = `pick-${row.family}`;
                return (
                  <tr key={row.family}>
                    <td>
                      <input
                        type="checkbox"
                        id={id}
                        checked={form.names.includes(row.family)}
                        onChange={() =>
                          applyEdit(
                            form.names.includes(row.family)
                              ? form.names.filter((name) => name !== row.family)
                              : [...form.names, row.family],
                            form.ack,
                          )
                        }
                      />
                    </td>
                    <td>
                      <label htmlFor={id}>{row.family}</label>
                    </td>
                    <td>
                      {validated ? (
                        <span className="pill pill-pass">validated</span>
                      ) : (
                        <span className="pill pill-warn">candidate</span>
                      )}
                    </td>
                    {/* The gear opens this strategy's own settings page, where per-strategy
                        options have room to grow without adding columns here. */}
                    <td className="detail">
                      <Link
                        href={`/strategies/${row.family}`}
                        className="setting-gear"
                        aria-label={`${row.family} settings`}
                        title="Settings"
                      >
                        ⚙
                      </Link>
                    </td>
                    {/* The mode in force, not an edit control: the gear opens the page that owns
                        the dropdown. */}
                    <td className="detail">
                      {saved?.tpMode?.[row.family] === "swing" ? (
                        <span className="pill pill-info">swing</span>
                      ) : (
                        <span className="muted">fixed</span>
                      )}
                    </td>
                    <td className="num detail">{row.n ?? "—"}</td>
                    <td className="num detail">
                      <R value={row.meanR} />
                    </td>
                    <td className="num detail">
                      <R value={row.edgeR} />
                    </td>
                    <td className="num detail">
                      <R value={row.stressedR} />
                    </td>
                    <td className="num detail">
                      {row.foldsPositive === null || row.foldsPositive === undefined
                        ? "—"
                        : `${Math.round(row.foldsPositive * 100)}%`}
                    </td>
                  </tr>
                );
              })}
              <tr>
                <td>
                  <input
                    type="checkbox"
                    id="pick-confluence"
                    checked={form.names.includes("confluence")}
                    onChange={() =>
                      applyEdit(
                        form.names.includes("confluence")
                          ? form.names.filter((name) => name !== "confluence")
                          : [...form.names, "confluence"],
                        form.ack,
                      )
                    }
                  />
                </td>
                <td>
                  <label htmlFor="pick-confluence">confluence</label>
                </td>
                <td>
                  <span className="pill pill-info">pair</span>
                </td>
                <td className="detail">
                  <Link
                    href="/strategies/confluence"
                    className="setting-gear"
                    aria-label="confluence settings"
                    title="Settings"
                  >
                    ⚙
                  </Link>
                </td>
                <td className="detail">
                  {saved?.tpMode?.["confluence"] === "swing" ? (
                    <span className="pill pill-info">swing</span>
                  ) : (
                    <span className="muted">fixed</span>
                  )}
                </td>
                {/* No registry row of its own - it is a composite - so its stat cells read as
                    dashes like any other strategy without a trial, and its explanation lives
                    on its settings page. */}
                {Array.from({ length: 5 }).map((_, i) => (
                  <td className="num detail" key={i}>
                    <span className="muted">—</span>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">
          The watcher has not written the research registry into its view yet. Restart it once with
          this build and the list appears here; in the meantime the .env list applies.
        </p>
      )}

      {unvalidatedTicked.length ? (
        <p className="muted">
          <span className="pill pill-warn">no validated edge</span>{" "}
          <strong>{unvalidatedTicked.join(", ")}</strong>{" "}
          {unvalidatedTicked.length === 1 ? "is" : "are"} still a research candidate: without the
          acknowledgement below, the watcher refuses it and says so. With it, every position it
          opens is recorded as unvalidated.
        </p>
      ) : null}

      <div role="group" aria-label="Strategy selection" style={{ marginBottom: "0.75rem" }}>
        <label style={{ display: "block", marginBottom: "0.5rem" }}>
          <input
            type="checkbox"
            checked={form.ack}
            onChange={(event) => applyEdit(form.names, event.target.checked)}
          />{" "}
          Trade a ticked strategy even though research has not validated it
        </label>
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="pill"
          style={{
            font: "inherit",
            fontSize: "0.875rem",
            padding: "0.35rem 0.9rem",
            cursor: busy ? "not-allowed" : "pointer",
            color: dirty ? "var(--pass)" : "var(--ink-soft)",
            background: "transparent",
          }}
        >
          {busy ? "Saving…" : "Save selection"}
        </button>{" "}
        <span className="muted" aria-live="polite">
          {message ?? (dirty ? "Unsaved changes." : `${form.names.length} ticked.`)}
        </span>{" "}
        <span className="muted">
          Take-profit exits (fixed / swing) and confluence&rsquo;s entry side are set per strategy
          from the ⚙ gear in each row.
        </span>
      </div>
      {error ? (
        <p className="muted" role="alert">
          <span className="pill pill-fail">selection error</span> {error}
        </p>
      ) : null}

      <p className="muted">
        {status.switch?.enabled ? "The last cycle traded" : "With the switch on this would trade"}{" "}
        <strong>{allowed.length ? allowed.join(", ") : "nothing"}</strong>
        {saved ? (
          saved.source === "dashboard" ? (
            " — chosen on this page (saved " + (saved.updatedUtc ?? "unknown") + ")"
          ) : (
            <> — from the .env list, since no selection has been saved here</>
          )
        ) : null}
        {tradedUnvalidated.length ? (
          <>
            {" "}
            · <span className="pill pill-warn">unvalidated</span> {tradedUnvalidated.join(", ")}
          </>
        ) : null}
      </p>
      {status.allow?.refused.length ? (
        <ul className="blockers">
          {status.allow.refused.map((row) => (
            <li key={row.strategy}>
              <strong>{row.strategy}</strong> — {row.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

// One list for the whole cycle rather than three headed ones: the tag says which, and three
// headings that mostly said "nothing happened" were the bulk of this panel's height.
function DecisionList({ title, rows }: { title: string; rows: AutoDecision[] }) {
  if (!rows.length) return null;
  return (
    <ul className="cycle">
      {rows.map((row, index) => (
        <li key={`${title}-${row.strategy}-${row.ticket ?? index}`}>
          <span className="tag">{title}</span>
          <span>
            <strong>{row.strategy}</strong>
            {row.families && row.families.length > 1 ? ` (${row.families.join(" + ")})` : ""}
            {row.side ? ` ${row.side}` : ""}
            {row.volume !== null && row.volume !== undefined ? ` ${row.volume} lots` : ""}
            {row.entry !== null && row.entry !== undefined ? ` at ${row.entry}` : ""}
            {row.ticket !== null && row.ticket !== undefined ? ` · ticket ${row.ticket}` : ""}
            {row.reason ? ` — ${row.reason}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}
