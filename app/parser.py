"""Pull creator handles out of whatever arrives in a DM.

People send three things: a bare @handle, a post/reel URL, or a native
Instagram share (which the webhook delivers as an attachment payload).
"""

from __future__ import annotations

import re

HANDLE_RE = re.compile(r"@([A-Za-z0-9._]{1,30})")
PROFILE_URL_RE = re.compile(
    r"instagram\.com/(?!p/|reel/|reels/|tv/|stories/|explore/)([A-Za-z0-9._]{1,30})"
)
POST_URL_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")

# Instagram usernames that are never a creator we want to score.
STOPWORDS = {"instagram", "explore", "accounts", "direct", "stories"}


def extract_targets(text: str | None, attachments: list[dict] | None = None) -> dict:
    """Return {'handles': [...], 'shortcodes': [...]} found in a message."""
    handles: list[str] = []
    shortcodes: list[str] = []

    def scan(blob: str) -> None:
        for m in POST_URL_RE.finditer(blob):
            shortcodes.append(m.group(1))
        for m in PROFILE_URL_RE.finditer(blob):
            handles.append(m.group(1))
        for m in HANDLE_RE.finditer(blob):
            handles.append(m.group(1))

    if text:
        scan(text)

    for att in attachments or []:
        payload = att.get("payload") or {}
        for key in ("url", "title", "permalink"):
            value = payload.get(key)
            if isinstance(value, str):
                scan(value)

    def dedupe(seq: list[str], fold_case: bool) -> list[str]:
        """Usernames are case-insensitive; shortcodes are NOT — lowercasing
        a shortcode turns a valid permalink into a dead one."""
        seen, out = set(), []
        for item in seq:
            value = item.strip(".")
            key = value.lower()
            if not value or key in seen or key in STOPWORDS:
                continue
            seen.add(key)
            out.append(key if fold_case else value)
        return out

    return {
        "handles": dedupe(handles, fold_case=True),
        "shortcodes": dedupe(shortcodes, fold_case=False),
    }
