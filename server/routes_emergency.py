"""The emergency card (#1126; emergency.py builds it, pdfdoc.py writes the PDF, the page draws the lock-screen image).

  GET  /api/profiles/{pid}/emergency          the card as JSON, with its parts as the card says them: the preview
  POST /api/profiles/{pid}/emergency/pdf      {"paper": "letter" | "a4"}: the printable card, a PDF download
  POST /api/profiles/{pid}/emergency/image    the card as JSON again, for the page to draw the lock-screen image from

Everyone a profile is shared with may make one, viewers included: it's the records they can already read and the About
fields they can already see, put in a file they carry. There is no address that opens it without signing in, and no
share link carries it. Each file made is logged on the profile's activity ("emergency": which file, never what's on
it). The PDF is made in memory and streamed, never kept (no-store, as every API answer).
"""
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from . import activity, db as dbm, emergency, profiles as prof
from .main import _client_ip, get_db, need, user

router = APIRouter()


def _card(db, s, pid):
    prof.sync_build(db, pid)
    r = db.execute("SELECT name FROM profiles WHERE id=?", (pid,)).fetchone()
    c = emergency.card(db, pid, s["id"], r["name"] if r else "")
    return {**c, "sections": [{"heading": h, "lines": lines} for h, lines in emergency.sections(c)],
            "short": emergency.short(c), "walletNote": emergency.WALLET_NOTE}


def _log(db, s, pid, request, what):
    with dbm.transaction(db):
        activity.log(db, "emergency", s, pid, what, _client_ip(request))


@router.get("/api/profiles/{pid}/emergency")
def preview(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return _card(db, s, pid)


class Paper(BaseModel):
    paper: Literal["letter", "a4"] = "letter"


@router.post("/api/profiles/{pid}/emergency/pdf")
def make_pdf(pid: int, body: Paper, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    c = _card(db, s, pid)
    pdf = emergency.render_pdf(c, body.paper)
    _log(db, s, pid, request, {"file": "pdf", "paper": body.paper})
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{emergency.filename(c, "pdf")}"',
        "Cache-Control": "no-store", "Pragma": "no-cache", "Content-Length": str(len(pdf))})


class Image(BaseModel):
    wallet: bool = False   # "I carry the printed card": the image says where the rest is


@router.post("/api/profiles/{pid}/emergency/image")
def make_image(pid: int, body: Image, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    c = _card(db, s, pid)
    _log(db, s, pid, request, {"file": "png", "wallet": body.wallet})
    return {**c, "filename": emergency.filename(c, "png")}
