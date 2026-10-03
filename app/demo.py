"""Synthetic roster so the scoring and economics models can be explored
before any Graph API credentials exist.

The shape is deliberately realistic for a beauty/DTC niche: a long nano and
micro tail, a few mid accounts, one macro, plus two accounts that should get
caught by the fraud and staleness checks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .scoring import Metrics, Score, compute_metrics, score_account

# handle, followers, engagement_rate_pct, comment_share, days_since_last_post
FIXTURES: list[tuple[str, int, float, float, int]] = [
    ("glow.daily", 4_200, 6.1, 0.06, 2),
    ("minimal.skin", 6_800, 5.4, 0.05, 4),
    ("seoul.routine", 8_900, 4.8, 0.05, 3),
    ("barrier.repair", 12_000, 4.1, 0.05, 2),
    ("sensitive.notes", 18_500, 3.6, 0.04, 5),
    ("the.derm.diary", 24_000, 3.2, 0.04, 3),
    ("clean.shelf", 31_000, 2.9, 0.04, 6),
    ("texture.talk", 46_000, 2.6, 0.03, 4),
    ("sunscreen.club", 88_000, 2.1, 0.03, 8),
    ("ritual.mag", 140_000, 1.8, 0.03, 5),
    ("beauty.edit.kr", 310_000, 1.4, 0.02, 7),
    ("mega.glowup", 720_000, 1.1, 0.02, 9),
    # Should trip LOW_ER_SUSPECT_FOLLOWERS: macro size, nano-sized audience.
    ("bought.followers", 210_000, 0.35, 0.01, 4),
    # Should trip STALE.
    ("abandoned.skin", 27_000, 3.0, 0.04, 95),
    # Should trip SHALLOW_COMMENTS: lots of likes, almost no conversation.
    ("like.farm", 15_000, 3.4, 0.002, 3),
]

CAPTION = "데일리 스킨케어 루틴 공유 #skincare #뷰티 #글로우"


def _posts(followers: int, er: float, comment_share: float, last_post_days: int,
           n: int, now: datetime) -> list[dict]:
    engagement = followers * er / 100
    comments = max(int(engagement * comment_share), 0)
    likes = max(int(engagement - comments), 1)
    return [
        {
            "like_count": likes,
            "comments_count": comments,
            "timestamp": (now - timedelta(days=last_post_days + i * 3)).strftime(
                "%Y-%m-%dT%H:%M:%S+0000"
            ),
            "caption": CAPTION,
            "media_type": "IMAGE" if i % 3 else "VIDEO",
        }
        for i in range(n)
    ]


def build(brand_keywords: list[str] | None = None,
          now: datetime | None = None) -> list[tuple[str, Metrics, Score]]:
    now = now or datetime.now(timezone.utc)
    keywords = brand_keywords or ["skincare", "뷰티", "글로우"]
    out = []
    for handle, followers, er, comment_share, last in FIXTURES:
        posts = _posts(followers, er, comment_share, last, 12, now)
        m = compute_metrics(followers, posts, keywords, now=now)
        s = score_account(handle, followers, m)
        out.append((handle, m, s))
    return out


def profile_for(handle: str, followers: int) -> dict:
    return {
        "username": handle,
        "name": handle.replace(".", " ").title(),
        "biography": "데모 데이터 — 실제 계정이 아닙니다",
        "website": "",
        "followers_count": followers,
        "media_count": 120,
        "profile_picture_url": "",
    }
