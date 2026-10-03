from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mid         TEXT UNIQUE,
    sender_id   TEXT,
    raw_text    TEXT,
    targets     TEXT,
    received_at TEXT,
    status      TEXT DEFAULT 'pending',
    error       TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    handle       TEXT PRIMARY KEY,
    name         TEXT,
    biography    TEXT,
    website      TEXT,
    followers    INTEGER,
    media_count  INTEGER,
    profile_pic  TEXT,
    first_seen   TEXT,
    last_checked TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    handle     TEXT NOT NULL,
    taken_at   TEXT NOT NULL,
    followers  INTEGER,
    metrics    TEXT,
    score      INTEGER,
    breakdown  TEXT,
    flags      TEXT,
    action     TEXT,
    est_cost   REAL,
    measured_reach REAL DEFAULT 0,
    quoted_rate    REAL DEFAULT 0,
    data_source    TEXT DEFAULT 'api',
    FOREIGN KEY (handle) REFERENCES accounts(handle)
);

CREATE TABLE IF NOT EXISTS audience_imports (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at  TEXT NOT NULL,
    source    TEXT,
    member_count INTEGER,
    -- followers / following / requests_received / requests_sent / unfollowed.
    -- Diffs only ever compare imports of the same kind.
    kind      TEXT DEFAULT 'followers'
);

CREATE TABLE IF NOT EXISTS audience_members (
    import_id   INTEGER NOT NULL,
    handle      TEXT NOT NULL,
    followed_at INTEGER,
    PRIMARY KEY (import_id, handle),
    FOREIGN KEY (import_id) REFERENCES audience_imports(id)
);

CREATE TABLE IF NOT EXISTS audience_screened (
    handle      TEXT PRIMARY KEY,
    checked_at  TEXT,
    is_professional INTEGER,
    followers   INTEGER,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_members_handle ON audience_members(handle);
CREATE INDEX IF NOT EXISTS idx_snapshots_handle ON snapshots(handle, taken_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(snapshots)")}
    have_imports = {r["name"] for r in conn.execute("PRAGMA table_info(audience_imports)")}
    if "kind" not in have_imports:
        conn.execute("ALTER TABLE audience_imports ADD COLUMN kind TEXT DEFAULT 'followers'")
    for column, ddl in (
        ("measured_reach", "REAL DEFAULT 0"),
        ("quoted_rate", "REAL DEFAULT 0"),
        ("data_source", "TEXT DEFAULT 'api'"),
    ):
        if column not in have:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {column} {ddl}")


def record_request(conn, mid: str, sender_id: str, text: str, targets: dict) -> bool:
    """Returns False if this message id was already stored (webhook retry)."""
    try:
        conn.execute(
            "INSERT INTO requests (mid, sender_id, raw_text, targets, received_at)"
            " VALUES (?,?,?,?,?)",
            (mid, sender_id, text, json.dumps(targets, ensure_ascii=False), now_iso()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def set_request_status(conn, mid: str, status: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE requests SET status = ?, error = ? WHERE mid = ?", (status, error, mid)
    )


def upsert_account(conn, profile: dict) -> None:
    handle = profile["username"].lower()
    conn.execute(
        """
        INSERT INTO accounts (handle, name, biography, website, followers,
                              media_count, profile_pic, first_seen, last_checked)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(handle) DO UPDATE SET
            name=excluded.name, biography=excluded.biography,
            website=excluded.website, followers=excluded.followers,
            media_count=excluded.media_count, profile_pic=excluded.profile_pic,
            last_checked=excluded.last_checked
        """,
        (
            handle,
            profile.get("name"),
            profile.get("biography"),
            profile.get("website"),
            profile.get("followers_count"),
            profile.get("media_count"),
            profile.get("profile_picture_url"),
            now_iso(),
            now_iso(),
        ),
    )


def save_snapshot(conn, handle: str, metrics, score) -> None:
    from dataclasses import asdict

    conn.execute(
        """INSERT INTO snapshots (handle, taken_at, followers, metrics, score,
                                  breakdown, flags, action, est_cost,
                                  measured_reach, quoted_rate, data_source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            handle.lower(),
            now_iso(),
            score.followers,
            json.dumps(asdict(metrics)),
            score.total,
            json.dumps(score.breakdown),
            json.dumps(score.flags),
            score.action,
            score.est_post_cost_usd,
            getattr(score, "measured_reach", 0.0),
            getattr(score, "quoted_rate_usd", 0.0),
            getattr(score, "data_source", "api"),
        ),
    )


def latest_scores(conn, limit: int = 500) -> list[dict]:
    """Most recent snapshot per handle, best score first."""
    rows = conn.execute(
        """
        SELECT s.*, a.name, a.profile_pic, a.website
        FROM snapshots s
        JOIN (SELECT handle, MAX(taken_at) AS t FROM snapshots GROUP BY handle) m
          ON s.handle = m.handle AND s.taken_at = m.t
        LEFT JOIN accounts a ON a.handle = s.handle
        ORDER BY s.score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"] or "{}")
        d["breakdown"] = json.loads(d["breakdown"] or "{}")
        d["flags"] = json.loads(d["flags"] or "[]")
        out.append(d)
    return out


# --- audience (your own follower list) -------------------------------------

def add_audience_import(conn, members: list[dict], source: str,
                        kind: str = "followers") -> int:
    cur = conn.execute(
        "INSERT INTO audience_imports (taken_at, source, member_count, kind)"
        " VALUES (?,?,?,?)",
        (now_iso(), source, len(members), kind),
    )
    import_id = cur.lastrowid
    conn.executemany(
        "INSERT OR IGNORE INTO audience_members (import_id, handle, followed_at)"
        " VALUES (?,?,?)",
        [(import_id, m["handle"], m.get("followed_at")) for m in members],
    )
    return import_id


def previous_import(conn, before_id: int, kind: str | None = None) -> int | None:
    """The previous import *of the same kind*.

    Diffing a following list against a followers list would report every
    account as both gained and lost.
    """
    if kind is None:
        row = conn.execute("SELECT kind FROM audience_imports WHERE id = ?",
                           (before_id,)).fetchone()
        kind = row["kind"] if row else "followers"
    row = conn.execute(
        "SELECT id FROM audience_imports WHERE id < ? AND kind IS ?"
        " ORDER BY id DESC LIMIT 1",
        (before_id, kind),
    ).fetchone()
    return row["id"] if row else None


def _handles(conn, import_id: int) -> set[str]:
    return {
        r["handle"]
        for r in conn.execute(
            "SELECT handle FROM audience_members WHERE import_id = ?", (import_id,)
        )
    }


def diff_imports(conn, new_id: int, old_id: int) -> dict:
    new, old = _handles(conn, new_id), _handles(conn, old_id)
    return {
        "gained": sorted(new - old),
        "lost": sorted(old - new),
        "retained": len(new & old),
        "total": len(new),
    }


def unscreened(conn, import_id: int, limit: int) -> list[str]:
    rows = conn.execute(
        """SELECT m.handle FROM audience_members m
           LEFT JOIN audience_screened s ON s.handle = m.handle
           WHERE m.import_id = ? AND s.handle IS NULL
           LIMIT ?""",
        (import_id, limit),
    ).fetchall()
    return [r["handle"] for r in rows]


def record_screened(conn, handle: str, is_professional: bool,
                    followers: int | None, note: str = "") -> None:
    conn.execute(
        """INSERT INTO audience_screened (handle, checked_at, is_professional,
                                          followers, note)
           VALUES (?,?,?,?,?)
           ON CONFLICT(handle) DO UPDATE SET
             checked_at=excluded.checked_at,
             is_professional=excluded.is_professional,
             followers=excluded.followers, note=excluded.note""",
        (handle.lower(), now_iso(), int(is_professional), followers, note),
    )


def latest_import(conn, kind: str | None = None) -> dict | None:
    if kind:
        row = conn.execute(
            "SELECT * FROM audience_imports WHERE kind IS ? ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM audience_imports ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def import_kinds(conn) -> list[dict]:
    """Latest import per kind, so each list is summarised separately."""
    rows = conn.execute(
        """SELECT i.* FROM audience_imports i
           JOIN (SELECT kind, MAX(id) AS id FROM audience_imports GROUP BY kind) m
             ON i.id = m.id
           ORDER BY CASE i.kind WHEN 'followers' THEN 0 WHEN 'following' THEN 1
                                ELSE 2 END, i.kind"""
    ).fetchall()
    return [dict(r) for r in rows]


def snapshots_for(conn, handle: str, limit: int = 40) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE handle = ? ORDER BY taken_at DESC LIMIT ?",
        (handle.lower(), limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"] or "{}")
        d["breakdown"] = json.loads(d["breakdown"] or "{}")
        d["flags"] = json.loads(d["flags"] or "[]")
        out.append(d)
    return out


def audience_imports(conn, limit: int = 20) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM audience_imports ORDER BY id DESC LIMIT ?", (limit,)
        )
    ]


def screening_counts(conn) -> tuple[int, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS n, SUM(is_professional) AS pro FROM audience_screened"
    ).fetchone()
    return int(row["n"] or 0), int(row["pro"] or 0)
