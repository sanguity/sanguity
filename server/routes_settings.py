"""Settings (settings.py, #35): the merged object the page reads, the Settings screen's sections, and changing or
resetting values. Who may change a value depends on its setting's scope: a profile's settings its owners and
editors, a user's settings that user (on any profile shared with them), the install's settings admins. Every
change passes main.guard's CSRF check like any other, and is logged: a profile's in its activity log, the others
as account events. A profile's or a person's values are never logged (they can hold lab names); only which setting
changed. An install setting's change (#1006) asks for the admin's fresh check (main.fresh: 403 reauth_required), on
either route, and its entry in the install log has the old and new values: none of the install's holds health
information or a secret (one that could, private, would be logged by name only). A whole value (a list, a map) that a
page made from the one it read is sent with that one, its version ("was", #923), and refused (409) if the setting
holds another by then: a change made since in another tab, or by someone else, is never lost under it."""
import functools
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import accounts, activity, config, db as dbm, general, main, presets, settings
from .main import _client_ip, admin, fresh, get_db, need, user

router = APIRouter()


def _check(db, s, pid, setting, role):
    if not settings.may_change(setting.scope, role, s["is_admin"]):
        raise HTTPException(403, {"profile": "Only the profile's owners and editors can change this.",
                                  "user": "You can't change this.",
                                  "install": "Only an admin can change this."}[setting.scope])
    if setting.scope == "install" and accounts.admin_needs_second_factor(db, s):
        raise HTTPException(403, "admin_two_factor_required")


def _not_set_by_environment(d):
    """(#999) A server setting whose variable is set in the server's environment can't be changed here: the
    environment's value is the one in force, and a value saved here would be a promise that isn't kept."""
    if d.env and config.source(d.env) == "environment":
        raise HTTPException(409, f"“{d.label}” is set by the server's environment ({d.env}), so it can't be changed here. "
                                 f"To change it here, take {d.env} out of where the server is set up (.env, or the app's "
                                 "settings on TrueNAS or Unraid) and start it again.")


def _held(db, d):
    """(#1006) What an install setting holds in the app: its saved value, else its default (the one in force can be the
    environment's instead, or the default while saved values are left out)."""
    return settings.stored(db, "install").get(d.key, d.default)


def _values(d, old, new):
    """(#1006) The old and new value for the install log: by name only for a setting that could hold health
    information (private); none of the install's does, and none is a secret."""
    return {} if d.private else {"from": old, "to": new}


def _said(v):
    return str("" if v is None else v).replace(",", "").strip()


def _confirmed(db, request, s, d, value, typed):
    """(#998) One rule for an install setting's change that must be confirmed first: its definition's confirm
    (settings.define(confirm=...): fewer days of activity kept, #1002; a shorter sign-in, #1001; the public addresses,
    #1000) says what the change does, confirm(db, before, after, request, s) -> None when there's nothing to ask, else
    {"count": how many it touches, "message": what it does, in words, "type": what's typed to confirm (the count
    unless it says), "title", "action": the question's and its red button's words, ...what more the page shows}; it may
    refuse the change outright instead (HTTPException), as the public addresses do a list the page's own address
    wouldn't pass. The change is made only with that typed back ("confirm", on a change or a reset alike); else
    refused, 409 {"code": "confirm", "message", "confirm": {...}}, for the page to ask with (admin.js confirmed()).
    Asked at this moment: a count grown since it was shown is asked again. Asked too where the value doesn't apply yet
    (#1042: saved values left out, SANGUITY_SAVED_SETTINGS=off, or the environment's in force, for a reset), from the
    value that applies once it does, the one saved here: else it would take effect then, unasked."""
    if not d.confirm:
        return
    later = config.saved_ignored() or (d.env and config.source(d.env) == "environment")
    before = _held(db, d) if later or not d.env else settings.in_force(d.key)[0]
    goes = d.confirm(db, before, value, request, s)
    if not goes:
        return
    goes = {"type": str(goes["count"]), **goes}
    if later:
        goes["message"] += (" Saved settings are left out while SANGUITY_SAVED_SETTINGS=off is set: this takes effect once it's taken out."
                            if config.saved_ignored() else f" The server's environment sets {d.env}: this takes effect if it's taken out.")
    if _said(typed) != _said(goes["type"]):
        raise HTTPException(409, {"code": "confirm", "message": f"{goes['message']} Type {goes['type']} to confirm.", "confirm": goes})


def _save_install(db, s, d, body, request, now):
    """(#1006) A change to an install setting, from either route: refused if the value isn't one it takes (400), nothing
    to do if it holds that one already, else the fresh check (403 reauth_required), then saved and logged with the old
    value and the new. now(): the value as a page shows it, for "was" (#923)."""
    try:
        value = d.clean(body.value)
    except settings.SettingError as e:
        raise HTTPException(400, str(e))
    if value == _held(db, d):
        return
    _confirmed(db, request, s, d, value, body.confirm)   # (#998) said and confirmed first, before the fresh check
    fresh(request, s, "change a server setting")
    with dbm.transaction(db):
        _unchanged(body, now(), d)
        old = _held(db, d)
        settings.put(db, d.key, value, by=s["id"])
        _log(db, s, None, "install", {"setting": d.key, "label": d.label, **_values(d, old, value)}, request)


def _ends_sign_ins(db, before, value, request, s, *, d):
    """(#1001) A shorter sign-in or idle sign-out ends the sign-ins it leaves too old or unused, at their next request:
    while it would end any, it's asked first (_confirmed) with how many, and whether this one is among them. (A reset
    never shortens one: their defaults are their longest.)"""
    n, yours = main.sessions_ended(db, {d.env: value}, s["sid"])
    if not n:
        return None
    return {"count": n, "yours": yours, "title": f"End {n} sign-in{'' if n == 1 else 's'}?",
            "action": "End it" if n == 1 else "End them",
            "message": f"{'1 sign-in is' if n == 1 else f'{n} sign-ins are'} older, or unused for longer, than this change "
                       f"allows: {'it ends' if n == 1 else 'each ends'} at its next request, and its person signs in again."
                       + (" Yours here is one of them: you'll be signed out too." if yours else "")}


for _d in settings.SETTINGS.values():   # (#1001) each sign-in length's change, asked first where it ends sign-ins
    if _d.env in main.SIGN_IN_LENGTHS:
        _d.confirm = functools.partial(_ends_sign_ins, d=_d)


def _reset_install(db, s, defs, request, section=None, label=None, confirm=None):
    """(#1006) Install settings back to their defaults: the fresh check when one has a saved value (403
    reauth_required); logged with what each held and goes back to. Returns the keys that had one."""
    have = settings.stored(db, "install")
    if not any(d.key in have for d in defs):
        return []
    for d in defs:   # (#998) a default that must be confirmed: asked as a change to it is
        if d.key in have:
            _confirmed(db, request, s, d, d.default, confirm)
    fresh(request, s, "reset a server setting")
    with dbm.transaction(db):
        have = settings.stored(db, "install")
        gone = settings.clear(db, [d.key for d in defs], "install")
        changes = [{"setting": d.key, "label": d.label, **_values(d, have.get(d.key), d.default)} for d in defs if d.key in gone]
        if section and gone:
            _log(db, s, None, "install", {"section": section, "label": label, "settings": gone, "reset": True, "changes": changes}, request)
        elif gone:
            _log(db, s, None, "install", {**changes[0], "reset": True}, request)
    return gone


def _log(db, s, pid, scope, detail, request):
    if scope == "profile":
        activity.log(db, "settings", s, pid, detail, _client_ip(request))
    else:
        activity.log(db, f"settings.{scope}", s, None, detail, _client_ip(request))


@router.get("/api/profiles/{pid}/settings")
def merged(pid: int, s=Depends(user), db=Depends(get_db)):
    """Every setting's value for this person on this profile, nested as settings.json was (what the page reads)."""
    need(db, s, pid, "viewer")
    return settings.merged(db, pid, s["id"], s["is_admin"])


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
            if d.section != sid or not d.screen or not settings.shown_to(k, s["is_admin"]):   # not d.screen: a page's own
                continue   # (the guide's, #199); not shown_to: an admin's alone (#998)
            v, src = eff[k]
            items.append({**d.public(), "value": v, "from": src, "can_change": settings.may_change(d.scope, role, s["is_admin"])})
        if items:
            out.append({"id": sid, "title": title, "help": help, "settings": items})
    return {"role": role, "sections": out}


class Value(BaseModel):
    value: Any
    # (#923) the value as the page read it, its version: sent with a whole value (a list, a map) that the page made
    # from it, so a change made since, in another tab or by someone else, isn't lost under it
    was: Any = None
    confirm: Any = None   # (#998) what's typed to confirm a change that's asked first (_confirmed)


def _unchanged(body, now, d):
    """(#923) Refuse (409) a change sent with the value it was read at ("was") when the setting holds another now: the
    page shows it as it is (it reads it again) and the change is made again on that. Call inside the transaction."""
    if "was" in body.model_fields_set and body.was != now:
        raise HTTPException(409, f"“{d.label}” was changed since this page read it, in another tab or by someone else. "
                                 "It's shown as it is now: make your change again.")


@router.put("/api/profiles/{pid}/settings/{key}")
def change(pid: int, key: str, body: Value, request: Request, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    _check(db, s, pid, d, role)
    _not_set_by_environment(d)
    if d.scope == "install":
        _save_install(db, s, d, body, request, lambda: settings.effective(db, pid, s["id"])[key][0])
        return settings.merged(db, pid, s["id"], s["is_admin"])
    try:
        with dbm.transaction(db):
            _unchanged(body, settings.effective(db, pid, s["id"])[key][0], d)
            settings.put(db, key, body.value, pid=pid, uid=s["id"], by=s["id"])
            _log(db, s, pid, d.scope, {"setting": key, "label": d.label}, request)
    except settings.SettingError as e:
        raise HTTPException(400, str(e))
    return settings.merged(db, pid, s["id"], s["is_admin"])


class Reset(BaseModel):
    confirm: Any = None   # (#998) as Value's: an install setting's reset that's asked first (_confirmed)


@router.delete("/api/profiles/{pid}/settings/{key}")
def reset_one(pid: int, key: str, request: Request, body: Reset | None = None, s=Depends(user), db=Depends(get_db)):
    """Back to the default (or to the value under it, an install-wide one)."""
    role = need(db, s, pid, "viewer")
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    _check(db, s, pid, d, role)
    if d.scope == "install":
        _reset_install(db, s, [d], request, confirm=body and body.confirm)
        return settings.merged(db, pid, s["id"], s["is_admin"])
    with dbm.transaction(db):
        if settings.clear(db, [key], d.scope, pid, s["id"]):
            _log(db, s, pid, d.scope, {"setting": key, "label": d.label, "reset": True}, request)
    return settings.merged(db, pid, s["id"], s["is_admin"])


class Section(BaseModel):
    section: str
    confirm: Any = None   # (#998) as Value's


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
    reset = _reset_install(db, s, [d for d in allowed if d.scope == "install"], request, body.section, titles[body.section], body.confirm)
    with dbm.transaction(db):
        for scope in ("profile", "user"):
            gone = settings.clear(db, [d.key for d in allowed if d.scope == scope], scope, pid, s["id"])
            if gone:
                _log(db, s, pid, scope, {"section": body.section, "label": titles[body.section], "settings": gone, "reset": True}, request)
            reset += gone
    return {"reset": reset, "kept": [d.key for d in mine if d not in allowed], "settings": settings.merged(db, pid, s["id"], s["is_admin"])}


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


class UserValue(Value):
    # the browser's own, filled in by the first page after signing in (dates.js datesReady): stored only if this
    # person hasn't one yet, and not logged, as they changed nothing (#524: each first sign-in logged "changed their
    # own setting Time zone")
    if_unset: bool = False


@router.put("/api/settings/user/{key}")
def change_user_value(key: str, body: UserValue, request: Request, s=Depends(user), db=Depends(get_db)):
    try:
        d = settings.get_def(key)
    except settings.SettingError as e:
        raise HTTPException(404, str(e))
    if d.scope != "user":
        raise HTTPException(404, "That setting belongs to a profile.")
    try:
        with dbm.transaction(db):
            if body.if_unset:
                d.clean(body.value)   # checked all the same
                if key not in settings.stored(db, "user", uid=s["id"]):
                    settings.put(db, key, body.value, uid=s["id"], by=s["id"])
            else:
                _unchanged(body, settings.effective(db, None, s["id"])[key][0], d)
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
# A setting that stands for a variable (#999) says which ("env"), and its value is the one in force: "from" is
# "environment" when the variable is set, and then it can't be changed here ("can_change" false, a change refused, 409).
# "variables": every variable of config.VARS, its value in force, where it comes from and its default, and the
# setting that stands for it, if one does (none of them is a secret).

def _install_item(d, have):
    if d.env:   # (#1006) "saved": a value saved here that isn't the one in force (the environment's is, or it's left out)
        value, src = settings.in_force(d.key)
        return {**d.public(), "value": value, "from": src, "env": d.env, "can_change": src != "environment",
                **({"saved": have[d.key]} if d.key in have and src != "install" else {})}
    return {**d.public(), "value": have.get(d.key, d.default), "from": "install" if d.key in have else "default", "can_change": True}


def _variables():
    out = {}
    for name, v in config.VARS.items():
        src, value = config.lookup(name)
        out[name] = {"value": list(value) if isinstance(value, tuple) else value, "from": src,
                     "default": list(v.default) if isinstance(v.default, tuple) else v.default, "setting": v.setting}
    return out


def _install_sections(db):
    have = settings.stored(db, "install")
    out = []
    for sid, title, help in settings.SECTIONS:
        items = [_install_item(d, have) for d in settings.SETTINGS.values() if d.section == sid and d.scope == "install"]
        if items:
            out.append({"id": sid, "title": title, "help": help, "settings": items})
    return {"sections": out, "variables": _variables(), "saved_ignored": config.saved_ignored()}


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
    _not_set_by_environment(d)
    _save_install(db, s, d, body, request, lambda: _install_item(d, settings.stored(db, "install"))["value"])
    return _install_sections(db)


@router.post("/api/settings/install/reset")
def reset_install_section(body: Section, request: Request, s=Depends(admin), db=Depends(get_db)):
    """Back to the defaults: every install-wide setting of the section."""
    titles = {sid: title for sid, title, _ in settings.SECTIONS}
    defs = [d for d in settings.SETTINGS.values() if d.section == body.section and d.scope == "install"]
    if not defs:
        raise HTTPException(404, "There's no such section.")
    gone = _reset_install(db, s, defs, request, body.section, titles[body.section], body.confirm)
    return {"reset": gone, **_install_sections(db)}


@router.get("/api/settings/install/backups-logging")
def install_backups_logging(s=Depends(admin)):
    """(#1002) General → Backups and Logging: the backups folder, the key, where the logs are read (general.py)."""
    return general.backups_logging()


@router.get("/api/settings/install/about")
def install_about(s=Depends(admin)):
    """(#1004) General → About: the version, the deployment, the folders, the limits (general.py). Contacts nobody."""
    return general.about()


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
    return settings.merged(db, pid, s["id"], s["is_admin"])
