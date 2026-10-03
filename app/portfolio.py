"""Roster economics.

Scoring one account tells you whether to work with them. This module
answers the other question: once you have assembled a pool of creators,
what is the pool actually worth, and which mix is cheapest per result?

Everything here is a model, not a measurement. The inputs you control
(product COGS, AOV, margin) are exact; the audience-side inputs
(overlap, reach ratio, conversion) are assumptions you should replace
with your own post-campaign numbers as soon as you have one wave of data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .scoring import Score

# Engagement is a fraction of reach. Industry observation puts post reach
# somewhere around 10-30x the engagement count for mid-size accounts.
DEFAULT_REACH_MULTIPLIER = 15.0

# Followings overlap inside a niche roster: the 20th beauty creator reaches
# far fewer new people than the 1st. Modelled as diminishing returns.
DEFAULT_OVERLAP_DECAY = 0.85


@dataclass
class Assumptions:
    product_cogs_usd: float = 15.0       # what one seeded unit costs you
    shipping_usd: float = 5.0
    aov_usd: float = 60.0                # average order value
    gross_margin: float = 0.6            # 0..1
    seeding_post_rate: float = 0.55      # share of seeded creators who post
    click_rate: float = 0.015            # clicks per person reached
    conversion_rate: float = 0.025       # orders per click
    reach_multiplier: float = DEFAULT_REACH_MULTIPLIER
    overlap_decay: float = DEFAULT_OVERLAP_DECAY


@dataclass
class ScenarioResult:
    name: str
    creators: int = 0
    cost_usd: float = 0.0
    gross_reach: float = 0.0
    unique_reach: float = 0.0
    engagements: float = 0.0
    orders: float = 0.0
    revenue_usd: float = 0.0
    gross_profit_usd: float = 0.0
    roas: float = 0.0
    cpm_usd: float = 0.0
    cost_per_engagement_usd: float = 0.0
    cac_usd: float = 0.0
    breakeven_orders: float = 0.0
    notes: list[str] = field(default_factory=list)


def reach_of(s: Score, a: Assumptions) -> float:
    """Measured reach if the creator gave us theirs, modelled otherwise."""
    if s.measured_reach > 0:
        return s.measured_reach
    return s.est_engagement_per_post * a.reach_multiplier


def cost_of(s: Score) -> float:
    """A quoted rate is a fact; our followers-based estimate is a guess."""
    return s.quoted_rate_usd if s.quoted_rate_usd > 0 else s.est_post_cost_usd


def _unique_reach(reaches: list[float], decay: float) -> float:
    """Diminishing returns on overlapping audiences.

    Sort descending, then discount each additional creator geometrically.
    The largest account contributes fully; the nth contributes decay^(n-1).
    """
    ordered = sorted(reaches, reverse=True)
    return sum(r * (decay ** i) for i, r in enumerate(ordered))


def _finish(r: ScenarioResult, a: Assumptions) -> ScenarioResult:
    clicks = r.unique_reach * a.click_rate
    r.orders = clicks * a.conversion_rate
    r.revenue_usd = r.orders * a.aov_usd
    r.gross_profit_usd = r.revenue_usd * a.gross_margin - r.cost_usd
    r.roas = r.revenue_usd / r.cost_usd if r.cost_usd else 0.0
    r.cpm_usd = r.cost_usd / r.unique_reach * 1000 if r.unique_reach else 0.0
    r.cost_per_engagement_usd = r.cost_usd / r.engagements if r.engagements else 0.0
    r.cac_usd = r.cost_usd / r.orders if r.orders else 0.0
    contribution = a.aov_usd * a.gross_margin
    r.breakeven_orders = r.cost_usd / contribution if contribution > 0 else 0.0
    return r


def seeding_scenario(scores: list[Score], a: Assumptions) -> ScenarioResult:
    """Send free product to everyone who passes. Pay nothing per post."""
    pool = [s for s in scores if s.action.startswith(("SEED_PRIORITY", "SEED_TEST"))]
    r = ScenarioResult(name="무상 시딩", creators=len(pool))
    unit = a.product_cogs_usd + a.shipping_usd
    r.cost_usd = len(pool) * unit

    # Only a fraction of seeded creators actually post.
    r.engagements = sum(s.est_engagement_per_post for s in pool) * a.seeding_post_rate
    reaches = [reach_of(s, a) * a.seeding_post_rate for s in pool]
    r.gross_reach = sum(reaches)
    r.unique_reach = _unique_reach(reaches, a.overlap_decay)
    r.notes.append(f"받은 사람 중 {a.seeding_post_rate:.0%}가 올린다고 가정 — 계약이 없으니 강제할 수 없습니다")
    r.notes.append(f"1인당 제품 원가와 배송비 ${unit:,.0f}")
    return _finish(r, a)


def paid_scenario(scores: list[Score], a: Assumptions) -> ScenarioResult:
    """Pay the recommended rate to everyone scoring 60+."""
    pool = [s for s in scores if s.total >= 60]
    r = ScenarioResult(name="유료 협찬", creators=len(pool))
    r.cost_usd = sum(cost_of(s) for s in pool)
    r.engagements = sum(s.est_engagement_per_post for s in pool)
    reaches = [reach_of(s, a) for s in pool]
    r.gross_reach = sum(reaches)
    r.unique_reach = _unique_reach(reaches, a.overlap_decay)
    r.notes.append("계약이 있으니 전원 게시한다고 가정")
    return _finish(r, a)


def single_macro_scenario(scores: list[Score], a: Assumptions) -> ScenarioResult:
    """The control case: spend the same money on one big account instead."""
    if not scores:
        return ScenarioResult(name="매크로 1건")
    biggest = max(scores, key=lambda s: s.followers)
    r = ScenarioResult(name="매크로 1건", creators=1)
    r.cost_usd = cost_of(biggest)
    r.engagements = biggest.est_engagement_per_post
    r.unique_reach = r.gross_reach = reach_of(biggest, a)
    r.notes.append(f"@{biggest.handle} 한 명에게만 집행 — 분산이 없습니다")
    r.notes.append("실패하면 전액 손실이고, 콘텐츠도 하나만 남습니다")
    return _finish(r, a)


def compare(scores: list[Score], a: Assumptions | None = None) -> list[ScenarioResult]:
    a = a or Assumptions()
    return [
        seeding_scenario(scores, a),
        paid_scenario(scores, a),
        single_macro_scenario(scores, a),
    ]


def roster_summary(scores: list[Score]) -> dict:
    """What the pool is, before you spend anything on it."""
    by_tier: dict[str, int] = {}
    for s in scores:
        by_tier[s.tier] = by_tier.get(s.tier, 0) + 1
    usable = [s for s in scores if s.total >= 60]
    return {
        "evaluated": len(scores),
        "usable": len(usable),
        "excluded": sum(s.action.startswith("EXCLUDE") for s in scores),
        "by_tier": by_tier,
        "total_followers": sum(s.followers for s in scores),
        "usable_followers": sum(s.followers for s in usable),
        "median_score": (
            sorted(s.total for s in scores)[len(scores) // 2] if scores else 0
        ),
        # The asset you are actually building: content licences, repeat
        # collaborators and a priced audience graph you can re-activate.
        "reusable_content_pieces": len(usable),
        # Data quality: modelled reach is a guess, measured reach is not.
        "with_measured_reach": sum(s.measured_reach > 0 for s in scores),
        "with_quoted_rate": sum(s.quoted_rate_usd > 0 for s in scores),
        "self_reported": sum(s.data_source == "mediakit" for s in scores),
    }
