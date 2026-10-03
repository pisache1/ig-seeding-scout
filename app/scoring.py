"""Seeding-fit scoring from public Instagram metrics.

Pure stdlib so it can be tested and tuned without any API access.

What we can actually observe for an account we do not own:
  - follower count
  - per-post like_count / comments_count (like_count is null if the
    account hides likes)
  - post timestamps, captions, media type

Reach, impressions, saves and shares are owner-only in the Graph API,
so every number here is derived from the three signals above.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)

# (name, min_followers, max_followers, healthy_er_pct)
# ER floors are industry rules of thumb, not laws. Tune in config.json.
TIERS: list[tuple[str, int, float, float]] = [
    ("nano", 0, 10_000, 4.0),
    ("micro", 10_000, 50_000, 2.5),
    ("mid", 50_000, 500_000, 1.5),
    ("macro", 500_000, 1_000_000, 1.2),
    ("mega", 1_000_000, math.inf, 1.0),
]

# Rough sponsored-post rate per 1,000 followers (USD), by tier.
DEFAULT_RATE_PER_K = {
    "nano": 5.0,
    "micro": 10.0,
    "mid": 15.0,
    "macro": 20.0,
    "mega": 25.0,
}


def tier_for(followers: int) -> tuple[str, float]:
    for name, lo, hi, floor in TIERS:
        if lo <= followers < hi:
            return name, floor
    return "mega", 1.0


def _parse_ts(value: str) -> datetime:
    """Graph API returns '2024-05-01T12:00:00+0000'."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Metrics:
    posts_analyzed: int = 0
    likes_hidden: bool = False
    median_likes: float = 0.0
    median_comments: float = 0.0
    median_engagement: float = 0.0
    engagement_rate: float = 0.0      # percent
    comment_ratio: float = 0.0        # comments as % of likes
    posts_per_week: float = 0.0
    days_since_last_post: float = 999.0
    consistency: float = 0.0          # 0..1, 1 = very stable engagement
    viral_rate: float = 0.0           # share of posts > 3x median engagement
    avg_hashtags: float = 0.0
    brand_fit: float = 0.0            # 0..1 keyword overlap
    reel_share: float = 0.0           # share of posts that are reels/video


def compute_metrics(
    followers: int,
    posts: list[dict],
    brand_keywords: list[str] | None = None,
    now: datetime | None = None,
) -> Metrics:
    m = Metrics()
    if not posts or followers <= 0:
        return m

    now = now or datetime.now(timezone.utc)
    brand_keywords = [k.lower() for k in (brand_keywords or [])]

    # Posts with a null like_count mean the creator hides likes. Keep them
    # for cadence, drop them from engagement math.
    scored = [p for p in posts if p.get("like_count") is not None]
    m.likes_hidden = len(scored) < len(posts)
    m.posts_analyzed = len(scored)

    stamps = sorted(_parse_ts(p["timestamp"]) for p in posts if p.get("timestamp"))
    if stamps:
        m.days_since_last_post = (now - stamps[-1]).total_seconds() / 86400
        span_days = max((stamps[-1] - stamps[0]).total_seconds() / 86400, 1.0)
        m.posts_per_week = len(stamps) / span_days * 7

    if not scored:
        return m

    likes = [float(p["like_count"]) for p in scored]
    comments = [float(p.get("comments_count") or 0) for p in scored]
    engagements = [l + c for l, c in zip(likes, comments)]

    m.median_likes = statistics.median(likes)
    m.median_comments = statistics.median(comments)
    m.median_engagement = statistics.median(engagements)
    m.engagement_rate = m.median_engagement / followers * 100
    m.comment_ratio = m.median_comments / max(m.median_likes, 1.0) * 100

    # Consistency via median absolute deviation — one viral post should not
    # make an otherwise steady account look erratic, and vice versa.
    if m.median_engagement > 0:
        mad = statistics.median([abs(e - m.median_engagement) for e in engagements])
        m.consistency = max(0.0, 1.0 - min(1.0, mad / m.median_engagement))
        m.viral_rate = sum(e > 3 * m.median_engagement for e in engagements) / len(engagements)

    captions = [(p.get("caption") or "") for p in scored]
    m.avg_hashtags = sum(len(HASHTAG_RE.findall(c)) for c in captions) / len(captions)

    if brand_keywords:
        hits = sum(
            any(k in c.lower() for k in brand_keywords) for c in captions
        )
        m.brand_fit = hits / len(captions)

    videoish = sum(
        (p.get("media_type") or "").upper() in {"VIDEO", "REELS", "CAROUSEL_ALBUM"}
        for p in scored
    )
    m.reel_share = videoish / len(scored)
    return m


@dataclass
class Score:
    handle: str
    followers: int
    tier: str
    er_floor: float
    total: int = 0
    breakdown: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    action: str = ""
    est_post_cost_usd: float = 0.0
    est_engagement_per_post: float = 0.0
    cost_per_engagement_usd: float = 0.0
    # Set only when the creator supplied real numbers (media kit / insights
    # screenshot). Measured reach beats any multiplier we could model, and a
    # quoted rate beats our followers-based estimate.
    measured_reach: float = 0.0
    quoted_rate_usd: float = 0.0
    data_source: str = "api"


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def score_account(
    handle: str,
    followers: int,
    m: Metrics,
    rate_per_k: dict[str, float] | None = None,
    max_hashtags: int = 20,
    stale_days: int = 21,
) -> Score:
    """Weighted 0-100 seeding-fit score. Weights sum to 100 before penalties."""
    tier, floor = tier_for(followers)
    rates = {**DEFAULT_RATE_PER_K, **(rate_per_k or {})}
    s = Score(handle=handle, followers=followers, tier=tier, er_floor=floor)

    # 1. Engagement rate against the tier floor (40 pts). Hitting the floor
    #    is a 70% score; 2x the floor maxes it out.
    er_ratio = m.engagement_rate / floor if floor else 0.0
    er_pts = 40 * _clamp(0.7 * er_ratio if er_ratio <= 1 else 0.7 + 0.3 * (er_ratio - 1))

    # 2. Comment depth (15 pts). 2% comments-to-likes is a healthy, talkative
    #    audience; below ~0.5% the likes are probably passive or bought.
    comment_pts = 15 * _clamp(m.comment_ratio / 2.0)

    # 3. Consistency (15 pts).
    consistency_pts = 15 * _clamp(m.consistency)

    # 4. Cadence and recency (15 pts), split evenly.
    cadence_pts = 7.5 * _clamp(m.posts_per_week / 3.0)
    recency_pts = 7.5 * _clamp(1.0 - m.days_since_last_post / stale_days)

    # 5. Brand fit (15 pts).
    fit_pts = 15 * _clamp(m.brand_fit)

    s.breakdown = {
        "engagement_rate": round(er_pts, 1),
        "comment_depth": round(comment_pts, 1),
        "consistency": round(consistency_pts, 1),
        "cadence": round(cadence_pts, 1),
        "recency": round(recency_pts, 1),
        "brand_fit": round(fit_pts, 1),
    }
    raw = sum(s.breakdown.values())

    # Penalties for things that make a roster slot risky.
    penalty = 0
    if m.posts_analyzed < 5:
        s.flags.append("THIN_DATA")
        penalty += 10
    if m.likes_hidden:
        s.flags.append("LIKES_HIDDEN")
    if m.engagement_rate < floor * 0.6 and m.posts_analyzed >= 5:
        s.flags.append("LOW_ER_SUSPECT_FOLLOWERS")
        penalty += 15
    if m.comment_ratio < 0.5 and m.median_likes > 0:
        s.flags.append("SHALLOW_COMMENTS")
        penalty += 10
    if m.days_since_last_post > stale_days:
        s.flags.append("STALE")
        penalty += 10
    if m.avg_hashtags > max_hashtags:
        s.flags.append("HASHTAG_SPAM")
        penalty += 5
    if m.viral_rate > 0.25 and m.consistency < 0.3:
        s.flags.append("ERRATIC_ENGAGEMENT")
        penalty += 5

    s.total = int(round(_clamp(raw - penalty, 0, 100)))

    s.est_engagement_per_post = round(m.median_engagement, 1)
    s.est_post_cost_usd = round(followers / 1000 * rates.get(tier, 10.0), 2)
    s.cost_per_engagement_usd = (
        round(s.est_post_cost_usd / m.median_engagement, 3)
        if m.median_engagement > 0 else 0.0
    )
    s.action = recommend(s, m)
    return s


def recommend(s: Score, m: Metrics) -> str:
    if "LOW_ER_SUSPECT_FOLLOWERS" in s.flags:
        return "EXCLUDE: 참여율이 티어 하한의 60%에 못 미칩니다. 팔로워를 샀을 가능성이 큽니다"
    if s.total >= 75:
        if s.tier in ("nano", "micro"):
            return "SEED_PRIORITY: 제품을 무상으로 보내세요. 게시 여부는 맡겨도 됩니다"
        return f"PAID_POST: 협찬비를 지급하세요. 예상 단가 ${s.est_post_cost_usd:,.0f}"
    if s.total >= 60:
        return "SEED_TEST: 소량만 먼저 보내고, 성과를 보고 다시 판단하세요"
    if s.total >= 45:
        return "AFFILIATE_ONLY: 제휴 코드만 주세요. 선지급은 하지 마세요"
    return "EXCLUDE: 지표가 기준에 못 미칩니다"


# --- verdict vocabulary -----------------------------------------------------
# The five verdicts are the organising fact of the UI: one hue each, used by
# the row rail and the roster composition bar.

# Each verdict is an action the operator takes, so they are phrased as
# actions and kept grammatically parallel.
VERDICTS: list[tuple[str, str]] = [
    ("seed", "바로 시딩"),
    ("paid", "유료 협찬"),
    ("test", "소량 테스트"),
    ("aff", "제휴 코드만"),
    ("excl", "제외"),
]
VERDICT_LABELS = dict(VERDICTS)

_PREFIX_TO_KEY = {
    "SEED_PRIORITY": "seed",
    "PAID_POST": "paid",
    "SEED_TEST": "test",
    "AFFILIATE_ONLY": "aff",
    "EXCLUDE": "excl",
}


def verdict_key(action: str | None) -> str:
    """Map an action string onto its verdict hue key."""
    head = (action or "").split(":", 1)[0].strip()
    return _PREFIX_TO_KEY.get(head, "excl")


def verdict_label(action: str | None) -> str:
    return VERDICT_LABELS[verdict_key(action)]


def action_detail(action: str | None) -> str:
    """The part after the colon — the reason, without the verdict name."""
    _, _, tail = (action or "").partition(":")
    return tail.strip()
