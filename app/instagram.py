"""Instagram Graph API client.

Only three endpoints matter here:

  business_discovery  public metrics for any *professional* account
  oembed              permalink -> author username (needs oembed_read)
  messages            send a DM reply inside the 24h window

Personal accounts are invisible to business_discovery; that is a platform
restriction, not a bug. There is no official way to read someone else's
follower list, so this client does not pretend to offer one.
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"

MEDIA_FIELDS = (
    "id,caption,like_count,comments_count,timestamp,media_type,permalink"
)


class InstagramError(RuntimeError):
    pass


class InstagramClient:
    def __init__(self, ig_user_id: str, access_token: str, timeout: int = 20):
        self.ig_user_id = ig_user_id
        self.token = access_token
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, path: str, params: dict, retries: int = 3) -> dict:
        params = {**params, "access_token": self.token}
        url = f"{GRAPH}/{path.lstrip('/')}"
        for attempt in range(retries):
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            err = body.get("error", {})
            # 4 = app rate limit, 17 = user rate limit, 613 = throttled
            if err.get("code") in (4, 17, 613) and attempt < retries - 1:
                wait = 2 ** attempt * 15
                log.warning("rate limited, sleeping %ss", wait)
                time.sleep(wait)
                continue
            raise InstagramError(
                f"{resp.status_code} {err.get('code')}: {err.get('message', resp.text[:200])}"
            )
        raise InstagramError("exhausted retries")

    def business_discovery(self, username: str, media_limit: int = 25) -> dict:
        """Public profile + recent media for a professional account."""
        field = (
            f"business_discovery.username({username})"
            f"{{username,name,biography,website,followers_count,follows_count,"
            f"media_count,profile_picture_url,"
            f"media.limit({media_limit}){{{MEDIA_FIELDS}}}}}"
        )
        data = self._get(self.ig_user_id, {"fields": field})
        bd = data.get("business_discovery")
        if not bd:
            raise InstagramError(
                f"@{username}: 비즈니스/크리에이터 계정이 아니거나 존재하지 않음"
            )
        return bd

    def username_from_permalink(self, permalink: str) -> str | None:
        """Resolve instagram.com/p/<shortcode> -> author username via oEmbed.

        Requires the oembed_read permission on the app. Returns None rather
        than raising so the caller can fall back to asking for the handle.
        """
        try:
            data = self._get(
                "instagram_oembed",
                {"url": permalink, "fields": "author_name", "omitscript": "true"},
            )
            return data.get("author_name")
        except InstagramError as exc:
            log.info("oembed failed for %s: %s", permalink, exc)
            return None

    def send_dm(self, recipient_igsid: str, text: str) -> dict:
        """Reply to a DM. Only valid within 24h of the user's last message."""
        url = f"{GRAPH}/{self.ig_user_id}/messages"
        resp = self.session.post(
            url,
            json={"recipient": {"id": recipient_igsid}, "message": {"text": text[:950]}},
            params={"access_token": self.token},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise InstagramError(f"send_dm {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def my_follower_demographics(self) -> dict:
        """Aggregated demographics for *your own* account (100+ followers).

        This is the only follower-side data Meta exposes, and only for the
        account you authenticated as.
        """
        return self._get(
            f"{self.ig_user_id}/insights",
            {
                "metric": "follower_demographics",
                "period": "lifetime",
                "metric_type": "total_value",
                "breakdown": "age,gender",
            },
        )
