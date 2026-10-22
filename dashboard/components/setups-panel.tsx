"use client";

import { PanelHead } from "@/components/info";
import { useLiveFeed } from "@/components/live-feed-context";
import { StrategyCell } from "@/components/strategy-guide";
import type { AutoStatus } from "@/lib/auto";
import { strategyInfo } from "@/lib/strategies";
import {
  agreementsOf,
  chartClock,
  combinationsOf,
  orderLine,
  readAgreement,
  type Agreement,
  type Combination,
} from "@/lib/confluence";
import type { SetupRow, SetupStatus, Setups } from "@/lib/setups";

/**
 * What every rule is in right now, as opposed to what it would have done in the past.
 *
 * A replay is a historical what-if and its cards are all graded, so the most recent one it can
 * show is the last signal that has already finished. That makes a trending market look silent:
 * a crossover fires on the bar the averages trade places, momentum fires when the trailing sign
 * flips, and a pullback rule fires only while price is back at its average. This panel is the
 * other half of the picture - the newest card of every rule, priced, with whether it is still
 * running and how far it has moved in R.
 */
// The writer rebuilds this view every 300 s while the market is open (watch.SETUPS_REFRESH_S),
// so three windows of silence is past any normal refresh, weekend idle included roughly twice
// over. Past it the panel says the view is not fresh instead of letting a frozen one pass as live.
const VIEW_STALE_S = 900;

/**
 * The payload's times as instants.
 *
 * `generated_utc` is ISO; the panel's stamps are "YYYY-MM-DD HH:MM" in UTC. `Date.parse` on the
 * second form reads it as *local* time, which would silently shift every age by the viewer's own
 * offset, so the shape decides which reading is applied.
 */
function instantMs(value: string | null): number | null {
  if (!value) return null;
  const iso = value.includes("T")
    ? value
    : `${value.replace(" ", "T")}${value.length === 16 ? ":00" : ""}Z`;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * How long ago a card's signal was, counted to now.
 *
 * The writer bakes `age_s` at the moment it writes, so reading it back means a view that stops
 * being rebuilt goes on claiming "3 d 8 h ago" for as long as it is left open, which is exactly
 * how a frozen tab reads as a live one. This recomputes from the signal's own UTC stamp instead,
 * and falls back to the baked number only when the payload is on the broker's clock and the stamp
 * cannot be placed (that case is labelled on the page).
 */
function ageSeconds(row: SetupRow, nowMs: number | null, clock: string): number | null {
  if (nowMs === null) return row.ageS;
  const signalMs = clock === "utc" ? instantMs(row.signalUtc) : null;
  return signalMs === null ? row.ageS : Math.max(0, Math.round((nowMs - signalMs) / 1000));
}

/**
 * The one thing a table cannot say: two rules pointed at the same candle.
 *
 * This is the card the chart draws in its agreement colour, with the question that follows it -
 * would the watcher trade it - answered in the same place. It is rendered above the rows rather
 * than as another row because it is not a third position: it is a fact about two of the rows below
 * it, and those rows are tinted to match so the pair is findable in the table.
 *
 * A pair that is too old to enter still belongs here. "Two rules agreed on this candle and the
 * entry has gone" is the answer to a question the chart raises and the table cannot, and hiding it
 * would leave the green arrows unexplained.
 */
/**
 * What the account actually has on this signal.
 *
 * The chart cannot answer this and draws the same thick arrows either way: the file it draws from is
 * the research book, where a trade exists whether or not an order was ever sent. So the one place
 * that knows is where the answer belongs, next to the reason nothing traded.
 */
function RunningNote({
  agreement,
  auto,
  viewAgeS,
  digits,
}: {
  agreement: Agreement;
  auto: AutoStatus | null;
  viewAgeS: number | null;
  digits: number;
}) {
  const reading = readAgreement(agreement, auto, viewAgeS, digits);
  return (
    <p className="agree-old">
      <strong>{reading.held ? "Auto-trade running:" : "Pair running:"}</strong>{" "}
      {agreement.families.join(" + ")} · {agreement.barsHeld} bars in
      {agreement.rNow === null ? "" : ` (${agreement.rNow > 0 ? "+" : ""}${agreement.rNow.toFixed(2)}R)`}.
    </p>
  );
}

function AgreementStrip({
  agreement,
  auto,
  viewAgeS,
  digits,
}: {
  agreement: Agreement;
  auto: AutoStatus | null;
  viewAgeS: number | null;
  digits: number;
}) {
  const reading = readAgreement(agreement, auto, viewAgeS, digits);
  // Only ever rendered for a pair inside its entry window, so the order line is an order.
  // Both readings stay on the card on purpose: the placement rule's verdict is information even
  // where the mode in force is "members", and a mode flip against the members is exactly the
  // thing a reader must not miss.
  const p = agreement.placement;
  const pct = p === null ? null : Math.round(p.fraction * 100);
  const fadeLine = agreement.fadeSide
    ? `${agreement.fadeSide} at the ${agreement.fadeSide === "sell" ? "top" : "bottom"} third`
    : pct !== null
      ? `middle third (${pct}%) — nothing to take`
      : "placement unreadable — nothing to take";
  return (
    <article className="agree agree-compact" aria-label="Rules agreeing on one candle">
      <p className="agree-head">
        <span className="pill pill-agree">{agreement.families.length} agree</span>
        <strong>{agreement.families.join(" + ")}</strong>
        <code>{orderLine(agreement, digits, reading.orderSide)}</code>
      </p>
      <p className="agree-verdict">
        {reading.tradeable ? (
          <span className="pill pill-pass">watcher would take this pair</span>
        ) : (
          <span className="pill pill-warn" title={reading.blockers.join(", ")}>
            not tradeable: {reading.blockers[0] ?? "?"}
          </span>
        )}{" "}
        <span className="muted">
          members said {agreement.side} · confluence mode: {reading.confluenceMode}
          {reading.mirrored ? " — mirrored against the members" : ""}
        </span>
      </p>
      <p className="muted">
        {p === null
          ? "The agreeing bar's placement could not be read."
          : `Agreeing bar sat at ${Math.round(p.fraction * 100)}% of the ${p.bars ?? 96}-bar range${
              p.freshHigh ? " · fresh high" : ""
            }${p.freshLow ? " · fresh low" : ""}.`} {" "}
        Fade reading: {fadeLine}
        {reading.confluenceMode === "members" && agreement.fadeSide !== null
          ? " (shown, not traded — the mode in force follows the members)"
          : ""}
      </p>
    </article>
  );
}

/**
 * One candle, two rules, opposite sides - named instead of dismissed.
 *
 * Two arrows on one candle look exactly like an agreement, and the difference matters: no pair forms
 * there and nothing trades it. But it is not noise either. The same two rules land on the same bar
 * again and again, so what the chart draws is an event with an identity, and this card gives it one:
 * the members' letters and sides, each member's own prices, where the candle sat in its range, and
 * what the exported history measured about this exact combination and about the class of them.
 *
 * The direction line is the part that was measured rather than assumed. The obvious reading - buy
 * when the arrows sit at a low, sell when they sit at a high - called the wrong side more often than
 * the right one on the exported history, so the card shows the record and the placement and never
 * turns either into an instruction. Nothing here is a recommendation, and nothing here trades.
 */
function CombinationCard({ group, digits: _digits }: { group: Combination; digits: number }) {
  const sides = group.members.map((m) => `${m.family} ${m.side === "buy" ? "↑" : "↓"}`).join(" · ");
  void _digits;
  return (
    <p className="combo-old" title={`${sides} - one candle, both sides; no pair forms, nothing trades it`}>
      <span className="pill pill-combo">combo</span> <code>{group.code}</code> {" - "}{" "}
      one candle, both sides. No pair forms; the members are named in the table below.
    </p>
  );
}

/** An older combination: one line, because the question it answered has stopped being live. */
function CombinationNote({ group }: { group: Combination }) {
  const names = group.members.map((m) => `${m.family} (${m.side})`).join(" and ");
  return (
    <p className="combo-old">
      <strong>Combination {group.code}</strong> — {names} shared the {group.arrowStamp.chart}{" "}
      candle{group.barsHeld === null ? "" : `, ${group.barsHeld} bars into their move`}. Not an
      entry: the two sides cancel, and an older combination is a shape to recognise on the chart
      rather than something to act on now.
    </p>
  );
}

export function SetupsPanel() {
  // The same payload set the strip and the switch are reading, on the same tick. This panel used
  // to keep a 30 s timer of its own, which is why two heartbeats in three painted a strip that had
  // moved next to a table that had not.
  const { setups, timeframe, auto: view, checkedAtMs } = useLiveFeed();
  const auto = view.status;
  // The reader's clock, stamped when those payloads landed: null until the browser has one, so the
  // first paint shows the writer's own ages, which is what the server rendered, and nothing here
  // can disagree with it on hydration.
  const nowMs = checkedAtMs;

  if (!setups) {
    return (
      <section className="panel" aria-labelledby="setups-heading">
        <h2 id="setups-heading">What every strategy says right now</h2>
        <p className="muted">
          The watcher has not written this view yet. It is rebuilt while the market is open, from
          the same bars the research runs on: every rule&rsquo;s newest card, its entry, stop and
          target, and whether that card is still running.
        </p>
        {timeframe !== "H1" ? <FrameMark timeframe={timeframe} /> : null}
        <pre><code>{`.\\.venv\\Scripts\\python.exe -m mt5_intel.research.setups ${timeframe}`}</code></pre>
      </section>
    );
  }
  // The artifact's own answer is what the screen marks: a payload that names its frame is the one
  // thing writers and readers agree on, so the badge and the banner read it rather than the URL.
  const frame = timeframe !== "H1" ? (setups.timeframe.match(/^[A-Z0-9]{1,4}$/i) ? setups.timeframe.toUpperCase() : timeframe) : null;

  const live = setups.setups.filter((row) => row.status === "open");
  const agreements = agreementsOf(setups);
  // Only these are highlighted. The rest are positions already in the move, reported as one line
  // rather than a card: a highlight that means "worth acting on" and one that means "just telling
  // you" cannot be the same mark on the same page.
  const fresh = agreements.filter((group) => group.fresh);
  const running = agreements.filter((group) => !group.fresh);
  const combinations = combinationsOf(setups);
  const freshCombinations = combinations.filter((group) => group.fresh);
  const oldCombinations = combinations.filter((group) => !group.fresh);
  const agreeing = new Set(fresh.flatMap((group) => group.families));
  const combined = new Set(combinations.flatMap((group) => group.members.map((m) => m.family)));
  const activeAutoByFamily = new Map<string, { ticket: number | null | undefined; strategy: string; families?: string[] }>();
  for (const openRow of auto?.open ?? []) {
    const fams =
      openRow.families && openRow.families.length > 0
        ? openRow.families
        : openRow.strategy
          ? [openRow.strategy]
          : [];
    for (const fam of fams) {
      activeAutoByFamily.set(fam, {
        ticket: openRow.ticket,
        strategy: openRow.strategy ?? fam,
        families: openRow.families,
      });
    }
  }
  const price = (value: number | null) => (value === null ? "—" : value.toFixed(setups.digits));
  const r = (value: number | null) =>
    value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
  const pips = (value: number | null) => (value === null ? "—" : `${value.toFixed(1)}`);
  const changes = Object.entries(setups.context.changePips)
    .filter(([, value]) => value !== null)
    .map(([bars, value]) => `${bars}: ${value! > 0 ? "+" : ""}${value!.toFixed(0)}`);
  // How long ago the watcher last rebuilt this view, measured against the reader's clock.
  const generatedMs = instantMs(setups.generatedUtc);
  const viewAgeS =
    generatedMs === null || nowMs === null
      ? null
      : Math.max(0, Math.round((nowMs - generatedMs) / 1000));
  const staleForS = viewAgeS !== null && viewAgeS > VIEW_STALE_S ? viewAgeS : null;
  const clockLabel = setups.clock === "utc" ? "UTC" : "broker clock";

  return (
    <section className="panel" aria-labelledby="setups-heading">
      <PanelHead
        id="setups-heading"
        title="What every strategy says right now"
        aside={
          <>
            {frame ? <span className="pill pill-info">{frame} frame</span> : null}
            {staleForS !== null ? (
              <span className="pill pill-warn">not rebuilt for {age(staleForS)}</span>
            ) : (
              <span className="muted">
                {live.length} of {setups.setups.length} rules running
              </span>
            )}
          </>
        }
        about={
          <>
            <p>
              One row per rule: the position it is in right now, with the entry, stop and target to
              check against your chart. Rules only enter while flat, so repeats while a card runs
              are set aside and shown on the card above.
            </p>
          </>
        }
      />
      {frame ? <FrameMark timeframe={frame} /> : null}
      { staleForS !== null ? (
        <p className="muted" aria-live="polite">
          Last rebuilt {age(staleForS)} ago, so these are the cards as of {setups.asofBarUtc}{" "}
          ({clockLabel}) and not as of now — an age still climbing means the watcher has stopped. The
          ages below count to now either way.
        </p>
      ) : null}
      {auto?.open && auto.open.length > 0 ? (
        <div className="agree" aria-label="Active auto-trade position">
          {auto.open.map((pos) => {
            const fams =
              pos.families && pos.families.length > 0
                ? pos.families
                : pos.strategy
                  ? [pos.strategy]
                  : [];
            return (
              <p className="agree-head" key={pos.id ?? pos.ticket ?? pos.strategy}>
                <span className="pill pill-pass">auto-trade</span>
                <strong>
                  {pos.strategy === "confluence" && fams.length > 0
                    ? `confluence (${fams.map((f) => strategyInfo(f).name).join(" + ")})`
                    : fams.map((f) => strategyInfo(f).name).join(" + ") || pos.strategy}
                </strong>
                <span>
                  {pos.side} at {price(pos.entry ?? null)} · stop {price(pos.sl ?? null)} ·
                  target {price(pos.tp ?? null)}
                </span>
              </p>
            );
          })}
        </div>
      ) : null}
      {fresh.map((group) => (
        <AgreementStrip
          key={group.key}
          agreement={group}
          auto={auto}
          viewAgeS={viewAgeS}
          digits={setups.digits}
        />
      ))}
      {running.map((group) => (
        <RunningNote
          key={group.key}
          agreement={group}
          auto={auto}
          viewAgeS={viewAgeS}
          digits={setups.digits}
        />
      ))}
      {freshCombinations.map((group) => (
        <CombinationCard key={group.key} group={group} digits={setups.digits} />
      ))}
      {oldCombinations.map((group) => (
        <CombinationNote key={group.key} group={group} />
      ))}
      <details className="fold-compact">
        <summary>Market context</summary>
        <dl className="facts">
        <div>
          <dt>Last closed bar</dt>
          {/* The chart's own reading of the same bar, which is the one the reader has in front of
              them, with the UTC the journal uses behind it. */}
          <dd>
            {chartClock(setups.asofBarEpoch) ?? setups.context.lastClosedUtc}
            <span className="muted">
              {chartClock(setups.asofBarEpoch) ? ` (chart clock) · ${setups.context.lastClosedUtc} UTC` : ""}{" "}
              · {setups.bars ?? "?"} bars scanned
            </span>
          </dd>
        </div>
        <div>
          <dt>Close</dt>
          <dd className="num">
            {price(setups.context.close)}
            <span className="muted"> · live {price(setups.context.lastPrice)}</span>
          </dd>
        </div>
        <div>
          <dt>
            Trend (EMA{setups.context.emaFastPeriod ?? 50} vs EMA{setups.context.emaSlowPeriod ?? 200})
          </dt>
          <dd>
            {setups.context.emaTrend ?? "not enough history"}
            {setups.context.emaFast !== null && setups.context.emaSlow !== null ? (
              <span className="muted">
                {" "}
                · {price(setups.context.emaFast)} vs {price(setups.context.emaSlow)}
              </span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt>Change (pips)</dt>
          <dd className="num">{changes.length ? changes.join(" · ") : "—"}</dd>
        </div>
        <div>
          <dt>ATR(14)</dt>
          <dd className="num">{pips(setups.context.atrPips)} pips</dd>
        </div>
        <div>
          <dt>Position in {setups.context.rangeBars ?? 96}-bar range</dt>
          <dd>
            {setups.context.rangePos === null
              ? "—"
              : `${(setups.context.rangePos * 100).toFixed(0)}%`}
            {setups.context.freshHigh ? <span className="tag">fresh high</span> : null}
            {setups.context.freshLow ? <span className="tag">fresh low</span> : null}
          </dd>
        </div>
        <div>
          <dt>View</dt>
          <dd className="num nowrap">{viewAgeS === null ? "—" : `${age(viewAgeS)} ago`}</dd>
        </div>
        </dl>
      </details>

      <div className="table-wrap">
        <table>
          <caption className="sr-only">
            Running position per rule, live ones first, with entry, stop, target and the move in R
          </caption>
          <thead>
            <tr>
              <th scope="col">Strategy</th>
              <th scope="col">Side</th>
              <th scope="col">Signal</th>
              <th scope="col" className="num">Entry</th>
              <th scope="col" className="num">Stop</th>
              <th scope="col" className="num">Target</th>
              <th scope="col">Status</th>
              <th scope="col" className="num">Now (R)</th>
            </tr>
          </thead>
          <tbody>
            {setups.setups.map((row) => {
              const signalAgeS = ageSeconds(row, nowMs, setups.clock);
              const autoTrade = activeAutoByFamily.get(row.family);
              return (
              <tr
                key={row.family}
                className={
                  agreeing.has(row.family) || autoTrade
                    ? "agree-row"
                    : combined.has(row.family)
                      ? "combo-row"
                      : undefined
                }
              >
                <StrategyCell
                  id={row.family}
                  showRules={false}
                  badge={
                    autoTrade
                      ? `in auto-trade${autoTrade.ticket ? ` #${autoTrade.ticket}` : ""}`
                      : undefined
                  }
                  settings={row.params ?? undefined}
                />
                <td>{row.side ?? "—"}</td>
                {/* Named in the chart's clock, so a row can be found on the chart. The UTC reading
                    the journal stores is kept as the cell's tooltip rather than a second column. */}
                <td title={row.signalUtc ? `UTC ${row.signalUtc}` : undefined}>
                  {/* The frame a card came from is on the card, every card: a filtered view is how
                      the reader got here, not what the row is made of. */}
                  {frame ? <span className="tag">{frame}</span> : null}{" "}
                  {chartClock(row.signalEpoch) ?? row.signalUtc ?? "—"}
                  {signalAgeS !== null ? <span className="muted"> · {age(signalAgeS)}</span> : null}
                </td>
                <td className="num mono">{price(row.entry)}</td>
                <td className="num mono">{price(row.sl)}</td>
                <td className="num mono">{price(row.tp)}</td>
                <td>
                  <StatusCell row={row} autoTrade={autoTrade} />
                </td>
                <td className="num mono">{r(row.status === "open" ? row.rNow : row.rResult)}</td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <CurrentSignals setups={setups} />
    </section>
  );
}

/**
 * The current signals of every rule that is holding a trade, which is what each rule is asking
 * for NOW rather than what it took.
 *
 * A rule holding one position re-fires (possibly on the opposite side) while the held trade runs,
 * and the writer records that newest signal per row. Until now nothing rendered it: a rule that
 * had flipped its reading sat on the panel looking like the old trade, and the fresh entry it is
 * asking for - the one that hands the target over, or reverses - was invisible until the trade
 * ended. Rendered below the table so the taken trades stay the primary grid, and tinted by side
 * so a flip is visible at a glance without a click.
 */
function CurrentSignals({ setups }: { setups: Setups }) {
  // The same price/R formatting the main grid uses, local because the main grid's helpers are
  // closures over that render's own `setups`.
  const price = (value: number | null) => (value === null ? "—" : value.toFixed(setups.digits));
  const r = (value: number | null) =>
    value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
  const current = setups.setups.filter((row) => row.status === "open" && row.latestSignal);
  if (current.length === 0) return null;
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">
          Each live rule&apos;s newest signal, the one it is asking for now
        </caption>
        <thead>
          <tr>
            <th scope="col">Current signal</th>
            <th scope="col">Side</th>
            <th scope="col">Signal</th>
            <th scope="col" className="num">Entry</th>
            <th scope="col" className="num">Stop</th>
            <th scope="col" className="num">Target</th>
            <th scope="col">Now (R)</th>
          </tr>
        </thead>
        <tbody>
          {current.map((row) => {
            const signal = row.latestSignal!;
            const signalMs = signal.signalEpoch ? chartClock(signal.signalEpoch) : null;
            return (
              <tr key={`${row.family}-now`}>
                <th scope="row" className="mono" title={`The newest signal of ${row.family}, which still holds the row above`}>
                  {row.family}
                </th>
                <td>
                  {signal.side === "buy" ? "△ buy" : "▽ sell"}
                  {row.side !== signal.side ? <span className="pnl-down"> flips</span> : null}
                </td>
                <td>{signalMs ?? signal.signalUtc ?? "—"}</td>
                <td className="num mono">{price(signal.entry)}</td>
                <td className="num mono">{price(signal.sl)}</td>
                <td className="num mono">{price(signal.tp)}</td>
                <td className="num mono">{r(signal.rNow)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StatusCell({
  row,
  autoTrade,
}: {
  row: SetupRow;
  autoTrade?: { ticket: number | null | undefined; strategy: string; families?: string[] };
}) {
  if (row.status === "none") {
    return <span className="muted">{row.note ?? "no card in this history"}</span>;
  }
  if (row.status === "error") {
    return <span className="pill pill-fail">rule error</span>;
  }
  if (row.status === "open") {
    const held =
      row.barsHeld === null ? "" : row.barsHeld === 0 ? "just entered" : `${row.barsHeld} bars in`;
    return (
      <>
        <span className="pill pill-info">live</span>
        {autoTrade ? <span className="pill pill-pass">traded</span> : null}
        {held ? <span className="muted"> {held}</span> : null}
      </>
    );
  }
  return (
    <>
      <StatusPill status={row.status} />
      {row.closedUtc ? <span className="muted"> {row.closedUtc}</span> : null}
    </>
  );
}

function StatusPill({ status }: { status: SetupStatus }) {
  if (status === "target") return <span className="pill pill-pass">target hit</span>;
  if (status === "stop") return <span className="pill pill-fail">stopped out</span>;
  return <span className="pill pill-info">time exit</span>;
}

/**
 * The observation-frame marking, wherever a faster frame's cards appear.
 *
 * M30 and M15 cards come from the same rules but were never validated on their own frame: nothing
 * in the research registry, and nothing in the auto engine, reads these artifacts. The marking
 * says exactly that wherever the view is served, so a reader cannot mistake an M15 card for one
 * the watcher might trade.
 */
function FrameMark({ timeframe }: { timeframe: string }) {
  return (
    <p className="agree-old" aria-label="untested timeframe">
      <span className="pill pill-warn">untested frame</span> <strong>{timeframe}</strong> — these
      cards are the same rules reading a faster candle. Information only: no strategy is validated
      on this frame and nothing here trades off it.
    </p>
  );
}

/** "3 h 12 m" reads faster than a bar count when the question is "how stale is this". */
function age(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} m`;
  if (seconds < 86_400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes ? `${hours} h ${minutes} m` : `${hours} h`;
  }
  const days = Math.floor(seconds / 86_400);
  const hours = Math.round((seconds % 86_400) / 3600);
  return hours ? `${days} d ${hours} h` : `${days} d`;
}
