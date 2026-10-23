"""Recommendation gate (D-027): run every check, record every reason code, default-deny.

Inputs are simple dataclasses so the gate is pure and testable without a terminal.
Unverifiable checks produce NOT_VERIFIED reason codes — the gate never passes on trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PASS = "CARD"
SKIP = "SKIP"

# Display order for reason codes; every check runs regardless of earlier failures.
CHECK_ORDER: tuple[str, ...] = (
    "DATA_STALE",
    "DATA_STALE_OR_UNVERIFIED",
    "SPREAD_TOO_WIDE",
    "SPREAD_UNVERIFIED",
    "MARKET_CLOSED",
    "MARKET_STATE_UNVERIFIED",
    "NEWS_WINDOW",
    "NEWS_UNVERIFIED",
    "FAST_MOVE_CIRCUIT_BREAKER",
    "DAILY_LOSS_LIMIT",
    "DAILY_LOSS_UNVERIFIED",
    "POSITION_LIMIT",
    "POSITIONS_UNVERIFIED",
    "NO_VALIDATED_STRATEGY",
    "RISK_EXCEEDS_BUDGET",
    "RISK_FIT_UNVERIFIED",
)


@dataclass(frozen=True)
class GateInputs:
    symbol: str = "USDJPY"
    bar_age_s: int | None = None            # None = cannot verify
    max_bar_age_s: int = 180
    spread_pips: float | None = None
    spread_ceiling_pips: float = 4.0
    market_open: bool | None = None
    news_verifiable: bool = False
    news_excluded: bool = True              # excluded until proven otherwise (D-032 default)
    circuit_breaker_tripped: bool = True    # tripped until measured calm
    daily_loss_pct: float | None = None     # None = cannot verify positions/equity
    daily_loss_limit_pct: float = 3.0
    # The count this gate's cap judges: the engine's own (magic-tagged) positions, not the
    # account total. Manual trades are the holder's book, not the machine's budget.
    open_positions: int | None = None       # None = cannot verify
    max_positions: int = 2
    validated_strategy: str | None = None   # None = nothing has passed research
    risk_fits: bool | None = None           # None = could not compute sizing


@dataclass
class GateResult:
    verdict: str
    reason_codes: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return self.verdict == PASS


def evaluate(inputs: GateInputs) -> GateResult:
    checks: dict[str, str] = {}  # reason code -> "fail" | "not_verified" | "pass"

    def fail(reason: str) -> None:
        checks[reason] = "fail"

    def unverified(reason: str) -> None:
        checks[reason] = "not_verified"

    if inputs.bar_age_s is None:
        unverified("DATA_STALE_OR_UNVERIFIED")
    elif inputs.bar_age_s > inputs.max_bar_age_s:
        fail("DATA_STALE")

    if inputs.spread_pips is None:
        unverified("SPREAD_UNVERIFIED")
    elif inputs.spread_pips > inputs.spread_ceiling_pips:
        fail("SPREAD_TOO_WIDE")

    if inputs.market_open is None:
        unverified("MARKET_STATE_UNVERIFIED")
    elif not inputs.market_open:
        fail("MARKET_CLOSED")

    if not inputs.news_verifiable:
        fail("NEWS_UNVERIFIED")
    elif inputs.news_excluded:
        fail("NEWS_WINDOW")

    if inputs.circuit_breaker_tripped:
        fail("FAST_MOVE_CIRCUIT_BREAKER")

    if inputs.daily_loss_pct is None:
        unverified("DAILY_LOSS_UNVERIFIED")
    elif inputs.daily_loss_pct >= inputs.daily_loss_limit_pct:
        fail("DAILY_LOSS_LIMIT")

    if inputs.open_positions is None:
        unverified("POSITIONS_UNVERIFIED")
    elif inputs.open_positions >= inputs.max_positions:
        fail("POSITION_LIMIT")

    if inputs.validated_strategy is None:
        fail("NO_VALIDATED_STRATEGY")

    if inputs.risk_fits is None:
        unverified("RISK_FIT_UNVERIFIED")
    elif not inputs.risk_fits:
        fail("RISK_EXCEEDS_BUDGET")

    # checks maps reason code -> "fail" | "not_verified"; anything present in checks is a blocker.
    blockers = [reason for reason in CHECK_ORDER if reason in checks]
    verdict = SKIP if blockers else PASS
    if verdict == SKIP:
        ordered = [r for r in CHECK_ORDER if r in checks]
        # Preserve check order for any code not in CHECK_ORDER (defensive).
        ordered += [r for r in checks if r not in CHECK_ORDER]
        return GateResult(verdict=SKIP, reason_codes=ordered, blockers=blockers)
    return GateResult(verdict=PASS, reason_codes=[])
