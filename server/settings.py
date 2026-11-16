"""Settings: the owner preferences that used to be a settings.json per profile, kept in the database (#35).

The registry (SETTINGS, filled by define()) says, for each setting, its key, where its value lives (scope), what it
holds and how a value is checked, its default, and which section of the Settings screen shows it. The API checks
every change against it, and the page reads the merged result (merged) exactly as it read settings.json.

  scope "profile"   one value per profile; owners and editors change it
        "user"      one value per account, the same on every profile; each person changes their own
        "install"   one value for the whole install; admins change it

A value is looked up from the broadest level to the narrowest, each overriding the one before: the default, then
the install's value, then the profile's, then the user's. A setting is changed only at its own scope; a broader
row (an install-wide value for a profile setting) acts as a default under it, which an import or a later issue may
use. Values are JSON; a setting that can hold health information (lab names, rules) has its value encrypted
(crypto.enc) in the "sealed" column, others are plain in "value".

Adding a setting (#36-#46): one define() call below, with its section (add one to SECTIONS if it's new), and an
editor for its kind on the Settings screen (www/js/settings.js; "labs", "tiles", "names", "rules", "tabs" and
"theme" exist).

settings.json, before schema version 18: each profile's copy (profiles/<id>/settings.json) is loaded into the
table once at start-up (migrate_files) and kept beside it as settings-before-v18.json. A setup build's settings.json
only seeds a linked profile's settings the first time the build is loaded into it, filling what isn't set; after
that a rebuild never changes settings, and says so in the log when the build's settings.json differs from them.
"""
import json, os, re

from . import config, crypto, db as dbm

SCOPES = ("install", "profile", "user")   # broadest first: the order values are layered in


class SettingError(ValueError):
    """A value the setting doesn't accept, said in plain language."""


class Setting:
    def __init__(self, key, section, label, default, check, scope="profile", private=True, kind=None, help="",
                 merge=False):
        self.key, self.section, self.label, self.default, self.check = key, section, label, default, check
        self.scope, self.private, self.kind, self.help, self.merge = scope, private, kind, help, merge

    def clean(self, value):
        return self.check(value)

    def public(self):
        return {"key": self.key, "section": self.section, "label": self.label, "help": self.help, "kind": self.kind,
                "scope": self.scope, "default": self.default, **({"choices": CHOICES[self.key]} if self.key in CHOICES else {})}


SECTIONS = []      # [(id, title, help)], in screen order
SETTINGS = {}      # key -> Setting, in screen order


def section(sid, title, help=""):
    SECTIONS.append((sid, title, help))


def define(key, section, label, default, check, **kw):
    """Add a setting to the registry. key may be dotted ("overview.keyLabs"): it nests in the merged object."""
    if key in SETTINGS:
        raise ValueError(f"setting {key} is defined twice")
    if section not in {s for s, _, _ in SECTIONS}:
        raise ValueError(f"setting {key}: no section {section}")
    if kw.get("scope", "profile") not in SCOPES:
        raise ValueError(f"setting {key}: scope must be one of {SCOPES}")
    s = SETTINGS[key] = Setting(key, section, label, check(default), check, **kw)
    return s


def get_def(key):
    s = SETTINGS.get(key)
    if s is None:
        raise SettingError(f"There's no setting called {key}.")
    return s


# ---------------------------------------------------------------- checks (each returns the cleaned value)

def _text(v, what, most=120):
    if not isinstance(v, str) or not v.strip():
        raise SettingError(f"{what} must be some text.")
    v = " ".join(v.split())
    if len(v) > most:
        raise SettingError(f"{what} is too long (at most {most} characters).")
    return v


def lab_list(most=50):
    def check(v):
        if not isinstance(v, list):
            raise SettingError("This must be a list of lab names.")
        out = []
        for x in v:
            x = _text(x, "A lab name")
            if x not in out:
                out.append(x)
        if len(out) > most:
            raise SettingError(f"At most {most} labs.")
        return out
    return check


def name_map(most=500):
    def check(v):
        if not isinstance(v, dict):
            raise SettingError("This must be a list of names, each with the name to show instead.")
        out = {}
        for k, x in v.items():
            out[_text(k, "A name")] = _text(x, f"The name shown for “{k}”")
        if len(out) > most:
            raise SettingError(f"At most {most} names.")
        return out
    return check


def workout_rules(most=50):
    def check(v):
        if not isinstance(v, list):
            raise SettingError("This must be a list of rules.")
        out = []
        for r in v:
            if not isinstance(r, dict):
                raise SettingError("Each rule needs a pattern, a name and minutes.")
            match = r.get("match")
            if not isinstance(match, str) or not match.strip() or len(match) > 200:
                raise SettingError("Each rule needs a pattern to match visit titles (at most 200 characters).")
            try:
                re.compile(match, re.I)
            except re.error as e:
                raise SettingError(f"The pattern “{match}” isn't a valid regular expression ({e}).") from None
            name = _text(r.get("name") or match, "A rule's name", 80)
            minutes = r.get("minutes", 30)
            if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 600 or minutes != int(minutes):
                raise SettingError(f"Minutes for “{name}” must be a whole number from 1 to 600.")
            out.append({"match": match, "name": name, "minutes": int(minutes)})
        if len(out) > most:
            raise SettingError(f"At most {most} rules.")
        return out
    return check


def whole(lo, hi, what):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not lo <= v <= hi:
            raise SettingError(f"{what} must be a whole number from {lo} to {hi}.")
        return int(v)
    return check


def yes_no(what):
    def check(v):
        if not isinstance(v, bool):
            raise SettingError(f"{what} must be on or off.")
        return v
    return check


def some_of(choices, what):
    """A list of some of choices ({id: label}), in their order (possibly none)."""
    def check(v):
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise SettingError(f"{what} must be a list.")
        bad = [x for x in v if x not in choices]
        if bad:
            raise SettingError(f"{what}: there's no choice called {bad[0]}.")
        return [c for c in choices if c in v]
    return check


# ---------------------------------------------------------------- the registry: every key of settings.example.json

KEY_LABS = ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Lymphocytes Absolute", "Creatinine",
            "ALT", "AST", "Total Bilirubin", "Potassium, Blood", "Sodium, Blood", "Magnesium"]

section("overview", "Overview", "The lab tiles at the top of the Overview tab.")
section("trends", "Trends", "What the Trends tab shows before you pick anything.")
section("names", "Names", "Shorter or clearer names shown instead of the ones in the records.")
section("health", "Health", "How the Health tab counts activity.")

define("overview.keyLabs", "overview", "Key labs", KEY_LABS, lab_list(), kind="tiles",
       help="One tile each, in this order, for the labs this profile has results for. While this is the default list "
            "and few of its labs have results here, the most-measured labs fill the tiles instead (#36).")
define("trends.defaultLabs", "trends", "Labs selected at first",
       ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Creatinine", "ALT"], lab_list(), kind="labs",
       help="Charted on Trends until you choose others on this device.")
define("labNicknames", "names", "Lab names", {
           "Automated Abs Neutrophil Cnt": "Neutrophils (absolute)", "Lymphocytes Absolute": "Lymphocytes (absolute)",
           "Platelet Count": "Platelets", "Potassium, Blood": "Potassium", "Sodium, Blood": "Sodium",
           "Total Bilirubin": "Bilirubin, total"},
       name_map(), kind="names", merge=True, help="A lab's name in the records, and the name to show for it.")
define("sources", "names", "Result sources", {}, name_map(), kind="names", merge=True,
       help="The short source tag on lab results (like EXT), and the name to show for it.")
ATTACHMENT_TYPES = {"pdf": "PDF", "image": "Images", "text": "Text", "html": "Web pages (HTML)"}
CEILING_MB = max(1, config.SYNC_MAX_ATTACHMENT // config.MB)   # SYNC_MAX_ATTACHMENT_MB: no setting goes above it

section("sync", "Connected sources", "Defaults for every connection on this server (each connection can change its "
        "own on the Connected sources page). Attachments are the files records point at: visit notes, scans, reports.")
define("sync.attachAuto", "sync", "Fetch attachments automatically", True, yes_no("Fetching attachments automatically"),
       scope="install", private=False, kind="toggle",
       help="Off: each sync lists the attachments it left out, and you fetch the ones you want.")
define("sync.attachMaxMB", "sync", "Largest attachment fetched (MB)", min(20, CEILING_MB),
       whole(1, CEILING_MB, "The largest attachment"), scope="install", private=False, kind="number",
       help=f"Bigger ones are left out and listed. At most {CEILING_MB} MB (SYNC_MAX_ATTACHMENT_MB in .env).")
define("sync.attachTypes", "sync", "Kinds of attachment fetched", list(ATTACHMENT_TYPES),
       some_of(ATTACHMENT_TYPES, "Kinds of attachment"), scope="install", private=False, kind="choices",
       help="Other kinds (and these, when unticked) are left out and listed, to fetch if you want them.")
define("sync.attachBudgetMB", "sync", "Attachments per connection, in all (MB)", 2048,
       whole(1, 1024 * 1024, "The total for attachments"), scope="install", private=False, kind="number",
       help="Once a connection's attachments add up to this, the rest are left out and listed.")
define("sync.staleDays", "sync", "Warn when a connection hasn't synced for (days)", 3,
       whole(1, 365, "The number of days"), scope="install", private=False, kind="number",
       help="The profile's main page and the admins' notices say so, with a link to the connection.")
CHOICES = {"sync.attachTypes": ATTACHMENT_TYPES}

define("visitWorkouts", "health", "Visits that count as workouts", [], workout_rules(), kind="rules",
       help="Past visits whose title matches a pattern count as a workout of that many minutes, once a day per rule. "
            "Patterns are regular expressions, matched ignoring case.")


# ---------------------------------------------------------------- tabs (#40) and theme (#45)

TABS = ("overview", "health", "timeline", "labs", "heatmap", "costs")   # the main tabs' ids ("labs" is Trends)
TAB_NAMES = {"overview": "Overview", "health": "Health", "timeline": "Timeline", "labs": "Trends", "heatmap": "Lab heatmap",
             "costs": "Costs"}


def tab_layout(v):
    """{"order": [tab ids], "hidden": [tab ids]}: every tab in order (one left out goes at the end, in the default
    order, so a tab added later shows up), the hidden ones in that order. At least one tab stays shown."""
    if not isinstance(v, dict) or set(v) - {"order", "hidden"}:
        raise SettingError("This must give the tabs' order and which are hidden.")
    lists = {}
    for part in ("order", "hidden"):
        x = v.get(part, [])
        if not isinstance(x, list) or not all(isinstance(t, str) for t in x):
            raise SettingError("The tabs must be a list of tab names.")
        for t in x:
            if t not in TABS:
                raise SettingError(f"There's no tab called “{t[:40]}”.")
        lists[part] = x
    order = list(dict.fromkeys(lists["order"])) + [t for t in TABS if t not in lists["order"]]
    hidden = [t for t in order if t in set(lists["hidden"])]
    if len(hidden) == len(order):
        raise SettingError("At least one tab must stay shown.")
    return {"order": order, "hidden": hidden}


THEMES = ("system", "light", "dark")


def theme(v):
    if v not in THEMES:
        raise SettingError("The theme must be light, dark, or follow the system.")
    return v


section("tabs", "Tabs", "Which tabs this profile shows, and in what order.")
section("appearance", "Appearance", "How Sanguity looks for you, on every device you sign in on.")

define("tabs", "tabs", "Tabs", {"order": list(TABS), "hidden": []}, tab_layout, kind="tabs", private=False,
       help="A hidden tab leaves the tab bar and the arrow keys; a saved view or link to it opens the first shown tab.")
define("theme", "appearance", "Theme", "system", theme, scope="user", kind="theme", private=False,
       help="Your default on every device. The ◐ button on the records page still picks light or dark for one browser.")


# ---------------------------------------------------------------- storage

def _where(scope, pid, uid):
    if scope == "install":
        return "profile_id IS NULL AND user_id IS NULL", ()
    if scope == "profile":
        return "profile_id=? AND user_id IS NULL", (pid,)
    return "profile_id IS NULL AND user_id=?", (uid,)


def _owner(scope, pid, uid):
    return (pid if scope == "profile" else None), (uid if scope == "user" else None)


def _load(row):
    return json.loads(crypto.dec(row["sealed"]) if row["sealed"] is not None else row["value"])


def put(db, key, value, scope=None, pid=None, uid=None, by=None):
    """Store a value (checked and cleaned first; raises SettingError) at scope, by default the setting's own.
    Returns the cleaned value."""
    s = get_def(key)
    scope = scope or s.scope
    value = s.clean(value)
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    p, u = _owner(scope, pid, uid)
    db.execute("""INSERT INTO settings(key, profile_id, user_id, value, sealed, changed, changed_by) VALUES(?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(key, IFNULL(profile_id, 0), IFNULL(user_id, 0))
                  DO UPDATE SET value=excluded.value, sealed=excluded.sealed, changed=excluded.changed, changed_by=excluded.changed_by""",
               (key, p, u, None if s.private else text, crypto.enc(text) if s.private else None, dbm.now(), by))
    return value


def clear(db, keys, scope, pid=None, uid=None):
    """Remove the values of keys at scope (back to what's under them). Returns the keys that had one."""
    where, args = _where(scope, pid, uid)
    gone = [r["key"] for r in db.execute(f"SELECT key FROM settings WHERE {where} AND key IN (SELECT value FROM json_each(?))",
                                         (*args, json.dumps(list(keys))))]
    db.execute(f"DELETE FROM settings WHERE {where} AND key IN (SELECT value FROM json_each(?))", (*args, json.dumps(gone)))
    return gone


def stored(db, scope, pid=None, uid=None):
    """{key: value} set at exactly this scope (keys no longer in the registry, or unreadable, left out)."""
    where, args = _where(scope, pid, uid)
    out = {}
    for r in db.execute(f"SELECT key, value, sealed FROM settings WHERE {where}", args):
        if r["key"] in SETTINGS:
            try:
                out[r["key"]] = _load(r)
            except (ValueError, TypeError):
                continue
    return out


def effective(db, pid, uid):
    """{key: (value, where it came from: "default" or a scope)} for every setting, as seen by user uid on profile pid."""
    out = {k: (s.default, "default") for k, s in SETTINGS.items()}
    for scope in SCOPES:
        for k, v in stored(db, scope, pid, uid).items():
            out[k] = (v, scope)
    return out


def nest(flat):
    """{"overview.keyLabs": [...]} -> {"overview": {"keyLabs": [...]}}"""
    out = {}
    for k, v in flat.items():
        *path, last = k.split(".")
        d = out
        for p in path:
            d = d.setdefault(p, {})
        d[last] = v
    return out


def install_values(db, prefix=""):
    """{key: value} of the install-wide settings (the install's value, else the default)."""
    have = stored(db, "install")
    return {k: have.get(k, s.default) for k, s in SETTINGS.items() if s.scope == "install" and k.startswith(prefix)}


def merged(db, pid, uid):
    """What GET /api/profiles/{pid}/settings returns: the object settings.json used to be, every key filled in."""
    return nest({k: v for k, (v, _) in effective(db, pid, uid).items()})


def may_change(scope, role, is_admin):
    """Whether someone with role on the profile (and the admin flag) may change a setting of this scope."""
    return {"profile": role in ("owner", "editor"), "user": role is not None, "install": bool(is_admin)}[scope]


# ---------------------------------------------------------------- settings.json files (migration, setup build, exports)

def _dig(obj, key):
    for p in key.split("."):
        if not isinstance(obj, dict) or p not in obj:
            return None, False
        obj = obj[p]
    return obj, True


FORMAT = "sanguity-settings/2"   # "_format" in an export's settings.json: its name maps are whole, not additions


def from_file(obj, scope="profile", where=""):
    """The values a settings.json (the shape of settings.example.json) holds for settings of this scope, cleaned.
    In a file written by hand (or before #35) a name map adds to the default names, as it did when the page read
    the file; in an export's (to_file, marked with _format) it is the whole map. A value the registry refuses is
    left out, with a line in the log."""
    out = {}
    if not isinstance(obj, dict):
        print(f"{where or 'settings.json'} isn't a JSON object; its settings are left out.", flush=True)
        return out
    whole = obj.get("_format") == FORMAT
    for k, s in SETTINGS.items():
        if s.scope != scope:
            continue
        v, there = _dig(obj, k)
        if not there:
            continue
        if s.merge and not whole and isinstance(v, dict):
            v = {**s.default, **v}
        try:
            out[k] = s.clean(v)
        except SettingError as e:
            print(f"{where or 'settings.json'}: {k} left at its default ({e})", flush=True)
    return out


def read_file(path):
    return json.loads(crypto.read_file(path))


def to_file(db, pid):
    """A profile's own values, nested like settings.json, for "Export everything" (only _format when it has none)."""
    return {"_format": FORMAT, **nest(stored(db, "profile", pid))}


def replace_profile(db, pid, values, by=None):
    """Make the profile's own values exactly these (an export loaded back in): the rest go back to the defaults."""
    clear(db, [k for k, s in SETTINGS.items() if s.scope == "profile"], "profile", pid)
    for k, v in values.items():
        put(db, k, v, "profile", pid, by=by)


def fill_profile(db, pid, values):
    """Set the values the profile doesn't have one for yet. Returns the keys set."""
    have = stored(db, "profile", pid)
    new = [k for k in values if k not in have]
    for k in new:
        put(db, k, values[k], "profile", pid)
    return new


MIGRATED = "settings-before-v18.json"


def migrate_files(db, profile_dir):
    """At start-up: load each profile's settings.json, from before schema version 18, into the table (only what isn't
    set, so a start that stopped half way does no harm), then keep the file beside it as settings-before-v18.json.
    profile_dir(pid) is the profile's folder. Returns the number of profiles whose file was loaded."""
    n = 0
    for r in db.execute("SELECT id FROM profiles ORDER BY id").fetchall():
        pid = r["id"]
        path = os.path.join(profile_dir(pid), "settings.json")
        if not os.path.isfile(path):
            continue
        try:
            obj = read_file(path)
        except Exception as e:   # left where it is, for someone to look at; the app still starts
            print(f"Couldn't move profile {pid}'s settings.json into the database ({type(e).__name__}: {e}); "
                  f"{path} is left as it was.", flush=True)
            continue
        with dbm.transaction(db):
            fill_profile(db, pid, from_file(obj, where=f"profile {pid}'s settings.json"))
            db.execute("UPDATE profiles SET settings_seeded=1 WHERE id=?", (pid,))
        os.replace(path, os.path.join(profile_dir(pid), MIGRATED))
        n += 1
    return n


def from_build(db, pid, path):
    """The setup build was just loaded into linked profile pid (inside import_dataset's transaction). The first time,
    its settings.json (if any) fills the profile's unset settings; later, it changes nothing, but a settings.json that
    differs from the profile's settings is reported, so a rebuild never overrides the app's settings silently."""
    seeded = db.execute("SELECT settings_seeded FROM profiles WHERE id=?", (pid,)).fetchone()["settings_seeded"]
    values = None
    if os.path.isfile(path):
        try:
            values = from_file(read_file(path), where="the setup build's settings.json")
        except (ValueError, OSError) as e:
            print(f"The setup build's settings.json can't be read ({type(e).__name__}: {e}); settings unchanged.", flush=True)
    if not seeded:
        if values:
            fill_profile(db, pid, values)
        db.execute("UPDATE profiles SET settings_seeded=1 WHERE id=?", (pid,))
        return
    if values is not None:
        have = {k: v for k, (v, _) in effective(db, pid, None).items() if SETTINGS[k].scope == "profile"}
        differ = sorted(k for k, v in values.items() if have.get(k) != v)
        if differ:
            print(f"The setup build's settings.json differs from profile {pid}'s settings ({', '.join(differ)}) and "
                  "wasn't applied: settings are changed in the app now (Settings page). Remove settings.json from the "
                  "setup, or change them there.", flush=True)
