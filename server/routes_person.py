"""About this person (person.py, #194): a profile's birth date, sex used for reference ranges, and height history; a
measured maximum heart rate, for heart rate zones on workouts (#197, hrzones.py); and targets of your own for home
readings, as the care team gave them (#234, targets.py); and for the emergency card (#1126, emergency.py) a blood type
typed in (a blood type lab result in the records is shown, and used, instead) and emergency contacts, in order.
Viewers read them (and the heights a clinic recorded in the records, or the phone in an uploaded Apple Health export
(#223), shown beside the history); owners and editors
change them. They are profile settings (person.*; the due date since #196), so every change is checked by the setting's own check and
logged in the profile's activity like any setting (which one, never its value); main.guard's CSRF check covers the
PUT, POST and DELETE like every other change."""
import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, db as dbm, emergency, hrzones, person, records, settings, targets, watch_store
from .main import _client_ip, get_db, need, user

router = APIRouter()
KEYS = {"birthDate": "person.birthDate", "sex": "person.sex", "heights": "person.heights",
        "maxHr": "person.maxHr",   # a measured maximum heart rate, for heart rate zones (#197; 0: not set)
        "dueDate": "person.dueDate",   # #196
        "targets": "person.targets",   # #234: targets of your own for home readings, as the care team gave them
        "bloodType": "person.bloodType", "contacts": "person.contacts"}   # #1126: for the emergency card


def _values(db, pid):
    have = settings.stored(db, "profile", pid)
    return {k: have.get(key, settings.SETTINGS[key].default) for k, key in KEYS.items()}


def view(db, pid, role):
    """What the page shows: the three values, the heights in the records, and whether this person may change them."""
    v = _values(db, pid)
    body = records.served(db, pid)
    mine = {h["date"] for h in v["heights"]}
    heights = person.recorded(json.loads(body) if body else {})
    heights = sorted(heights + person.from_phone(watch_store.load(db, pid)), key=lambda h: h["date"], reverse=True)   # (#223)
    recorded = [{**h, "inHistory": h["date"] in mine} for h in heights]
    lab = emergency.blood_from_labs(json.loads(body) if body else {})   # #1126: shown instead of the one typed
    return {**v, "recorded": recorded, "sexes": person.SEXES, "can_change": role in ("owner", "editor"),
            "measures": {k: m[0] for k, m in targets.MEASURES.items()},   # #234
            "bloodTypes": person.BLOOD_TYPES, "bloodLab": lab, "mostContacts": person.MOST_CONTACTS}


def _save(db, s, pid, update, request, detail=None):
    """update(values) gives {short name: new value} from the values stored now (read inside the transaction, so two
    people adding heights at once both keep theirs). Each is checked, then that they agree; then stored, with one
    activity entry per setting changed."""
    with dbm.transaction(db):
        apply(db, s, pid, update, request, detail)


def apply(db, s, pid, update, request, detail=None):
    """_save's work inside a transaction the caller holds (heights typed in on the import page, routes_manual.py #224)."""
    have = _values(db, pid)
    changes = update(have)
    if not changes:
        return
    v = {**have, **changes}
    try:
        v["birthDate"], v["sex"], v["heights"], v["dueDate"] = (person.check_birth(v["birthDate"]), person.check_sex(v["sex"]),
                                                                person.check_heights(v["heights"]), person.check_due(v["dueDate"]))
        person.check_together(v["birthDate"], v["heights"], v["dueDate"])
        v["maxHr"] = hrzones.check_max(v["maxHr"])
        v["targets"] = targets.check(v["targets"])   # #234
        v["bloodType"], v["contacts"] = person.check_blood(v["bloodType"]), person.check_contacts(v["contacts"])   # #1126
    except (person.PersonError, hrzones.ZoneError, targets.TargetError) as e:
        raise HTTPException(400, str(e))
    for k in changes:
        key = KEYS[k]
        settings.put(db, key, v[k], "profile", pid, by=s["id"])
        activity.log(db, "settings", s, pid, {"setting": key, "label": settings.SETTINGS[key].label, **(detail or {})},
                     _client_ip(request))


@router.get("/api/profiles/{pid}/person")
def about(pid: int, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    return view(db, pid, role)


class Details(BaseModel):
    birthDate: Optional[str] = None     # "" clears it; left out: unchanged
    sex: Optional[str] = None           # "", "female" or "male"; left out: unchanged
    maxHr: Optional[float] = None       # a measured maximum heart rate, bpm; 0 clears it; left out: unchanged (#197)

    dueDate: Optional[str] = None       # "" clears it; for a baby born early (#196)
    # #234: {measure: target, in its "unit" as typed, or null to clear it}; only the measures given change
    targets: Optional[dict] = None
    bloodType: Optional[str] = None     # #1126: "" clears it
    contacts: Optional[list] = None     # #1126: the emergency contacts, all of them in order ([] for none)


@router.put("/api/profiles/{pid}/person")
def change(pid: int, body: Details, request: Request, s=Depends(user), db=Depends(get_db)):
    """Change the birth date, the sex used for reference ranges, the measured maximum heart rate (#197), the due date
    (#196), the blood type or the emergency contacts (#1126: the whole list, in order), a target of your own for a home measure (#234: set, changed or cleared one measure at a time), or any of
    them (only those given, and only if they differ)."""
    role = need(db, s, pid, "editor")
    try:
        typed = {k: None if t is None else targets.typed(k, t) for k, t in (body.targets or {}).items()}   # #234
    except targets.TargetError as e:
        raise HTTPException(400, str(e))

    def changes(have):
        out = {k: v for k, v in (("birthDate", body.birthDate), ("sex", body.sex), ("maxHr", body.maxHr), ("dueDate", body.dueDate),
                                 ("bloodType", body.bloodType), ("contacts", body.contacts))
               if v is not None and v != have[k]}
        if typed:   # each measure given set or cleared, the others kept as they are now (read inside the transaction)
            t = {**have["targets"], **typed}
            t = {k: v for k, v in t.items() if v is not None}
            if t != have["targets"]:
                out["targets"] = t
        return out
    _save(db, s, pid, changes, request)
    return view(db, pid, role)


class Height(BaseModel):
    date: str                           # YYYY-MM-DD
    unit: Literal["cm", "ftin"] = "cm"  # how it was typed
    cm: Optional[float] = None          # unit "cm", or a clinic's height already in cm
    ft: Optional[float] = None          # unit "ftin": feet and inches
    inches: Optional[float] = None


@router.post("/api/profiles/{pid}/person/heights")
def add_height(pid: int, body: Height, request: Request, s=Depends(user), db=Depends(get_db)):
    """Add a height to the history; one on the same day is replaced."""
    role = need(db, s, pid, "editor")
    try:
        if body.unit == "ftin" and body.cm is None:   # typed in feet and inches (a clinic's in inches comes as cm)
            if body.ft is None and body.inches is None:
                raise person.PersonError("Give the height in feet and inches.")
            cm = person.from_ftin(body.ft, body.inches)
        elif body.cm is None:
            raise person.PersonError("Give the height in cm.")
        else:
            cm = body.cm
        entry = person.check_height({"date": body.date, "cm": cm, "unit": body.unit})
    except person.PersonError as e:
        raise HTTPException(400, str(e))
    _save(db, s, pid, lambda have: {"heights": [h for h in have["heights"] if h["date"] != entry["date"]] + [entry]},
          request, {"change": "add"})
    return view(db, pid, role)


@router.delete("/api/profiles/{pid}/person/heights/{day}")
def remove_height(pid: int, day: str, request: Request, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "editor")

    def without(have):
        left = [h for h in have["heights"] if h["date"] != day]
        if len(left) == len(have["heights"]):
            raise HTTPException(404, "There's no height on that day.")
        return {"heights": left}
    _save(db, s, pid, without, request, {"change": "remove"})
    return view(db, pid, role)
