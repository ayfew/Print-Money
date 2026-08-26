"""Where every number in the brief is allowed to come from.

The rule this file exists to enforce: nothing reaches the reader without a place
they can go and check it themselves.  A brief that asserts things is asking to be
trusted; a brief that cites is asking to be verified, and only the second one is
worth reading every morning.

Sources are ranked in tiers, because "the Fed published this" and "a model
inferred this" are not the same kind of claim and should never look the same on
the page:

    1  official     the body that creates the fact - a central bank publishing
                    its own meeting date, a statistical agency publishing its own
                    release schedule.  Cannot be wrong about itself.
    2  primary      an exchange or venue reporting its own prices and funding.
    3  derived      arithmetic this project did on tier 1-2 data.  Correct if the
                    arithmetic is correct, which is what the tests are for.
    4  inferred     anything that required a judgement call.

There is deliberately no news tier.  Summarising headlines would mean either
paying an API for every run or letting a language model decide what a story
implies for a market, and this project has already measured that the second one
is a bet on return forecastability - which came back at r = +0.02.  Scheduled
releases from tier 1 carry the same information a news feed would carry about
*what is coming*, without anyone having to guess what it means.

If a source is not in :data:`REGISTRY`, no part of the brief may cite it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TIER_NAMES = {1: "official", 2: "primary", 3: "derived", 4: "inferred"}


@dataclass(frozen=True)
class Source:
    id: str
    tier: int
    name: str
    url: str
    what: str

    @property
    def tier_name(self) -> str:
        return TIER_NAMES.get(self.tier, "unknown")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tier": self.tier, "tier_name": self.tier_name,
                "name": self.name, "url": self.url, "what": self.what}


REGISTRY: dict[str, Source] = {
    s.id: s
    for s in [
        Source(
            id="fed",
            tier=1,
            name="Federal Reserve",
            url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            what="FOMC meeting and decision dates, published years ahead",
        ),
        Source(
            id="bls",
            tier=1,
            name="US Bureau of Labor Statistics",
            url="https://www.bls.gov/schedule/news_release/empsit.htm",
            what="Employment Situation release schedule",
        ),
        Source(
            id="yahoo",
            tier=2,
            name="Yahoo Finance",
            url="https://finance.yahoo.com/",
            what="daily open/high/low/close for every market in the universe",
        ),
        Source(
            id="binance",
            tier=2,
            name="Binance",
            url="https://www.binance.com/en/futures/funding-history/perpetual/real-time-funding-rate",
            what="perpetual funding rates and index prices",
        ),
        Source(
            id="study",
            tier=3,
            name="printmoney study",
            url="https://github.com/ayfew/Print-Money#readme",
            what="ten years of measured cost and persistence arithmetic, "
                 "reproducible with `pm study`",
        ),
        Source(
            id="events",
            tier=3,
            name="printmoney event impact",
            url="https://github.com/ayfew/Print-Money#readme",
            what="how much bigger a typical move was on each kind of scheduled "
                 "event day, reproducible with `pm events --measure`",
        ),
        Source(
            id="vol",
            tier=3,
            name="printmoney volatility percentile",
            url="https://github.com/ayfew/Print-Money#readme",
            what="where a market's 21-day volatility sits inside its own two-year "
                 "history",
        ),
    ]
}


def get(source_id: str) -> Source:
    """Look up a source, refusing anything that is not on the list.

    A KeyError here is the intended behaviour and the whole point of the module:
    it means a claim was written without deciding where it came from, and that is
    a bug to fix at the callsite rather than a condition to degrade around.
    """
    try:
        return REGISTRY[source_id]
    except KeyError:
        raise KeyError(
            f"'{source_id}' is not an allowed source. Add it to "
            f"printmoney/research/sources.py with a tier, or cite one of: "
            f"{', '.join(sorted(REGISTRY))}"
        ) from None


def cited(source_ids: list[str]) -> list[Source]:
    """De-duplicated sources, most authoritative first, for a footer."""
    seen = {sid: get(sid) for sid in source_ids}
    return sorted(seen.values(), key=lambda s: (s.tier, s.name))
