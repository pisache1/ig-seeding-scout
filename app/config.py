from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.json"


@dataclass
class Config:
    ig_user_id: str = ""
    access_token: str = ""
    app_secret: str = ""
    verify_token: str = ""
    db_path: str = str(ROOT / "scout.db")
    brand_keywords: list[str] = field(default_factory=list)
    media_limit: int = 25
    stale_days: int = 21
    max_hashtags: int = 20
    rate_per_k: dict = field(default_factory=dict)
    assumptions: dict = field(default_factory=dict)
    reply_to_dm: bool = True

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG) -> "Config":
        data: dict = {}
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
        # Environment wins over the file so secrets can stay out of git.
        for key in ("ig_user_id", "access_token", "app_secret", "verify_token"):
            env = os.getenv(f"IG_{key.upper()}")
            if env:
                data[key] = env
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
