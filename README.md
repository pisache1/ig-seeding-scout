# IG Seeding Scout

DM a creator handle (or a post link) to your Instagram business account.
The webhook reads it, pulls that account's **public** performance data,
scores it for seeding fit, DMs the verdict back, and files it into a roster
you can run campaign economics on.

## Scope — read this first

This tool indexes **professional creator accounts** by public performance
metrics and content category. It does not profile people by appearance,
and it does not collect data on private individuals. Those are not
features that were left out — they are not what this is for.

## What the API actually gives you

| Data | Your own account | Someone else's |
|---|---|---|
| Followers, media count | ✅ | ✅ `business_discovery` |
| Per-post likes, comments, timestamp | ✅ | ✅ `business_discovery` |
| Follower demographics (aggregated age/gender/city) | ✅ `insights` | ❌ |
| Reach, impressions, saves, shares | ✅ `insights` | ❌ |
| Follower **list** | ⚠️ export only, not API | ❌ |
| Private or personal accounts | — | ❌ (use the CSV path below) |

There is no official endpoint for anyone's follower list, and scraping one
violates the platform terms and gets the account actioned. So every score
here is derived from three observable signals: **likes, comments,
timestamps**. Anything presented as reach is explicitly a modelled estimate.

Also note: `business_discovery` only sees Business/Creator accounts.
Personal accounts return nothing.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # then fill it in
```

## Running it somewhere other than your laptop

The tool was written to run locally, where the only person who can reach it
is you. On the internet that stops being true, so there is a password gate:
nothing is served without a session.

```bash
export SCOUT_PASSWORD='a long passphrase'
export SCOUT_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
./run.sh
```

With no `SCOUT_PASSWORD` set, requests from anywhere except the local
machine are **refused**, not served openly — deploying without configuring it
fails closed rather than publishing your roster. Set `SCOUT_HTTPS_ONLY=1`
behind TLS so the session cookie is not sent over plain HTTP. Failed logins
lock an IP out for a minute after five tries.

`/webhook` is exempt from the session check because Instagram cannot carry a
cookie; it is gated by the `X-Hub-Signature-256` check instead, which needs
`app_secret` configured.

### Deploying

`Dockerfile` and `render.yaml` are included, and the image runs anywhere
that sets `$PORT`.

**The disk matters more than the host.** The database is SQLite, so on a
platform with an ephemeral filesystem — which includes most free tiers —
every redeploy silently wipes the roster, the follower import history and
the record of which followers you already screened. Mount a volume and point
`SCOUT_DB_PATH` at it, as `render.yaml` does. GitHub Pages cannot host this
at all: it serves static files only, and this is a Python server.

What to set on the host:

| | |
|---|---|
| `SCOUT_PASSWORD` | the login password |
| `SCOUT_SECRET_KEY` | signs the session cookie; changing it logs everyone out |
| `SCOUT_HTTPS_ONLY` | `1` behind TLS |
| `SCOUT_DB_PATH` | a path on the mounted volume |
| `IG_ACCESS_TOKEN`, `IG_USER_ID` | only if you have a token |

## The site

```bash
./run.sh          # http://localhost:8000
```

Five pages, all server-rendered — no build step, no JS framework:

| | |
|---|---|
| `/` | 로스터 — composition bar, sortable table, verdict rail per row |
| `/creator/<handle>` | 점수 구성, 플래그 설명, 스냅샷 시계열 |
| `/economics` | 가정을 폼에서 바꾸면 세 시나리오가 다시 계산됩니다 |
| `/audience` | 팔로워 증감, 가져오기 기록 |
| `/intake` | 붙여넣기 · 미디어킷 CSV 업로드 · 팔로워 목록 업로드 |

### Design notes

**The verdict is the organising fact.** Every creator resolves to one of five
verdicts, so the interface is built around that rather than the score. Each
verdict gets a hue, used in exactly two places: the rail down the left of a
row, and the composition bar at the top of the roster. Sorted by score, like
verdicts cluster and their rails fuse into unbroken bands — the roster
stratifies before a number is read.

The ramp is teal / blue / amber / violet / rose rather than a traffic light,
because paying a creator is a different axis from excluding one, not a worse
grade — and five hues stay distinguishable for colourblind readers.

**The hue is kept off everything else.** Score-component bars are neutral
ink: on an excluded account, a component that scored full marks must not
render as alarm.

**Columns are grouped by kind of fact** — 평가 / 규모 / 성과 / 비용 / 결정 —
with a hairline between groups, and secondary metrics (댓글비, 주 게시, 최근,
CPE) set in dimmed ink so the eye lands on the score and the engagement rate
first.

**Flags are typed, not uniformly alarming.** `SELF_REPORTED` is information
about provenance; `LOW_ER_SUSPECT_FOLLOWERS` is a reason to walk away. They
render in three registers — severe, caution, info — and sort severe-first.

**Korean never takes the Latin small-caps treatment.** Monospace + uppercase
+ wide tracking is a Latin device. Hangul has no case, so `text-transform:
uppercase` does nothing, and a Latin mono stack falls back to a different
Korean face entirely — leaving only the wide tracking, which reads as a
mistake. So monospace is reserved for figures, handles, tier tags and flag
names; the tracked uppercase eyebrow is reserved for the Latin page-kind
label (`ROSTER`, `AUDIENCE`); everything containing Korean is Pretendard at
normal tracking. Tight tracking caps at −0.015em, past which Hangul syllable
blocks go cramped.

Seven sizes, each with exactly one job (`--t-display` through `--t-eyebrow`),
and nothing set off-scale. Pretendard is self-hosted in `app/static/fonts/`
rather than pulled from a CDN, so the tool renders the same offline.

**Confidence is separate from score.** A creator can look excellent and still
be a row you shouldn't bet money on, because the numbers are theirs. A
three-segment meter reads down from full for self-reported data, thin
samples, and hidden likes, with the reasons on the detail page.

## Try the models with no credentials at all

```bash
python3 -m app.cli demo        # loads a synthetic 15-account roster
python3 -m app.cli report
python3 -m app.cli economics --aov-usd 72 --gross-margin 0.55
./run.sh                       # dashboard at localhost:8000
```

The fixture roster includes three planted bad actors (bought followers,
abandoned account, like farm) so you can see the flags fire. Delete
`scout.db` before loading real data.

## Use it today, without Meta app review

The CLI needs only `instagram_basic` + a linked business account — no
messaging permissions, no review queue.

```bash
python -m app.cli scout glossier rhode drunkelephant
python -m app.cli scout --file handles.txt
python -m app.cli report
python -m app.cli economics --aov-usd 72 --gross-margin 0.55
python -m app.cli demographics          # your own followers, aggregated
```

## Your own follower list (Meta's official export)

Settings -> Accounts Center -> Your information and permissions -> Download
your information -> JSON. The file you want is
`connections/followers_and_following/followers_1.json`.

```bash
python3 -m app.cli followers followers_1.json     # import + diff vs last time
python3 -m app.cli screen --limit 100             # which of them are creators
```

This only ever reads *your own* account's list, exported by Meta at your
request. There is no equivalent for anyone else's followers — no API, and
nothing here will scrape one.

Import periodically and each import is diffed against the previous one, so
you get **gained / lost / net** plus the actual handles. The data stays in
the local SQLite file.

`screen` then runs each follower through `business_discovery`. A successful
lookup *is* the filter — only Business/Creator accounts return anything —
and it hands back the follower count at the same time. Results are cached in
`audience_screened`, so re-running picks up where it stopped; use `--limit`
to stay inside the rate limit and work through a large list over several
sessions.

Why bother: people already following you convert on seeding far better than
cold outreach. This is the warm half of the candidate pool, and the export
is the only legitimate way to enumerate it.

Also accepts a plain `.txt` (one handle per line) or a `.csv` with a
`handle`/`username` column, for lists you already keep elsewhere.

## Paste intake (no API, no account, no cost)

You browse Instagram yourself, copy the text off a profile and a few of its
posts, and paste it in. This does the typing-up, not the collecting.

```bash
python3 -m app.cli paste -o candidates.csv     # paste, then Ctrl-D
python3 -m app.cli ingest candidates.csv
```

Reads both the English and Korean interface, and their abbreviations
(`12.4K`, `1.2만`, `3천`). Paste a profile header for the follower count,
then two or three posts for engagement — repeated handles merge into one
row with the post figures averaged, and the note records how many posts the
average came from.

Reach, story views and rate stay blank: they are not on a public profile.
Ask the creator and fill those columns in by hand.

## Media-kit CSV intake (no API access at all)

`business_discovery` cannot see private or personal accounts, and for the
public ones it never returns reach, saves or story views. The creator can
see all of it in their own Insights — so ask them for a media kit and type
the numbers in.

```bash
python3 -m app.cli ingest mediakit.example.csv
```

Columns are matched loosely, so a media kit's own spelling usually works
(`username`/`account`/`핸들`, `likes`/`평균좋아요`, …). `12.4k`, `1.2m`,
`8,400` and `$150` all parse. Only `handle` and `followers` are required.

Two upgrades happen when the data allows it:

- **`avg_reach` replaces the reach model.** Every CPM elsewhere in this tool
  is `engagement × reach_multiplier`, i.e. a guess. A measured reach turns it
  into an actual CPM, reported as `measured_cpm_usd`.
- **`rate_usd` replaces the price estimate.** A quoted rate is a fact; our
  followers-based number is not.

Every ingested row is flagged `SELF_REPORTED` — a media kit is marketing
material. Reach outside 5-80% of follower count also trips
`REACH_IMPLAUSIBLE_LOW` / `REACH_IMPLAUSIBLE_HIGH`; in practice that catches
kits quoting impressions or video views as if they were reach.

## Scoring

100 points, then penalties.

| Component | Pts | Rationale |
|---|---|---|
| Engagement rate vs tier floor | 40 | Hitting the floor scores 70% of the band; 2× maxes it |
| Comment depth (comments ÷ likes) | 15 | 2% is a talkative audience; <0.5% is passive or bought |
| Consistency (median absolute deviation) | 15 | One viral post shouldn't flatter a dead account |
| Cadence | 7.5 | ~3 posts/week saturates |
| Recency | 7.5 | Decays to zero at `stale_days` |
| Brand fit (caption keyword overlap) | 15 | Set `brand_keywords` in config |

Tier ER floors — nano 4.0% / micro 2.5% / mid 1.5% / macro 1.2% / mega 1.0%.
These are industry rules of thumb, not measurements. Tune them in `config.json`
once you have your own category data.

**Penalties:** `LOW_ER_SUSPECT_FOLLOWERS` −15 (auto-exclude),
`SHALLOW_COMMENTS` −10, `STALE` −10, `THIN_DATA` −10,
`HASHTAG_SPAM` −5, `ERRATIC_ENGAGEMENT` −5.

Medians are used everywhere instead of means — one viral post should not
move the verdict.

**Verdicts:** 75+ seed first (nano/micro) or pay (mid+) · 60–74 test batch ·
45–59 affiliate code only, no cash up front · <45 exclude.

## Roster economics

`python -m app.cli economics` compares three ways to spend on the same pool:

- **Seeding** — free product to everyone who passes. Cost = COGS + shipping,
  discounted by the share who actually post (default 55%).
- **Paid** — the recommended rate to everyone at 60+. 100% posting.
- **Single macro** — the control: same niche, one big name.

Audience overlap is modelled as geometric decay (the 10th creator in one
niche reaches far fewer *new* people than the 1st), so unique reach is always
below the naive sum. Output gives unique reach, orders, ROAS, CPM, CAC and
break-even order count.

Every audience-side input (`reach_multiplier`, `overlap_decay`, `click_rate`,
`conversion_rate`) is a guess until your first wave lands. Replace them in
`config.json` with measured numbers as soon as you have any — the cost-side
inputs (COGS, AOV, margin) are the only ones that start out exact.

## Wiring up DM intake

1. Meta app → add **Instagram** → connect your Business/Creator account.
2. Permissions: `instagram_basic`, `instagram_manage_messages`,
   `pages_manage_metadata`, plus `oembed_read` if you want to send post links
   rather than handles. These need App Review before non-test users work.
3. Webhooks → subscribe to the `messages` field → callback
   `https://your-host/webhook`, verify token = your `verify_token`.
4. `./run.sh` and expose it (`ngrok http 8000` for development).
5. DM `@somehandle` to your business account from another account.

`app_secret` turns on `X-Hub-Signature-256` verification. It's optional so
local development works, and the app logs a warning when it's off — set it
in production.

Replies are bound by Meta's 24-hour messaging window. Duplicate webhook
deliveries are dropped on the `mid` unique index.

## Layout

```
app/scoring.py    metrics + 0-100 score      (pure stdlib, unit-tested)
app/portfolio.py  roster + campaign economics (pure stdlib, unit-tested)
app/parser.py     handle/shortcode extraction from DM text & attachments
app/instagram.py  Graph API client w/ rate-limit backoff
app/pipeline.py   handle -> metrics -> score -> snapshot
app/db.py         SQLite: requests, accounts, snapshots
app/main.py       web routes + DM webhook
app/web.py        presentation helpers        (unit-tested)
app/static/       one stylesheet, no build step
app/templates/    base + 5 pages
app/audience.py   your own follower export    (pure stdlib, unit-tested)
app/paste.py      copied-text -> CSV row      (pure stdlib, unit-tested)
app/ingest.py     media-kit CSV intake        (pure stdlib, unit-tested)
app/demo.py       synthetic roster fixtures
app/cli.py        demo / followers / screen / paste / ingest / scout /
                  report / economics / demographics
```

Snapshots are append-only, so re-running `scout` on the same handle builds
a time series rather than overwriting.

```bash
pytest -q
```


## Third-party

`app/static/fonts/PretendardVariable.woff2` — [Pretendard](https://github.com/orioncactus/pretendard)
by orioncactus, SIL Open Font License 1.1. Vendored rather than loaded from a
CDN so the interface renders identically offline.
