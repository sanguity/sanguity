"""The same lab result from two sources (crossmatch.py, #96): say a possible match is the same result, or not, and
undo that. Owners and editors only (viewers see the flags and links in the records the page loads, under "possible"
and "same"); the CSRF check in main.guard covers the POST and DELETE like every other change, and each change goes in
the profile's activity log as an edit.

  POST   /api/profiles/{pid}/same        {"ref": a possible match's ref, "same": true | false}   -> {"id"}
  DELETE /api/profiles/{pid}/same/{id}   undo it: both results show again (or the flag comes back)   -> {"ok"}
Each reply also carries the records as the page loads them, "records", as an edit's does (routes_edits.py, #271).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import activity, crossmatch, db as dbm, records
from .main import _client_ip, get_db, need, user
from .routes_edits import with_records

router = APIRouter()


class Decision(BaseModel):
    ref: str = Field(min_length=1, max_length=64)   # from the records' "possible" list
    same: bool = True                               # false: not the same, don't flag it again


@router.post("/api/profiles/{pid}/same", status_code=201)
def decide(pid: int, body: Decision, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        data = records.assemble(db, pid) or {}
        hit = next((x for x in data.get("possible") or [] if x["ref"] == body.ref), None)
        if hit is None:
            raise HTTPException(409, "These two results aren't flagged as possibly the same any more. Reload the page.")
        keys = []
        for side in (hit["a"], hit["b"]):
            key = next((crossmatch.point_key(se, p) for se in data.get("labs") or [] if se.get("imp") == side["imp"]
                        and se.get("name") == side["name"] for p in se.get("pts") or []
                        if isinstance(p, list) and p and p[0] == side["t"] and crossmatch._shown(p) == side["value"]), None)
            keys.append(key)
        if None in keys or crossmatch.pair_hash(*keys) != body.ref:
            raise HTTPException(409, "These two results can't be told apart from others. Reload the page.")
        sides = [{k: v for k, v in side.items() if k != "row"} for side in (hit["a"], hit["b"])]
        for side in sides:
            side["label"] = ((data.get("imports") or {}).get(str(side["imp"])) or {}).get("label") or ""
        rid = crossmatch.decide(db, pid, s["id"], keys[0], keys[1], body.same, sides)
        activity.log(db, "edit", s, pid, {"action": "same" if body.same else "not_same", "title": hit["a"]["name"]},
                     _client_ip(request))
    return with_records(db, pid, {"id": rid}, 201)


@router.delete("/api/profiles/{pid}/same/{rid}")
def undo(pid: int, rid: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    with dbm.transaction(db):
        gone = crossmatch.remove(db, pid, rid)
        if gone is None:
            raise HTTPException(404, "No such link.")
        title = (gone["sides"][0] if gone["sides"] else {}).get("name")
        activity.log(db, "edit", s, pid, {"action": "same" if gone["same"] else "not_same", "title": title, "undone": True},
                     _client_ip(request))
    return with_records(db, pid, {"ok": True})
