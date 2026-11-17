"""Settings (settings.py, #35): the merged object the page reads, the Settings screen's sections, and changing or
resetting values. Who may change a value depends on its setting's scope: a profile's settings its owners and
editors, a user's settings that user (on any profile shared with them), the install's settings admins. Every
change passes main.guard's CSRF check like any other, and is logged: a profile's in its activity log, the others
as account events. Values are never logged (they can hold lab names); only which setting changed."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import accounts, activity, db as dbm, presets, settings
from .main import _client_ip, admin, get_db, need, user

router = APIRouter()


def _check(db, s, pid, setting, role):
    if not settings.may_change(setting.scope, role, s["is_admin"]):
        raise HTTPException(403, {"profile": "Only the profile's owners and editors can change this.",
                                  "user": "You can't change this.",
                                  "install": "Only an admin can change this."}[setting.scope])
    if setting.scope == "install" and accounts.admin_needs_second_factor(db, s):
        raise HTTPException(403, "admin_two_factor_required")


def _log(db, s, pid, scope, detail, request):
    if scope == "profile":
        activity.log(db, "settings", s, pid, detail, _client_ip(request))
    else:
        activity.log(db, f"settings.{scope}", s, None, detail, _client_ip(request))


@router.get("/api/profiles/{pid}/settings")
def merged(pid: int, s=Depends(user), db=Depends(get_db)):
    """Every setting's value for this person on this profile, nested as settings.json was (what the page reads)."""
    need(db, s, pid, "viewer")
    return settings.merged(db, pid, s["id"])


@router.get("/api/profiles/{pid}/settings/sections")
def sections(pid: int, s=Depends(user), db=Depends(get_db)):
    """The Settings screen: each section with its settings, their values, where each value comes from ("default" or
    a scope) and whether this person may change it."""
    role = need(db, s, pid, "viewer")
    eff = settings.effective(db, pid, s["id"])
    out = []
    for sid, title, help in settings.SECTIONS:
        items = []
        for k, d in settings.SETTINGS.items():
            if d.section != sid or not d.screen:   # not d.screen: a page's own (the guide's, #199)
                continue
            v, src = eff[k]
            items.append({**d.public(), "value": v, "from": src, "can_change": settings.may_change(d.scope, role, s["is_admin"])})
        if items:
            out.append({"id": sid, "title": title, "help": help, "settings": items})
    return {"role": role, "sections": out}


class Value(BaseModel):
    value: Any


@router.put("/api/profiles/{pid}/settings/{key}")
def change(pid: int, key: str, body: Value, request: Request, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    _check(db, s, pid, d, role)
    try:
        with dbm.transaction(db):
            settings.put(db, key, body.value, pid=pid, uid=s["id"], by=s["id"])
            _log(db, s, pid, d.scope, {"setting": key, "label": d.label}, request)
    except settings.SettingError as e:
        raise HTTPException(400, str(e))
    return settings.merged(db, pid, s["id"])


@router.delete("/api/profiles/{pid}/settings/{key}")
def reset_one(pid: int, key: str, request: Request, s=Depends(user), db=Depends(get_db)):
    """Back to the default (or to the value under it, an install-wide one)."""
    role = need(db, s, pid, "viewer")
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    _check(db, s, pid, d, role)
    with dbm.transaction(db):
        if settings.clear(db, [key], d.scope, pid, s["id"]):
            _log(db, s, pid, d.scope, {"setting": key, "label": d.label, "reset": True}, request)
    return settings.merged(db, pid, s["id"])


class Section(BaseModel):
    section: str


@router.post("/api/profiles/{pid}/settings/reset")
def reset_section(pid: int, body: Section, request: Request, s=Depends(user), db=Depends(get_db)):
    """Reset to defaults: every setting of the section this person may change. Those they may not (an install
    setting, for someone who isn't an admin) are left as they are and listed in "kept"."""
    role = need(db, s, pid, "viewer")
    titles = {sid: title for sid, title, _ in settings.SECTIONS}
    if body.section not in titles:
        raise HTTPException(404, "There's no such section.")
    mine = [d for d in settings.SETTINGS.values() if d.section == body.section]
    allowed = [d for d in mine if settings.may_change(d.scope, role, s["is_admin"])
               and not (d.scope == "install" and accounts.admin_needs_second_factor(db, s))]
    if not allowed:
        raise HTTPException(403, "You can't change the settings in this section.")
    reset = []
    with dbm.transaction(db):
        for scope in settings.SCOPES:
            gone = settings.clear(db, [d.key for d in allowed if d.scope == scope], scope, pid, s["id"])
            if gone:
                _log(db, s, pid, scope, {"section": body.section, "label": titles[body.section], "settings": gone, "reset": True}, request)
            reset += gone
    return {"reset": reset, "kept": [d.key for d in mine if d not in allowed], "settings": settings.merged(db, pid, s["id"])}


# ---------------------------------------------------------------- dashboard presets by condition (#186)

@router.get("/api/profiles/{pid}/condition-presets")
def condition_presets(pid: int, s=Depends(user), db=Depends(get_db)):
    """Settings → Dashboard → Start from a condition (presets.py): the conditions, their labs matched to this
    profile's series, and those its problem list names. Owners and editors, who change the settings they fill in;
    nothing is changed here."""
    need(db, s, pid, "editor")
    return presets.offer(db, pid, s["id"])


# ---------------------------------------------------------------- a person's own settings, without a profile (#42)
# Every page reads these (the time zone and date format), including pages that aren't about one profile (the
# account page) and accounts with no profile yet. The first page loaded after signing in fills those not set yet
# from the browser.

def _user_values(db, uid):
    eff = settings.effective(db, None, uid)
    return {k: v for k, (v, _) in eff.items() if settings.SETTINGS[k].scope == "user"}


@router.get("/api/settings/user")
def user_values(s=Depends(user), db=Depends(get_db)):
    """{key: value} for every per-user setting (flat keys), as this person has them."""
    return _user_values(db, s["id"])


@router.put("/api/settings/user/{key}")
def change_user_value(key: str, body: Value, request: Request, s=Depends(user), db=Depends(get_db)):
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    if d.scope != "user":
        raise HTTPException(404, "That setting belongs to a profile.")
    try:
        with dbm.transaction(db):
            settings.put(db, key, body.value, uid=s["id"], by=s["id"])
            _log(db, s, None, "user", {"setting": key, "label": d.label}, request)
    except settings.SettingError as e:
        raise HTTPException(400, str(e))
    return _user_values(db, s["id"])


@router.get("/api/settings/user/sections")
def user_sections(s=Depends(user), db=Depends(get_db)):
    """The Settings screen's sections of this person's own settings (Display: theme, dates, units), as
    /api/profiles/{pid}/settings/sections gives them: for someone with no profile yet (#169)."""
    eff = settings.effective(db, None, s["id"])
    out = []
    for sid, title, help in settings.SECTIONS:
        items = [{**d.public(), "value": eff[k][0], "from": eff[k][1], "can_change": True}
                 for k, d in settings.SETTINGS.items() if d.section == sid and d.scope == "user" and d.screen]
        if items:
            out.append({"id": sid, "title": title, "help": help, "settings": items})
    return {"role": None, "sections": out}


@router.post("/api/settings/user/reset")
def reset_user_section(body: Section, request: Request, s=Depends(user), db=Depends(get_db)):
    """Back to the defaults: every one of this person's own settings in the section."""
    titles = {sid: title for sid, title, _ in settings.SECTIONS}
    keys = [k for k, d in settings.SETTINGS.items() if d.section == body.section and d.scope == "user"]
    if not keys:
        raise HTTPException(404, "There's no such section.")
    with dbm.transaction(db):
        gone = settings.clear(db, keys, "user", None, s["id"])
        if gone:
            _log(db, s, None, "user", {"section": body.section, "label": titles[body.section], "settings": gone, "reset": True}, request)
    return {"reset": gone, "settings": _user_values(db, s["id"])}


# ---------------------------------------------------------------- the install's own settings, without a profile (#167)
# The Admin page shows these (connected sources' sync defaults): an admin may have no profile at all. Admins only,
# with their own second factor once the server has more than one account (main.admin).

def _install_sections(db):
    have = settings.stored(db, "install")
    out = []
    for sid, title, help in settings.SECTIONS:
        items = [{**d.public(), "value": have.get(k, d.default), "from": "install" if k in have else "default", "can_change": True}
                 for k, d in settings.SETTINGS.items() if d.section == sid and d.scope == "install"]
        if items:
            out.append({"id": sid, "title": title, "help": help, "settings": items})
    return {"sections": out}


@router.get("/api/settings/install")
def install_values(s=Depends(admin), db=Depends(get_db)):
    """The install-wide settings in their sections, each with its value and whether it's the default."""
    return _install_sections(db)


@router.put("/api/settings/install/{key}")
def change_install_value(key: str, body: Value, request: Request, s=Depends(admin), db=Depends(get_db)):
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    if d.scope != "install":
        raise HTTPException(404, "That setting isn't one of the server's.")
    try:
        with dbm.transaction(db):
            settings.put(db, key, body.value, by=s["id"])
            _log(db, s, None, "install", {"setting": key, "label": d.label}, request)
    except settings.SettingError as e:
        raise HTTPException(400, str(e))
    return _install_sections(db)


@router.post("/api/settings/install/reset")
def reset_install_section(body: Section, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Back to the defaults: every install-wide setting of the section."""
    titles = {sid: title for sid, title, _ in settings.SECTIONS}
    keys = [k for k, d in settings.SETTINGS.items() if d.section == body.section and d.scope == "install"]
    if not keys:
        raise HTTPException(404, "There's no such section.")
    with dbm.transaction(db):
        gone = settings.clear(db, keys, "install")
        if gone:
            _log(db, s, None, "install", {"section": body.section, "label": titles[body.section], "settings": gone, "reset": True}, request)
    return {"reset": gone, **_install_sections(db)}


class Rename(BaseModel):
    name: str
    to: Any = None


@router.put("/api/profiles/{pid}/settings/{key}/name")
def rename(pid: int, key: str, body: Rename, request: Request, s=Depends(user), db=Depends(get_db)):
    """One name of a name map, changed where it's shown (#38): {"name", "to"}; no "to" shows the name as it is again.
    Returns the merged settings, like a change."""
    role = need(db, s, pid, "viewer")
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    _check(db, s, pid, d, role)
    try:
        with dbm.transaction(db):
            settings.rename(db, key, body.name, body.to, pid=pid, uid=s["id"], by=s["id"])
            _log(db, s, pid, d.scope, {"setting": key, "label": d.label}, request)
    except settings.SettingError as e:
        raise HTTPException(400, str(e))
    return settings.merged(db, pid, s["id"])
