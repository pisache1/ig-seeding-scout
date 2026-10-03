"""Media-kit CSV intake.

The Graph API cannot see private or personal accounts, and even for public
professional accounts it never returns reach, saves or story views. The
creator can see all of it in their own Insights. So: ask them, and type the
numbers in here.

Rows ingested this way are scored on the same scale as API rows, with two
upgrades where the data allows it — a measured reach replaces our reach
multiplier, and a quoted rate replaces our followers-based price estimate.

They are also flagged SELF_REPORTED, because a media kit is marketing
material. Spot-check anything that looks too good.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .scoring import Metrics, Score, score_account, tier_for

# Media kits are not standardised, so accept the common spellings.
ALIASES: dict[str, tuple[str, ...]] = {
    "handle": ("handle", "username", "account", "계정", "핸들"),
    "followers": ("followers", "follower_count", "팔로워"),
    "avg_likes": ("avg_likes", "likes", "average_likes", "평균좋아요", "좋아요"),
    "avg_comments": ("avg_comments", "comments", "average_comments", "평균댓글", "댓글"),
    "avg_reach": ("avg_reach", "reach", "average_reach", "도달", "평균도달"),
    "avg_saves": ("avg_saves", "saves", "저장"),
    "story_views": ("story_views", "avg_story_views", "스토리조회", "스토리"),
    "posts_per_week": ("posts_per_week", "cadence", "주간게시", "게시빈도"),
    "days_since_last_post": ("days_since_last_post", "last_post_days", "최근게시일"),
    "consistency": ("consistency", "안정성"),
    "brand_fit": ("brand_fit", "fit", "적합도"),
    "rate_usd": ("rate_usd", "rate", "quoted_rate", "단가", "견적"),
    "notes": ("notes", "note", "비고"),
}


class IngestError(ValueError):
    pass


def _norm(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_").lstrip("﻿")


def _pick(row: dict, field: str) -> str | None:
    for alias in ALIASES[field]:
        for key, value in row.items():
            if key and _norm(key) == alias and str(value).strip():
                return str(value).strip()
    return None


def _num(row: dict, field: str, default: float | None = None) -> float | None:
    raw = _pick(row, field)
    if raw is None:
        return default
    cleaned = raw.replace(",", "").replace("%", "").replace("$", "").strip()
    # Media kits love "12.4k" and "1.2m".
    mult = 1.0
    if cleaned and cleaned[-1].lower() in "km":
        mult = 1_000 if cleaned[-1].lower() == "k" else 1_000_000
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * mult
    except ValueError:
        raise IngestError(f"'{field}' 값을 숫자로 읽을 수 없습니다: {raw!r}")


def metrics_from_row(row: dict) -> tuple[str, int, Metrics, float, float]:
    handle = _pick(row, "handle")
    if not handle:
        raise IngestError("handle 열이 비어 있습니다")
    handle = handle.lstrip("@").lower()

    followers = _num(row, "followers")
    if not followers or followers <= 0:
        raise IngestError(f"@{handle}: followers 가 필요합니다")

    likes = _num(row, "avg_likes", 0.0) or 0.0
    comments = _num(row, "avg_comments", 0.0) or 0.0

    m = Metrics()
    m.posts_analyzed = 12  # media kits quote an average, not a post count
    m.median_likes = likes
    m.median_comments = comments
    m.median_engagement = likes + comments
    m.engagement_rate = m.median_engagement / followers * 100
    m.comment_ratio = comments / max(likes, 1.0) * 100
    m.posts_per_week = _num(row, "posts_per_week", 3.0) or 3.0
    m.days_since_last_post = _num(row, "days_since_last_post", 5.0) or 5.0
    # No per-post series, so consistency cannot be computed. Default to a
    # neutral value rather than a zero that would silently tank the score.
    m.consistency = _num(row, "consistency", 0.6) or 0.6
    m.brand_fit = _num(row, "brand_fit", 0.5) or 0.5

    reach = _num(row, "avg_reach", 0.0) or 0.0
    rate = _num(row, "rate_usd", 0.0) or 0.0
    return handle, int(followers), m, reach, rate


def score_row(row: dict, cfg=None) -> Score:
    return score_row_with_metrics(row, cfg)[1]


def score_row_with_metrics(row: dict, cfg=None) -> tuple[Metrics, Score]:
    """Both halves, so a caller can store the metrics it scored from.

    Storing a fresh Metrics() instead would silently drop cadence and
    recency, and the roster would report every ingested creator as 999 days
    stale even though the score was computed from the real figures.
    """
    handle, followers, m, reach, rate = metrics_from_row(row)
    s = score_account(
        handle,
        followers,
        m,
        rate_per_k=getattr(cfg, "rate_per_k", None),
        max_hashtags=getattr(cfg, "max_hashtags", 20),
        stale_days=getattr(cfg, "stale_days", 21),
    )
    s.measured_reach = reach
    s.quoted_rate_usd = rate
    s.data_source = "mediakit"
    s.flags.append("SELF_REPORTED")
    if reach > 0:
        # True CPM, not a modelled one.
        cost = rate if rate > 0 else s.est_post_cost_usd
        s.breakdown["measured_cpm_usd"] = round(cost / reach * 1000, 2)
        # A plausible reach is 10-60% of followers. Outside that, someone is
        # either inflating or quoting a different metric (impressions, views).
        ratio = reach / followers
        if ratio > 0.8:
            s.flags.append("REACH_IMPLAUSIBLE_HIGH")
        elif ratio < 0.05:
            s.flags.append("REACH_IMPLAUSIBLE_LOW")
    return m, s


def load_csv(path: str | Path, cfg=None, with_metrics: bool = False):
    """Returns (scores, errors), or ((metrics, score) pairs, errors)."""
    scores: list = []
    errors: list[tuple[int, str]] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise IngestError("CSV 헤더가 없습니다")
        for lineno, row in enumerate(reader, start=2):
            if not any(str(v).strip() for v in row.values()):
                continue
            try:
                pair = score_row_with_metrics(row, cfg)
                scores.append(pair if with_metrics else pair[1])
            except IngestError as exc:
                errors.append((lineno, str(exc)))
    return scores, errors


def profile_from_score(s: Score, notes: str = "") -> dict:
    return {
        "username": s.handle,
        "name": s.handle,
        "biography": notes or "미디어킷 수동 입력",
        "website": "",
        "followers_count": s.followers,
        "media_count": 0,
        "profile_picture_url": "",
    }
