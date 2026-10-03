"""Handle -> public metrics -> score -> stored snapshot."""

from __future__ import annotations

import logging

from .config import Config
from .db import connect, save_snapshot, upsert_account
from .instagram import InstagramClient, InstagramError
from .scoring import Metrics, Score, compute_metrics, score_account

log = logging.getLogger(__name__)


def evaluate(client: InstagramClient, cfg: Config, handle: str) -> tuple[Metrics, Score]:
    profile = client.business_discovery(handle, media_limit=cfg.media_limit)
    posts = (profile.get("media") or {}).get("data", [])
    followers = int(profile.get("followers_count") or 0)

    metrics = compute_metrics(followers, posts, cfg.brand_keywords)
    score = score_account(
        handle=profile.get("username", handle),
        followers=followers,
        m=metrics,
        rate_per_k=cfg.rate_per_k,
        max_hashtags=cfg.max_hashtags,
        stale_days=cfg.stale_days,
    )
    with connect(cfg.db_path) as conn:
        upsert_account(conn, profile)
        save_snapshot(conn, score.handle, metrics, score)
    return metrics, score


def evaluate_many(
    client: InstagramClient, cfg: Config, handles: list[str]
) -> tuple[list[Score], list[tuple[str, str]]]:
    scores: list[Score] = []
    failures: list[tuple[str, str]] = []
    for h in handles:
        try:
            _, score = evaluate(client, cfg, h)
            scores.append(score)
        except InstagramError as exc:
            log.warning("skip @%s: %s", h, exc)
            failures.append((h, str(exc)))
    return scores, failures


def resolve_shortcodes(client: InstagramClient, shortcodes: list[str]) -> list[str]:
    """Post URLs do not contain the author, so ask oEmbed."""
    handles = []
    for code in shortcodes:
        author = client.username_from_permalink(f"https://www.instagram.com/p/{code}/")
        if author:
            handles.append(author.lower())
    return handles


def format_dm_reply(score: Score, metrics: Metrics) -> str:
    """Short enough for a DM, specific enough to act on."""
    flags = ", ".join(score.flags) if score.flags else "없음"
    return (
        f"@{score.handle} · {score.tier} · 팔로워 {score.followers:,}\n"
        f"점수 {score.total}/100\n"
        f"참여율 {metrics.engagement_rate:.2f}% (티어 기준 {score.er_floor:.1f}%)\n"
        f"댓글비 {metrics.comment_ratio:.2f}% · 주 {metrics.posts_per_week:.1f}회 "
        f"· 최근 {metrics.days_since_last_post:.0f}일 전\n"
        f"예상 단가 ${score.est_post_cost_usd:,.0f} "
        f"(CPE ${score.cost_per_engagement_usd:.3f})\n"
        f"주의: {flags}\n"
        f"→ {score.action}"
    )
