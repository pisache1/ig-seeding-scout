"""Paste parser.

You browse Instagram yourself, select a profile or post, copy the text, and
paste it here. This turns it into a scoreable CSV row.

The collection is manual — a person reading a page they already have access
to. This module only does the typing-up part, which is the boring half.

Handles both English and Korean interface text, and the abbreviations each
uses for large numbers (12.4K, 1.2만, 3천).
"""

from __future__ import annotations

import re

# 만 = 10,000 and 천 = 1,000 in the Korean interface.
SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "만": 10_000, "천": 1_000}
NUM = r"([\d][\d.,]*\s*[KkMmBb만천]?)"

PATTERNS: dict[str, list[str]] = {
    "followers": [rf"{NUM}\s*followers\b", rf"팔로워\s*{NUM}", rf"{NUM}\s*명의?\s*팔로워"],
    "following": [rf"{NUM}\s*following\b", rf"팔로우\s*{NUM}", rf"{NUM}\s*명\s*팔로잉"],
    "posts": [rf"{NUM}\s*posts?\b", rf"게시물\s*{NUM}"],
    "likes": [rf"{NUM}\s*likes\b", rf"좋아요\s*{NUM}\s*개", rf"liked by .*and {NUM} others"],
    "comments": [rf"[Vv]iew all {NUM} comments", rf"댓글\s*{NUM}\s*개", rf"{NUM}\s*comments\b"],
    "views": [rf"{NUM}\s*views\b", rf"조회\s*{NUM}", rf"{NUM}\s*회\s*재생"],
}

HANDLE_INLINE = re.compile(r"@([A-Za-z0-9._]{2,30})")
HANDLE_LINE = re.compile(r"^([a-z0-9][a-z0-9._]{1,29})$")
# Words that look like handles but are interface chrome.
CHROME = {
    "following", "followers", "message", "follow", "posts", "reels", "tagged",
    "instagram", "edit", "profile", "contact", "email", "subscribe",
    "팔로우", "팔로워", "메시지", "게시물", "릴스", "저장됨",
}


def to_number(raw: str | None) -> float | None:
    if not raw:
        return None
    text = raw.strip().replace(",", "").replace(" ", "")
    mult = 1
    if text and text[-1].lower() in SUFFIX:
        mult = SUFFIX[text[-1].lower()]
        text = text[:-1]
    elif text and text[-1] in SUFFIX:
        mult = SUFFIX[text[-1]]
        text = text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return None


def find_handle(text: str) -> str | None:
    m = HANDLE_INLINE.search(text)
    if m and m.group(1).lower() not in CHROME:
        return m.group(1).lower()
    for line in text.splitlines():
        stripped = line.strip()
        m = HANDLE_LINE.match(stripped)
        if m and m.group(1) not in CHROME and not stripped.isdigit():
            return m.group(1)
    return None


def parse(text: str) -> dict:
    """Extract whatever is present. Missing keys simply stay absent."""
    out: dict[str, float | str] = {}
    handle = find_handle(text)
    if handle:
        out["handle"] = handle
    for field, patterns in PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                value = to_number(m.group(1))
                if value is not None:
                    out[field] = value
                    break
    return out


def parse_many(text: str) -> list[dict]:
    """Split a paste into one record per profile.

    A copied profile often contains blank lines of its own, so blocks cannot
    be records directly. Instead, every block that names a handle opens a new
    record, and blocks without one belong to the record above them.
    """
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    groups: list[list[str]] = []
    for block in blocks:
        if find_handle(block) or not groups:
            groups.append([block])
        else:
            groups[-1].append(block)
    rows = [parse("\n".join(g)) for g in groups]
    return merge_by_handle([r for r in rows if r.get("handle")])


# Profile-level figures are the same on every paste of that creator; post-level
# ones differ per post and are what we want an average of.
PROFILE_FIELDS = ("followers", "following", "posts")
POST_FIELDS = ("likes", "comments", "views")


def merge_by_handle(rows: list[dict]) -> list[dict]:
    """Collapse repeated handles into one record.

    The normal workflow is to copy a creator's profile header for the
    follower count, then two or three of their posts for engagement. Those
    arrive as separate records of the same person; averaging the post-level
    numbers across them is exactly the figure the scorer wants.
    """
    merged: dict[str, dict] = {}
    samples: dict[str, dict[str, list[float]]] = {}

    for row in rows:
        handle = row["handle"]
        target = merged.setdefault(handle, {"handle": handle})
        bucket = samples.setdefault(handle, {})
        for field in PROFILE_FIELDS:
            if row.get(field) is not None:
                target[field] = max(target.get(field, 0), row[field])
        for field in POST_FIELDS:
            if row.get(field) is not None:
                bucket.setdefault(field, []).append(row[field])

    for handle, bucket in samples.items():
        for field, values in bucket.items():
            merged[handle][field] = round(sum(values) / len(values), 1)
        merged[handle]["samples"] = max(
            (len(v) for v in bucket.values()), default=0
        )
    return list(merged.values())


CSV_COLUMNS = [
    "handle", "followers", "avg_likes", "avg_comments", "avg_reach",
    "story_views", "posts_per_week", "days_since_last_post",
    "brand_fit", "rate_usd", "notes",
]


def to_csv_row(parsed: dict) -> dict:
    """Map parsed fields onto the ingest CSV schema.

    Reach, story views and rate are left blank on purpose — they are not
    visible on a public profile. Ask the creator for those.
    """
    return {
        "handle": parsed.get("handle", ""),
        "followers": int(parsed["followers"]) if parsed.get("followers") else "",
        "avg_likes": int(parsed["likes"]) if parsed.get("likes") else "",
        "avg_comments": int(parsed["comments"]) if parsed.get("comments") else "",
        "avg_reach": "",
        "story_views": "",
        "posts_per_week": "",
        "days_since_last_post": "",
        "brand_fit": "",
        "rate_usd": "",
        "notes": (
            f"수동 입력 (게시물 {parsed['samples']}개 평균)"
            if parsed.get("samples") else "수동 입력 (공개 프로필)"
        ),
    }
