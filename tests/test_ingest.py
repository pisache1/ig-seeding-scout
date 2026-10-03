import csv

import pytest

from app.ingest import IngestError, load_csv, score_row
from app.portfolio import Assumptions, cost_of, reach_of, roster_summary

BASE = {
    "handle": "@private.glow",
    "followers": "8,400",
    "avg_likes": "520",
    "avg_comments": "34",
    "avg_reach": "3100",
    "rate_usd": "$150",
}


def test_parses_commas_currency_and_at_sign():
    s = score_row(dict(BASE))
    assert s.handle == "private.glow"
    assert s.followers == 8400
    assert s.quoted_rate_usd == 150.0
    assert s.measured_reach == 3100.0


def test_k_and_m_suffixes():
    s = score_row({**BASE, "followers": "12.4k", "avg_likes": "1.2k", "avg_reach": "1m"})
    assert s.followers == 12_400
    assert s.est_engagement_per_post == pytest.approx(1234.0)


def test_korean_column_names_work():
    s = score_row({"핸들": "seoul.skin", "팔로워": "9000", "좋아요": "500", "댓글": "30"})
    assert s.handle == "seoul.skin"
    assert s.followers == 9000


def test_every_ingested_row_is_marked_self_reported():
    s = score_row(dict(BASE))
    assert "SELF_REPORTED" in s.flags
    assert s.data_source == "mediakit"


def test_measured_cpm_uses_quoted_rate_not_estimate():
    s = score_row(dict(BASE))
    assert s.breakdown["measured_cpm_usd"] == pytest.approx(150 / 3100 * 1000, abs=0.01)


def test_measured_cpm_falls_back_to_estimate_without_a_quote():
    row = dict(BASE)
    del row["rate_usd"]
    s = score_row(row)
    assert s.quoted_rate_usd == 0.0
    assert s.breakdown["measured_cpm_usd"] == pytest.approx(
        s.est_post_cost_usd / 3100 * 1000, abs=0.01
    )


def test_inflated_reach_is_flagged():
    s = score_row({**BASE, "followers": "45000", "avg_reach": "44000"})
    assert "REACH_IMPLAUSIBLE_HIGH" in s.flags


def test_suspiciously_low_reach_is_flagged():
    s = score_row({**BASE, "followers": "100000", "avg_reach": "2000"})
    assert "REACH_IMPLAUSIBLE_LOW" in s.flags


def test_missing_followers_is_an_error():
    with pytest.raises(IngestError):
        score_row({"handle": "x"})


def test_garbage_number_is_an_error():
    with pytest.raises(IngestError):
        score_row({**BASE, "followers": "약 8천명"})


def test_portfolio_prefers_measured_values_over_models():
    a = Assumptions()
    s = score_row(dict(BASE))
    # measured reach wins over est_engagement * reach_multiplier
    assert reach_of(s, a) == 3100.0
    assert cost_of(s) == 150.0

    row = dict(BASE)
    del row["avg_reach"]
    del row["rate_usd"]
    modelled = score_row(row)
    assert reach_of(modelled, a) == pytest.approx(554 * a.reach_multiplier)
    assert cost_of(modelled) == modelled.est_post_cost_usd


def test_load_csv_reports_bad_rows_without_losing_good_ones(tmp_path):
    path = tmp_path / "kit.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["handle", "followers", "avg_likes", "avg_comments"])
        w.writerow(["@good.one", "9000", "400", "25"])
        w.writerow(["", "", "", ""])              # blank row, skipped silently
        w.writerow(["@no.followers", "", "10", "1"])
        w.writerow(["@good.two", "12000", "500", "30"])
    scores, errors = load_csv(path)
    assert [s.handle for s in scores] == ["good.one", "good.two"]
    assert len(errors) == 1 and errors[0][0] == 4


def test_summary_counts_data_quality():
    scores = [score_row(dict(BASE)), score_row({**BASE, "handle": "b", "avg_reach": ""})]
    summary = roster_summary(scores)
    assert summary["with_measured_reach"] == 1
    assert summary["self_reported"] == 2


def test_stored_metrics_keep_cadence_and_recency():
    """A fresh Metrics() would report every ingested creator as 999 days stale."""
    from app.ingest import score_row_with_metrics

    m, s = score_row_with_metrics({**BASE, "posts_per_week": "3.5",
                                   "days_since_last_post": "4"})
    assert m.posts_per_week == 3.5
    assert m.days_since_last_post == 4.0
    assert m.engagement_rate == pytest.approx(s.est_engagement_per_post / 8400 * 100)


def test_load_csv_can_return_pairs(tmp_path):
    from app.ingest import load_csv as lc
    path = tmp_path / "k.csv"
    path.write_text("handle,followers,avg_likes\n@a,9000,400\n", encoding="utf-8")
    pairs, errors = lc(path, with_metrics=True)
    assert not errors
    metrics, score = pairs[0]
    assert score.handle == "a" and metrics.days_since_last_post == 5.0
