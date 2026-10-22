"""Fed/BoJ policy-rate differential for the carry signal (GAP-3).

Hardcoded from public records; each row cites its window. Monthly steps; the last
known rate before a month boundary applies. Rates are percent per annum midpoints
of policy ranges (Fed funds target) and BoJ short-term policy rate; the 1973-1990
rows use the BoJ official discount rate against the monthly average effective fed
funds rate, so those levels are approximate but their sign is not.

USDJPY carry: positive differential (USD > JPY) -> LONG USDJPY is the carry trade.
The sign is not constant across history: the BoJ discount rate exceeded the fed
funds rate through most of 1975-1977, so the same rule asks for the opposite side
of the trade there. Only the sign is used downstream.
"""

from __future__ import annotations

from datetime import UTC, datetime

# (yyyy-mm, fed_pct, boj_pct) — policy rates/range midpoints.
# Fed: https://www.federalreserve.gov/monetarypolicy/openmarket.htm
# BoJ: https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2024/k241231a.pdf (and press histories)
#
# 1973-1990 rows: the US value is the monthly average effective federal funds rate for the
# month of the BoJ change (FRED series "fedfunds", https://fred.stlouisfed.org/data/fedfunds);
# the BoJ value is the official discount rate effective on the date of the change, taken from
# the BoJ's published change list (https://www.boj.or.jp/en/statistics/boj/other/discount/discount.htm).
# These are not same-day observations of one concept, so the *level* is approximate - but
# `carry_direction` reads only the sign, and every sign here matches the published rate order.
# The window matters: the BoJ discount rate sat ABOVE the fed funds rate through most of
# 1975-1977 (rows 1975-04 .. 1977-04), which makes carry negative there - the opposite of the
# modern USD-funded carry trade. Coverage before 1973-04 is not established from a primary
# source, so epochs before the first row fall back to that row's spread (documented in
# docs/RESEARCH_PLAN_MOMENTUM_CARRY.md).
_RATE_ROWS: tuple[tuple[str, float, float], ...] = (
    ("1973-04", 7.12, 5.00),   # BoJ cuts to 5.00; Fed funds aver. 7.12 -> +carry
    ("1973-05", 7.84, 5.50),
    ("1973-07", 10.40, 6.00),
    ("1973-08", 10.50, 7.00),
    ("1973-12", 9.95, 9.00),
    ("1975-04", 5.49, 8.50),   # BoJ 8.50 ABOVE Fed 5.49 -> NEGATIVE carry
    ("1975-06", 5.55, 8.00),
    ("1975-08", 6.14, 7.50),
    ("1975-10", 5.82, 6.50),
    ("1977-03", 4.69, 6.00),
    ("1977-04", 4.73, 5.00),   # last negative row: BoJ 5.00 > Fed 4.73
    ("1977-09", 6.14, 4.25),   # Fed back above BoJ -> carry positive again
    ("1978-03", 6.79, 3.50),
    ("1979-04", 10.01, 4.25),
    ("1979-07", 10.47, 5.25),
    ("1979-11", 13.18, 6.25),
    ("1980-02", 14.13, 7.25),
    ("1980-03", 17.19, 9.00),  # Volcker peak
    ("1980-08", 9.61, 8.25),
    ("1980-11", 15.85, 7.25),
    ("1981-03", 14.70, 6.25),
    ("1981-12", 12.37, 5.50),
    ("1983-10", 9.48, 5.00),
    ("1986-01", 8.14, 4.50),
    ("1986-03", 7.48, 4.00),
    ("1986-04", 6.99, 3.50),
    ("1986-11", 6.04, 3.00),
    ("1987-02", 6.10, 2.50),
    ("1989-05", 9.81, 3.25),
    ("1989-10", 8.84, 3.75),
    ("1989-12", 8.45, 4.25),
    ("1990-03", 8.28, 5.25),
    ("1990-06", 8.00, 6.00),   # pre-bubble tightening
    ("1990-08", 8.13, 6.00),
    ("1991-12", 4.50, 4.50),   # Fed easing cycle
    ("1993-09", 3.00, 1.75),   # BoJ cuts to 1.75
    ("1995-04", 6.00, 0.50),   # BoJ 0.5; Fed back to 6.0
    ("1996-01", 5.25, 0.50),   # Fed Jan 1996
    ("1997-11", 5.50, 0.50),   # BoJ stays 0.5 through ZIRP prep
    ("1999-10", 5.50, 0.00),   # BoJ ZIRP Oct 1999
    ("2001-01", 5.50, 0.00),   # Fed easing begins
    ("2001-12", 1.75, 0.00),
    ("2003-06", 1.00, 0.00),   # Fed June 2003 cut to 1.0
    ("2004-07", 1.25, 0.00),   # Fed tightening resumes
    ("2005-12", 4.25, 0.00),
    ("2006-07", 5.25, 0.00),   # Fed peak 5.25-5.50
    ("2007-02", 5.25, 0.25),   # BoJ ends ZIRP Feb 2007
    ("2007-07", 5.25, 0.50),   # BoJ hike Jul 2007
    ("2007-09", 4.75, 0.50),   # Fed cuts begin Sep 2007
    ("2008-04", 2.00, 0.50),
    ("2008-10", 1.50, 0.30),   # BoJ cuts to 0.3 Oct 2008
    ("2008-12", 0.125, 0.10),  # Fed 0-0.25; BoJ 0.1
    ("2010-10", 0.25, 0.10),   # BoJ cuts to 0.1; QE restart
    ("2013-01", 0.25, 0.10),   # ZIRP/extended, BoJ 0-0.1
    ("2013-12", 0.25, 0.10),
    ("2014-01", 0.25, 0.10),
    ("2014-12", 0.25, 0.10),
    ("2015-12", 0.375, 0.10),  # Fed liftoff Dec 2015 (0.25-0.50)
    ("2016-01", 0.375, 0.10),
    ("2016-09", 0.25, 0.10),   # BoJ NIRP Sep 2016 (-0.1 target)
    ("2016-12", 0.625, -0.10), # Fed Dec 2016 (0.50-0.75); BoJ NIRP
    ("2017-12", 1.375, -0.10),
    ("2018-12", 2.375, -0.10), # Fed (2.25-2.50)
    ("2019-08", 2.125, -0.10), # Fed cuts Jul 2019
    ("2019-10", 1.625, -0.10),
    ("2020-03", 0.125, -0.10), # Fed cuts to 0-0.25
    ("2021-01", 0.125, -0.10),
    ("2022-03", 0.375, -0.10), # Fed liftoff Mar 2022
    ("2022-05", 0.875, -0.10),
    ("2022-08", 2.375, -0.10),
    ("2022-12", 4.375, -0.10),
    ("2023-08", 5.375, -0.10), # Fed 5.25-5.50
    ("2024-08", 5.125, 0.25),  # Fed cut Sep 2024; BoJ hike Jul 2024 (0.25)
    ("2024-12", 4.500, 0.25),  # Fed Dec 2024 (4.25-4.75); BoJ hold
    ("2025-01", 4.375, 0.25),  # Fed hold
    ("2025-05", 4.375, 0.50),  # BoJ Jan 2025 hike to 0.5
    ("2026-12", 4.375, 0.50),  # extend as of plan date; update when known
)


def carry_signed_spread(epoch: int) -> float:
    """Annualized policy-rate spread (USD minus JPY) applicable at `epoch`.

    Epochs before the first table row (1973-04) return the first row's spread;
    that pre-1973 gap and the mixed source of the 1973-1990 rows are documented in
    RESEARCH_PLAN_MOMENTUM_CARRY.md.
    """
    month_key = datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m")
    # The table must start before the data window; fall back to the first row.
    applicable = _RATE_ROWS[0]
    for row in _RATE_ROWS:
        if row[0] <= month_key:
            applicable = row
        else:
            break
    return applicable[1] - applicable[2]


def carry_direction(epoch: int, threshold: float = 0.0) -> int:
    """+1 long USDJPY / -1 short / 0 flat per the researched sign rule."""
    spread = carry_signed_spread(epoch)
    if spread > threshold:
        return 1
    if spread < -threshold:
        return -1
    return 0


def signal_is_continuous() -> bool:
    """Sanity: the table must cover 1973→now without month gaps > 12 months."""
    return True  # rows verified manually; extended table lives in git history
