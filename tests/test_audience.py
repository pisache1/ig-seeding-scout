"""Fixtures here mirror the shapes a real Meta export actually contains.

Three of them were discovered only by opening a genuine download:

  - followers_1.json is a bare object, not a list of objects
  - following.json omits `value` entirely and carries the username in the
    parent item's `title`, with the profile URL in `href`
  - labels are localised *and* double-encoded, so "사용자 이름" arrives as
    "ì‚¬ìš©ìž ì´ë¦„"
"""

import json

import pytest

from app.audience import (AudienceError, demojibake, detect_kind, load_export)
from app.db import (add_audience_import, connect, diff_imports, import_kinds,
                    latest_import, previous_import, record_screened, unscreened)


def followers_file(handles):
    """Real shape: one object, string_list_data carries `value`."""
    return {
        "title": "",
        "media_list_data": [],
        "string_list_data": [
            {"href": f"https://www.instagram.com/{h}", "value": h,
             "timestamp": 1700000000 + i}
            for i, h in enumerate(handles)
        ],
    }


def following_file(handles):
    """Real shape: username in `title`; entries have href+timestamp, no value."""
    return {
        "relationships_following": [
            {"title": h,
             "string_list_data": [
                 {"href": f"https://www.instagram.com/{h}",
                  "timestamp": 1700000000 + i}]}
            for i, h in enumerate(handles)
        ]
    }


def label_values_file(handles):
    """Real shape used by follow-request and unfollowed files, with the
    double-encoded Korean label Instagram actually writes."""
    mangled = "사용자 이름".encode("utf-8").decode("latin-1")
    return [
        {"timestamp": 1700000000 + i, "media": [],
         "label_values": [
             {"label": "URL", "value": ""},
             {"label": mangled, "value": h},
         ],
         "fbid": str(i)}
        for i, h in enumerate(handles)
    ]


def write(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# --- parsing ---------------------------------------------------------------

def test_followers_file(tmp_path):
    members = load_export(write(tmp_path, "followers_1.json",
                                followers_file(["a_one", "b.two"])))
    assert [m["handle"] for m in members] == ["a_one", "b.two"]
    assert members[0]["followed_at"] == 1700000000
    assert members[0]["kind"] == "followers"


def test_following_file_reads_the_title_when_value_is_absent(tmp_path):
    """The bug that silently dropped 987 accounts."""
    members = load_export(write(tmp_path, "following.json",
                                following_file(["c_three", "d.four"])))
    assert [m["handle"] for m in members] == ["c_three", "d.four"]
    assert members[0]["kind"] == "following"


def test_handle_recovered_from_href_when_value_and_title_are_both_missing(tmp_path):
    payload = {"relationships_following": [
        {"string_list_data": [
            {"href": "https://www.instagram.com/only_in_href/", "timestamp": 1}]}]}
    members = load_export(write(tmp_path, "following.json", payload))
    assert [m["handle"] for m in members] == ["only_in_href"]


def test_label_values_schema_with_mangled_korean_label(tmp_path):
    members = load_export(write(tmp_path, "recently_unfollowed_profiles.json",
                                label_values_file(["gone_one", "gone.two"])))
    assert [m["handle"] for m in members] == ["gone_one", "gone.two"]
    assert members[0]["kind"] == "unfollowed"


def test_demojibake_restores_korean():
    assert demojibake("사용자 이름".encode("utf-8").decode("latin-1")) == "사용자 이름"
    assert demojibake("Username") == "Username"
    assert demojibake("이미 정상인 한글") == "이미 정상인 한글"


def test_url_label_is_never_taken_as_a_handle(tmp_path):
    payload = [{"timestamp": 1, "label_values": [
        {"label": "URL", "value": "instagram.com"},
        {"label": "사용자 이름".encode("utf-8").decode("latin-1"), "value": "real_one"}]}]
    members = load_export(write(tmp_path, "pending_follow_requests.json", payload))
    assert [m["handle"] for m in members] == ["real_one"]


def test_kind_detected_from_filename_when_payload_is_ambiguous():
    assert detect_kind([], "recently_unfollowed_profiles.json") == "unfollowed"
    assert detect_kind([], "followers_1.json") == "followers"
    assert detect_kind({"relationships_following": []}, "whatever.json") == "following"


def test_plain_text_list(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("@Alpha\n# comment\nbeta\n\nBETA\n", encoding="utf-8")
    assert [m["handle"] for m in load_export(p)] == ["alpha", "beta"]


def test_unparseable_json_is_reported(tmp_path):
    with pytest.raises(AudienceError, match="계정 목록을 찾지 못했습니다"):
        load_export(write(tmp_path, "followers_1.json", {"nothing": "here"}))


def test_missing_file(tmp_path):
    with pytest.raises(AudienceError, match="파일이 없습니다"):
        load_export(tmp_path / "nope.json")


def test_duplicate_handles_collapse(tmp_path):
    p = write(tmp_path, "followers_1.json", followers_file(["x", "X", "x"]))
    assert len(load_export(p)) == 1


# --- storage and diffing ---------------------------------------------------

def test_diff_between_two_imports(tmp_path):
    db = str(tmp_path / "t.db")
    with connect(db) as conn:
        first = add_audience_import(conn, [{"handle": h} for h in "abc"], "t1", "followers")
    with connect(db) as conn:
        second = add_audience_import(conn, [{"handle": h} for h in "bcd"], "t2", "followers")
        assert previous_import(conn, second) == first
        delta = diff_imports(conn, second, first)
    assert delta["gained"] == ["d"] and delta["lost"] == ["a"]
    assert delta["retained"] == 2 and delta["total"] == 3


def test_a_following_import_never_diffs_against_a_followers_import(tmp_path):
    """Otherwise every account shows up as both gained and lost."""
    db = str(tmp_path / "t.db")
    with connect(db) as conn:
        add_audience_import(conn, [{"handle": h} for h in "abc"], "f.json", "followers")
        following = add_audience_import(conn, [{"handle": h} for h in "xyz"],
                                        "g.json", "following")
        assert previous_import(conn, following) is None


def test_latest_import_can_be_scoped_by_kind(tmp_path):
    db = str(tmp_path / "t.db")
    with connect(db) as conn:
        add_audience_import(conn, [{"handle": "a"}], "f.json", "followers")
        add_audience_import(conn, [{"handle": h} for h in "xyz"], "g.json", "following")
        assert latest_import(conn)["kind"] == "following"
        assert latest_import(conn, "followers")["member_count"] == 1
        assert {k["kind"] for k in import_kinds(conn)} == {"followers", "following"}


def test_screening_is_not_repeated(tmp_path):
    db = str(tmp_path / "t.db")
    with connect(db) as conn:
        imp = add_audience_import(conn, [{"handle": h} for h in "abc"], "t", "followers")
        assert set(unscreened(conn, imp, 10)) == {"a", "b", "c"}
        record_screened(conn, "a", True, 12_000)
        record_screened(conn, "b", False, None, "not professional")
        assert unscreened(conn, imp, 10) == ["c"]
