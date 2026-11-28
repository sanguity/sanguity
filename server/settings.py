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

A server setting the environment can also set (#999): an install setting with env="NAME", a variable of config.VARS,
private=False and the variable's default. Where NAME is used, config.setting("NAME") gives the value in force: the
environment's when it's set (then shown "set by the environment", and a change to it refused), else the one saved
here, else the default. Its value and where it comes from (in_force) are the same everywhere: the API, effective(),
install_values() and the server's own use.

settings.json, before schema version 18: each profile's copy (profiles/<id>/settings.json) is loaded into the
table once at start-up (migrate_files), then deleted (#934). A setup build's settings.json
only seeds a linked profile's settings the first time the build is loaded into it, filling what isn't set; after
that a rebuild never changes settings, and says so in the log when the build's settings.json differs from them.
"""
import contextlib, datetime, json, os, re, sqlite3, threading, time, urllib.parse

from . import config, crypto, db as dbm, security

SCOPES = ("install", "profile", "user")   # broadest first: the order values are layered in


class SettingError(ValueError):
    """A value the setting doesn't accept, said in plain language."""


class Setting:
    def __init__(self, key, section, label, default, check, scope="profile", private=True, kind=None, help="",
                 merge=False, reread=False, screen=True, env=None, confirm=None, records=False):
        self.key, self.section, self.label, self.default, self.check = key, section, label, default, check
        # (#998) an install setting whose change is confirmed first: confirm(db, before, after, request, session) ->
        # None when there's nothing to ask, else {"count", "message", "type", "title", "action"}; the change is made
        # only once the admin has typed "type" (the count unless it says) back (routes_settings._confirmed, one rule)
        self.confirm = confirm
        self.scope, self.private, self.kind, self.help, self.merge = scope, private, kind, help, merge
        self.env = env   # the variable of config.VARS it stands for (#999): the environment's value wins over it
        self.reread = reread   # a stored value is checked again when read: its check gives older values in today's terms
        self.screen = screen   # shown on the Settings screen (False: kept for a page of its own, like the guide's, #199)
        self.records = records   # the records carry it (#1135: keyDocs): a change moves the profile's records' revision

    def clean(self, value):
        return self.check(value)

    def public(self):
        help = self.help() if callable(self.help) else self.help   # a function: words with a limit read at each use
        lo, hi = getattr(self.check, "lo", None), getattr(self.check, "hi", None)   # (#1001) a number's bounds, when it has them
        return {"key": self.key, "section": self.section, "label": self.label, "help": help, "kind": self.kind,
                "scope": self.scope, "default": self.default, **({"choices": CHOICES[self.key]} if self.key in CHOICES else {}),
                **({"min": lo, "max": hi() if callable(hi) else hi} if lo is not None else {})}


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
    if (env := kw.get("env")):   # (#999) a variable's own: the install's, plain, with the variable's default
        v = config.VARS.get(env)
        if v is None or v.setting not in (None, key):
            raise ValueError(f"setting {key}: {env} isn't a variable of config.VARS, or another setting stands for it")
        if kw.get("scope") != "install" or kw.get("private", True):
            raise ValueError(f"setting {key}: a variable's setting is scope='install', private=False")
        if v.keep(check(default)) != v.default:
            raise ValueError(f"setting {key}: its default isn't {env}'s ({v.default!r})")
    s = SETTINGS[key] = Setting(key, section, label, check(default), check, **kw)
    if s.env:
        config.VARS[s.env].setting = key
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
    """hi may be a function, read at each check (a limit of the server's that can change: ceiling_mb)."""
    def check(v):
        top = hi() if callable(hi) else hi
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not lo <= v <= top:
            raise SettingError(f"{what} must be a whole number from {lo} to {top}.")
        return int(v)
    check.lo, check.hi = lo, hi   # (#1001) its bounds, for the page's field (Setting.public)
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
       help="One tile each, in this order, for the labs this profile has results for.\nWhile this is the default list "
            "and few of its labs have results here, the most-measured labs fill the tiles instead.")
define("trends.defaultLabs", "trends", "Labs selected at first",
       ["Hemoglobin", "Platelet Count", "WBC", "Automated Abs Neutrophil Cnt", "Creatinine", "ALT"], lab_list(), kind="labs",
       help="Charted on Labs until you choose others on this device.")
define("labNicknames", "names", "Lab names", {
           "Automated Abs Neutrophil Cnt": "Neutrophils (absolute)", "Lymphocytes Absolute": "Lymphocytes (absolute)",
           "Platelet Count": "Platelets", "Potassium, Blood": "Potassium", "Sodium, Blood": "Sodium",
           "Total Bilirubin": "Bilirubin, total"},
       name_map(), kind="names", merge=True, help="A lab's name in the records, and the name to show for it. To chart two names as one test: Lab name matches, below.")
define("sources", "names", "Result sources", {}, name_map(), kind="names", merge=True,
       help="The short source tag on lab results (like EXT), and the name to show for it.")
ATTACHMENT_TYPES = {"pdf": "PDF", "image": "Images", "text": "Text", "html": "Web pages (HTML)"}


def ceiling_mb():
    """SYNC_MAX_ATTACHMENT_MB, read at each use: no setting goes above it."""
    return max(1, config.setting("SYNC_MAX_ATTACHMENT_MB"))


section("sync", "Sync defaults", "Defaults for every connection on this server; each connection can change its own.\n"
        "Attachments are the files records point at: visit notes, scans, reports.")
define("sync.attachAuto", "sync", "Fetch attachments automatically", True, yes_no("Fetching attachments automatically"),
       scope="install", private=False, kind="toggle",
       help="Off: each sync lists the attachments it left out, and you fetch the ones you want.")
define("sync.attachMaxMB", "sync", "Largest attachment fetched (MB)", min(20, ceiling_mb()),
       whole(1, ceiling_mb, "The largest attachment"), scope="install", private=False, kind="number",
       # (#1008) where the ceiling is set: .env, which both compose files pass it from (#999), or the app's own
       # environment variables where there's no .env (TrueNAS's Additional Environment Variables, an Unraid variable)
       help=lambda: f"Bigger ones are left out and listed. At most {ceiling_mb()} MB, the server's limit: "
                    "SYNC_MAX_ATTACHMENT_MB, on General → Needs a restart (or in .env or the app's environment variables).")
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

# ---------------------------------------------------------------- General → Host (#1000)
# The addresses people open Sanguity at (PUBLIC_ORIGINS), read at each use: the cross-site check, the address the app
# gives out (share links, calendar feeds, connected sources' return, the phone upload address) and Find your
# provider's return addresses. A change is guarded (routes_host.py): its effects said first, typed confirmation, and a
# list this page's own address wouldn't pass refused.
_ORIGIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*|\[[0-9a-f:.]+\]")


def origin_list(most=20):
    """A list of addresses as a browser's Origin says them (https://health.example.com, a port only where it isn't
    the scheme's own), lower case, each once; text is split at commas, spaces and line breaks."""
    def check(v):
        if isinstance(v, str):
            v = [p for p in re.split(r"[\s,]+", v) if p]
        if not isinstance(v, list):
            raise SettingError("The public addresses must be a list of addresses.")
        out = []
        for o in v:
            o = _text(o, "An address", 300).lower().rstrip("/")
            u = urllib.parse.urlsplit(o)
            host, _, port = u.netloc.rpartition(":") if not u.netloc.endswith("]") else (u.netloc, "", "")
            host, port = (host, port) if host and port.isdigit() else (u.netloc, "")
            if (u.scheme not in ("http", "https") or u.path or u.query or u.fragment or "@" in u.netloc
                    or not _ORIGIN.fullmatch(host) or (port and not 0 < int(port) < 65536)):
                raise SettingError(f"“{o}” isn't an address like https://health.example.com: https:// or http://, then "
                                   "its name, with a port only if it needs one, and nothing after.")
            if port in {"https": ("443",), "http": ("80",)}[u.scheme]:
                port = ""   # (a browser leaves the scheme's own port out of Origin)
            o = f"{u.scheme}://{host}{':' + port if port else ''}"
            if o not in out:
                out.append(o)
        if len(out) > most:
            raise SettingError(f"At most {most} addresses.")
        return out
    return check


section("host", "Host", "The addresses people open Sanguity at, and how this page reached the server.")
define("host.publicOrigins", "host", "Public addresses", [], origin_list(), scope="install", private=False, kind="origins",
       env="PUBLIC_ORIGINS",
       help=lambda: "One a line, like https://health.example.com. The first https:// one is the address the app gives out: "
                    "share links, calendar feeds, " + ("connected sources' return and " if connected_sources() else "") +   # (#1096)
                    "the phone upload address. Needed behind a reverse proxy that changes the Host header and sends no "
                    "X-Forwarded-Host; empty, each page uses the address it was opened at.\nOnce any is set, the server "
                    "answers only at these addresses' names, an IP address, localhost, a name without a dot and names "
                    "ending .local, .lan, .home, .home.arpa or .internal: a page elsewhere can't point a name of its own at "
                    "it. Any other name is refused, a home-network name on a domain of your own (nas.example.com, by split "
                    "DNS) or a Tailscale name (nas.tailnet.ts.net) included.\nTo open Sanguity at one, add its address here, "
                    "as it's typed in the browser, with its port if it has one (https://nas.example.com:8443).")

# ---------------------------------------------------------------- General → Phone receiver (#1003)
# The upload address (WATCH_UPLOAD_URL), read at each use by Sources → Phone and watch (watch_keys.addresses). A change
# says first how many profiles' phones need the new one (routes_host.py's effects). The receiver's own settings are
# shown read-only beside it (#998's default).

def upload_url(v):
    """An http(s) address with a name, or "" (none: each public address's host, or the page's, on RECEIVER_PORT)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return ""
    v = _text(v, "The upload address", 300)
    u = urllib.parse.urlsplit(v)
    try:
        u.port
    except ValueError:
        u = None
    if u is None or u.scheme not in ("http", "https") or not u.hostname or " " in v or "@" in u.netloc:
        raise SettingError(f"“{v}” isn't an upload address like https://health.example.com/ingest: https:// or http://, "
                           "then its name and the path your reverse proxy sends to the receiver.")
    return v


section("phone", "Phone receiver", "Where phones send watch and scale data, and the receiver's own settings.")
define("phone.uploadUrl", "phone", "Upload address", "", upload_url, scope="install", private=False, kind="url",
       env="WATCH_UPLOAD_URL",
       help="Only when the receiver is behind your own reverse proxy, like https://health.example.com/ingest. Empty, "
            "phones are given each public address's name (or this page's) on the receiver's published port.")

# ---------------------------------------------------------------- General → Notifications (#1130, notify.py)
# The install's own notification server: a self-hosted ntfy server's address (each person's own topic goes after it,
# notify.py). https only, unless its host is on the
# local network: an address of a private range, or a name that is one word or ends in a local suffix (notify.py checks
# again, as it sends, that such a name's addresses are all on the local network). Its access token is a secret, kept
# apart (notify.py: encrypted in meta, never given to a page), as the sign-in provider's client secret is.
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa")


def local_host(host):
    """Whether a host (a name or an address, no port) is on the local network, by how it's written."""
    import ipaddress
    host = (host or "").strip("[]").lower().rstrip(".")
    try:
        return local_address(ipaddress.ip_address(host.split("%")[0]))
    except ValueError:
        return bool(host) and ("." not in host or host == "localhost" or host.endswith(LOCAL_SUFFIXES))


def local_address(ip):
    """An address of the local network: private, loopback, link-local or the carrier-grade range (Tailscale's)."""
    import ipaddress
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))


def notify_url(v):
    """An ntfy server's address (each person's own topic goes after it), or "" for none (nothing is sent)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return ""
    v = _text(v, "The notification server", 300)
    u = urllib.parse.urlsplit(v)
    try:
        u.port
    except ValueError:
        u = None
    if (u is None or u.scheme not in ("http", "https") or not u.hostname or " " in v or "@" in u.netloc
            or u.query or u.fragment):
        raise SettingError(f"“{v}” isn't a server's address like https://ntfy.example.com: https://, then your server's "
                           "name (and a path, if it's served under one).")
    if u.scheme == "http" and not local_host(u.hostname):
        raise SettingError(f"“{v}” would send notifications unencrypted over the internet: use https://. http:// is "
                           "only for a server on your local network (like 192.168.1.20, or a name like ntfy.lan).")
    return v.rstrip("/")


section("notify", "Notifications", "Sent only to a notification server you run yourself, and only to the people who "
        "turn them on.\nA self-hosted ntfy server. Nothing is sent until it's set and someone turns them on for a profile, "
        "under Settings → Notifications: each person gets a topic of their own there, unguessable, that only they "
        "subscribe to, and only the profiles they can see. Each says only which profile and what kind (\u201cSanguity: "
        "new results for Ben\u201d, \u201cSanguity: a dose is due for Ben\u201d): never a result, a medication or "
        "anything else of the records. Tapping one opens Sanguity, where its person signs in to see what it is.")
define("notify.url", "notify", "Notification server", "", notify_url, scope="install", private=False, kind="url",
       help="Your ntfy server's address, like https://ntfy.example.com. https:// only, unless the server is on your "
            "local network (like http://192.168.1.20, or http://ntfy.lan). Empty: nothing is sent.\nChanging it to "
            "another server (another scheme, name or port) makes every person's topic new: each sees the new address "
            "under Settings, Notifications, and subscribes to it again.")


# ---------------------------------------------------------------- Accounts and sign-in → Sign-in rules (#1001)
# How people sign in, each applying at once where it's used (config.setting, or install_value for those no variable
# stands for), with the fresh check and logged old and new as every install setting's change (#1006). Each is bounded,
# and never more lenient than Sanguity's own default (#998): a value outside is refused (400), and the environment's
# wins over it (a variable set there shows locked). Requiring two-factor sign-in for everyone is the meta row
# require_2fa (accounts.two_factor_required, PATCH /api/settings), shown in this section too; the Secure cookie and the
# password checks at once (COOKIE_SECURE, HASH_PARALLEL) are the environment's alone, shown read-only. Shortening a
# sign-in or turning on idle sign-out ends sign-ins: the change counts them and asks first (routes_settings.py).
IDLE_LEAST = 5   # minutes: a shorter idle sign-out would end a sign-in while its person reads a page


def idle_minutes(v):
    v = whole(0, 1440, "Signing out when unused")(v)
    if 0 < v < IDLE_LEAST:
        raise SettingError(f"Signing out when unused must be off (0) or from {IDLE_LEAST} to 1440 minutes.")
    return v


idle_minutes.lo, idle_minutes.hi = 0, 1440


def one_of(choices, what):
    """One of choices ({id: label})."""
    def check(v):
        if not isinstance(v, str) or v not in choices:
            raise SettingError(f"{what} must be one of: {', '.join(choices.values())}.")
        return v
    return check


section("security", "Sign-in rules", "How people sign in to this server, and for how long. The switch and the choice apply at once; "
        "the numbers, with Save.")   # (Accounts and sign-in's, General → Security before #1119)
define("security.sessionDays", "security", "A sign-in lasts, with a second factor (days)", 30,
       whole(1, 30, "A sign-in's length"), scope="install", private=False, kind="number", env="SESSION_DAYS",
       help="From signing in, with an authenticator app or a passkey. Shorter ends the sign-ins older than that; "
            "longer applies to new ones.")
define("security.sessionHours", "security", "…without one (hours)", 12,
       whole(1, 12, "A sign-in's length without a second factor"), scope="install", private=False, kind="number",
       env="SESSION_HOURS_WITHOUT_2FA", help="For an account with no second factor yet.")
define("security.idleMinutes", "security", "Sign out when unused for (minutes)", 0, idle_minutes, scope="install",
       private=False, kind="number", env="SESSION_IDLE_MINUTES",
       help=f"0 is off. Otherwise from {IDLE_LEAST} to 1440 (a day): a sign-in left unused that long ends.")
PROFILE_MAKERS = {"everyone": "Everyone signed in", "admins": "Admins only"}
define("security.profiles", "security", "Who may create profiles", "everyone", one_of(PROFILE_MAKERS, "Who may create profiles"),
       scope="install", private=False, kind="choice",
       help="A new profile is its maker's, to share from its Sharing page. Admins only: others ask an admin, who makes "
            "it and shares it with them.")
CHOICES["security.profiles"] = PROFILE_MAKERS
define("security.passwordMin", "security", "Shortest password (characters)", 15, whole(15, 64, "The shortest password"),
       scope="install", private=False, kind="number", env="PASSWORD_MIN_LENGTH",
       help="While a password is an account's only factor; never below 15 (NIST SP 800-63B). Asked the next time a "
            "password is set: those set already keep working.")
define("security.passwordMin2fa", "security", "…with two-factor sign-in on (characters)", 8,
       whole(8, 64, "The shortest password with two-factor sign-in"), scope="install", private=False, kind="number",
       env="PASSWORD_MIN_LENGTH_2FA", help="Never below 8.")
define("security.tempPasswordDays", "security", "A one-time password works for (days)", 7,
       whole(1, 7, "A one-time password's days"), scope="install", private=False, kind="number", env="TEMP_PASSWORD_DAYS",
       help="The one an admin gives a new account, or with a password reset: those given after a change.")
define("security.reauthMinutes", "security", "The fresh check lasts (minutes)", 5,
       whole(1, 5, "The fresh check's minutes"), scope="install", private=False, kind="number", env="REAUTH_MINUTES",
       help="Before the most powerful changes, like these, Sanguity asks for the password, a code or a passkey again, "
            "unless one was given within this long.")
# failed sign-ins before each further try waits (security.Throttle's kinds), never more than its FREE
SIGN_IN_TRIES = {"pair": "security.triesHere", "ip": "security.triesAddress", "user": "security.triesAccount"}
define("security.triesHere", "security", "Wrong tries before a wait: one account, from one place", 5,
       whole(3, 5, "Wrong tries from one place"), scope="install", private=False, kind="number",
       help="Then each try waits longer, up to 15 minutes. A browser that signed in before counts on its own.")
define("security.triesAddress", "security", "…from one address, at any account", 20, whole(10, 20, "Wrong tries from one address"),
       scope="install", private=False, kind="number", help="At least 10: a household often shares one address.")
define("security.triesAccount", "security", "…at one account, from anywhere", 30, whole(10, 30, "Wrong tries at one account"),
       scope="install", private=False, kind="number",
       help="At least 10: fewer would let a stranger hold someone up from signing in more cheaply.")

# Install → Connected sources' switch (#1096; General → Experimental before #1153): connected sources, off by default
# (a fresh install's), on for an install that had a registered app or a connection when it was upgraded
# (keep_sources_on). connected_sources() is the one rule every connections, sync and directory route, the sync and
# directory schedulers and the outside gateway read; /api/me gives every page its plain on or off. Off keeps every connection and what it pulled: on again, they sync again.
section("experimental", "Experimental", "Parts of Sanguity that work but aren't recommended yet. Each change applies at once.")
define("experimental.connectedSources", "experimental", "Connected sources (experimental, not recommended yet)", False,
       yes_no("Connected sources"), scope="install", private=False, kind="toggle", env="CONNECTED_SOURCES",
       help="Patient portals and health plans connected from Sources, synced on a schedule, and Find your provider. "
            "Each portal needs an app registered with its vendor first, under Set up a source. Off: none of it "
            "is shown, nothing is synced and nobody is contacted for it; connections and what they brought are kept, "
            "and sync again when it's turned back on.")
SOURCES_KEPT = "connected_sources.kept"   # meta: keep_sources_on has run


def connected_sources():
    """(#1096) Whether connected sources are on: the value in force of Install → Connected sources' switch (#1153)."""
    return bool(install_value("experimental.connectedSources"))


def keep_sources_on(db):
    """(#1096) Once, at start-up: an install from before the switch that has a registered app or a connection keeps
    connected sources on (nothing stops syncing unasked); any other starts off. True when it turned the switch on."""
    if dbm.meta_get(db, SOURCES_KEPT):
        return False
    had = db.execute("SELECT EXISTS(SELECT 1 FROM smart_clients) OR EXISTS(SELECT 1 FROM connections)").fetchone()[0]
    on = bool(had) and "experimental.connectedSources" not in stored(db, "install")
    with dbm.transaction(db):
        if on:
            put(db, "experimental.connectedSources", True)
        dbm.meta_set(db, SOURCES_KEPT, "1")
    return on


# #992: the provider directory's weekly update (directory/update.py), shown in Install → Provider directory, not General
section("directory", "Provider directory", "What Find your provider searches: Epic's and Oracle Health's lists, read by this server.")
define("directory.weekly", "directory", "Keep the lists up to date", False, yes_no("Keeping the lists up to date"),
       scope="install", private=False, kind="toggle",
       help="Once a week, this server reads Epic's and Oracle Health's lists again. A failed update keeps the lists it has.")

# #1129: text recognition (ocr.py), in Install → General → Text recognition (gen-ocr.js, its own card)
LANGUAGE_CODE = re.compile(r"^[a-z_]{3,16}$")   # a Tesseract language file's name, without .traineddata


def language_codes(v):
    """The languages text recognition reads besides English (built in, always used): Tesseract's codes, at most 8."""
    if not isinstance(v, list) or len(v) > 8 or not all(isinstance(c, str) and LANGUAGE_CODE.match(c) and c not in ("eng", "osd") for c in v):
        raise SettingError("Languages are at most 8 language files' names (like deu or chi_sim), English aside.")
    return sorted(set(v), key=v.index)


section("ocr", "Text recognition", "Scanned documents read on this server, so a search finds them by their words.")
define("ocr.languages", "ocr", "Languages in use", [], language_codes, scope="install", private=False, kind="languages",
       screen=False, help="English always; each other language a document may be in. More languages make reading slower.")
define("ocr.downloads", "ocr", "Download languages from the tesseract-ocr project", False, yes_no("Downloading languages"),
       scope="install", private=False, kind="toggle", screen=False,
       help="Lets an admin download a language file from the tesseract-ocr project on GitHub, when they ask for one. "
            "Off: nothing is downloaded; a language file you have can still be imported.")

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
       help="Past visits whose title contains the text count as a workout of that many minutes, once a day per rule.\n"
            "The text is matched ignoring case. Tick “Regular expression” to match a pattern instead (an advanced option).")


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


section("milestones", "Milestones", "Named dates, like a diagnosis, a surgery or the start of a treatment, marked on "
        "every chart.\nPeriods on Body and activity and Costs count “since” each one.")
define("milestones", "milestones", "Milestones", [], milestone_list(), kind="milestones",
       help="The primary milestone is the one the Overview counts from, with its own “Since” button above the "
            "records (the first, if none is marked).")


# ---------------------------------------------------------------- tabs (#40) and theme (#45)

TABS = ("overview", "health", "medications", "timeline", "labs", "costs")   # the main tabs' ids ("health" is Body and activity)
TAB_NAMES = {"overview": "Overview", "health": "Body and activity", "medications": "Medications", "timeline": "Timeline",
             "labs": "Labs", "costs": "Costs"}
OLD_TABS = {"heatmap": "labs", "trends": "labs"}   # #172: Trends ("labs") and Lab heatmap are the one Labs tab
NEW_TABS = {"medications": "health"}   # #275: a tab added since layouts were saved, and the tab it goes after in one


def tab_layout(v):
    """{"order": [tab ids], "hidden": [tab ids]}: every tab in order (one left out goes at the end, in the default
    order, so a tab added later shows up; Medications, added later still, goes after Body and activity wherever that
    is, NEW_TABS: #275), the hidden ones in that order. At least one tab stays shown.

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
    for t in TABS:
        if t not in order:
            after = NEW_TABS.get(t)
            order.insert(order.index(after) + 1 if after in order else len(order), t)
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
       help="A hidden tab leaves the top bar and its More menu; a saved view or a link to it opens the first shown tab.")
define("theme", "appearance", "Theme", "system", theme, scope="user", kind="theme", private=False,
       help="On every device you sign in on. Dark mode or Light mode in your menu changes it too.")
IN_RANGE = {"grey": "Grey", "green": "Green"}   # (#1108) the heatmap's in-range cells: data-inrange on <html>, util.js applyTheme
define("inRange", "appearance", "In-range results", "grey", one_of(IN_RANGE, "In-range results"), scope="user",
       kind="choice", private=False,
       help="How the Labs heatmap draws a result inside the range its report gave. Green says only that, not that it's "
            "good; out of range stays red with ▲ or ▼.")
CHOICES["inRange"] = IN_RANGE


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
       help="Named sets of labs to pick from on Labs (save one there from what's charted).\nThe default view is "
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


section("units", "Units", "The units you see values in, on every profile.\nOnly exact conversions are made; a test "
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


section("ranges", "Reference ranges", "Your own ranges, used only for results whose report gives none.\nEach is "
        "labelled “range from settings” wherever it colours a result; a range printed on a report is always the one used.")
define("ranges.overrides", "ranges", "Ranges for results without one", [], _range_check(_ranges.check_overrides),
       kind="ranges", help="By the lab's name in the records and its unit. Leave the age empty for every age.\n"
                           "A result in another unit gets none, and nothing is converted. A row for female or male wins over "
                           "one for anyone. The age and sex are those set under About this person.")


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
define("person.bloodType", "person", "Blood type", "", _person_check(_person.check_blood), kind="bloodtype", screen=False,
       help="As typed, for the emergency card; a blood type lab result in the records is used instead (#1126).")
define("person.contacts", "person", "Emergency contacts", [], _person_check(_person.check_contacts), kind="contacts", screen=False,
       help="Name, relationship and phone of each, in order, for the emergency card (#1126).")
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


section("costs", "Costs", "The month a tax year starts in, for Costs' tax-year report.")
define("costs.taxStart", "costs", "Tax year starts in", 1, whole(1, 12, "The tax year's first month"), kind="month", screen=False,
       private=False, help="The first month of the tax year (1, January, for the calendar year). Costs' tax-year report "
                           "(taxreport.py, #1136) sums the payments from it.")
section("journal", "Journal", "Symptom groups: your own named sets of symptoms, logged in one go.")
define("journal.groups", "journal", "Symptom groups", [], _groups_check, kind="groups", screen=False,
       help="Named sets of symptoms, such as “Migraine day”: headache, nausea, light sensitivity. Each is logged with "
            "its own severity.")


# ---------------------------------------------------------------- #1135 key documents (server/keydocs.py)
# Not on the Settings screen: a document's drawer marks it. Sealed: what a document is, and where it's kept, are health
# information. The records carry the marks (keydocs.annotate), so a change moves their revision.
from . import keydocs as _keydocs


def _keydocs_check(v):
    try:
        return _keydocs.check(v)
    except _keydocs.KeyDocError as e:
        raise SettingError(str(e)) from None


section("keydocs", "Key documents", "Documents marked as key ones: an advance directive, a power of attorney, a POLST.")
define("keyDocs", "keydocs", "Key documents", {}, _keydocs_check, kind="keydocs", screen=False, records=True,
       help="Each marked document's kind and where it's kept, by its record (#1135).")


# ---------------------------------------------------------------- Logs and activity → Logging (#1002; General's before #1153)
section("logging", "Logging", "What the server keeps of who did what.")
_entries = lambda k: f"{k:,} {'entry' if k == 1 else 'entries'}"   # noqa: E731


def _activity_goes(db, before, after, request=None, s=None):
    """(#1002) Fewer days of activity kept: the entries the next daily clean-up then deletes (activity.py), every
    profile's and the install's own, counted."""
    if after >= before:
        return None
    profiles, install = db.execute("SELECT IFNULL(SUM(profile_id IS NOT NULL), 0), IFNULL(SUM(profile_id IS NULL), 0) "
                                   "FROM activity WHERE at < ?", (dbm.now() - after * 86400,)).fetchone()
    if not profiles + install:
        return None
    n = profiles + install
    return {"count": n, "title": f"Delete {n:,} activity {'entry' if n == 1 else 'entries'}?", "action": "Keep fewer days",
            "message": f"Keeping {after} days of activity instead of {before} deletes {_entries(profiles + install)} older "
                       f"than {after} days at the next daily clean-up: {_entries(profiles)} of the profiles' activity "
                       f"and {_entries(install)} of the install's own. They can't be brought back."}


define("activity.days", "logging", "Activity kept for (days)", 365, whole(30, 3650, "The days of activity kept"),
       scope="install", private=False, kind="number", env="ACTIVITY_DAYS", confirm=_activity_goes,
       help="Each profile's activity and the install's own: older entries are deleted once a day.")


# ---------------------------------------------------------------- storage
INSTALL_ROWS = "profile_id IS NULL AND user_id IS NULL"   # the install's own values: a restore keeps today's (#1005)


def _where(scope, pid, uid):
    if scope == "install":
        return INSTALL_ROWS, ()
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
    if scope == "install":
        _changed(db)
    if s.records and p is not None:
        from . import records as _records
        _records._bump(db, p)
    return value


def clear(db, keys, scope, pid=None, uid=None):
    """Remove the values of keys at scope (back to what's under them). Returns the keys that had one."""
    where, args = _where(scope, pid, uid)
    gone = [r["key"] for r in db.execute(f"SELECT key FROM settings WHERE {where} AND key IN (SELECT value FROM json_each(?))",
                                         (*args, json.dumps(list(keys))))]
    db.execute(f"DELETE FROM settings WHERE {where} AND key IN (SELECT value FROM json_each(?))", (*args, json.dumps(gone)))
    if scope == "install" and gone:
        _changed(db)
    if pid is not None and any(SETTINGS[k].records for k in gone if k in SETTINGS):
        from . import records as _records
        _records._bump(db, pid)
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
    """{key: (value, where it came from: "default", a scope or "environment")} for every setting, as seen by user uid
    on profile pid."""
    out = {k: (s.default, "default") for k, s in SETTINGS.items()}
    for scope in SCOPES:
        for k, v in stored(db, scope, pid, uid).items():
            out[k] = (v, scope)
    for k, s in SETTINGS.items():
        if s.env:
            out[k] = in_force(k)
    return out


# ---------------------------------------------------------------- the server's own settings, as config.setting reads them (#999)
# config.setting(NAME) asks here for the values saved in the app (config.saved), on every use: they are read from
# the database at most every SAVED_SECONDS, and at once after a change made in this process (put, clear). A change
# made by another (python -m server.manage config reset) applies within SAVED_SECONDS. Read over a connection of
# their own, read-only, so a process with no database (the watch receiver, which imports config) never makes one.

SAVED_SECONDS = 2
_saved = {"values": None, "at": 0.0, "db": None, "good": None}   # good: the values last read, for a read that fails
_saved_lock = threading.Lock()
# (#1046) a read that fails (busy past its timeout, the database briefly missing) keeps the values last read: it
# fails closed. With none read yet, Security's permissions and limits are at their strictest (only admins create
# profiles, the longest passwords, the fewest tries, the shortest fresh check and one-time password). Never the
# sign-ins' lengths or idle sign-out: those stay at the defaults, so a failed first read signs no one out
STRICTEST = {"security.profiles": "admins", "security.passwordMin": "hi", "security.passwordMin2fa": "hi",
             "security.tempPasswordDays": "lo", "security.reauthMinutes": "lo", "security.triesHere": "lo",
             "security.triesAddress": "lo", "security.triesAccount": "lo"}


def strictest():
    """{key: value}: STRICTEST, each number's bound as its check has it."""
    out = {}
    for k, v in STRICTEST.items():
        bound = getattr(SETTINGS[k].check, v, None) if v in ("lo", "hi") else v
        out[k] = bound() if callable(bound) else bound
    return out


def _read_saved():
    """The saved values, or None where they couldn't be read (a database there that can't be, now)."""
    keys = [k for k, s in SETTINGS.items() if s.scope == "install" and not s.private]   # (#1001: install_value's too)
    if not keys:
        return {}
    if not os.path.exists(config.DB_PATH):
        return None
    try:
        db = sqlite3.connect(f"file:{urllib.parse.quote(config.DB_PATH)}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        rows = db.execute("SELECT key, value FROM settings WHERE profile_id IS NULL AND user_id IS NULL AND key IN "
                          "(SELECT value FROM json_each(?))", (json.dumps(keys),)).fetchall()
    except sqlite3.Error:   # busy past the timeout, or not made yet (a database before schema version 18)
        return None
    finally:
        db.close()
    out = {}
    for key, value in rows:
        try:
            out[key] = SETTINGS[key].clean(json.loads(value))   # a value no longer accepted is left at its default
        except (ValueError, TypeError):
            continue
    return out


def saved_values():
    """{key: value} of the plain install settings saved in the app: those a variable's setting stands for (config.saved),
    and the rest install_value reads (#1001)."""
    with _saved_lock:
        c = _saved
        if c["values"] is None or c["db"] != config.DB_PATH or time.monotonic() - c["at"] > SAVED_SECONDS:
            if c["db"] != config.DB_PATH:
                c["good"] = None
            got = _read_saved()
            if got is None:   # (#1046) unreadable now: the last good values; none yet, the strictest (no database: none saved)
                got = c["good"] if c["good"] is not None else strictest() if os.path.exists(config.DB_PATH) else {}
            else:
                c["good"] = got
            c.update(values=got, at=time.monotonic(), db=config.DB_PATH)
        return c["values"]


def forget_saved():
    with _saved_lock:
        _saved["values"] = None


def _changed(db):
    """An install value changed: read again from now (a read meanwhile may still see the one before), and once it's
    committed."""
    forget_saved()
    dbm.after_commit(db, forget_saved)


config.saved = saved_values


def in_force(key):
    """(value, where it comes from) of an install setting that stands for a variable: "environment" when it's set,
    "install" when saved here (and not left out: SANGUITY_SAVED_SETTINGS=off, #1006), else "default"."""
    src, value = config.lookup(SETTINGS[key].env)
    return (list(value) if isinstance(value, tuple) else value), src


def install_value(key):
    """(#1001) The value in force of an install setting, for the server's own use where no database is at hand (a sign-in's
    throttle): a variable's as config.setting has it; else the one saved in the app (read as config.setting reads
    them, and left out with SANGUITY_SAVED_SETTINGS=off), else the default."""
    s = SETTINGS[key]
    if s.env:
        return in_force(key)[0]
    have = {} if config.saved_ignored() else saved_values()
    return have.get(key, s.default)


security.throttle.limit = lambda kind: install_value(SIGN_IN_TRIES[kind]) if kind in SIGN_IN_TRIES else None


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
    """{key: value} of the install-wide settings (the install's value, else the default; a variable's, its value in
    force)."""
    have = stored(db, "install")
    return {k: in_force(k)[0] if s.env else have.get(k, s.default)
            for k, s in SETTINGS.items() if s.scope == "install" and k.startswith(prefix)}


# (#998) the install's settings a member's page reads, given to everyone signed in: the sync defaults, which each
# connection's own rules start from. The rest of the install's (the public addresses, the upload address, sign-in's
# limits, the provider directory's, the activity kept) are an admin's alone: a member's page is told how long a
# sign-in lasts by /api/me's session.
MEMBER_SECTIONS = {"sync"}


def shown_to(key, is_admin):
    """Whether a setting's value is given to this person (the merged object, the Settings screen's sections)."""
    d = SETTINGS[key]
    return is_admin or d.scope != "install" or d.section in MEMBER_SECTIONS


def merged(db, pid, uid, is_admin=True):
    """What GET /api/profiles/{pid}/settings returns: the object settings.json used to be, every key filled in (for
    someone who isn't an admin, the install's only where shown_to)."""
    return nest({k: v for k, (v, _) in effective(db, pid, uid).items() if shown_to(k, is_admin)})


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
            print(f"{where or 'settings.json'}: {k} left at its default: not a value it takes", flush=True)   # (#1065: not
            # the refusal's words, which can quote the value)
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


MIGRATED = "settings-before-v18.json"   # the file as versions before #934 kept it once it was loaded


def migrate_files(db, profile_dir):
    """At start-up: load each profile's settings.json, from before schema version 18, into the table (only what isn't
    set, so a start that stopped half way does no harm), then delete the file (#934: kept, it outlived the settings
    and every import undone since; versions before kept it as settings-before-v18.json, deleted here too).
    profile_dir(pid) is the profile's folder. Returns the number of profiles whose file was loaded."""
    n = 0
    for r in db.execute("SELECT id FROM profiles ORDER BY id").fetchall():
        pid = r["id"]
        with contextlib.suppress(FileNotFoundError):   # loaded by an older version, which kept it
            os.remove(os.path.join(profile_dir(pid), MIGRATED))
        path = os.path.join(profile_dir(pid), "settings.json")
        if not os.path.isfile(path):
            continue
        try:
            obj = read_file(path)
        except Exception as e:   # left where it is, for someone to look at; the app still starts
            print(f"Couldn't move profile {pid}'s settings.json into the database ({type(e).__name__}); "
                  f"{path} is left as it was.", flush=True)
            continue
        with dbm.transaction(db):
            fill_profile(db, pid, from_file(obj, where=f"profile {pid}'s settings.json"))
            db.execute("UPDATE profiles SET settings_seeded=1 WHERE id=?", (pid,))
        os.remove(path)
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
            print(f"The setup build's settings.json can't be read ({type(e).__name__}); settings unchanged.", flush=True)
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
