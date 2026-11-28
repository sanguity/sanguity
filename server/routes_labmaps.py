"""The lab names matched to a profile's tests (labmap.py, #23).

A profile's own (#927), for its owners and editors (viewers read them), on Settings → This person → Labs and ranges and
from a lab's chart on Labs (Same test as…, #1097): made at an import's review, or here; changed or removed here, each
with an Undo; a change re-files the results already saved under the match when asked to (move):

  GET    /api/profiles/{pid}/labmaps            {"matches": [{id, importer, label, name, unit, loinc, target, check,
                                                 created, created_by, changed, changed_by, results, imports}],
                                                 "tests": [{name, unit}] (the picker's: the profile's tests as charted),
                                                 "can_change"}
  POST   /api/profiles/{pid}/labmaps            {"name": a test as charted, "target": one of the tests or null (kept as
                                                 its own), "move"}: every name, unit and source charted under that name
                                                 matched (Add, Same test as…) -> {change, matches, series, results}
  PATCH  /api/profiles/{pid}/labmaps/{id}       {"target", "move"} (Change, Keep as its own test), or {"target": the one
                                                 shown, "keep": true} (Keep, a match to check: 409 if it isn't the match's
                                                 now, #575) -> the same
  DELETE /api/profiles/{pid}/labmaps/{id}       forget it (the next import asks again; results stay where they are) -> {change}
  POST   /api/profiles/{pid}/labmaps/undo/{cid} a change undone (409 if one of its matches was changed since)

One an admin made before #927 is for every profile, with no results counted (whose would they be). They're listed on
each profile's card, under For every profile on this server (#1114: the one place; Install had a page of them
before): GET /api/profiles/{pid}/labmaps's "every" (who made or changed one said to admins alone), with
"can_change_every" for an admin, who changes them there:

  GET    /api/labmaps          [{id, importer, label, name, unit, loinc, target, check, one_profile, created, created_by,
                                 changed, changed_by}]   (check: remembered before #546 and not applied until kept, first)
  PATCH  /api/labmaps/{id}     {"target": series name, or null to keep it as a series of its own}; the same target: Keep
                               ({"target": the one shown, "keep": true}: refused with 409 if it isn't the match's now, #575)
  DELETE /api/labmaps/{id}     forget it: the next import with that name asks again
         ?profile={pid} on either: made from that profile's card (one the admin sees), with an Undo -> {..., "change"}
  POST   /api/labmaps/undo/{cid}?profile={pid}   that change undone (404 once it's undone; 409 if changed since)

They apply to later imports; results already saved keep their series. Each change is logged: a profile's (activity
"labmap", never the names), these ("labmap.change", with "kept" for a Keep, "labmap.remove" and "labmap.undo",
account events). The CSRF check in main.guard covers every change.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import accounts, activity, db as dbm, importers as registry, labmap, routes_edits
from .main import RANK, _client_ip, admin, get_db, need, user

router = APIRouter()
HAND_ENTRY = "Typed in"


def _label(name):
    mod = registry.get(name)
    return mod.LABEL if mod else HAND_ENTRY if name == "manual" else name


# ---------------------------------------------------------------- a profile's own (#1097)

class NewMatch(BaseModel):
    name: str = Field(..., max_length=labmap.NAME_MAX)               # a test as the profile charts it
    target: Optional[str] = Field(None, max_length=labmap.NAME_MAX)  # one of its tests; null: kept as its own
    move: bool = True                                                # its saved results re-filed too


class OwnChange(BaseModel):
    target: Optional[str] = Field(None, max_length=labmap.NAME_MAX)
    move: bool = True
    keep: bool = False   # Keep (#575): only if target is still the match's


@router.get("/api/profiles/{pid}/labmaps")
def profile_labmaps(pid: int, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    hide = {"one_profile"} if s["is_admin"] else {"one_profile", "created_by", "changed_by"}   # (#1114: who, to admins)
    return {"matches": [{**{k: v for k, v in m.items() if k != "one_profile"}, "label": _label(m["importer"])} for m in labmap.own(db, pid)],
            "tests": labmap.tests(db, pid), "can_change": RANK[role] >= RANK["editor"],
            "every": [{**{k: v for k, v in m.items() if k not in hide}, "label": _label(m["importer"])} for m in labmap.listing(db)],
            "can_change_every": bool(s["is_admin"]) and not accounts.admin_needs_second_factor(db, s)}   # (as main.admin lets them)


def _done(db, pid, s, request, change, out):
    activity.log(db, "labmap", s, pid, {"change": change, "matches": len(out.get("matches") or []) or 1,
                                        **({"moved": out["results"]} if out.get("results") else {})}, _client_ip(request))
    return out


def _reply(db, pid, out, records):
    """The answer, with the records as the page loads them when it asks (records=1: Labs, #271's way), so a lab
    re-filed is charted where it now goes without loading them again."""
    return routes_edits.with_records(db, pid, out) if records else out


@router.post("/api/profiles/{pid}/labmaps")
def add_profile_labmap(pid: int, body: NewMatch, request: Request, records: bool = False, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        idents = labmap.charted_as(db, pid, body.name)
        if not idents:
            raise HTTPException(404, f"This profile has no test “{' '.join(body.name.split())}” now. Look at the list again.")
        try:
            out = labmap.set_matches(db, pid, idents, body.target, s, move=body.move)
        except labmap.Refused as e:
            raise HTTPException(400, str(e))
        out = _done(db, pid, s, request, "add" if body.target else "keep", out)
    return _reply(db, pid, out, records)


@router.patch("/api/profiles/{pid}/labmaps/{mid}")
def change_profile_labmap(pid: int, mid: int, body: OwnChange, request: Request, records: bool = False, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        m = labmap.get(db, mid, pid)
        if not m:
            raise HTTPException(404, "No such lab name match: it was removed meanwhile.")
        if body.keep and labmap.clean_target(body.target) != m["target"]:
            raise HTTPException(409, f"Not kept: this match was changed meanwhile; {_goes(m)}. Look at it again.")
        try:
            out = labmap.set_matches(db, pid, [(m["importer"], m["name"], m["unit"], m["loinc"])],
                                     m["target"] if body.keep else body.target, s, move=body.move and not body.keep, kept=body.keep)
        except labmap.Refused as e:
            raise HTTPException(400, str(e))
        out = _done(db, pid, s, request, "check" if body.keep else "change", out)
    return _reply(db, pid, out, records)


@router.delete("/api/profiles/{pid}/labmaps/{mid}")
def remove_profile_labmap(pid: int, mid: int, request: Request, records: bool = False, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        cid = labmap.remove_own(db, pid, mid, s)
        if cid is None:
            raise HTTPException(404, "No such lab name match: it was removed meanwhile.")
        out = _done(db, pid, s, request, "remove", {"change": cid})
    return _reply(db, pid, out, records)


@router.post("/api/profiles/{pid}/labmaps/undo/{cid}")
def undo_profile_labmap(pid: int, cid: int, request: Request, records: bool = False, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        try:
            labmap.undo(db, pid, cid)
        except LookupError:
            raise HTTPException(404, "Nothing to undo: it was undone already.")
        except labmap.Refused as e:
            raise HTTPException(409, str(e))
        out = _done(db, pid, s, request, "undo", {"ok": True})
    return _reply(db, pid, out, records)


# ---------------------------------------------------------------- Install's, for every profile (admins)

@router.get("/api/labmaps")
def list_labmaps(s=Depends(admin), db=Depends(get_db)):
    return [{**m, "label": _label(m["importer"])} for m in labmap.listing(db)]


class Change(BaseModel):
    target: Optional[str] = Field(None, max_length=labmap.NAME_MAX)
    keep: bool = False   # Keep: only if target is still the match's (#575: not an old look-alike put back over another admin's change)


def _goes(m):
    return f"it now goes to “{m['target']}”" if m["target"] else "it's now kept as a series of its own"


def _found(db, mid):
    m = labmap.get(db, mid)
    if not m:
        raise HTTPException(404, "No such lab name match.")
    return m


@router.patch("/api/labmaps/{mid}")
def change_labmap(mid: int, body: Change, request: Request, profile: Optional[int] = None, s=Depends(admin), db=Depends(get_db)):
    if profile is not None:
        need(db, s, profile, "viewer")
    with dbm.transaction(db):
        m = _found(db, mid)
        if body.keep and labmap.clean_target(body.target) != m["target"]:
            raise HTTPException(409, f"Not kept: this match was changed meanwhile; {_goes(m)}. Look at it again.")
        before = labmap.every_before(db, mid)
        labmap.change(db, mid, body.target, s)
        after = labmap.get(db, mid)
        activity.log(db, "labmap.change", s, detail={"name": m["name"], "importer": m["importer"], "from": m["target"],
                                                     "to": after["target"], **({"kept": True} if body.keep else {})},   # Keep (#575)
                     ip=_client_ip(request))
        cid = labmap.every_record(db, profile, s, before) if profile is not None else None
    return {**after, "label": _label(after["importer"]), **({"change": cid} if cid else {})}


@router.delete("/api/labmaps/{mid}")
def remove_labmap(mid: int, request: Request, profile: Optional[int] = None, s=Depends(admin), db=Depends(get_db)):
    if profile is not None:
        need(db, s, profile, "viewer")
    with dbm.transaction(db):
        m = _found(db, mid)
        before = labmap.every_before(db, mid)
        labmap.remove(db, mid)
        activity.log(db, "labmap.remove", s, detail={"name": m["name"], "importer": m["importer"], "target": m["target"]},
                     ip=_client_ip(request))
        cid = labmap.every_record(db, profile, s, before) if profile is not None else None
    return {"ok": True, **({"change": cid} if cid else {})}


@router.post("/api/labmaps/undo/{cid}")
def undo_labmap(cid: int, profile: int, request: Request, s=Depends(admin), db=Depends(get_db)):
    """(#1114) An admin's change to a match for every profile, made from a profile's card, undone there."""
    need(db, s, profile, "viewer")
    with dbm.transaction(db):
        try:
            m = labmap.undo(db, profile, cid, every=True)
        except LookupError:
            raise HTTPException(404, "Nothing to undo: it was undone already.")
        except labmap.Refused as e:
            raise HTTPException(409, str(e))
        activity.log(db, "labmap.undo", s, detail=m, ip=_client_ip(request))
    return {"ok": True}
