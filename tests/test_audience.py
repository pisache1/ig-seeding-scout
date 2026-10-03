import json

import pytest

from app.audience import AudienceError, load_export
from app.db import (add_audience_import, connect, diff_imports, latest_import,
                    previous_import, record_screened, unscreened)


def dyi(handles):
    """Shape of Meta's connections/followers_and_following/followers_1.json."""
    return [
        {
            "title": "",
            "media_list_data": [],
            "string_list_data": [
                {"href": f"https://www.instagram.com/{h}",
                 "value": h, "timestamp": 1700000000 + i}
            ],
        }
        for i, h in enumerate(handles)
    ]


def write(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_reads_meta_export(tmp_path):
    members = load_export(write(tmp_path, "followers_1.json", dyi(["a_one", "b.two"])))
    assert [m["handle"] for m in members] == ["a_one", "b.two"]
    assert members[0]["followed_at"] == 1700000000


def test_reads_following_wrapper_shape(tmp_path):
    # following.json wraps the same entries under a key.
    payload = {"relationships_following": dyi(["c_three"])}
    members = load_export(write(tmp_path, "following.json", payload))
    assert [m["handle"] for m in members] == ["c_three"]


def test_plain_text_list(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("@Alpha\n# comment\nbeta\n\nBETA\n", encoding="utf-8")
    assert [m["handle"] for m in load_export(p)] == ["alpha", "beta"]


def test_rejects_html_export(tmp_path):
    with pytest.raises(AudienceError, match="string_list_data"):
        load_export(write(tmp_path, "followers_1.json", {"nothing": "here"}))


def test_missing_file(tmp_path):
    with pytest.raises(AudienceError, match="파일이 없습니다"):
        load_export(tmp_path / "nope.json")


def test_diff_between_two_imports(tmp_path):
    db = str(tmp_path / "t.db")
    with connect(db) as conn:
        first = add_audience_import(conn, [{"handle": h} for h in ("a", "b", "c")], "t1")
    with connect(db) as conn:
        second = add_audience_import(conn, [{"handle": h} for h in ("b", "c", "d")], "t2")
        assert previous_import(conn, second) == first
        delta = diff_imports(conn, second, first)
    assert delta["gained"] == ["d"]
    assert delta["lost"] == ["a"]
    assert delta["retained"] == 2 and delta["total"] == 3


def test_first_import_has_no_previous(tmp_path):
    db = str(tmp_path / "t.db")
    with connect(db) as conn:
        first = add_audience_import(conn, [{"handle": "a"}], "t1")
        assert previous_import(conn, first) is None


def test_screening_is_not_repeated(tmp_path):
    db = str(tmp_path / "t.db")
    with connect(db) as conn:
        imp = add_audience_import(conn, [{"handle": h} for h in ("a", "b", "c")], "t")
        assert set(unscreened(conn, imp, 10)) == {"a", "b", "c"}
        record_screened(conn, "a", True, 12_000)
        record_screened(conn, "b", False, None, "not professional")
        assert unscreened(conn, imp, 10) == ["c"]
        assert latest_import(conn)["member_count"] == 3


def test_duplicate_handles_collapse(tmp_path):
    p = tmp_path / "dupes.json"
    p.write_text(json.dumps(dyi(["x", "X", "x"])), encoding="utf-8")
    assert len(load_export(p)) == 1
