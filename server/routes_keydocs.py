"""Key documents (#1135, keydocs.py): a document marked as an advance directive, a power of attorney, a POLST and the
like, in its drawer. Owners and editors only; everyone the profile is shared with sees the marks in the records
("keyDoc" on the event). Each change is logged as a settings change (the setting, never the document or where it's
kept), and its reply carries the records as the page loads them (records.page_reply, #271), as an edit's does.

  PUT    /api/profiles/{pid}/keydocs             {"event": id, "kind", "kept"}: mark it, or change its mark
  DELETE /api/profiles/{pid}/keydocs/{event}     unmark it
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import activity, db as dbm, edits, keydocs, records, settings
from .main import _client_ip, get_db, need, user
from .routes_edits import with_records

router = APIRouter()
KEY = "keyDocs"


class Mark(BaseModel):
    event: int
    kind: Literal["advance_directive", "poa", "polst", "dnr", "organ", "other"]
    kept: Optional[str] = Field("", max_length=keydocs.KEPT_MOST * 2)


def _key(db, pid, eid):
    """The event's key, as its edits find it (from the event as imported); 404 if it isn't this profile's."""
    try:
        e = edits._event(db, pid, eid)
    except edits.EditError:
        raise HTTPException(404, "There's no such document in this profile's records.") from None
    return e, edits.key_of(e)


def _save(db, s, pid, request, change):
    with dbm.transaction(db):
        have = dict(keydocs.marks(db, pid))
        change(have)
        try:
            settings.put(db, KEY, have, "profile", pid, by=s["id"])
        except settings.SettingError as x:
            raise HTTPException(400, str(x)) from None
        activity.log(db, "settings", s, pid, {"setting": KEY, "label": settings.SETTINGS[KEY].label}, _client_ip(request))


@router.put("/api/profiles/{pid}/keydocs")
def mark(pid: int, body: Mark, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    e, key = _key(db, pid, body.event)
    if e.get("k") not in keydocs.DOC_KINDS:
        raise HTTPException(400, "Only a document, a letter or a report can be a key document.")
    _save(db, s, pid, request, lambda have: have.__setitem__(key, {"kind": body.kind, "kept": body.kept or ""}))
    return with_records(db, pid, {"event": body.event})


@router.delete("/api/profiles/{pid}/keydocs/{event}")
def unmark(pid: int, event: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    _, key = _key(db, pid, event)

    def gone(have):
        if have.pop(key, None) is None:
            raise HTTPException(404, "That document isn't marked as a key document.")
    _save(db, s, pid, request, gone)
    return with_records(db, pid, {"event": event})
