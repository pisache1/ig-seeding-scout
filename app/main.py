"""Web app: roster, creator detail, intake, economics, audience.

Also hosts the Instagram DM webhook — you message a handle to your business
account and it comes back scored.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth

from .config import ROOT, Config
from .db import (audience_imports, add_audience_import, connect, diff_imports,
                 latest_import, latest_scores, previous_import, record_request,
                 save_snapshot, screening_counts, set_request_status,
                 snapshots_for, upsert_account)
from .instagram import InstagramClient, InstagramError
from .parser import extract_targets
from .pipeline import evaluate, format_dm_reply, resolve_shortcodes
from .portfolio import Assumptions, compare, roster_summary
from .scoring import VERDICTS, verdict_key, verdict_label
from .web import COMPONENTS, FLAG_NOTES, SCENARIO_HUE, decorate, sort_rows, verdict_counts

log = logging.getLogger(__name__)
cfg = Config.load()
app = FastAPI(title="Seeding Scout")
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))

ECONOMICS_INPUTS = [
    ("product_cogs_usd", "제품 원가 $", "0.5", "시딩 1건당 실제 원가"),
    ("shipping_usd", "배송비 $", "0.5", ""),
    ("aov_usd", "객단가 $", "1", "주문 1건 평균 매출"),
    ("gross_margin", "매출총이익률", "0.01", "0~1"),
    ("seeding_post_rate", "시딩 게재율", "0.05", "제품 받고 실제로 올리는 비율"),
    ("click_rate", "클릭률", "0.001", "도달 1명당 클릭 — 추정"),
    ("conversion_rate", "전환율", "0.001", "클릭 1건당 주문 — 추정"),
    ("reach_multiplier", "도달 배수", "1", "참여 1건당 도달 — 추정"),
    ("overlap_decay", "오디언스 중복 감쇠", "0.01", "1에 가까울수록 중복 적음"),
]


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not auth.needs_auth(path):
        return await call_next(request)

    host = request.client.host if request.client else None
    if not auth.configured_password():
        # Fail closed: an unconfigured deployment must not serve the roster
        # or the follower list to the internet.
        if not auth.is_local(host):
            return Response(
                status_code=503,
                content="SCOUT_PASSWORD 가 설정되지 않아 외부 접속을 막았습니다.",
                media_type="text/plain; charset=utf-8",
            )
        return await call_next(request)

    if request.session.get("ok"):
        return await call_next(request)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None) -> HTMLResponse:
    if request.session.get("ok"):
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html", {"error": error})


@app.post("/login")
async def login(request: Request, password: str = Form("")) -> Response:
    ip = request.client.host if request.client else "?"
    wait = auth.locked_out(ip)
    if wait:
        return _render(request, "login.html",
                       {"error": f"시도가 너무 많습니다. {wait:.0f}초 후 다시 시도하세요."})
    if auth.password_matches(password):
        auth.clear_failures(ip)
        request.session["ok"] = True
        return RedirectResponse("/", status_code=303)
    auth.record_failure(ip)
    log.warning("로그인 실패 from %s", ip)
    return _render(request, "login.html", {"error": "비밀번호가 맞지 않습니다."})


@app.get("/logout")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def client() -> InstagramClient:
    return InstagramClient(cfg.ig_user_id, cfg.access_token)


def _rows() -> list[dict]:
    with connect(cfg.db_path) as conn:
        return [decorate(r) for r in latest_scores(conn)]


def _render(request: Request, name: str, ctx: dict) -> HTMLResponse:
    ctx.setdefault("signed_in", bool(request.session.get("ok")))
    return templates.TemplateResponse(request, name, ctx)


# --- roster -----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def roster(request: Request, sort: str = "score", dir: str = "desc",
                 notice: str | None = None, notice_kind: str = "",
                 notice_detail: str | None = None) -> HTMLResponse:
    rows = sort_rows(_rows(), sort, dir)
    from .cli import _scores_from_db

    return _render(request, "roster.html", {
        "rows": rows,
        "counts": verdict_counts(rows),
        "verdicts": VERDICTS,
        "summary": roster_summary(_scores_from_db(cfg)),
        "sort": sort, "dir": dir,
        "notice": notice, "notice_kind": notice_kind,
        "notice_detail": notice_detail,
    })


@app.get("/creator/{handle}", response_class=HTMLResponse)
async def creator(request: Request, handle: str) -> HTMLResponse:
    with connect(cfg.db_path) as conn:
        history = snapshots_for(conn, handle)
    if not history:
        return _render(request, "roster.html", {
            "rows": [], "counts": verdict_counts([]), "verdicts": VERDICTS,
            "summary": roster_summary([]), "sort": "score", "dir": "desc",
            "notice": f"@{handle} 기록이 없습니다.", "notice_kind": "bad",
        })

    row = decorate(dict(history[0]))
    row["measured_reach"] = history[0].get("measured_reach") or 0
    row["quoted_rate"] = history[0].get("quoted_rate") or 0
    row["data_source"] = history[0].get("data_source") or "api"

    for h in history:
        h["verdict"] = verdict_key(h.get("action"))
        h["verdict_label"] = verdict_label(h.get("action"))
        h["er"] = h["metrics"].get("engagement_rate", 0)
        h["data_source"] = h.get("data_source") or "api"

    earned = sum(row["breakdown"].get(k, 0) for k, _, _ in COMPONENTS)
    return _render(request, "creator.html", {
        "row": row, "history": history, "components": COMPONENTS,
        "flag_notes": FLAG_NOTES, "penalty": max(0.0, earned - row["score"]),
    })


# --- economics --------------------------------------------------------------

@app.get("/economics", response_class=HTMLResponse)
async def economics(request: Request) -> HTMLResponse:
    from .cli import _scores_from_db

    scores = _scores_from_db(cfg)
    base = {**asdict(Assumptions()), **(cfg.assumptions or {})}
    for name, *_ in ECONOMICS_INPUTS:
        raw = request.query_params.get(name)
        if raw:
            try:
                base[name] = float(raw)
            except ValueError:
                pass
    a = Assumptions(**base)

    scenarios = []
    for r in compare(scores, a):
        d = vars(r).copy()
        d["hue"] = SCENARIO_HUE.get(r.name, "paid")
        scenarios.append(d)

    return _render(request, "economics.html", {
        "a": asdict(a), "inputs": ECONOMICS_INPUTS, "scenarios": scenarios,
        "roster_size": len(scores),
        "measured": sum(s.measured_reach > 0 for s in scores),
    })


# --- audience ---------------------------------------------------------------

@app.get("/audience", response_class=HTMLResponse)
async def audience(request: Request) -> HTMLResponse:
    with connect(cfg.db_path) as conn:
        imports = audience_imports(conn)
        current = latest_import(conn)
        delta = None
        if current:
            prev = previous_import(conn, current["id"])
            if prev:
                delta = diff_imports(conn, current["id"], prev)
        screened, professional = screening_counts(conn)

    churn = 0.0
    if delta and (delta["retained"] + len(delta["lost"])):
        churn = len(delta["lost"]) / (delta["retained"] + len(delta["lost"])) * 100

    return _render(request, "audience.html", {
        "imports": imports, "current": current, "delta": delta,
        "churn_rate": churn, "screened": screened, "professional": professional,
    })


# --- intake -----------------------------------------------------------------

@app.get("/intake", response_class=HTMLResponse)
async def intake(request: Request) -> HTMLResponse:
    return _render(request, "intake.html", {})


def _store(pairs, source_note: str) -> None:
    """pairs: (Metrics, Score) — both, so cadence and recency survive."""
    from .ingest import profile_from_score

    with connect(cfg.db_path) as conn:
        for metrics, score in pairs:
            upsert_account(conn, profile_from_score(score, source_note))
            save_snapshot(conn, score.handle, metrics, score)


@app.post("/intake/paste", response_class=HTMLResponse)
async def intake_paste(request: Request, text: str = Form("")) -> HTMLResponse:
    from .ingest import score_row_with_metrics
    from .paste import parse_many, to_csv_row

    parsed = parse_many(text)
    if not parsed:
        return await roster(
            request, notice="핸들을 찾지 못했습니다.", notice_kind="bad",
            notice_detail="프로필 상단(사용자명 + 게시물/팔로워 줄)을 포함해 복사하세요.")

    pairs, skipped = [], []
    for p in parsed:
        row = to_csv_row(p)
        if not row["followers"]:
            skipped.append(row["handle"])
            continue
        pairs.append(score_row_with_metrics(row, cfg))
    _store(pairs, "붙여넣기 입력")
    scores = [s for _, s in pairs]

    detail = f"팔로워 수가 없어 건너뜀: {', '.join(skipped)}" if skipped else None
    return await roster(
        request, notice=f"{len(scores)}개 계정을 평가했습니다.",
        notice_kind="good" if scores else "bad", notice_detail=detail)


@app.post("/intake/mediakit", response_class=HTMLResponse)
async def intake_mediakit(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
    from .ingest import IngestError, load_csv

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        pairs, errors = load_csv(path, cfg, with_metrics=True)
    except (IngestError, OSError) as exc:
        return await roster(request, notice="CSV 를 읽지 못했습니다.",
                            notice_kind="bad", notice_detail=str(exc))
    finally:
        Path(path).unlink(missing_ok=True)

    _store(pairs, f"미디어킷 {file.filename}")
    scores = [s for _, s in pairs]
    detail = "\n".join(f"{n}행: {e}" for n, e in errors) or None
    return await roster(request, notice=f"{len(scores)}건 적재, {len(errors)}건 실패.",
                        notice_kind="good" if scores else "bad", notice_detail=detail)


@app.post("/intake/followers", response_class=HTMLResponse)
async def intake_followers(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
    from .audience import AudienceError, load_export

    suffix = Path(file.filename or "x.json").suffix or ".json"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        members = load_export(path)
    except AudienceError as exc:
        return await audience_error(request, str(exc))
    finally:
        Path(path).unlink(missing_ok=True)

    with connect(cfg.db_path) as conn:
        add_audience_import(conn, members, file.filename or "upload")
    return await audience(request)


async def audience_error(request: Request, message: str) -> HTMLResponse:
    return _render(request, "intake.html",
                   {"notice": "목록을 읽지 못했습니다.", "notice_kind": "bad",
                    "notice_detail": message})


# --- Instagram DM webhook ---------------------------------------------------

def verify_signature(body: bytes, header: str | None) -> bool:
    if not cfg.app_secret:
        log.warning("app_secret 미설정 — 서명 검증 건너뜀 (개발 전용)")
        return True
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(cfg.app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.split("=", 1)[1])


@app.get("/webhook")
async def verify(request: Request) -> Response:
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == cfg.verify_token:
        return Response(content=q.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403, content="verification failed")


@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks) -> JSONResponse:
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        return JSONResponse({"error": "bad signature"}, status_code=401)

    payload = await request.json()
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            message = event.get("message") or {}
            if message.get("is_echo"):
                continue
            mid = message.get("mid")
            sender = (event.get("sender") or {}).get("id", "")
            text = message.get("text", "")
            targets = extract_targets(text, message.get("attachments"))
            if not mid or not (targets["handles"] or targets["shortcodes"]):
                continue
            with connect(cfg.db_path) as conn:
                if not record_request(conn, mid, sender, text, targets):
                    continue
            background.add_task(process, mid, sender, targets)
    return JSONResponse({"status": "ok"})


def process(mid: str, sender: str, targets: dict) -> None:
    api = client()
    handles = list(targets["handles"])
    if targets["shortcodes"]:
        handles += resolve_shortcodes(api, targets["shortcodes"])

    replies, errors = [], []
    for handle in dict.fromkeys(handles):
        try:
            metrics, score = evaluate(api, cfg, handle)
            replies.append(format_dm_reply(score, metrics))
        except InstagramError as exc:
            errors.append(f"@{handle}: {exc}")
            log.warning("evaluate failed: %s", exc)

    with connect(cfg.db_path) as conn:
        set_request_status(conn, mid, "done" if replies else "failed",
                           "; ".join(errors) or None)

    if cfg.reply_to_dm and sender:
        body = "\n\n".join(replies) or "평가 실패:\n" + "\n".join(errors)
        try:
            api.send_dm(sender, body)
        except InstagramError as exc:
            log.warning("reply failed: %s", exc)


@app.get("/api/roster")
async def api_roster() -> JSONResponse:
    from .cli import _scores_from_db

    scores = _scores_from_db(cfg)
    return JSONResponse({
        "summary": roster_summary(scores),
        "scenarios": [vars(r) for r in compare(scores, Assumptions(**cfg.assumptions))],
    })


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "configured": bool(cfg.ig_user_id and cfg.access_token)}


# Registered last so it ends up outermost: Starlette runs the most recently
# added middleware first, and require_login below needs request.session to
# already be populated by the time it runs.
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.secret_key(),
    session_cookie="scout_session",
    same_site="lax",
    https_only=bool(os.getenv("SCOUT_HTTPS_ONLY")),
    max_age=60 * 60 * 24 * 14,
)
