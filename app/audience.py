"""Your own follower list, imported from Meta's official export.

Instagram's "Download Your Information" (Settings -> Accounts Center ->
Your information and permissions -> Download your information) produces
connections/followers_and_following/followers_1.json. That is your own data,
exported by the platform at your request.

Two things come out of it that nothing else in this tool can provide:

  1. Churn. Import periodically and each import is diffed against the last,
     so you see who arrived and who left between exports.
  2. A warm candidate pool. People already following you accept seeding at
     far higher rates than cold outreach.

The list itself holds usernames and a follow timestamp — no follower counts,
no engagement. Screening it for creators needs the API (or the paste path,
one at a time). Everything stays in the local SQLite file.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

class AudienceError(ValueError):
    pass


HANDLE_OK = re.compile(r"^[A-Za-z0-9._]{1,30}$")
HREF_HANDLE = re.compile(r"instagram\.com/([A-Za-z0-9._]{1,30})")

# Labels inside the export are localised, and Instagram writes them
# double-encoded (UTF-8 bytes read back as Latin-1), so "사용자 이름" arrives as
# "ì‚¬ìš©ìž ì´ë¦„". Decode before matching anything against them.
USERNAME_LABELS = {"username", "사용자 이름", "ユーザーネーム", "用户名",
                   "nombre de usuario", "nom d'utilisateur", "benutzername"}


def demojibake(text: str) -> str:
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _clean_handle(value: str | None) -> str | None:
    if not value:
        return None
    handle = str(value).strip().lstrip("@").rstrip("/").lower()
    return handle if HANDLE_OK.match(handle) else None


def _handle_from_entry(entry: dict, title: str | None) -> str | None:
    """Where the username lives differs between files in the same export.

    followers_1.json puts it in string_list_data[].value. following.json
    omits `value` entirely and carries it in the parent item's `title`, with
    the profile URL in `href`. Try all three rather than assuming one.
    """
    for candidate in (entry.get("value"), title):
        handle = _clean_handle(candidate)
        if handle:
            return handle
    match = HREF_HANDLE.search(str(entry.get("href") or ""))
    return _clean_handle(match.group(1)) if match else None


def _from_string_lists(node, out: list[dict], title: str | None = None) -> None:
    if isinstance(node, dict):
        here = node.get("title") or title
        entries = node.get("string_list_data")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    handle = _handle_from_entry(entry, here)
                    if handle:
                        out.append({"handle": handle,
                                    "followed_at": entry.get("timestamp")})
        for key, value in node.items():
            if key != "string_list_data":
                _from_string_lists(value, out, here)
    elif isinstance(node, list):
        for item in node:
            _from_string_lists(item, out, title)


def _from_label_values(node, out: list[dict]) -> None:
    """The follow-request and recently-unfollowed files use another schema:
    a flat list of {timestamp, label_values: [{label, value}]}."""
    if not isinstance(node, list):
        return
    for item in node:
        if not isinstance(item, dict):
            continue
        pairs = item.get("label_values")
        if not isinstance(pairs, list):
            continue
        labelled, fallback = None, None
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            label = demojibake(str(pair.get("label") or "")).strip().lower()
            handle = _clean_handle(pair.get("value"))
            if not handle:
                continue
            if label in USERNAME_LABELS:
                labelled = handle
            elif fallback is None and label != "url":
                fallback = handle
        handle = labelled or fallback
        if handle:
            out.append({"handle": handle, "followed_at": item.get("timestamp")})


KINDS = {
    "relationships_following": "following",
    "relationships_followers": "followers",
    "relationships_follow_requests_sent": "requests_sent",
    "relationships_follow_requests_received": "requests_received",
    "relationships_unfollowed_users": "unfollowed",
}


def detect_kind(data, filename: str) -> str:
    """Label the import so 987 accounts you follow are never shown as
    987 followers."""
    if isinstance(data, dict):
        for key, kind in KINDS.items():
            if key in data:
                return kind
    name = filename.lower()
    for needle, kind in (("recently_unfollowed", "unfollowed"),
                         ("pending_follow_requests", "requests_sent"),
                         ("follow_requests", "requests_received"),
                         ("following", "following"),
                         ("followers", "followers")):
        if needle in name:
            return kind
    return "followers"


def load_export(path: str | Path) -> list[dict]:
    """Read a DYI JSON export, or a plain .txt/.csv list of usernames."""
    p = Path(path)
    if not p.exists():
        raise AudienceError(f"파일이 없습니다: {p}")

    raw: list[dict] = []
    kind = "followers"
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AudienceError(f"JSON 파싱 실패: {exc}") from exc
        kind = detect_kind(data, p.name)
        _from_string_lists(data, raw)
        if not raw:
            _from_label_values(data, raw)
        if not raw:
            raise AudienceError(
                "계정 목록을 찾지 못했습니다. 내 정보 다운로드에서 받은 "
                "JSON 파일인지 확인하세요 (HTML 내보내기는 지원 안 함)."
            )
    elif p.suffix.lower() == ".csv":
        with open(p, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                value = next(
                    (v for k, v in row.items()
                     if k and k.strip().lower() in ("handle", "username", "account")),
                    None,
                )
                if value:
                    raw.append({"handle": value.strip().lstrip("@").lower(),
                                "followed_at": None})
    else:
        for line in p.read_text(encoding="utf-8").splitlines():
            value = line.strip().lstrip("@")
            if value and not value.startswith("#"):
                raw.append({"handle": value.lower(), "followed_at": None})

    seen: set[str] = set()
    members: list[dict] = []
    for entry in raw:
        handle = entry["handle"]
        if handle and handle not in seen:
            seen.add(handle)
            entry["kind"] = kind
            members.append(entry)
    if not members:
        raise AudienceError("유효한 핸들을 찾지 못했습니다.")
    return members
