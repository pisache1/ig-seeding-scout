from app.paste import find_handle, parse, parse_many, to_csv_row, to_number

ENGLISH_PROFILE = """
glow.daily
1,234 posts   8,432 followers   512 following
Glow Daily
Skincare routines, honest reviews
"""

KOREAN_PROFILE = """
seoul.routine
게시물 892  팔로워 1.2만  팔로우 340
서울 루틴
"""

POST_VIEW = """
@barrier.repair
2,145 likes
View all 87 comments
"""


def test_number_suffixes():
    assert to_number("8,432") == 8432
    assert to_number("12.4K") == 12_400
    assert to_number("1.2m") == 1_200_000
    assert to_number("1.2만") == 12_000
    assert to_number("3천") == 3_000
    assert to_number("garbage") is None
    assert to_number(None) is None


def test_english_profile():
    p = parse(ENGLISH_PROFILE)
    assert p["handle"] == "glow.daily"
    assert p["followers"] == 8432
    assert p["following"] == 512
    assert p["posts"] == 1234


def test_korean_profile():
    p = parse(KOREAN_PROFILE)
    assert p["handle"] == "seoul.routine"
    assert p["followers"] == 12_000
    assert p["posts"] == 892


def test_post_view_gives_likes_and_comments():
    p = parse(POST_VIEW)
    assert p["handle"] == "barrier.repair"
    assert p["likes"] == 2145
    assert p["comments"] == 87


def test_interface_chrome_is_not_mistaken_for_a_handle():
    assert find_handle("following\nfollowers\nmessage") is None
    assert find_handle("팔로워\n메시지") is None


def test_missing_fields_are_simply_absent():
    p = parse("someone.handle\nno numbers here")
    assert p == {"handle": "someone.handle"}


def test_multiple_profiles_split_on_blank_lines():
    rows = parse_many(ENGLISH_PROFILE + "\n\n" + KOREAN_PROFILE)
    assert [r["handle"] for r in rows] == ["glow.daily", "seoul.routine"]


def test_single_profile_with_internal_blank_lines():
    rows = parse_many("glow.daily\n\n8,432 followers\n")
    assert len(rows) == 1 and rows[0]["followers"] == 8432


def test_csv_row_leaves_unobservable_fields_blank():
    row = to_csv_row(parse(ENGLISH_PROFILE))
    assert row["followers"] == 8432
    # Reach, story views and rate are not on a public profile.
    assert row["avg_reach"] == "" and row["story_views"] == "" and row["rate_usd"] == ""


def test_profile_and_its_posts_merge_into_one_row():
    pasted = """
glow.daily
1,234 posts   8,432 followers   512 following

glow.daily
400 likes
View all 20 comments

glow.daily
600 likes
View all 40 comments
"""
    rows = parse_many(pasted)
    assert len(rows) == 1
    row = rows[0]
    assert row["followers"] == 8432      # profile figure kept
    assert row["likes"] == 500.0         # post figures averaged
    assert row["comments"] == 30.0
    assert row["samples"] == 2
    assert "게시물 2개 평균" in to_csv_row(row)["notes"]


def test_distinct_handles_do_not_merge():
    rows = parse_many("a.one\n100 followers\n\nb.two\n200 followers\n")
    assert {r["handle"] for r in rows} == {"a.one", "b.two"}
