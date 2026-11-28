"""Out-of-pocket costs on the Costs tab (oop.py, #55): what a person says about one payment or one claim's share.
Owners and editors only; the CSRF check in main.guard covers the PUT like every other change, and each change goes
in the profile's activity log (as an "edit" with action "cost": what was said, never an amount or a payee).

  PUT /api/profiles/{pid}/costs/mark   {"payment": key} or {"event": claim event id}, then any of
                                       "hsa": "HSA" | "FSA" | "none" | "auto"
                                       "claim": a claim's event id, 0 for no claim, -1 to match it again (payments)
                                       "same": false (two payments, not one) | true (look for it again) (payments)
                                       -> {"mark": what's said now, "oop": data["oop"] as the records page reads it
                                       after the change}: the page puts it in place (store.js, #405), and its
                                       X-Records-Rev header is the records' revision after it
  POST   /api/profiles/{pid}/costs/plans        a plan year (#1127; costplan.py): {"start": "YYYY-MM-DD",
                                                "deductible", "oop", "familyDeductible"?, "familyOop"?} (amounts)
  PUT    /api/profiles/{pid}/costs/plans/{id}   the same, changed
  DELETE /api/profiles/{pid}/costs/plans/{id}   removed
                                       each -> {"oop": as the mark's reply}, logged as an "edit" with action "cost"
                                       ("of": "plan", "change": "add" | "change" | "remove"; never an amount)
"""
import json

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import activity, costplan, db as dbm, edits, oop, records
from .main import _client_ip, get_db, need, user

router = APIRouter()


class Mark(BaseModel):
    payment: Optional[str] = None                          # a payment's key, as data["oop"]["payments"][].key
    event: Optional[int] = None                            # or a claim's event id: its share
    hsa: Optional[Literal["HSA", "FSA", "none", "auto"]] = None
    claim: Optional[int] = None
    same: Optional[bool] = None


@router.put("/api/profiles/{pid}/costs/mark")
def mark(pid: int, body: Mark, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
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
    return {"mark": {k: (v if k != "claim" else bool(v)) for k, v in now.items()}, **_oop(db, pid, response)}


def _oop(db, pid, response):
    """{"oop": data["oop"] as the records page reads it now}, its revision in X-Records-Rev."""
    got = records.page(db, pid)   # worked out once, for the page's next load too
    response.headers["X-Records-Rev"] = got[1] if got else ""
    return {"oop": json.loads(got[0]).get("oop") if got else None}


def _entry(db, s, pid, request, of, change, do):
    """A plan year's or a bill's change (do(db) -> its id, or None when there's no such entry), logged; the reply's
    {"oop"}. A refusal (costplan.PlanError) is a 400 saying why."""
    need(db, s, pid, "editor")
    try:
        with dbm.transaction(db):
            if do(db) is None:
                raise HTTPException(404, f"There's no such {'plan year' if of == 'plan' else 'bill'} in this profile.")
            activity.log(db, "edit", s, pid, {"action": "cost", "of": of, "change": change}, _client_ip(request))
    except costplan.PlanError as e:
        raise HTTPException(400, str(e)) from None


class Plan(BaseModel):
    start: str
    deductible: Optional[float] = None
    oop: Optional[float] = None
    familyDeductible: Optional[float] = None
    familyOop: Optional[float] = None


@router.post("/api/profiles/{pid}/costs/plans", status_code=201)
def add_plan(pid: int, body: Plan, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    _entry(db, s, pid, request, "plan", "add", lambda db: costplan.save_plan(db, pid, s, body.model_dump()))
    return _oop(db, pid, response)


@router.put("/api/profiles/{pid}/costs/plans/{eid}")
def change_plan(pid: int, eid: int, body: Plan, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    _entry(db, s, pid, request, "plan", "change", lambda db: costplan.save_plan(db, pid, s, body.model_dump(), eid))
    return _oop(db, pid, response)


@router.delete("/api/profiles/{pid}/costs/plans/{eid}")
def remove_plan(pid: int, eid: int, request: Request, response: Response, s=Depends(user), db=Depends(get_db)):
    _entry(db, s, pid, request, "plan", "remove", lambda db: costplan.drop_plan(db, pid, eid) or None)
    return _oop(db, pid, response)
