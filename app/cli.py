"""Command line entry point.

    python -m app.cli demo                       합성 데이터로 모델 체험
    python -m app.cli scout nike adidas          평가 + 저장 (토큰 필요)
    python -m app.cli ingest mediakit.csv        미디어킷 CSV 수동 입력
    python -m app.cli paste -o candidates.csv    복사한 프로필 텍스트 -> CSV 행
    python -m app.cli followers followers_1.json 내 팔로워 목록 가져오기 + 증감
    python -m app.cli screen --limit 100         내 팔로워 중 크리에이터 선별
    python -m app.cli scout --file handles.txt
    python -m app.cli report                     저장된 로스터 랭킹
    python -m app.cli economics                  시나리오 비교
    python -m app.cli demographics               내 계정 팔로워 인구통계

Works without any DM wiring, so it is usable before Meta app review.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from .config import Config
from .db import (add_audience_import, connect, diff_imports, latest_import,
                 latest_scores, previous_import, record_screened,
                 save_snapshot, unscreened, upsert_account)
from .instagram import InstagramClient, InstagramError
from .pipeline import evaluate_many
from .portfolio import Assumptions, compare, roster_summary
from .scoring import Metrics, Score


def _client(cfg: Config) -> InstagramClient:
    if not cfg.ig_user_id or not cfg.access_token:
        sys.exit("config.json 또는 환경변수에 ig_user_id / access_token 이 필요합니다.")
    return InstagramClient(cfg.ig_user_id, cfg.access_token)


def _scores_from_db(cfg: Config) -> list[Score]:
    with connect(cfg.db_path) as conn:
        rows = latest_scores(conn)
    out = []
    for r in rows:
        s = Score(
            handle=r["handle"],
            followers=r["followers"] or 0,
            tier="",
            er_floor=0.0,
            total=r["score"] or 0,
            breakdown=r["breakdown"],
            flags=r["flags"],
            action=r["action"] or "",
            est_post_cost_usd=r["est_cost"] or 0.0,
        )
        s.measured_reach = r.get("measured_reach") or 0.0
        s.quoted_rate_usd = r.get("quoted_rate") or 0.0
        s.data_source = r.get("data_source") or "api"
        m = r["metrics"]
        s.est_engagement_per_post = m.get("median_engagement", 0.0)
        s.tier = m.get("tier") or _tier_name(s.followers)
        out.append(s)
    return out


def _tier_name(followers: int) -> str:
    from .scoring import tier_for

    return tier_for(followers)[0]


def cmd_scout(args, cfg: Config) -> None:
    handles = list(args.handles)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            handles += [
                line.strip().lstrip("@")
                for line in fh
                if line.strip() and not line.startswith("#")
            ]
    if not handles:
        sys.exit("평가할 핸들을 지정하세요.")

    scores, failures = evaluate_many(_client(cfg), cfg, handles)
    for s in sorted(scores, key=lambda x: -x.total):
        print(f"{s.total:3d}  @{s.handle:<24} {s.tier:<6} "
              f"{s.followers:>10,}  ${s.est_post_cost_usd:>8,.0f}  {s.action}")
    for h, err in failures:
        print(f"  !   @{h}: {err}", file=sys.stderr)
    print(f"\n{len(scores)}건 평가 완료, {len(failures)}건 실패. DB: {cfg.db_path}")


EMPTY_DB = (
    "아직 평가한 계정이 없습니다.\n"
    "  인스타에서 바로 수집: python3 -m app.cli scout <handle> ...  (토큰 필요)\n"
    "  샘플로 둘러보기: python3 -m app.cli demo"
)


def cmd_demo(args, cfg: Config) -> None:
    """Load a synthetic roster into the DB so the models can be explored
    without Graph API credentials."""
    from .demo import build, profile_for

    rows = build(cfg.brand_keywords or None)
    with connect(cfg.db_path) as conn:
        for handle, metrics, score in rows:
            upsert_account(conn, profile_for(handle, score.followers))
            save_snapshot(conn, handle, metrics, score)

    print(f"샘플 크리에이터 {len(rows)}명을 {cfg.db_path} 에 적재했습니다. "
          "(샘플입니다 — 실제 계정이 아닙니다)\n")
    for _, _, s in sorted(rows, key=lambda r: -r[2].total):
        flags = f"  [{','.join(s.flags)}]" if s.flags else ""
        print(f"{s.total:3d}  @{s.handle:<20} {s.tier:<6} {s.followers:>9,}  "
              f"{s.action}{flags}")
    print("\n다음: python3 -m app.cli economics --aov-usd 72 --gross-margin 0.55")


def cmd_followers(args, cfg: Config) -> None:
    """Import your own follower list from Meta's export and diff it."""
    from .audience import AudienceError, load_export

    try:
        members = load_export(args.path)
    except AudienceError as exc:
        sys.exit(f"가져오기 실패: {exc}")

    with connect(cfg.db_path) as conn:
        import_id = add_audience_import(conn, members, str(args.path))
        prev = previous_import(conn, import_id)
        delta = diff_imports(conn, import_id, prev) if prev else None

    print(f"팔로워 {len(members):,}명 가져옴 (import #{import_id})")
    if not delta:
        print("첫 가져오기입니다. 다음에 다시 넣으면 증감이 나옵니다.")
    else:
        net = len(delta["gained"]) - len(delta["lost"])
        print(f"직전 대비: 신규 +{len(delta['gained']):,} / "
              f"이탈 -{len(delta['lost']):,} / 순증 {net:+,}")
        for label, key in (("신규", "gained"), ("이탈", "lost")):
            sample = delta[key][: args.show]
            if sample:
                more = len(delta[key]) - len(sample)
                tail = f" 외 {more:,}명" if more > 0 else ""
                print(f"  {label}: " + ", ".join("@" + h for h in sample) + tail)
    print("\n다음: python3 -m app.cli screen --limit 100   (토큰 필요)")


def cmd_screen(args, cfg: Config) -> None:
    """Find which of your followers are professional accounts worth seeding.

    business_discovery returns data only for Business/Creator accounts, so a
    successful lookup is itself the filter — and it hands back the follower
    count at the same time.
    """
    from .instagram import InstagramError

    api = _client(cfg)
    with connect(cfg.db_path) as conn:
        current = latest_import(conn)
        if not current:
            sys.exit("먼저 followers 명령으로 팔로워 목록을 가져오세요.")
        todo = unscreened(conn, current["id"], args.limit)

    if not todo:
        print("미확인 팔로워가 없습니다. --limit 을 늘리거나 새 목록을 가져오세요.")
        return

    found = 0
    for handle in todo:
        try:
            profile = api.business_discovery(handle, media_limit=0)
            followers = int(profile.get("followers_count") or 0)
            with connect(cfg.db_path) as conn:
                record_screened(conn, handle, True, followers)
            if followers >= args.min_followers:
                found += 1
                print(f"  @{handle:<28} {followers:>9,}")
        except InstagramError as exc:
            # Not professional, private, or gone — all the same to us here.
            with connect(cfg.db_path) as conn:
                record_screened(conn, handle, False, None, str(exc)[:120])

    print(f"\n{len(todo)}명 확인, 팔로워 {args.min_followers:,}+ 프로페셔널 계정 "
          f"{found}건 발견.")
    print("평가하려면: python3 -m app.cli scout <handle> ...")


def cmd_paste(args, cfg: Config) -> None:
    """Turn copied profile text into ingest-ready CSV rows."""
    import csv as _csv
    import os

    from .paste import CSV_COLUMNS, parse_many, to_csv_row

    print("프로필 텍스트를 붙여넣고 Ctrl-D (여러 개면 빈 줄로 구분):", file=sys.stderr)
    rows = [to_csv_row(p) for p in parse_many(sys.stdin.read())]
    if not rows:
        sys.exit("핸들을 찾지 못했습니다. 프로필 상단(사용자명 + 게시물/팔로워 줄)을 포함해 복사하세요.")

    missing = [r["handle"] for r in rows if not r["followers"]]
    for r in rows:
        print(f"  @{r['handle']:<24} 팔로워 {r['followers'] or '?':>9}  "
              f"좋아요 {r['avg_likes'] or '?':>7}  댓글 {r['avg_comments'] or '?':>5}")

    is_new = not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    with open(args.out, "a", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if is_new:
            w.writeheader()
        w.writerows(rows)

    print(f"\n{len(rows)}행을 {args.out} 에 추가했습니다.")
    if missing:
        print(f"팔로워 수가 비어 있어 ingest 에서 떨어질 행: {', '.join(missing)}",
              file=sys.stderr)
    print("도달/스토리/단가는 공개 프로필에 없습니다 — 크리에이터에게 직접 받아 "
          "CSV에 채운 뒤:")
    print(f"  python3 -m app.cli ingest {args.out}")


def cmd_ingest(args, cfg: Config) -> None:
    """Score creators from a media-kit CSV — no API access needed."""
    from .ingest import IngestError, load_csv, profile_from_score

    try:
        pairs, errors = load_csv(args.path, cfg, with_metrics=True)
    except (IngestError, OSError) as exc:
        sys.exit(f"CSV 읽기 실패: {exc}")

    scores = [s for _, s in pairs]
    with connect(cfg.db_path) as conn:
        for metrics, score in pairs:
            upsert_account(conn, profile_from_score(score))
            save_snapshot(conn, score.handle, metrics, score)

    for s in sorted(scores, key=lambda x: -x.total):
        cpm = s.breakdown.get("measured_cpm_usd")
        cpm_txt = f"  실측CPM ${cpm}" if cpm else "  (도달 미제공)"
        flags = f"  [{','.join(s.flags)}]" if s.flags else ""
        print(f"{s.total:3d}  @{s.handle:<22} {s.followers:>9,}{cpm_txt}  "
              f"{s.action}{flags}")
    for lineno, err in errors:
        print(f"  !   {args.path}:{lineno}  {err}", file=sys.stderr)
    print(f"\n{len(scores)}건 적재, {len(errors)}건 실패.")


def cmd_report(args, cfg: Config) -> None:
    scores = _scores_from_db(cfg)
    if not scores:
        sys.exit(EMPTY_DB)
    summary = roster_summary(scores)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print()
    for s in scores[: args.limit]:
        flags = f"  [{','.join(s.flags)}]" if s.flags else ""
        print(f"{s.total:3d}  @{s.handle:<24} {s.followers:>10,}  {s.action}{flags}")


def cmd_economics(args, cfg: Config) -> None:
    scores = _scores_from_db(cfg)
    if not scores:
        sys.exit(EMPTY_DB)
    a = Assumptions(**{**cfg.assumptions, **{
        k: v for k, v in vars(args).items()
        if k in Assumptions.__dataclass_fields__ and v is not None
    }})
    print("계산 전제:", json.dumps(asdict(a), indent=2, ensure_ascii=False))
    print()
    header = f"{'시나리오':<22}{'인원':>5}{'비용$':>11}{'도달':>12}{'주문':>8}{'매출$':>11}{'총이익$':>11}{'ROAS':>7}{'CPM$':>8}{'CAC$':>8}"
    print(header)
    print("-" * len(header))
    for r in compare(scores, a):
        print(f"{r.name:<22}{r.creators:>5}{r.cost_usd:>11,.0f}{r.unique_reach:>12,.0f}"
              f"{r.orders:>8,.1f}{r.revenue_usd:>11,.0f}{r.gross_profit_usd:>11,.0f}"
              f"{r.roas:>7.2f}{r.cpm_usd:>8.2f}{r.cac_usd:>8.2f}")
    print()
    for r in compare(scores, a):
        if r.notes:
            print(f"· {r.name}: " + " / ".join(r.notes))
        print(f"  주문 {r.breakeven_orders:,.1f}건이면 본전")


def cmd_demographics(args, cfg: Config) -> None:
    try:
        print(json.dumps(_client(cfg).my_follower_demographics(), indent=2, ensure_ascii=False))
    except InstagramError as exc:
        sys.exit(f"실패: {exc}\n(팔로워 100명 이상 + instagram_manage_insights 권한 필요)")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="ig-seeding-scout")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scout", help="핸들로 평가해서 저장 (토큰 필요)")
    sc.add_argument("handles", nargs="*")
    sc.add_argument("--file", help="한 줄에 하나씩 핸들이 담긴 파일")
    sc.set_defaults(func=cmd_scout)

    rp = sub.add_parser("report", help="평가한 크리에이터 순위")
    rp.add_argument("--limit", type=int, default=50)
    rp.set_defaults(func=cmd_report)

    ec = sub.add_parser("economics", help="무상 시딩 / 유료 협찬 / 매크로 1건 비교")
    for fname, ftype in (
        ("product_cogs_usd", float), ("shipping_usd", float), ("aov_usd", float),
        ("gross_margin", float), ("seeding_post_rate", float),
        ("click_rate", float), ("conversion_rate", float),
        ("reach_multiplier", float), ("overlap_decay", float),
    ):
        ec.add_argument(f"--{fname.replace('_', '-')}", dest=fname, type=ftype, default=None)
    ec.set_defaults(func=cmd_economics)

    fol = sub.add_parser("followers", help="내 팔로워 목록 가져오기 (Meta 공식 내보내기)")
    fol.add_argument("path", help="followers_1.json, 또는 한 줄에 하나씩 적힌 .txt/.csv")
    fol.add_argument("--show", type=int, default=15, help="증감 목록 표시 개수")
    fol.set_defaults(func=cmd_followers)

    scr = sub.add_parser("screen", help="내 팔로워 중 크리에이터 가려내기 (토큰 필요)")
    scr.add_argument("--limit", type=int, default=100, help="이번에 확인할 인원")
    scr.add_argument("--min-followers", dest="min_followers", type=int, default=1000)
    scr.set_defaults(func=cmd_screen)

    pst = sub.add_parser("paste", help="복사한 프로필 텍스트를 CSV 행으로 변환")
    pst.add_argument("-o", "--out", default="candidates.csv")
    pst.set_defaults(func=cmd_paste)

    ing = sub.add_parser("ingest", help="크리에이터에게 받은 수치를 CSV로 입력")
    ing.add_argument("path")
    ing.set_defaults(func=cmd_ingest)

    dmo = sub.add_parser("demo", help="샘플 데이터로 둘러보기 (토큰 불필요)")
    dmo.set_defaults(func=cmd_demo)

    dm = sub.add_parser("demographics", help="내 계정 팔로워 인구통계")
    dm.set_defaults(func=cmd_demographics)

    args = p.parse_args(argv)
    cfg = Config.load(args.config) if args.config else Config.load()
    args.func(args, cfg)


if __name__ == "__main__":
    main()
