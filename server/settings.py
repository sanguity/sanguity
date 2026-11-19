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
editor for its kind on the Settings screen (www/js/settings.js; "labs", "tiles", "names", "rules", "tabs",
"theme" and "units" exist).

settings.json, before schema version 18: each profile's copy (profiles/<id>/settings.json) is loaded into the
table once at start-up (migrate_files) and kept beside it as settings-before-v18.json. A setup build's settings.json
only seeds a linked profile's settings the first time the build is loaded into it, filling what isn't set; after
that a rebuild never changes settings, and says so in the log when the build's settings.json differs from them.
"""
import datetime, json, os, re

from . import config, crypto, db as dbm

SCOPES = ("install", "profile", "user")   # broadest first: the order values are layered in


class SettingError(ValueError):
    """A value the setting doesn't accept, said in plain language."""


class Setting:
    def __init__(self, key, section, label, default, check, scope="profile", private=True, kind=None, help="",
                 merge=False, reread=False, screen=True):
        self.key, self.section, self.label, self.default, self.check = key, section, label, default, check
        self.scope, self.private, self.kind, self.help, self.merge = scope, private, kind, help, merge
        self.reread = reread   # a stored value is checked again when read: its check gives older values in today's terms
        self.screen = screen   # shown on the Settings screen (False: kept for a page of its own, like the guide's, #199)

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


# ---------------------------------------------------------------- #43 visits counted as workouts: rule checks
# A rule matches visit titles either as plain text (anywhere in the title, ignoring case: the default) or, as an
# advanced option, as a regular expression. Both the server and the page (www/js/health.js) run the pattern, so a
# regular expression is held to a small, safe subset that means the same in Python and JavaScript, and that can't
# backtrack catastrophically: short; few quantifiers, and at most two that repeat more than once (*, +, {2,}...),
# since even side by side, as in a*a*a*b, each one more multiplies the work on a title that doesn't match; no
# quantifier around something that is itself repeated or has alternatives (the "(a+)+" shape); no backreferences,
# lookarounds or inline flags. And only a title's first TITLE_MAX characters are matched, which bounds what's left.

PATTERN_MAX, REGEX_MAX, REGEX_REPEATS, REGEX_WIDE, REGEX_BOUND, TITLE_MAX = 200, 100, 8, 2, 100, 200
_REGEX_SIGNS = re.compile(r"[\\^$.|?*+()\[\]{}]")


def safe_regex(pattern):
    """Raise SettingError unless pattern is a valid regular expression of the safe subset described above."""
    from re import _constants as C, _parser as P
    if len(pattern) > REGEX_MAX:
        raise SettingError(f"A regular expression can be at most {REGEX_MAX} characters.")
    try:
        tree = P.parse(pattern)
    except re.error as e:
        raise SettingError(f"“{pattern}” isn't a valid regular expression ({e}).") from None
    if tree.state.groupdict or "(?#" in pattern:   # written differently in JavaScript, or not at all
        raise SettingError("Regular expressions here can't use named groups or (?#…) comments.")
    if tree.state.flags != P.parse("").state.flags:   # (?i) and the like at the start
        raise SettingError("Regular expressions here can't set flags; matching always ignores case.")
    repeats = wide = 0
    REPEAT = {C.MAX_REPEAT, C.MIN_REPEAT}
    BANNED = {C.GROUPREF, C.GROUPREF_EXISTS, C.ASSERT, C.ASSERT_NOT, C.ATOMIC_GROUP, C.POSSESSIVE_REPEAT}

    def complex_(items):   # does this part repeat, or have alternatives, inside?
        for op, av in items:
            if op in REPEAT and av[1] > 1 or op == C.BRANCH:
                return True
            if op == C.SUBPATTERN and complex_(av[3]):
                return True
        return False

    def walk(items):
        nonlocal repeats, wide
        for op, av in items:
            if op in BANNED:
                raise SettingError("Regular expressions here can't use backreferences, lookarounds (?=…), atomic groups "
                                   "or possessive repeats (++).")
            if op == C.AT and av in (C.AT_BEGINNING_STRING, C.AT_END_STRING):
                raise SettingError("Use ^ and $ for the start and end of the title (\\A and \\Z aren't supported).")
            if op in REPEAT:
                lo, hi, body = av
                repeats += 1
                if repeats > REGEX_REPEATS:
                    raise SettingError(f"A regular expression can repeat at most {REGEX_REPEATS} things (*, +, ? or {{…}}).")
                if hi != C.MAXREPEAT and hi > REGEX_BOUND or lo > REGEX_BOUND:
                    raise SettingError(f"A repeat count can be at most {REGEX_BOUND}.")
                wide += hi > 1
                if wide > REGEX_WIDE:
                    raise SettingError(f"A regular expression can have at most {REGEX_WIDE} repeats that match more "
                                       "than once (*, + or {…}): more can make matching very slow.")
                if hi > 1 and complex_(body):
                    raise SettingError("A repeat around something that itself repeats or has alternatives, like (a+)+ "
                                       "or (a|b)*, can make matching very slow, so it isn't allowed.")
                walk(body)
            elif op == C.SUBPATTERN:
                if av[1] or av[2]:
                    raise SettingError("Regular expressions here can't set flags; matching always ignores case.")
                walk(av[3])
            elif op == C.BRANCH:
                for b in av[1]:
                    walk(b)
    walk(tree)
    return pattern


def workout_rules(most=50):
    def check(v):
        if not isinstance(v, list):
            raise SettingError("This must be a list of rules.")
        out = []
        for r in v:
            if not isinstance(r, dict):
                raise SettingError("Each rule needs the text visit titles match, a name and minutes.")
            match = r.get("match")
            if not isinstance(match, str) or not match.strip() or len(match) > PATTERN_MAX:
                raise SettingError(f"Each rule needs the text visit titles match (at most {PATTERN_MAX} characters).")
            # a rule from before #43 has no "regex": its pattern was always a regular expression (one without any
            # of their signs means the same as plain text, so it's shown as plain text)
            regex = r.get("regex")
            if regex is None:
                regex = bool(_REGEX_SIGNS.search(match))
            if not isinstance(regex, bool):
                raise SettingError("“Regular expression” must be on or off.")
            if regex:
                safe_regex(match)
            else:
                match = " ".join(match.split())
            name = _text(r.get("name") or match, "A rule's name", 80)
            minutes = r.get("minutes", 30)
            if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 600 or minutes != int(minutes):
                raise SettingError(f"Minutes for “{name}” must be a whole number from 1 to 600.")
            out.append({"match": match, "regex": regex, "name": name, "minutes": int(minutes)})
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


def rule_matches(rule, title):
    """Whether a visit title (its first TITLE_MAX characters) matches a rule, as www/js/health.js does it."""
    title = " ".join((title or "").split())[:TITLE_MAX]
    if rule.get("regex"):
        return re.search(rule["match"], title, re.I) is not None
    return rule["match"].casefold() in title.casefold()


# ---------------------------------------------------------------- the registry: every key of settings.example.json

KEY_LABS = ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Lymphocytes Absolute", "Creatinine",
            "ALT", "AST", "Total Bilirubin", "Potassium, Blood", "Sodium, Blood", "Magnesium"]

section("overview", "Overview", "The tiles at the top of the Overview tab: key labs, and readings taken at home if you choose them.")
section("trends", "Lab charts", "What the Labs tab charts before you pick anything.")
section("names", "Names", "Shorter or clearer names shown instead of the ones in the records.")
section("health", "Workouts", "How the Body and activity tab counts workouts.")   # named as its group is (#409)

define("overview.keyLabs", "overview", "Key labs", KEY_LABS, lab_list(), kind="tiles",
       help="One tile each, in this order, for the labs this profile has results for. While this is the default list "
            "and few of its labs have results here, the most-measured labs fill the tiles instead.")
define("trends.defaultLabs", "trends", "Labs selected at first",
       ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Creatinine", "ALT"], lab_list(), kind="labs",
       help="Charted on Labs until you choose others on this device.")
define("labNicknames", "names", "Lab names", {
           "Automated Abs Neutrophil Cnt": "Neutrophils (absolute)", "Lymphocytes Absolute": "Lymphocytes (absolute)",
           "Platelet Count": "Platelets", "Potassium, Blood": "Potassium", "Sodium, Blood": "Sodium",
           "Total Bilirubin": "Bilirubin, total"},
       name_map(), kind="names", merge=True, help="A lab's name in the records, and the name to show for it.")
define("sources", "names", "Result sources", {}, name_map(), kind="names", merge=True,
       help="The short source tag on lab results (like EXT), and the name to show for it.")
ATTACHMENT_TYPES = {"pdf": "PDF", "image": "Images", "text": "Text", "html": "Web pages (HTML)"}
CEILING_MB = max(1, config.SYNC_MAX_ATTACHMENT // config.MB)   # SYNC_MAX_ATTACHMENT_MB: no setting goes above it

section("sync", "Sync defaults", "Defaults for every connection on this server (each connection can change its "
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

# #225: readings taken at home (and at visits) as Overview tiles beside the key labs, each with its latest value
HOME_TILES = {"bp": "Blood pressure", "glucose": "Blood glucose", "temp": "Body temperature"}
define("overview.homeTiles", "overview", "Home readings", [], some_of(HOME_TILES, "Home readings"), kind="choices",
       help="A tile each, after the key labs, with the latest reading, its date and where it came from: a phone app at "
            "home, or a visit's records.")
CHOICES["overview.homeTiles"] = HOME_TILES

# #186: the phone's and watch's measures the visit summary ticks at first (server/measures.py's sections, otherwise
# unticked; tests/test_condition_presets.py keeps the two lists the same), chosen here or by a condition's preset
SUMMARY_TICKS = {"home_bp": "Blood pressure at home", "glucose": "Blood glucose at home",
                 "temperature": "Body temperature at home", "oxygen": "Blood oxygen at home",
                 "respiratory": "Respiratory rate from the watch", "vo2max": "Cardio fitness (VO2 max)",
                 "immunizations": "Immunizations", "ecg": "ECGs and irregular rhythm notifications", "bmi": "BMI and growth",
                 "targets": "Your targets for home readings", "medlog": "Medication log"}   # as measures.LABELS (#234, #235)
section("summary", "Visit summary", "What the visit summary on the Sharing page ticks before you choose.")
define("summary.ticked", "summary", "Measures ticked at first", [], some_of(SUMMARY_TICKS, "Measures ticked at first"),
       kind="choices", help="Those this profile has some of are ticked in the visit summary at first; you can still "
                            "untick them there. The others start unticked.")
CHOICES["summary.ticked"] = SUMMARY_TICKS

define("visitWorkouts", "health", "Visits that count as workouts", [], workout_rules(), kind="rules",
       help="Past visits whose title contains the text count as a workout of that many minutes, once a day per rule, "
            "ignoring case. Tick “Regular expression” to match a pattern instead (an advanced option).")


# ---------------------------------------------------------------- #39 milestones
# Named dates (diagnosis, surgery, treatment start...): markers on every chart and "since" periods on Health and
# Costs. They were corrections.json's, carried into the setup build's data.json; since #39 they are this profile
# setting (encrypted, like every private one), moved in once at start-up (server/milestones.py).

def milestone_list(most=50):
    def check(v):
        if not isinstance(v, list):
            raise SettingError("This must be a list of milestones.")
        out = {}
        for m in v:
            if not isinstance(m, dict):
                raise SettingError("Each milestone needs a date and a name.")
            d = m.get("date")
            try:
                day = datetime.date.fromisoformat(d[:10]) if isinstance(d, str) else None
            except ValueError:
                day = None
            if day is None or not 1900 <= day.year <= 2100:
                raise SettingError(f"“{d}” isn't a date (year-month-day, like 2026-03-01).")
            label = _text(m.get("label") or "Milestone", "A milestone's name", 60)
            primary = m.get("primary", False)
            if not isinstance(primary, bool):
                raise SettingError("“Primary” must be on or off.")
            key = (day.isoformat(), label)
            out[key] = out.get(key, False) or primary   # the same date and name twice: one milestone
        if sum(out.values()) > 1:
            raise SettingError("Only one milestone can be the primary one.")
        if len(out) > most:
            raise SettingError(f"At most {most} milestones.")
        return [{"date": d, "label": n, **({"primary": True} if p else {})} for (d, n), p in sorted(out.items())]
    return check


section("milestones", "Milestones", "Named dates, like a diagnosis, a surgery or the start of a treatment: marked on "
        "every chart, and periods on Health and Costs count “since” each one.")
define("milestones", "milestones", "Milestones", [], milestone_list(), kind="milestones",
       help="The primary milestone is the one the Overview counts from, with its own “Since” button above the "
            "records (the first, if none is marked).")


# ---------------------------------------------------------------- tabs (#40) and theme (#45)

TABS = ("overview", "health", "timeline", "labs", "costs")   # the main tabs' ids ("health" is Body and activity)
TAB_NAMES = {"overview": "Overview", "health": "Body and activity", "timeline": "Timeline", "labs": "Labs", "costs": "Costs"}
OLD_TABS = {"heatmap": "labs", "trends": "labs"}   # #172: Trends ("labs") and Lab heatmap are the one Labs tab


def tab_layout(v):
    """{"order": [tab ids], "hidden": [tab ids]}: every tab in order (one left out goes at the end, in the default
    order, so a tab added later shows up), the hidden ones in that order. At least one tab stays shown.

    A layout from before #172 names Trends ("labs") and Lab heatmap ("heatmap"): Labs takes the place of the first of
    them, and is hidden only if both were (stored values are read through this too: reread)."""
    if not isinstance(v, dict) or set(v) - {"order", "hidden"}:
        raise SettingError("This must give the tabs' order and which are hidden.")
    lists = {}
    for part in ("order", "hidden"):
        x = v.get(part, [])
        if not isinstance(x, list) or not all(isinstance(t, str) for t in x):
            raise SettingError("The tabs must be a list of tab names.")
        for t in x:
            if t not in TABS and t not in OLD_TABS:
                raise SettingError(f"There's no tab called “{t[:40]}”.")
        lists[part] = x
    hidden = set(lists["hidden"])
    if (set(lists["order"]) | hidden) & set(OLD_TABS):   # from before #172: Labs hidden only if Trends and Lab heatmap were
        labs = "heatmap" in hidden and bool(hidden & {"labs", "trends"})
        hidden = {t for t in hidden if t not in ("labs", "trends", "heatmap")} | ({"labs"} if labs else set())
    order = list(dict.fromkeys(OLD_TABS.get(t, t) for t in lists["order"]))
    order += [t for t in TABS if t not in order]
    hidden = [t for t in order if t in hidden]
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

define("tabs", "tabs", "Tabs", {"order": list(TABS), "hidden": []}, tab_layout, kind="tabs", private=False, reread=True,
       help="A hidden tab leaves the tab bar and the arrow keys; a saved view or link to it opens the first shown tab.")
define("theme", "appearance", "Theme", "system", theme, scope="user", kind="theme", private=False,
       help="On every device you sign in on. Dark mode or Light mode in your menu changes it too.")


# ---------------------------------------------------------------- the getting-started guide (#199): each person's own
# Whether this person finished the guide (guide.html) or skipped it: until then, signing in to an empty profile of
# their own opens it. Not on the Settings screen; the guide stays a menu item away whatever this says.

GUIDE_STATES = ("", "skipped", "done")


def guide_state(v):
    if v not in GUIDE_STATES:
        raise SettingError("The guide is done, skipped, or not yet either.")
    return v


section("guide", "Getting started", "The guide to a profile's first records.")
define("guide", "guide", "Getting started guide", "", guide_state, scope="user", kind="guide", private=False, screen=False)


# ---------------------------------------------------------------- dates and times (#42): each person's own

def time_zone(v):
    """An IANA time zone name ("America/Chicago"), or "" for not chosen yet (the page then sets the browser's own
    the first time it loads). Checked against the system's time zone database when it has one."""
    if v == "":
        return v
    if not isinstance(v, str) or len(v) > 64 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}", v):
        raise SettingError("A time zone is a name like America/Chicago or Europe/Paris.")
    known = _zones()
    if known and v not in known:
        raise SettingError(f"There's no time zone called {v}.")
    return v


def _zones():
    try:
        import zoneinfo
        return zoneinfo.available_timezones()   # empty where there's no time zone database (Windows without tzdata)
    except Exception:
        return set()


DATE_FORMATS = {"mdy": "Nov 15, 2026", "dmy": "15 Nov 2026", "ymd": "2026-11-15"}


def date_format(v):
    if v == "" or v in DATE_FORMATS:
        return v
    raise SettingError("The date format must be one of: " + ", ".join(DATE_FORMATS.values()) + ".")


def local_now(db, uid):
    """Now, as a naive wall-clock time in the person's own time zone (the server's, when theirs isn't set or the
    system has no time zone database): for dates the server writes for someone, like an export's file name."""
    from datetime import datetime
    zone = effective(db, None, uid)["dates.timeZone"][0]
    if zone:
        try:
            import zoneinfo
            return datetime.now(zoneinfo.ZoneInfo(zone)).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now()


section("dates", "Dates and times", "How dates are shown to you, and where your days begin and end.")
define("dates.timeZone", "dates", "Time zone", "", time_zone, scope="user", private=False, kind="timezone",
       help="Days on the timeline, Health and Costs start at midnight here, and times are shown in it. "
            "Set from this browser the first time you sign in.")
define("dates.format", "dates", "Date format", "", date_format, scope="user", private=False, kind="dateformat",
       help="How dates are written. Set from this browser's language the first time you sign in.")


# ---------------------------------------------------------------- saved Trends views (#37)

def trend_views(most=30):
    """[{"name", "labs": [lab names], "default": bool}]: named sets of labs for Trends, names unique (ignoring case),
    at most one the default (the labs Trends starts with, instead of trends.defaultLabs)."""
    labs = lab_list()
    def check(v):
        if not isinstance(v, list):
            raise SettingError("This must be a list of views.")
        out, seen = [], set()
        for x in v:
            if not isinstance(x, dict) or set(x) - {"name", "labs", "default"}:
                raise SettingError("Each view needs a name and its labs.")
            name = _text(x.get("name"), "A view's name", 60)
            if name.casefold() in seen:
                raise SettingError(f"There's more than one view called “{name}”.")
            seen.add(name.casefold())
            chosen = labs(x.get("labs"))
            if not chosen:
                raise SettingError(f"The view “{name}” needs at least one lab.")
            if not isinstance(x.get("default", False), bool):
                raise SettingError(f"Whether “{name}” is the default must be yes or no.")
            out.append({"name": name, "labs": chosen, "default": x.get("default", False)})
        if sum(x["default"] for x in out) > 1:
            raise SettingError("Only one view can be the default.")
        if len(out) > most:
            raise SettingError(f"At most {most} views.")
        return out
    return check


define("trends.views", "trends", "Saved views", [], trend_views(), kind="views",
       help="Named sets of labs to pick from on Labs (save one there from what's charted). The default view is "
            "what Labs charts at first, instead of the labs above.")


# ---------------------------------------------------------------- unit preferences (#41)

UNIT_CHOICES = {   # part -> {choice: label}; the conversions themselves are units.py's, exact ones only
    "lab": {"reported": "As reported", "conventional": "mg/dL, g/dL (conventional)", "si": "mmol/L, g/L (SI)"},
    "weight": {"reported": "As reported", "lb": "lb", "kg": "kg"},
    "temperature": {"reported": "As reported", "F": "°F", "C": "°C"},
    "height": {"reported": "As reported", "cm": "cm", "ftin": "ft and in"},   # #194: heights, and lengths in cm or in
}


def unit_prefs(v):
    """{"lab": ..., "weight": ..., "temperature": ..., "height": ...}, each one of UNIT_CHOICES; a part left out is
    "reported"."""
    if not isinstance(v, dict) or set(v) - set(UNIT_CHOICES):
        raise SettingError("This must say which units to show lab values, weights, temperatures and heights in.")
    out = {}
    for part, choices in UNIT_CHOICES.items():
        x = v.get(part, "reported")
        if x not in choices:
            raise SettingError(f"There's no {part} unit called “{str(x)[:20]}”.")
        out[part] = x
    return out


section("units", "Units", "The units you see values in, on every profile. Only exact conversions are made; a test "
        "without one stays in the unit its lab reported, and the reported value is always shown beside a converted one.")
define("units", "units", "Units", {p: "reported" for p in UNIT_CHOICES}, unit_prefs, scope="user", kind="units",
       private=False, help="Lab tests: glucose, creatinine, urea nitrogen (BUN), calcium and magnesium convert between "
       "mg/dL and mmol/L (creatinine in µmol/L); hemoglobin, MCHC, albumin and total protein between g/dL and g/L. "
       "Heights and other lengths between cm and inches (feet and inches in About this person).")
CHOICES["units"] = UNIT_CHOICES


# ---------------------------------------------------------------- #44 reference ranges from settings (server/ranges.py)

from . import ranges as _ranges


def _range_check(f):
    def check(v):
        try:
            return f(v)
        except _ranges.RangeError as e:
            raise SettingError(str(e)) from None
    return check


section("ranges", "Reference ranges", "Your own ranges, used only for results whose report gives none. Each is "
        "labelled “range from settings” wherever it colours a result; a range printed on a report is always the one used.")
define("ranges.overrides", "ranges", "Ranges for results without one", [], _range_check(_ranges.check_overrides),
       kind="ranges", help="By the lab's name in the records and its unit: a result in another unit gets none, and nothing "
                           "is converted. Leave the age empty for every age; a row for female or male wins over one for anyone. "
                           "The age and sex are those set under About this person.")


# ---------------------------------------------------------------- #194 about this person (server/person.py)
# Not on the Settings screen: Settings → About this person (routes_person.py) changes them. Before #194 the birth date
# and sex were ranges.birthDate and ranges.sex; schema step 30 renamed them, and LEGACY reads a file that has the old names.

from . import person as _person


def _person_check(f):
    def check(v):
        try:
            return f(v)
        except _person.PersonError as e:
            raise SettingError(str(e)) from None
    return check


section("person", "About this person", "Details some views depend on: the age on each result's day, ranges of your "
        "own by sex and age, and what is built on height. Each is optional.")
define("person.birthDate", "person", "Birth date", "", _person_check(_person.check_birth), kind="birthdate", screen=False,
       help="For the age on the day of each result (ranges of your own by age band) and what depends on age.")
define("person.sex", "person", "Sex used for reference ranges", "", _person_check(_person.check_sex), kind="rangesex",
       screen=False, help="What labs base their reference ranges on: female, male, or not set. Not gender identity.")
define("person.heights", "person", "Height", [], _person_check(_person.check_heights), kind="heights", screen=False,
       help="Dated heights, in centimetres, each with the unit it was typed in.")
from . import hrzones as _hrzones   # #197: a measured maximum heart rate, for heart rate zones on workouts


def _max_hr_check(v):
    try:
        return _hrzones.check_max(v)
    except _hrzones.ZoneError as e:
        raise SettingError(str(e)) from None


define("person.maxHr", "person", "Maximum heart rate", 0, _max_hr_check, kind="maxhr", screen=False,
       help="A maximum heart rate measured (in an exercise test, say), in beats a minute; 0 when not set. Heart rate "
            "zones on workouts use it, else the common estimate of 220 minus the age.")

define("person.dueDate", "person", "Due date, if born early", "", _person_check(_person.check_due), kind="duedate", screen=False,
       help="For a baby born early, the day they were due: growth charts use the corrected age before 2 years (#196).")
from . import targets as _targets   # #234: targets of your own for home readings, as the care team gave them


def _targets_check(v):
    try:
        return _targets.check(v)
    except _targets.TargetError as e:
        raise SettingError(str(e)) from None


define("person.targets", "person", "Targets of your own", {}, _targets_check, kind="targets", screen=False,
       help="For readings taken at home: a target each, as your care team gave it, with who set it and when (#234).")
LEGACY = {"ranges.birthDate": "person.birthDate", "ranges.sex": "person.sex"}   # old name in a settings.json -> now


# ---------------------------------------------------------------- #161 the journal's symptom groups (server/journal.py)
# Not on the Settings screen: the journal's card on the Timeline tab changes them (journal.js). Sealed: symptom names
# are health information.
from . import journal as _journal


def _groups_check(v):
    try:
        return _journal.check_groups(v)
    except _journal.JournalError as e:
        raise SettingError(str(e)) from None


section("journal", "Journal", "Symptom groups: your own named sets of symptoms, logged in one go.")
define("journal.groups", "journal", "Symptom groups", [], _groups_check, kind="groups", screen=False,
       help="Named sets of symptoms, such as “Migraine day”: headache, nausea, light sensitivity. Each is logged with "
            "its own severity.")


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
        s = SETTINGS.get(r["key"])
        if s:
            try:
                out[r["key"]] = s.clean(_load(r)) if s.reread else _load(r)
            except (ValueError, TypeError):   # SettingError is one: a value no longer accepted is left at its default
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
    old_names = {now: was for was, now in LEGACY.items()}   # #194: a file from before says ranges.birthDate, ranges.sex
    for k, s in SETTINGS.items():
        if s.scope != scope:
            continue
        v, there = _dig(obj, k)
        if not there and k in old_names:
            v, there = _dig(obj, old_names[k])
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


# ---------------------------------------------------------------- one name at a time (#38)

def rename(db, key, name, to, pid=None, uid=None, by=None):
    """Change one entry of a name map ("names" kind: labNicknames, sources) where the name is shown: name is shown as
    to from now on, or as itself again when to is empty or the same. The rest of the map is kept as it is (whoever
    changed another name meanwhile keeps theirs). Call inside a transaction. Returns the map stored."""
    s = get_def(key)
    if s.kind != "names":
        raise SettingError(f"{s.label} isn't a list of names.")
    name = _text(name, "The name")
    to = " ".join(to.split()) if isinstance(to, str) else ""
    have = dict(effective(db, pid, uid)[key][0])
    if to and to != name:
        have[name] = to
    else:
        have.pop(name, None)
    return put(db, key, have, pid=pid, uid=uid, by=by)
