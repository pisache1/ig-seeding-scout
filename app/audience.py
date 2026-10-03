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

HANDLE_OK = re.compile(r"^[A-Za-z0-9._]{1,30}$")


class AudienceError(ValueError):
    pass


def _walk_string_lists(node, out: list[dict]) -> None:
    """Meta nests the entries differently per export type and version, so
    collect every string_list_data wherever it appears."""
    if isinstance(node, dict):
        entries = node.get("string_list_data")
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and e.get("value"):
                    out.append(
                        {"handle": str(e["value"]).strip().lstrip("@").lower(),
                         "followed_at": e.get("timestamp")}
                    )
        for value in node.values():
            _walk_string_lists(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_string_lists(item, out)


def load_export(path: str | Path) -> list[dict]:
    """Read a DYI JSON export, or a plain .txt/.csv list of usernames."""
    p = Path(path)
    if not p.exists():
        raise AudienceError(f"파일이 없습니다: {p}")

    raw: list[dict] = []
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AudienceError(f"JSON 파싱 실패: {exc}") from exc
        _walk_string_lists(data, raw)
        if not raw:
            raise AudienceError(
                "string_list_data 를 찾지 못했습니다. "
                "followers_1.json 인지 확인하세요 (HTML 내보내기는 지원 안 함)."
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
        if handle and handle not in seen and HANDLE_OK.match(handle):
            seen.add(handle)
            members.append(entry)
    if not members:
        raise AudienceError("유효한 핸들을 찾지 못했습니다.")
    return members
