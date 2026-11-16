"""The journal (journal.py, #49): list, add, change and delete a profile's notes, symptoms and side effects. Viewers
read (the records the page loads list the entries too, as "journal"); owners and editors write. The CSRF check in
main.guard covers the POST, PUT and DELETE like every other change, and each change goes in the profile's activity
log (the kind of entry, never what it says)."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, db as dbm, journal
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Entry(BaseModel):
    kind: Literal["note", "symptom", "side_effect"]
    t: str                                   # YYYY-MM-DD or YYYY-MM-DDTHH:MM
    title: Optional[str] = None              # symptom, side effect: what it was
    severity: Optional[int] = None           # symptom, side effect: 0-10
    note: Optional[str] = None
    med: Optional[str] = None                # symptom, side effect: one of the profile's medications, by name


def _checked(db, pid, body, keep_med=None):
    try:
        return journal.check(db, pid, body.kind, body.t, body.title, body.severity, body.note, body.med, keep_med)
    except journal.JournalError as x:
        raise HTTPException(400, str(x))


@router.get("/api/profiles/{pid}/journal")
def list_journal(pid: int, s=Depends(user), db=Depends(get_db)):
    """The entries, oldest first, and the medications a symptom or side effect can be linked to."""
    need(db, s, pid, "viewer")
    return {"entries": journal.listing(db, pid), "medications": journal.medications(db, pid)}


@router.post("/api/profiles/{pid}/journal", status_code=201)
def add_entry(pid: int, body: Entry, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    entry = _checked(db, pid, body)
    with dbm.transaction(db):
        jid = journal.add(db, pid, s, entry)
        activity.log(db, "journal", s, pid, journal.log_detail("add", entry), _client_ip(request))
    return {"id": jid}


@router.put("/api/profiles/{pid}/journal/{jid}")
def change_entry(pid: int, jid: int, body: Entry, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    was = journal.get(db, pid, jid)
    if was is None:
        raise HTTPException(404, "No such journal entry.")
    entry = _checked(db, pid, body, was.get("med"))
    with dbm.transaction(db):
        if journal.change(db, pid, jid, s, entry) is None:
            raise HTTPException(404, "No such journal entry.")
        activity.log(db, "journal", s, pid, journal.log_detail("change", entry), _client_ip(request))
    return {"ok": True}


@router.delete("/api/profiles/{pid}/journal/{jid}")
def remove_entry(pid: int, jid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = journal.remove(db, pid, jid)
        if was is None:
            raise HTTPException(404, "No such journal entry.")
        activity.log(db, "journal", s, pid, journal.log_detail("delete", was), _client_ip(request))
    return {"ok": True}
