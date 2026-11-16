"""The lab names matched during imports (labmap.py, #23), for admins: they apply to every profile on the install, so
only an admin may change or remove one. Owners and editors make them by confirming an import's preview. Each entry
names a test as a lab reported it and the series it goes to; never anyone's results, nor which profile it came from.

  GET    /api/labmaps          [{id, importer, label, name, unit, loinc, target, created, created_by, changed, changed_by}]
  PATCH  /api/labmaps/{id}     {"target": series name, or null to keep it as a series of its own}
  DELETE /api/labmaps/{id}     forget it: the next import with that name asks again

Changes apply to later imports; results already saved keep their series. Each is logged (activity "labmap.change",
"labmap.remove", account events). The CSRF check in main.guard covers the PATCH and DELETE like every other change.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import activity, db as dbm, importers as registry, labmap
from .main import _client_ip, admin, get_db

router = APIRouter()
HAND_ENTRY = "Typed in"


def _label(name):
    mod = registry.get(name)
    return mod.LABEL if mod else HAND_ENTRY if name == "manual" else name


@router.get("/api/labmaps")
def list_labmaps(s=Depends(admin), db=Depends(get_db)):
    return [{**m, "label": _label(m["importer"])} for m in labmap.listing(db)]


class Change(BaseModel):
    target: Optional[str] = Field(None, max_length=labmap.NAME_MAX)


def _found(db, mid):
    m = labmap.get(db, mid)
    if not m:
        raise HTTPException(404, "No such lab name match.")
    return m


@router.patch("/api/labmaps/{mid}")
def change_labmap(mid: int, body: Change, request: Request, s=Depends(admin), db=Depends(get_db)):
    with dbm.transaction(db):
        m = _found(db, mid)
        labmap.change(db, mid, body.target, s)
        after = labmap.get(db, mid)
        activity.log(db, "labmap.change", s, detail={"name": m["name"], "importer": m["importer"], "from": m["target"],
                                                     "to": after["target"]}, ip=_client_ip(request))
    return {**after, "label": _label(after["importer"])}


@router.delete("/api/labmaps/{mid}")
def remove_labmap(mid: int, request: Request, s=Depends(admin), db=Depends(get_db)):
    with dbm.transaction(db):
        m = _found(db, mid)
        labmap.remove(db, mid)
        activity.log(db, "labmap.remove", s, detail={"name": m["name"], "importer": m["importer"], "target": m["target"]},
                     ip=_client_ip(request))
    return {"ok": True}
