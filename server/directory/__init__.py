"""Find your provider (#93): search the directory of hospitals', clinics' and insurers' FHIR endpoints.

The directory is read from files, never looked up: the app makes no outside requests for it.
- providers.json (next to this file) ships with releases. It holds only what may be redistributed: the vendors'
  sandboxes and Medicare's Blue Button 2.0. tools/build_directory.py writes it and cites each source's terms.
- $SANGUITY_DATA/directory/providers.json.gz (SANGUITY_DIRECTORY to put it elsewhere) is the install's own copy of
  Epic's and Oracle Health's published lists, which their terms don't let the project redistribute. An admin makes
  it with `python3 tools/build_directory.py` on the host (about 1.1 MB gzipped, 5 MB as JSON, 2,600 entries in
  November 2026). It is read on the first search and again whenever it changes.

Connecting follows option 1 of docs/connected-sources-oauth.md: each install registers its own app with each vendor.
So an entry doesn't say "enabled"; VENDORS says what connecting needs there. Sign-in itself comes with #32.

Search is fuzzy, over names, location names, the health system and city or state: each word of the query must match
a word of the entry exactly, as the start of one, or within one or two typing mistakes (one for words of four to
six letters, two for longer ones).
"""
import bisect, gzip, json, os, threading, unicodedata

from .. import config

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(HERE, "providers.json")
RETURN_PATH = "/connect/return"   # where a vendor sends a sign-in back to (#32); the address to register

VENDORS = {
    "epic": {
        "name": "Epic",
        "realistic": "possible",
        "realistic_label": "Possible with an HTTPS address",
        "developer_site": "https://fhir.epic.com/Developer/Apps",
        "docs": "https://fhir.epic.com/Documentation?docId=oauth2",
        "needs_secret": False,
        "client": "A client ID. Epic issues a non-production one for its sandbox and a production one for hospitals. "
                  "Choose the confidential profile if you want access that lasts (refresh tokens); then you get a "
                  "client secret as well.",
        "steps": [
            "Sign up at Epic on FHIR (fhir.epic.com), then open Build Apps and create an app.",
            "Application audience: Patients. Choose the R4 read APIs for the records you want (Patient, "
            "AllergyIntolerance, Condition, DiagnosticReport, DocumentReference, Encounter, Immunization, "
            "MedicationRequest, Observation, Procedure). Apps using only USCDI APIs are sent to US hospitals "
            "automatically.",
            "Under redirect URIs, paste the return address shown here, exactly.",
            "Save the app, accept Epic's terms, and copy its client ID (and secret, if you chose one). You'll enter "
            "them in Sanguity when sign-in arrives (#32).",
        ],
        "scopes": ["openid", "fhirUser", "launch/patient", "offline_access", "patient/*.read"],
        "note": "Epic's sandbox works from any install. For a real hospital, your install needs a proper HTTPS "
                "address, and the hospital needs your app's client ID: Epic sends it to US provider organisations "
                "automatically for patient apps that use only USCDI APIs. Whether Epic accepts an app registered by "
                "one person, or a tailnet name such as *.ts.net, isn't verified yet.",
    },
    "oracle": {
        "name": "Oracle Health (Cerner)",
        "realistic": "unlikely",
        "realistic_label": "Unlikely for a home install",
        "developer_site": "https://code-console.cerner.com/",
        "docs": "https://docs.oracle.com/en/industries/health/millennium-platform-apis/authorization-framework",
        "needs_secret": False,
        "client": "A client ID from Oracle Health's code Console. A client secret only if your app is registered as "
                  "a confidential (server) app.",
        "steps": [
            "Sign up at Oracle Health's code Console and register a new app of type Patient, as a provider app "
            "for patient access.",
            "Under redirect URI, paste the return address shown here, exactly (trailing slashes count).",
            "Choose the patient scopes listed here, with SMART v1 or v2.",
            "Add a DNS TXT record cerner-client-id=<your client ID> on your address's domain, so the sign-in "
            "page names your app instead of \"Unknown Application\".",
            "Copy the client ID. You'll enter it in Sanguity when sign-in arrives (#32).",
        ],
        "scopes": ["openid", "fhirUser", "launch/patient", "offline_access", "patient/*.read"],
        "note": "Oracle Health is unlikely to work for a home install. Outside its sandbox it wants the return "
                "address on HTTPS, port 443, with a certificate from a public authority (not self-signed), and a DNS "
                "TXT record on that address's domain. A home network address or a ts.net name can't provide those.",
    },
    "cms-bluebutton": {
        "name": "Medicare Blue Button 2.0 (CMS)",
        "realistic": "sandbox",
        "realistic_label": "Sandbox only, for now",
        "developer_site": "https://sandbox.bluebutton.cms.gov/v2/accounts/mfa/login",
        "docs": "https://bluebutton.cms.gov/api-documentation/authorization",
        "needs_secret": True,
        "client": "A client ID and a client secret: Blue Button accepts only confidential apps. Sanguity keeps the "
                  "secret on your server, encrypted.",
        "steps": [
            "Create a Blue Button 2.0 sandbox account and register an app.",
            "Client type: confidential; grant type: authorization code (with PKCE).",
            "Under callback URLs, paste the return address shown here, exactly.",
            "Copy the client ID and client secret. You'll enter them in Sanguity when sign-in arrives (#32).",
        ],
        "scopes": ["openid", "profile", "launch/patient", "patient/Patient.rs", "patient/ExplanationOfBenefit.rs",
                   "patient/Coverage.rs"],
        "note": "Production access means a public privacy policy and terms of service, an application and a demo "
                "to CMS, which is written for organisations, so only the sandbox is realistic for one person "
                "(https://bluebutton.cms.gov/production-access/).",
    },
}

STOP = {"the", "of", "and", "at", "for", "a", "an", "in", "inc", "llc"}
STATES = {"al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
          "ct": "connecticut", "de": "delaware", "dc": "district columbia", "fl": "florida", "ga": "georgia",
          "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
          "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
          "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
          "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
          "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
          "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
          "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
          "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming", "pr": "puerto rico", "gu": "guam",
          "vi": "virgin islands"}
# how much a word counts by where it is in an entry, and by how it matched
W_NAME, W_PLACE, W_ORG, W_ALIAS = 1.0, 0.8, 0.7, 0.6
M_EXACT, M_PREFIX, M_FUZZY = 1.0, 0.85, 0.7
MAX_RESULTS = 25
QUERY_MAX = 200


def words(text):
    """Lower-case words without accents or punctuation."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return "".join(c if c.isalnum() else " " for c in t).split()


def _deletes(w):
    return {w[:i] + w[i + 1:] for i in range(len(w))}


def _distance(a, b, limit):
    """Edit distance with adjacent swaps (optimal string alignment), or limit + 1 once it's over limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _typos(w):
    return 0 if len(w) < 4 else 1 if len(w) < 7 else 2


class Index:
    def __init__(self, doc):
        self.sources = doc.get("sources", [])
        self.built = doc.get("built")
        self.entries, self.by_id = [], {}
        for e in doc.get("entries", []):
            if not (isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("name"), str)
                    and e.get("vendor") in VENDORS and str(e.get("fhir_base", "")).startswith("https://")):
                continue   # only entries #32 could use: a known vendor and an HTTPS base
            if e["id"] not in self.by_id:
                self.by_id[e["id"]] = len(self.entries)
                self.entries.append(e)
        self.postings = {}   # word -> {entry index: weight}
        for i, e in enumerate(self.entries):
            self._add(i, [e["name"]], W_NAME)
            self._add(i, [e.get("org")], W_ORG)
            self._add(i, e.get("aliases") or [], W_ALIAS)
            places = [p for p in e.get("places") or [] if isinstance(p, list)]
            self._add(i, [" ".join(str(x) for x in p) for p in places], W_PLACE)
            self._add(i, [STATES.get(str(p[-1]).lower(), "") for p in places if p], W_PLACE)
        self.vocab = sorted(self.postings)
        self.near = {}   # a word with one letter left out -> the words it comes from
        for w in self.vocab:
            if len(w) >= 4:
                for d in _deletes(w):
                    self.near.setdefault(d, set()).add(w)

    def _add(self, i, texts, weight):
        for t in texts:
            for w in words(t):
                p = self.postings.setdefault(w, {})
                if p.get(i, 0) < weight:
                    p[i] = weight

    def _matches(self, q):
        """The directory's words that match query word q, each with how well."""
        found = {q: M_EXACT} if q in self.postings else {}
        if len(q) >= 2:   # words starting with it
            lo = bisect.bisect_left(self.vocab, q)
            hi = bisect.bisect_left(self.vocab, q + "\x7f")
            for w in self.vocab[lo:hi][:400]:
                found.setdefault(w, M_PREFIX)
        limit = _typos(q)
        if limit:
            close = set(self.near.get(q, ()))   # q with a letter added
            for d in _deletes(q):
                if d in self.postings:   # q with a letter left out
                    close.add(d)
                close |= self.near.get(d, set())   # one changed or swapped letter
                if limit > 1:
                    for d2 in _deletes(d):
                        close |= self.near.get(d2, set())
                        if d2 in self.postings:
                            close.add(d2)
            for w in close:
                if w not in found and _distance(q, w, limit) <= limit:
                    found[w] = M_FUZZY
        return found

    def search(self, query, limit=MAX_RESULTS):
        qs = [w for w in words(query[:QUERY_MAX]) if w not in STOP][:8]
        if not qs:
            return []
        score, hits = {}, {}
        for q in dict.fromkeys(qs):
            best = {}
            for w, m in self._matches(q).items():
                for i, weight in self.postings[w].items():
                    if best.get(i, 0) < weight * m:
                        best[i] = weight * m
            for i, s in best.items():
                score[i] = score.get(i, 0) + s
                hits[i] = hits.get(i, 0) + 1
        need = len(dict.fromkeys(qs))
        found = [i for i in score if hits[i] == need] or (
            [i for i in score if hits[i] == need - 1] if need > 2 else [])
        phrase = " ".join(qs)

        def rank(i):
            e = self.entries[i]
            name = " ".join(words(e["name"]))
            bonus = 0.5 if name.startswith(phrase) else 0.25 if phrase in name else 0
            return (-(score[i] + bonus), bool(e.get("sandbox")), len(e["name"]), e["name"])
        return [self.entries[i] for i in sorted(found, key=rank)[:limit]]


_lock = threading.Lock()
_cache = {"key": None, "index": None}


def local_path():
    return os.environ.get("SANGUITY_DIRECTORY") or os.path.join(config.DATA_DIR, "directory", "providers.json.gz")


def _read(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        doc = json.loads(f.read().decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("entries"), list):
        raise ValueError(f"{path} isn't a directory file this version reads")
    return doc


def index():
    """The directory: the install's own copy when there is one, with any bundled entries it lacks."""
    path = local_path()
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    with _lock:
        if _cache["index"] is None or _cache["key"] != key:
            doc = _read(BUNDLED)
            if key:
                try:
                    local = _read(path)
                    have = {e.get("id") for e in local["entries"] if isinstance(e, dict)}
                    doc = {**local, "entries": local["entries"] + [e for e in doc["entries"] if e["id"] not in have]}
                except (OSError, ValueError) as e:   # a broken copy: keep to the bundled list, and say why
                    print(f"The provider directory {path} couldn't be read ({e}); using the bundled list only.", flush=True)
            _cache.update(key=key, index=Index(doc))
        return _cache["index"]
