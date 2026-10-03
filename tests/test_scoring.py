from datetime import datetime, timedelta, timezone

import pytest

from app.parser import extract_targets
from app.portfolio import Assumptions, compare, roster_summary
from app.scoring import compute_metrics, score_account, tier_for

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def posts(n=12, likes=400, comments=12, days_apart=3, start_days_ago=2, **over):
    out = []
    for i in range(n):
        ts = NOW - timedelta(days=start_days_ago + i * days_apart)
        out.append({
            "like_count": likes,
            "comments_count": comments,
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "caption": over.get("caption", "오늘의 스킨케어 루틴 #skincare #glow"),
            "media_type": "IMAGE",
        })
    return out


def test_tiers():
    assert tier_for(5_000)[0] == "nano"
    assert tier_for(10_000)[0] == "micro"
    assert tier_for(250_000)[0] == "mid"
    assert tier_for(5_000_000)[0] == "mega"


def test_healthy_micro_scores_high_and_gets_seeded():
    m = compute_metrics(12_000, posts(), ["skincare"], now=NOW)
    s = score_account("good", 12_000, m)
    assert m.engagement_rate == pytest.approx(412 / 12_000 * 100, rel=1e-3)
    assert s.total >= 75
    assert s.action.startswith("SEED_PRIORITY")
    assert s.flags == []


def test_bought_followers_are_excluded():
    # 200k followers but engagement of a 5k account
    m = compute_metrics(200_000, posts(likes=300, comments=4), ["skincare"], now=NOW)
    s = score_account("fake", 200_000, m)
    assert "LOW_ER_SUSPECT_FOLLOWERS" in s.flags
    assert s.action.startswith("EXCLUDE")


def test_stale_account_is_flagged():
    m = compute_metrics(20_000, posts(start_days_ago=90), ["skincare"], now=NOW)
    s = score_account("stale", 20_000, m)
    assert "STALE" in s.flags
    assert m.days_since_last_post == pytest.approx(90, abs=0.1)


def test_shallow_comments_flagged():
    m = compute_metrics(12_000, posts(likes=800, comments=1), ["skincare"], now=NOW)
    s = score_account("shallow", 12_000, m)
    assert "SHALLOW_COMMENTS" in s.flags


def test_hidden_likes_do_not_crash():
    data = posts(6)
    for p in data[:3]:
        p["like_count"] = None
    m = compute_metrics(12_000, data, now=NOW)
    s = score_account("hidden", 12_000, m)
    assert m.likes_hidden is True
    assert m.posts_analyzed == 3
    assert 0 <= s.total <= 100


def test_thin_data_penalised():
    m = compute_metrics(12_000, posts(2), ["skincare"], now=NOW)
    s = score_account("thin", 12_000, m)
    assert "THIN_DATA" in s.flags


def test_no_posts_is_safe():
    m = compute_metrics(12_000, [], now=NOW)
    s = score_account("empty", 12_000, m)
    assert s.total == 0 or s.total < 45
    assert s.cost_per_engagement_usd == 0.0


def test_median_resists_one_viral_outlier():
    data = posts(10)
    data[0]["like_count"] = 500_000
    m = compute_metrics(12_000, data, now=NOW)
    assert m.median_likes == 400
    assert m.viral_rate > 0


def test_parser_handles_mixed_input():
    t = extract_targets(
        "이거 봐 @glow.studio 랑 https://www.instagram.com/reel/Cxyz-123/ "
        "그리고 instagram.com/another_one"
    )
    assert "glow.studio" in t["handles"]
    assert "another_one" in t["handles"]
    assert t["shortcodes"] == ["Cxyz-123"]


def test_parser_reads_attachment_payloads():
    t = extract_targets(None, [{"payload": {"url": "https://instagram.com/p/AbC_1/"}}])
    assert t["shortcodes"] == ["AbC_1"]


def test_portfolio_scenarios_are_consistent():
    roster = []
    for i, followers in enumerate([8_000, 15_000, 40_000, 120_000]):
        m = compute_metrics(followers, posts(likes=int(followers * 0.04)), ["skincare"], now=NOW)
        roster.append(score_account(f"c{i}", followers, m))

    summary = roster_summary(roster)
    assert summary["evaluated"] == 4
    assert summary["total_followers"] == 183_000

    seeding, paid, macro = compare(roster, Assumptions())
    # Overlap discount means unique reach never exceeds the naive sum.
    assert seeding.unique_reach <= seeding.gross_reach
    assert paid.cost_usd > seeding.cost_usd
    # Spreading the same niche across many creators should beat one macro on CPM.
    assert paid.cpm_usd > 0 and macro.cpm_usd > 0
    assert paid.breakeven_orders == pytest.approx(
        paid.cost_usd / (Assumptions().aov_usd * Assumptions().gross_margin)
    )
