"""Out-of-pocket costs on the Costs tab (oop.py, #55): what a person says about one payment or one claim's share.
Owners and editors only; the CSRF check in main.guard covers the PUT like every other change, and each change goes
in the profile's activity log (as an "edit" with action "cost": what was said, never an amount or a payee).

  PUT /api/profiles/{pid}/costs/mark   {"payment": key} or {"event": claim event id}, then any of
                                       "hsa": "HSA" | "FSA" | "none" | "auto"
                                       "claim": a claim's event id, 0 for no claim, -1 to match it again (payments)
                                       "same": false (two payments, not one) | true (look for it again) (payments)
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, db as dbm, edits, oop, records
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Mark(BaseModel):
    payment: Optional[str] = None                          # a payment's key, as data["oop"]["payments"][].key
    event: Optional[int] = None                            # or a claim's event id: its share
    hsa: Optional[Literal["HSA", "FSA", "none", "auto"]] = None
    claim: Optional[int] = None
    same: Optional[bool] = None


@router.put("/api/profiles/{pid}/costs/mark")
def mark(pid: int, body: Mark, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    if (body.payment is None) == (body.event is None):
        raise HTTPException(400, "Say which payment or which claim this is about.")
    data = records.assemble(db, pid) or {}
    o = data.get("oop") or {}
    events = {e["id"]: e for e in data.get("events") or []}
    claims = {c.get("id") for c in data.get("costs") or []   # claims with a share of their own: what oop.py lists
              if isinstance(c, dict) and isinstance(c.get("owe"), (int, float)) and c["owe"] >= 0.01}
    said = {}
    if body.payment is not None:
        if body.payment not in {p["key"] for p in o.get("payments") or []}:
            raise HTTPException(404, "There's no such payment in this profile's records.")
        key, what = body.payment, "payment"
        if body.claim is not None:
            if body.claim > 0:
                if body.claim not in claims or body.claim not in events:
                    raise HTTPException(400, "Choose one of this profile's claims with a share for you to pay.")
                said["claim"] = edits.key_of(events[body.claim])
            else:
                said["claim"] = "" if body.claim == 0 else None
        if body.same is not None:
            said["same"] = None if body.same else False
    else:
        if body.event not in claims or body.event not in events:
            raise HTTPException(404, "There's no such claim in this profile's records.")
        if body.claim is not None or body.same is not None:
            raise HTTPException(400, "A claim's share can only be tagged HSA or FSA.")
        key, what = edits.key_of(events[body.event]), "claim"
    if body.hsa is not None:
        said["hsa"] = None if body.hsa == "auto" else "" if body.hsa == "none" else body.hsa
    if not said:
        raise HTTPException(400, "Nothing to change.")
    with dbm.transaction(db):
        now = oop.set_mark(db, pid, s["id"], key, **said)
        log = {k: (("claim" if v else "none" if v == "" else "auto") if k == "claim" else v) for k, v in said.items()}
        activity.log(db, "edit", s, pid, {"action": "cost", "of": what, **log}, _client_ip(request))
    return {"mark": {k: (v if k != "claim" else bool(v)) for k, v in now.items()}}
