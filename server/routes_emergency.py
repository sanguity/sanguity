"""The emergency card (#1126, #1162; ecard.py keeps it, emergency.py says it and writes the PDF, the page draws the
lock-screen image and the QR code).

  GET  /api/profiles/{pid}/emergency           the saved card, as it is (card) and as its files say it (view); for
                                               owners and editors, what the records suggest and how many are new
  POST /api/profiles/{pid}/emergency/preview   {card}: a card being edited, as its files would say it (nothing kept)
  PUT  /api/profiles/{pid}/emergency           {card}: saves it (owners and editors), the day and the suggestions
                                               there are now with it
  POST /api/profiles/{pid}/emergency/pdf       {paper: letter | a4, form: page | wallet, qr: its rows of "0" and "1"}:
                                               the saved card, printable, a PDF download
  POST /api/profiles/{pid}/emergency/image     {wallet}: the saved card's view again, for the lock-screen image

Everyone a profile is shared with may make its files, viewers included; owners and editors change what's on it. There is
no address that opens it without signing in, and no share link carries it. Each file made and each save is logged on
the profile's activity ("emergency": which file, or "saved", never what's on it). The PDF is made in memory and
streamed, never kept (no-store, as every API answer).
"""
import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import activity, db as dbm, ecard, emergency, profiles as prof, settings, summary
from .main import _client_ip, get_db, need, user

router = APIRouter()
KEY = "emergency.card"


def _stored(db, pid):
    return ecard.check(settings.stored(db, "profile", pid).get(KEY) or {})


def _name(db, pid):
    r = db.execute("SELECT name FROM profiles WHERE id=?", (pid,)).fetchone()
    return r["name"] if r else ""


def _view(db, s, pid, card):
    return emergency.view(card, _name(db, pid), summary.date_format(db, s["id"]))


def _log(db, s, pid, request, what):
    with dbm.transaction(db):
        activity.log(db, "emergency", s, pid, what, _client_ip(request))


def _clean(card):
    try:
        return ecard.check(card)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/profiles/{pid}/emergency")
def get_card(pid: int, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    card = _stored(db, pid)
    out = {"card": card, "view": _view(db, s, pid, card), "canEdit": role in ("owner", "editor"),
           "sections": [{"id": i, "heading": h, "nouns": list(n)} for i, h, _, _, n in ecard.SECTIONS],
           "details": [{"id": k, "label": label} for k, label in ecard.DETAILS], "walletNote": emergency.WALLET_NOTE}
    if out["canEdit"]:
        prof.sync_build(db, pid)
        out["suggestions"], out["gone"] = emergency.offered(card, emergency.suggestions(db, pid, s["id"]))
        out["newSince"] = sum(x["new"] for x in out["suggestions"])
    return out


class Card(BaseModel):
    card: dict


@router.post("/api/profiles/{pid}/emergency/preview")
def preview(pid: int, body: Card, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    card = _clean(body.card)
    card["saved"] = _stored(db, pid)["saved"]
    return _view(db, s, pid, card)


@router.put("/api/profiles/{pid}/emergency")
def save(pid: int, body: Card, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    prof.sync_build(db, pid)
    card = _clean(body.card)
    card["seen"] = [x["key"] for x in emergency.suggestions(db, pid, s["id"])]
    card["saved"] = summary.today(db, s["id"]).isoformat()
    with dbm.transaction(db):
        settings.put(db, KEY, card, "profile", pid, by=s["id"])
    _log(db, s, pid, request, {"saved": True})
    return get_card(pid, s, db)


class Pdf(BaseModel):
    paper: Literal["letter", "a4"] = "letter"
    form: Literal["page", "wallet"] = "page"
    qr: Optional[List[str]] = None   # the QR code of the view's qr text, made on the page (www/js/qr.js)


def _modules(rows):
    n = len(rows or [])
    if rows is None:
        return None
    if not (21 <= n <= 177 and (n - 17) % 4 == 0 and all(isinstance(r, str) and len(r) == n and re.fullmatch("[01]+", r) for r in rows)):
        raise HTTPException(400, "That isn't a QR code.")
    return rows


@router.post("/api/profiles/{pid}/emergency/pdf")
def make_pdf(pid: int, body: Pdf, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    v = _view(db, s, pid, _stored(db, pid))
    pdf = emergency.render_pdf(v, body.paper, body.form, _modules(body.qr))
    _log(db, s, pid, request, {"file": "pdf", "paper": body.paper, "form": body.form})
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{emergency.filename(v, "pdf", body.form)}"',
        "Cache-Control": "no-store", "Pragma": "no-cache", "Content-Length": str(len(pdf))})


class Image(BaseModel):
    wallet: bool = False   # "I carry the printed card": the image says where the rest is


@router.post("/api/profiles/{pid}/emergency/image")
def make_image(pid: int, body: Image, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    v = _view(db, s, pid, _stored(db, pid))
    _log(db, s, pid, request, {"file": "png", "wallet": body.wallet})
    return {**v, "filename": emergency.filename(v, "png")}
