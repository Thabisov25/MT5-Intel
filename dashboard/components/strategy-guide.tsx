import { STRATEGIES, strategyInfo } from "@/lib/strategies";

/**
 * The one explanation of how this system decides, enters and judges a trade.
 *
 * Body only, no panel: it lives behind the info control on the research page instead of above the
 * runs. The steps are a real sequence - a signal is not a trade until the next bar opens - so they
 * are numbered, and the glossary is here because every table on that page is read while it is open.
 */
export function TradingManual() {
  return (
    <>
      <ol className="steps">
        <li>
          <strong>A rule watches finished bars.</strong> No family can act on a price that is
          still forming; every signal is confirmed on a closed bar.
        </li>
        <li>
          <strong>The entry is the next bar&rsquo;s open.</strong> A signal on the close of one
          bar is entered at the open of the next, and a short pays the spread to get in.
        </li>
        <li>
          <strong>Stop and target are set before entry.</strong> The stop comes from the rule&rsquo;s
          own structure &mdash; a swing extreme, a range edge, or a multiple of ATR. The target is a
          fixed multiple of that stop distance, so the risk is known at entry and every card shows
          all three prices.
        </li>
        <li>
          <strong>It is graded bar by bar afterwards.</strong> Stop touched first: the trade is
          wrong and scores &minus;1R before costs, so a card records about &minus;1.2R once the
          spread is charged. Target first: it pays the target multiple. Neither within the hold
          limit: it exits at the close for whatever it earned. When one bar touches both, the stop
          is assumed first &mdash; the conservative reading.
        </li>
      </ol>
      <h3>How the system knows it was wrong</h3>
      <p className="guide-copy">
        At the trade level, <strong>the stop is the falsification point</strong>: if price trades
        through it before the target, the trade is wrong by construction, and the card records
        &minus;1R. At the rule level, a family is rejected unless it is positive on the training
        window <em>and</em> the held-out test window, has at least 30 test trades, beats a
        side-flipped placebo with identical timing, and stays positive when costs are multiplied by
        1.5. <strong>Rejection is the default outcome</strong>, and the last column of every table
        says so plainly.
      </p>
      <h3>What each column means</h3>
      <dl className="facts">
        <div>
          <dt>R</dt>
          <dd>The stop distance. &minus;1R is a stop-out, +2R is a target hit; costs are charged in R.</dd>
        </div>
        <div>
          <dt>Cards</dt>
          <dd>Graded trades a replay issued &mdash; each is a signal, an entry, a stop and a target.</dd>
        </div>
        <div>
          <dt>Mean (R)</dt>
          <dd>Average result per trade. Negative means doing nothing would have been better.</dd>
        </div>
        <div>
          <dt>Gross test</dt>
          <dd>
            The same trades with zero cost charged. If gross is also about zero, there is no edge to
            find, however the stops are tuned.
          </dd>
        </div>
        <div>
          <dt>Placebo</dt>
          <dd>The same timing with every side flipped. A rule has to beat this, or it is noise.</dd>
        </div>
        <div>
          <dt>1.5&times; costs</dt>
          <dd>The test re-run with the spread multiplied by 1.5.</dd>
        </div>
        <div>
          <dt>Kept by cost rule</dt>
          <dd>Signals that survived the spread screen, out of every signal the rule produced.</dd>
        </div>
        <div>
          <dt>Stop / floor</dt>
          <dd>The rule&rsquo;s median stop, against the minimum the account&rsquo;s spread allows.</dd>
        </div>
        <div>
          <dt>TP / SL / time exit</dt>
          <dd>Which barrier the trade met: target, stop, or neither before the hold limit.</dd>
        </div>
        <div>
          <dt>Replay clock</dt>
          <dd>
            An old moment treated as now. Only bars at or before it can produce a card; everything
            after it is used solely to grade them.
          </dd>
        </div>
      </dl>
    </>
  );
}

/**
 * A replay is a historical what-if, and the columns only make sense once that is said.
 * Body only, no panel: the replays page keeps its runs on screen and offers this on demand.
 */
export function ReplayManual() {
  return (
    <>
      <p>
        A replay turns the clock back: cards are generated using only bars at or before the chosen
        moment, then graded on what the market actually did afterwards. It answers one question
        &mdash; <strong>if the gate had issued these cards, would that have beaten doing
        nothing?</strong> &mdash; because every card is a trade that was not taken.
      </p>
      <ul className="guide-list">
        <li>
          Mean and total R are net of cost: one spread going in, one coming out, charged against the
          stop distance.
        </li>
        <li>
          A strategy with no cards is not a fault. The cost rule removed its signals; the research
          page&rsquo;s &ldquo;kept by cost rule&rdquo; column shows how many survived out of how many.
        </li>
        <li>
          Small card counts make an average noisy. Ten cards is an anecdote. A rule is judged on the
          held-out test windows on the research page, not here.
        </li>
      </ul>
    </>
  );
}

/**
 * A family's identity, inline: human name, the machine id as a tag, and what it waits for.
 * Used in every table so a reader never has to learn an id or leave the page.
 */
export function StrategyCell({
  id,
  showRules = true,
  badge,
  settings,
  as = "th",
}: {
  id: string;
  showRules?: boolean;
  badge?: string;
  /**
   * The parameters the rule behind this row actually ran with, when the name does not say them.
   *
   * A family id can cover several rules: `ma_crossover` is 9/21 EMA, 20/50 SMA and 50/200 SMA in
   * the sweep, and the reader is comparing the row against lines on a chart. Naming the settings
   * is what lets them see it is a different pair rather than a card that disagrees with the chart.
   */
  settings?: string;
  /** Row header by default; use "td" when another cell already heads the row. */
  as?: "th" | "td";
}) {
  const info = strategyInfo(id);
  const body = (
    <>
      <span className="strategy-name">{info.name}</span>
      <span className="tag">{id}</span>
      {badge !== undefined && <span className="tag tag-group">{badge}</span>}
      {settings !== undefined && <span className="tag">{settings}</span>}
      {showRules && <span className="cell-rules">{info.waits}</span>}
    </>
  );
  if (as === "td") {
    return <td className="strategy-cell">{body}</td>;
  }
  return (
    <th scope="row" className="strategy-cell">
      {body}
    </th>
  );
}

/**
 * Every family's rules on one surface: what it waits for, where the stop and target go,
 * where the rule came from, and how it has actually performed. No navigation required.
 */
export function StrategyRuleboard() {
  return (
    <section className="panel" aria-labelledby="ruleboard">
      <h2 id="ruleboard">The rules, family by family</h2>
      <p className="guide-copy muted">
        Each block is one rule as the code implements it, including where it came from. Prices on a
        card are built from the stop: the target is a fixed multiple of it.
      </p>
      <div className="rule-grid">
        {Object.entries(STRATEGIES).map(([id, info]) => (
          <article className="rule-card" key={id}>
            <h3>
              {info.name} <span className="tag">{id}</span>
            </h3>
            <dl className="facts">
              <div>
                <dt>Entry</dt>
                <dd>{info.entry}</dd>
              </div>
              <div>
                <dt>Stop loss</dt>
                <dd>{info.stop}</dd>
              </div>
              <div>
                <dt>Take profit</dt>
                <dd>{info.target}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>{info.origin}</dd>
              </div>
              <div>
                <dt>Measured status</dt>
                <dd>{info.evidence}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

