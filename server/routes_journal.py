"""The journal (journal.py, #49): list, add, change and delete a profile's notes, symptoms, side effects and (#161)
moods. Viewers read (the records the page loads list the entries too, as "journal"); owners and editors write. The
CSRF check in main.guard covers the POST, PUT and DELETE like every other change, and each change goes in the
profile's activity log (the kind of entry, never what it says).

#161 adds:
  POST   /api/profiles/{pid}/journal/group          a symptom group logged in one go: {group, t, note, items: [{title,
                                                    severity, where}]}, one symptom entry each -> {"ids"}
  PUT    /api/profiles/{pid}/journal/photos         a photo for an entry about to be saved: the request body is the
                                                    picture (journal_photos.py checks and cleans it) -> {"id", "type",
                                                    "bytes", "removed"}; nginx lets this one be large, as uploads
  GET    /api/profiles/{pid}/journal/photos/{id}    a photo an entry lists, to anyone who can view the profile
The symptom groups themselves are the setting journal.groups (PUT .../settings/journal.groups).

Each change's reply carries what it changed (#271), so the records page updates in place instead of loading the records
again: "journal", the entries added or changed, as the records list them; "removed", the ids of those deleted. Its
X-Records-Rev header is the records' revision after it (records.stamp, #405)."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, StrictInt

from . import activity, db as dbm, journal, journal_photos as photos, records
from .main import DOC_CSP, _client_ip, get_db, need, user

router = APIRouter()


class Entry(BaseModel):
    kind: Literal["note", "symptom", "side_effect", "mood"]
    t: str                                   # YYYY-MM-DD or YYYY-MM-DDTHH:MM (a mood: the day only)
    title: Optional[str] = None              # symptom, side effect: what it was
    severity: Optional[int] = None           # symptom, side effect: 0-10
    note: Optional[str] = None
    med: Optional[str] = None                # symptom, side effect: one of the profile's medications, by name
    where: Optional[list] = None             # symptom, side effect: body regions (journal.REGIONS ids)
    group: Optional[str] = None              # symptom, side effect: the symptom group it was logged with
    photos: Optional[list] = None            # note, symptom, side effect: [{"id", "alt"}], sent first
    mood: Optional[StrictInt] = None         # mood: 0-10 (#161)
    energy: Optional[StrictInt] = None       # mood: 0-10


class Group(BaseModel):
    group: str
    t: str
    note: Optional[str] = None
    items: list


def _checked(db, pid, body, keep_med=None, jid=None):
    try:
        return journal.check(db, pid, body.model_dump(), keep_med, jid)
    except (journal.JournalError, photos.PhotoError) as x:
        raise HTTPException(getattr(x, "status", 400), str(x))


@router.get("/api/profiles/{pid}/journal")
def list_journal(pid: int, s=Depends(user), db=Depends(get_db)):
    """The entries, oldest first, and the medications a symptom or side effect can be linked to."""
    need(db, s, pid, "viewer")
    return {"entries": journal.listing(db, pid), "medications": journal.medications(db, pid)}


@router.post("/api/profiles/{pid}/journal", status_code=201)
def add_entry(pid: int, body: Entry, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):   # checked inside (#925): two moods for one day sent at once, the second is refused
        entry = _checked(db, pid, body)
        jid = journal.add(db, pid, s, entry)
        activity.log(db, "journal", s, pid, journal.log_detail("add", entry), _client_ip(request))
    records.stamp(response, db, pid)
    return {"id": jid, "journal": journal.listing(db, pid, [jid])}


@router.post("/api/profiles/{pid}/journal/group", status_code=201)
def add_group(pid: int, body: Group, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    """A symptom group (#161), each symptom an entry of its own with its own severity, saved together or not at all."""
    need(db, s, pid, "editor")
    try:
        entries = journal.check_group(db, pid, body.model_dump())
    except journal.JournalError as x:
        raise HTTPException(400, str(x))
    ids = []
    with dbm.transaction(db):
        for entry in entries:
            ids.append(journal.add(db, pid, s, entry))
            activity.log(db, "journal", s, pid, journal.log_detail("add", entry), _client_ip(request))
    records.stamp(response, db, pid)
    return {"ids": ids, "journal": journal.listing(db, pid, ids)}


def _store_photo(db, pid, uid, data):
    with dbm.transaction(db):
        return photos.add(db, pid, uid, data)


@router.put("/api/profiles/{pid}/journal/photos", status_code=201)
async def add_photo(pid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    """A photo for an entry (#161): checked, its metadata taken out, sealed. Refused before reading past the limit."""
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(need, db, s, pid, "editor")
    try:
        if int(request.headers.get("content-length") or 0) > photos.max_bytes():
            raise photos.too_big()
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > photos.max_bytes():
                raise photos.too_big()
        return await run_in_threadpool(_store_photo, db, pid, s["id"], bytes(data))
    except ValueError as x:   # PhotoError, or a content-length that isn't a number
        raise HTTPException(getattr(x, "status", 400), str(x) if isinstance(x, photos.PhotoError) else "Send the photo as the request body.")


@router.get("/api/profiles/{pid}/journal/photos/{photo_id}")
def photo(pid: int, photo_id: int, s=Depends(user), db=Depends(get_db)):
    """A photo an entry lists, shown inline under the documents' strict policy (nothing in it can run or load)."""
    need(db, s, pid, "viewer")
    got = photos.get(db, pid, photo_id)
    if got is None:
        raise HTTPException(404, "That photo isn't here.")
    data, media = got
    return Response(data, media_type=media, headers={"Content-Disposition": f'inline; filename="photo-{photo_id}"',
                                                     "Content-Security-Policy": DOC_CSP, "X-Frame-Options": "SAMEORIGIN"})


@router.put("/api/profiles/{pid}/journal/{jid}")
def change_entry(pid: int, jid: int, body: Entry, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):   # checked inside, as an entry added is (#925)
        was = journal.get(db, pid, jid)
        if was is None:
            raise HTTPException(404, "No such journal entry.")
        entry = _checked(db, pid, body, was.get("med"), jid)
        if journal.change(db, pid, jid, s, entry) is None:
            raise HTTPException(404, "No such journal entry.")
        activity.log(db, "journal", s, pid, journal.log_detail("change", entry), _client_ip(request))
    records.stamp(response, db, pid)
    return {"ok": True, "journal": journal.listing(db, pid, [jid])}


@router.delete("/api/profiles/{pid}/journal/{jid}")
def remove_entry(pid: int, jid: int, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        was = journal.remove(db, pid, jid)
        if was is None:
            raise HTTPException(404, "No such journal entry.")
        activity.log(db, "journal", s, pid, journal.log_detail("delete", was), _client_ip(request))
    records.stamp(response, db, pid)
    return {"ok": True, "removed": [jid]}
