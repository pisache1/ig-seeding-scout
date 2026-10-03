import pytest

from app.scoring import (VERDICTS, action_detail, compute_metrics,
                         score_account, verdict_key, verdict_label)
from app.web import COMPONENTS, FLAG_NOTES, decorate, sort_rows, verdict_counts


def test_every_action_prefix_maps_to_a_hue():
    for prefix in ("SEED_PRIORITY", "PAID_POST", "SEED_TEST",
                   "AFFILIATE_ONLY", "EXCLUDE"):
        key = verdict_key(f"{prefix}: 설명")
        assert key in dict(VERDICTS)


def test_unknown_action_does_not_crash():
    assert verdict_key(None) == "excl"
    assert verdict_key("") == "excl"
    assert verdict_label("쓰레기") in dict(VERDICTS).values()


def test_action_detail_strips_the_verdict_name():
    assert action_detail("SEED_PRIORITY: 무상 제품 시딩 1순위") == "무상 제품 시딩 1순위"
    assert action_detail("없음") == ""


def test_component_weights_match_the_scorer():
    m = compute_metrics(12_000, [], now=None)
    s = score_account("x", 12_000, m)
    # Every bar the detail page draws must exist in the scorer's breakdown.
    assert {k for k, _, _ in COMPONENTS} == set(s.breakdown)
    assert sum(cap for _, _, cap in COMPONENTS) == 100


def test_every_flag_the_scorer_emits_has_an_explanation():
    emitted = {
        "THIN_DATA", "LIKES_HIDDEN", "LOW_ER_SUSPECT_FOLLOWERS",
        "SHALLOW_COMMENTS", "STALE", "HASHTAG_SPAM", "ERRATIC_ENGAGEMENT",
        "SELF_REPORTED", "REACH_IMPLAUSIBLE_HIGH", "REACH_IMPLAUSIBLE_LOW",
    }
    assert emitted <= set(FLAG_NOTES)


def row(handle, score, followers, action, er=3.0, cost=100.0):
    return decorate({
        "handle": handle, "score": score, "followers": followers,
        "action": action, "est_cost": cost, "flags": [],
        "metrics": {"engagement_rate": er, "median_engagement": followers * er / 100},
        "breakdown": {},
    })


def test_decorate_computes_cost_per_engagement():
    r = row("a", 90, 10_000, "SEED_PRIORITY: x", er=4.0, cost=100.0)
    assert r["cpe"] == pytest.approx(100 / 400)
    assert r["tier"] == "micro"


def test_decorate_survives_zero_engagement():
    r = row("a", 0, 10_000, "EXCLUDE: x", er=0.0)
    assert r["cpe"] == 0.0


def test_counts_cover_all_five_verdicts():
    rows = [row("a", 90, 9_000, "SEED_PRIORITY: x"),
            row("b", 80, 900_000, "PAID_POST: x"),
            row("c", 40, 50_000, "EXCLUDE: x")]
    counts = verdict_counts(rows)
    assert set(counts) == {k for k, _ in VERDICTS}
    assert counts["seed"] == 1 and counts["paid"] == 1 and counts["excl"] == 1
    assert counts["aff"] == 0


def test_sorting_both_directions_and_unknown_key():
    rows = [row("a", 70, 5_000, "SEED_TEST: x"),
            row("b", 90, 50_000, "PAID_POST: x")]
    assert [r["handle"] for r in sort_rows(rows, "score", "desc")] == ["b", "a"]
    assert [r["handle"] for r in sort_rows(rows, "score", "asc")] == ["a", "b"]
    assert [r["handle"] for r in sort_rows(rows, "followers", "desc")] == ["b", "a"]
    # An unknown sort key falls back to score rather than erroring.
    assert [r["handle"] for r in sort_rows(rows, "nonsense", "desc")] == ["b", "a"]
