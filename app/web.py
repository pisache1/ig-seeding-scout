"""Presentation helpers shared by the web routes.

Keeps formatting decisions out of the route functions and out of the
templates, so the roster, the detail page and the history table all describe
a verdict the same way.
"""

from __future__ import annotations

from .scoring import (VERDICTS, action_detail, tier_for, verdict_key,
                      verdict_label)

# (breakdown key, label, points available) — mirrors score_account's weights.
COMPONENTS = [
    ("engagement_rate", "참여율", 40),
    ("comment_depth", "댓글 깊이", 15),
    ("consistency", "안정성", 15),
    ("cadence", "게시 주기", 7.5),
    ("recency", "최신성", 7.5),
    ("brand_fit", "브랜드 적합", 15),
]

FLAG_NOTES = {
    "LOW_ER_SUSPECT_FOLLOWERS":
        "팔로워 대비 참여율이 티어 하한의 60% 미만입니다. 구매 팔로워를 의심하세요. (−15)",
    "SHALLOW_COMMENTS":
        "좋아요 대비 댓글이 0.5% 미만입니다. 수동적 오디언스거나 봇 좋아요일 수 있습니다. (−10)",
    "STALE": "마지막 게시가 기준일을 넘었습니다. 지금 협업해도 노출이 안 나올 수 있습니다. (−10)",
    "THIN_DATA": "분석한 게시물이 5개 미만이라 평균을 믿기 어렵습니다. (−10)",
    "HASHTAG_SPAM": "게시물당 해시태그가 과도합니다. 도달을 태그로 사는 계정일 수 있습니다. (−5)",
    "ERRATIC_ENGAGEMENT":
        "바이럴 게시물은 잦은데 편차가 큽니다. 참여 조작(팟)일 수 있습니다. (−5)",
    "LIKES_HIDDEN": "좋아요를 숨긴 계정입니다. 해당 게시물은 참여 계산에서 제외했습니다.",
    "SELF_REPORTED": "크리에이터가 제출한 수치입니다. 미디어킷은 영업자료라는 점을 감안하세요.",
    "REACH_IMPLAUSIBLE_HIGH":
        "도달이 팔로워의 80%를 넘습니다. 노출수나 조회수를 도달로 적었을 가능성이 큽니다.",
    "REACH_IMPLAUSIBLE_LOW": "도달이 팔로워의 5% 미만입니다. 수치를 다시 확인하세요.",
}

# Flag identifiers are the scorer's vocabulary, not the reader's. The screen
# shows these labels; the identifier stays in the code and the API.
FLAG_LABELS = {
    "LOW_ER_SUSPECT_FOLLOWERS": "가짜 팔로워 의심",
    "SHALLOW_COMMENTS": "댓글 빈약",
    "ERRATIC_ENGAGEMENT": "참여 편차 큼",
    "REACH_IMPLAUSIBLE_HIGH": "도달 과장 의심",
    "REACH_IMPLAUSIBLE_LOW": "도달 과소",
    "STALE": "휴면",
    "HASHTAG_SPAM": "해시태그 과다",
    "THIN_DATA": "표본 부족",
    "LIKES_HIDDEN": "좋아요 비공개",
    "SELF_REPORTED": "본인 제출",
}

# Flags differ in kind, not just severity. A fraud signal and a note about
# where the data came from should not look alike.
FLAG_SEVERITY = {
    "LOW_ER_SUSPECT_FOLLOWERS": "severe",
    "SHALLOW_COMMENTS": "severe",
    "ERRATIC_ENGAGEMENT": "severe",
    "REACH_IMPLAUSIBLE_HIGH": "severe",
    "STALE": "caution",
    "HASHTAG_SPAM": "caution",
    "REACH_IMPLAUSIBLE_LOW": "caution",
    "SELF_REPORTED": "info",
    "LIKES_HIDDEN": "info",
    "THIN_DATA": "info",
}
SEVERITY_ORDER = {"severe": 0, "caution": 1, "info": 2}

CONFIDENCE_LABELS = {3: "높음", 2: "보통", 1: "낮음", 0: "매우 낮음"}


def confidence(row: dict) -> dict:
    """How much the numbers in this row can be leaned on.

    Separate from the score: a creator can be excellent on paper and still
    be a row you should not bet money on, because the paper is theirs.
    """
    flags = set(row.get("flags") or [])
    posts = (row.get("metrics") or {}).get("posts_analyzed", 0)
    level, why = 3, []

    if "SELF_REPORTED" in flags:
        level -= 1
        why.append("크리에이터가 직접 준 수치")
    if "THIN_DATA" in flags:
        level -= 1
        why.append("분석 게시물 5개 미만")
    elif posts and posts < 8:
        level -= 1
        why.append(f"분석 게시물 {posts}개")
    if "LIKES_HIDDEN" in flags:
        level -= 1
        why.append("좋아요 비공개 게시물 포함")

    level = max(0, min(3, level))
    if level == 3:
        why.append("API 수집, 표본 충분")
    return {"level": level, "label": CONFIDENCE_LABELS[level], "why": why}


def sort_flags(flags: list[str]) -> list[dict]:
    """Severe first — the thing that kills a deal should be read first."""
    return sorted(
        ({"name": f,
          "label": FLAG_LABELS.get(f, f),
          "severity": FLAG_SEVERITY.get(f, "caution")} for f in flags),
        key=lambda f: SEVERITY_ORDER[f["severity"]],
    )


SCENARIO_HUE = {"무상 시딩": "seed", "유료 협찬": "paid", "매크로 1건": "aff"}

SORT_KEYS = {
    "score": lambda r: r["score"] or 0,
    "followers": lambda r: r["followers"] or 0,
    "er": lambda r: r["metrics"].get("engagement_rate", 0),
    "cost": lambda r: r["est_cost"] or 0,
}


def decorate(row: dict) -> dict:
    """Add the display fields every roster view needs."""
    row["verdict"] = verdict_key(row.get("action"))
    row["verdict_label"] = verdict_label(row.get("action"))
    row["action_detail"] = action_detail(row.get("action"))
    row["tier"], row["er_floor"] = tier_for(row.get("followers") or 0)
    engagement = row.get("metrics", {}).get("median_engagement", 0)
    cost = row.get("est_cost") or 0
    row["cpe"] = cost / engagement if engagement else 0.0
    row["measured_cpm"] = row.get("breakdown", {}).get("measured_cpm_usd")
    row["flag_list"] = sort_flags(row.get("flags") or [])
    row["confidence"] = confidence(row)
    return row


def verdict_counts(rows: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key, _ in VERDICTS}
    for r in rows:
        counts[r["verdict"]] += 1
    return counts


def sort_rows(rows: list[dict], sort: str, direction: str) -> list[dict]:
    key = SORT_KEYS.get(sort, SORT_KEYS["score"])
    return sorted(rows, key=key, reverse=direction != "asc")
