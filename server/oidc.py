"""Sign in with OIDC (#1109): one identity provider for the install (Authentik, Keycloak, Authelia, Google...), and the
identities each person links to their account.

The owner's decisions (2026-11-28):
  - linked by each person (Settings → You → Sign-in and security, after the fresh check) or by an admin for an account
    (Install → Accounts, by the identity's subject); no account is made automatically, and an identity linked to no
    account is refused with a plain message;
  - an OIDC sign-in counts as the second factor when the ID token says so (amr says "mfa" or names methods of two
    factors, or acr is the one the admin set: multi_factor); otherwise the account's own second factor (a code or a passkey) is asked for
    after it, as after a password, and where two-factor sign-in is required an account with none sets one up;
  - "Sign in with OIDC only": password sign-in and a passkey on its own refused, the switch itself refused until the
    admin's own identity has signed in with the provider as it's set up now; python -m server.manage oidc-only off
    turns it back.

The flow is the authorization code flow with PKCE (S256), state and nonce:
  start()   a random state, nonce and PKCE verifier, kept here (in memory: a restart means starting again) for
            STATE_TTL seconds against a random browser token, set as a cookie (BROWSER_COOKIE, HttpOnly, SameSite=Strict)
            that only this browser sends back; the provider's authorization address to go to.
  finish()  from the sign-in page, where the provider sent the browser back (same-origin, so the Strict cookie is
            sent): the state is used up first, whatever follows; refused unless this browser's token is the one it was
            issued with, unexpired, for the provider still set up. The code is exchanged at the token endpoint with the
            verifier only the server holds, and the ID token checked (verify): its signature against the provider's
            JWKS with `cryptography` (RS256 or ES256 only: "none", the HS* family and anything else refused), then iss,
            aud (and azp), exp, iat, nbf and the nonce, with SKEW seconds of clock difference.
The redirect URI is the sign-in page at the install's public address (else the page's own): <origin>/login.html, as
registered with the provider, exactly.

Every contact goes through outside.py's gateway (net.Permit) for PURPOSE: the issuer's host, and the hosts its own
discovery document names for the token endpoint and the JWKS; none at all while no provider is set up. The browser,
not the server, visits the authorization endpoint.

The provider's settings are the install's (meta OIDC_META, plain: none is a secret), its client secret apart
(SECRET_META, encrypted with the data key, never given to a page), or the server's environment's OIDC_CLIENT_SECRET,
which wins (#999's rule). Linked identities: the oidc_identities table (schema 49): issuer, subject (the ID token's
sub), the account, when linked and last used, and the name the provider gave for it (encrypted), to show.
"""
import base64, hashlib, hmac, ipaddress, json, re, secrets, threading, time, urllib.parse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from . import config, crypto, db as dbm, net, outside, smart

OIDC_META, SECRET_META = "oidc", "oidc.secret"
SECRET_ENV = "OIDC_CLIENT_SECRET"
PURPOSE = "Sign-in provider"
BROWSER_COOKIE = "sanguity_oidc"
COOKIE_PATH = "/api/login/oidc"
RETURN_PATH = "/login.html"
SCOPES = "openid email profile"
LABEL = "single sign-on"
STATE_TTL = 600        # seconds from Sign in with... to coming back
SKEW = 120             # seconds of clock difference allowed for exp, iat and nbf
IAT_MOST = 600         # an ID token issued longer ago than this (beyond SKEW) is refused: it was just asked for
CACHE_SECONDS = 600    # the discovery document and JWKS are read again after this
REFETCH_SECONDS = 30   # an unknown key id reads the JWKS again, at most this often
MAX_BODY = 2**20
# RFC 8176's authentication methods by the factor each is: an amr naming methods of two factors (or "mfa") says more
# than one factor was used; methods of one factor (pwd and pin; or an emailed code's otp alone) are one. The rest (user,
# pop, rba, geo, mca, wia, and any not listed) say nothing about factors and count for none
FACTORS = {"pwd": "knowledge", "pin": "knowledge", "kba": "knowledge",
           "hwk": "possession", "swk": "possession", "otp": "possession", "sms": "possession", "tel": "possession", "sc": "possession",
           "fpt": "inherence", "face": "inherence", "iris": "inherence", "retina": "inherence", "vbm": "inherence"}
ALGS = {"RS256": "RSA", "ES256": "EC"}
PURPOSES = ("login", "link", "reauth")   # reauth: the fresh check at the provider (prompt=login, max_age=0), #1109's review
STARTS_MOST = 30   # links and fresh checks at the provider started by one account in STATE_TTL seconds, at most
FIELDS = ("issuer", "client_id", "label", "scopes", "acr")


class OidcError(Exception):
    """A refusal, said plainly: its HTTP status and words."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- the provider's settings

def _check_issuer(v):
    v = (v or "").strip() if isinstance(v, str) else None
    if v is None:
        raise OidcError("The issuer must be an address.")
    if not v:
        return ""
    u = urllib.parse.urlsplit(v)
    if (len(v) > 300 or u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.query
            or u.fragment or " " in v):
        raise OidcError("The issuer must be a full https:// address with no ?… or #…, like https://auth.example.com/realms/home.")
    if u.scheme == "http" and not config.CONNECT_ALLOW_HTTP:
        raise OidcError("The issuer must use https://.")
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), u.path, "", ""))


_TOKEN = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")   # a scope token (RFC 6749 §3.3); a client ID and an acr alike


def _check_word(v, what, most):
    if not isinstance(v, str):
        raise OidcError(f"{what} must be some text.")
    v = v.strip()
    if v and (len(v) > most or not _TOKEN.fullmatch(v)):
        raise OidcError(f"{what} can't have spaces, and is at most {most} characters.")
    return v


def _check_scopes(v):
    if not isinstance(v, str):
        raise OidcError("The scopes must be some text.")
    words = list(dict.fromkeys(v.split())) or SCOPES.split()
    if len(words) > 20 or any(len(w) > 100 or not _TOKEN.fullmatch(w) for w in words):
        raise OidcError("The scopes are words with spaces between them, like openid email profile.")
    if "openid" not in words:
        raise OidcError("The scopes must include openid.")
    return " ".join(words)


def _check_label(v):
    v = " ".join(v.split()) if isinstance(v, str) else ""
    if len(v) > 40:
        raise OidcError("The button's name is at most 40 characters.")
    return v or LABEL


CHECKS = {"issuer": _check_issuer, "client_id": lambda v: _check_word(v, "The client ID", 255), "label": _check_label,
          "scopes": _check_scopes, "acr": lambda v: _check_word(v, "The acr", 200)}
DEFAULTS = {"issuer": "", "client_id": "", "label": LABEL, "scopes": SCOPES, "acr": "", "only": False, "changed": 0}


def load(db):
    """The provider as set up: {issuer, client_id, label, scopes, acr, only, changed} (defaults where unset)."""
    try:
        have = json.loads(dbm.meta_get(db, OIDC_META) or "{}")
    except ValueError:
        have = {}
    return {**DEFAULTS, **{k: v for k, v in have.items() if k in DEFAULTS}}


def configured(cfg):
    return bool(cfg["issuer"] and cfg["client_id"])


def only(db):
    """Sign in with OIDC only: on, and a provider set up."""
    cfg = load(db)
    return configured(cfg) and bool(cfg["only"])


def secret_source(db):
    """Where the client secret comes from: "install" or None (a public client: PKCE alone). (#1158) The environment's
    OIDC_CLIENT_SECRET was taken in once (settings_import.py), and isn't read after."""
    return "install" if dbm.meta_get(db, SECRET_META) else None


def client_secret(db):
    stored = dbm.meta_get(db, SECRET_META)
    return crypto.dec(stored) if stored else ""


def clean(cfg, changes):
    """cfg with changes (any of FIELDS) checked and applied. Raises OidcError."""
    out = dict(cfg)
    for k, v in changes.items():
        if k in CHECKS and v is not None:
            out[k] = CHECKS[k](v)
    return out


def save(db, cfg, secret=None):
    """Store the provider (cfg as clean made it); secret: a new client secret, "" to remove it, None to keep it."""
    before = load(db)
    if any(cfg[k] != before[k] for k in ("issuer", "client_id")) or secret is not None:
        cfg = {**cfg, "changed": dbm.now()}   # its identities' sign-ins before this don't show it works (may_go_only)
    if not configured(cfg):
        cfg = {**cfg, "only": False}
    dbm.meta_set(db, OIDC_META, json.dumps({k: cfg[k] for k in DEFAULTS}, separators=(",", ":")))
    if secret is not None:
        dbm.meta_set(db, SECRET_META, crypto.enc(secret) if secret else None)
    _cache.clear()


def issuer_key(issuer):
    """The issuer as linked identities are kept by: without a trailing slash, so retyping one doesn't unlink anyone."""
    return (issuer or "").rstrip("/")


def redirect_uri(page_origin=None):
    """<origin>/login.html: the install's public address (config.public_address), else the page's own."""
    origin = config.public_address() or page_origin
    return (origin.rstrip("/") + RETURN_PATH) if origin else None


# ---------------------------------------------------------------- contact: the gateway, discovery and the JWKS

class _Cache:
    def __init__(self):
        self.lock, self.disc, self.jwks, self.fetched = threading.Lock(), {}, {}, {}

    def clear(self):
        with self.lock:
            self.disc.clear(); self.jwks.clear(); self.fetched.clear()


_cache = _Cache()


def _hosts(db):
    """The hosts a provider's contact may reach: none until one is set up; its issuer's, and the token endpoint's and
    the JWKS's as its own discovery document names them."""
    cfg = load(db)
    if not configured(cfg):
        return set()
    hosts = {net.host_of(cfg["issuer"])}
    with _cache.lock:
        conf = (_cache.disc.get(cfg["issuer"]) or (0, None))[1]
    for k in ("token_endpoint", "jwks_uri"):
        if conf and isinstance(conf.get(k), str):
            hosts.add(net.host_of(conf[k]))
    return hosts


def hosts(db):
    return sorted(net.norm(h) for h in _hosts(db))


def client(db, who=None, ip=None):
    permit = net.Permit(PURPOSE, lambda: _hosts(db), lambda host, purpose: outside.record(db, None, host, purpose, who, ip))
    return smart.Client(permit, timeout=min(15, config.setting("CONNECT_TIMEOUT")))


def _get_json(c, url, what):
    try:
        r = c.request("GET", url, {"Accept": "application/json"}, max_bytes=MAX_BODY, retry=False)
    except smart.SmartError as e:
        raise OidcError(f"The sign-in provider couldn't be reached for its {what}: {e}", 502) from None
    data = r.json()
    if r.status != 200 or not isinstance(data, dict):
        raise OidcError(f"The sign-in provider's {what} wasn't there ({url} answered {r.status}).", 502)
    return data


def discover(db, c, force=False):
    """The provider's .well-known/openid-configuration, checked: its issuer is the one set up (a trailing slash aside),
    an authorization endpoint, a token endpoint and a JWKS, each https; the code flow; PKCE with S256 where it lists
    its methods; RS256 or ES256 among its ID token algorithms where it lists them."""
    issuer = load(db)["issuer"]
    with _cache.lock:
        got = _cache.disc.get(issuer)
    if got and not force and time.monotonic() - got[0] < CACHE_SECONDS:
        return got[1]
    conf = _get_json(c, issuer.rstrip("/") + "/.well-known/openid-configuration", "discovery document")
    said = conf.get("issuer")
    if not isinstance(said, str) or issuer_key(said) != issuer_key(issuer):
        raise OidcError(f"The provider's discovery document names its issuer as {str(said)[:200]}, not {issuer}. "
                        "Copy the issuer exactly as the provider shows it.", 502)
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        try:
            smart.check_url(conf.get(k), k.replace("_", " "))
        except smart.SmartError:
            raise OidcError(f"The provider's discovery document has no https:// {k.replace('_', ' ')}.", 502) from None
    if isinstance(conf.get("response_types_supported"), list) and "code" not in conf["response_types_supported"]:
        raise OidcError("The provider doesn't offer the authorization code flow, which Sanguity uses.", 502)
    if isinstance(conf.get("code_challenge_methods_supported"), list) and "S256" not in conf["code_challenge_methods_supported"]:
        raise OidcError("The provider doesn't offer PKCE with S256, which Sanguity requires.", 502)
    algs = conf.get("id_token_signing_alg_values_supported")
    if isinstance(algs, list) and not set(algs) & set(ALGS):
        raise OidcError("The provider doesn't sign ID tokens with RS256 or ES256, the ones Sanguity accepts.", 502)
    with _cache.lock:
        _cache.disc[issuer] = (time.monotonic(), conf)
    return conf


def _jwks(c, uri, force=False):
    with _cache.lock:
        got = _cache.jwks.get(uri)
        if force and time.monotonic() - _cache.fetched.get(uri, -1e9) < REFETCH_SECONDS:
            force = False   # read moments ago: an unknown key id isn't a reason to ask again so soon
    if got and not force and time.monotonic() - got[0] < CACHE_SECONDS:
        return got[1]
    keys = _get_json(c, uri, "signing keys (JWKS)").get("keys")
    keys = [k for k in keys if isinstance(k, dict)][:50] if isinstance(keys, list) else []
    with _cache.lock:
        _cache.jwks[uri] = (time.monotonic(), keys)
        _cache.fetched[uri] = time.monotonic()
    return keys


def test(db, who=None, ip=None):
    """Test connection: the discovery document read again, and the JWKS; what it says, for the card."""
    if not configured(load(db)):
        raise OidcError("Set the issuer and the client ID first.", 409)
    c = client(db, who, ip)
    conf = discover(db, c, force=True)
    keys = _jwks(c, conf["jwks_uri"], force=True)
    usable = [k for k in keys if _usable(k)]
    if not usable:
        raise OidcError("The provider's JWKS has no RS256 or ES256 signing key Sanguity can use.", 502)
    return {"issuer": conf["issuer"], "keys": len(usable), "hosts": hosts(db)}


# ---------------------------------------------------------------- the ID token

def _b64d(part):
    if not isinstance(part, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", part):
        raise OidcError("The ID token isn't one Sanguity can read.", 401)
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (ValueError, TypeError):
        raise OidcError("The ID token isn't one Sanguity can read.", 401) from None


def _int(b):
    return int.from_bytes(_b64d(b), "big")


def _usable(k, alg=None):
    """A signing key of the JWKS for alg (any accepted one, without): its kind, and its use, alg and curve where it
    says them."""
    return any(k.get("kty") == ALGS[a] and k.get("use", "sig") == "sig" and k.get("alg", a) == a
               and (ALGS[a] != "EC" or k.get("crv") == "P-256") for a in ([alg] if alg else ALGS))


def _verify_with(k, alg, signed, sig):
    try:
        if alg == "RS256":
            key = rsa.RSAPublicNumbers(_int(k.get("e", "")), _int(k.get("n", ""))).public_key()
            if key.key_size < 2048:
                return False
            key.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
            return True
        if len(sig) != 64:
            return False
        key = ec.EllipticCurvePublicNumbers(_int(k.get("x", "")), _int(k.get("y", "")), ec.SECP256R1()).public_key()
        key.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")), signed,
                   ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError, OidcError):
        return False


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def verify(token, keys_for, issuer, client_id, nonce, now=None):
    """The claims of ID token `token`, once its signature is a key's of the provider (keys_for(force) -> the JWKS's
    keys; asked again, forced, for a key id it doesn't have) and its claims are right. Raises OidcError (401)."""
    bad = lambda why: OidcError(f"The sign-in provider's ID token was refused: {why}.", 401)
    if not isinstance(token, str) or len(token) > 16384 or token.count(".") != 2:
        raise bad("it isn't a signed token")
    h, p, s = token.split(".")
    try:
        header = json.loads(_b64d(h))
    except ValueError:
        raise bad("its header isn't JSON") from None
    alg = header.get("alg") if isinstance(header, dict) else None
    if alg not in ALGS:   # none, HS256 (a public key used as a shared secret) and every other alg: never
        raise bad(f"it's signed with {str(alg)[:20]}, and Sanguity accepts only RS256 and ES256")
    if "crit" in header or "jwk" in header or "jku" in header or "x5u" in header:
        raise bad("its header names keys or extensions of its own")
    kid, signed, sig = header.get("kid"), f"{h}.{p}".encode("ascii"), _b64d(s)
    for force in (False, True):
        keys = [k for k in keys_for(force) if _usable(k, alg) and (kid is None or k.get("kid") == kid)]
        if any(_verify_with(k, alg, signed, sig) for k in keys[:10]):
            break
        if keys:   # the key named is there, and the signature isn't its: no reason to read the JWKS again
            raise bad("its signature isn't the provider's")
    else:
        raise bad("its signature isn't the provider's")
    try:
        claims = json.loads(_b64d(p))
    except ValueError:
        raise bad("its claims aren't JSON") from None
    if not isinstance(claims, dict):
        raise bad("its claims aren't JSON")
    now = time.time() if now is None else now
    if claims.get("iss") != issuer:
        raise bad(f"it was issued by {str(claims.get('iss'))[:200]}, not {issuer}")
    aud = claims.get("aud")
    auds = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) and all(isinstance(a, str) for a in aud) else []
    if client_id not in auds:
        raise bad("it's for another client")
    if (len(auds) > 1 or "azp" in claims) and claims.get("azp") != client_id:
        raise bad("it was asked for by another client (azp)")
    if not _number(claims.get("exp")) or claims["exp"] + SKEW <= now:
        raise bad("it has expired")
    if not _number(claims.get("iat")) or claims["iat"] > now + SKEW or claims["iat"] < now - IAT_MOST - SKEW:
        raise bad("its time of issue isn't now (check the clocks of this server and the provider)")
    if "nbf" in claims and (not _number(claims["nbf"]) or claims["nbf"] > now + SKEW):
        raise bad("it isn't valid yet")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"].encode(), nonce.encode()):
        raise bad("its nonce isn't this sign-in's")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not 0 < len(sub) <= 255:
        raise bad("it names no subject")
    return claims


def multi_factor(claims, acr):
    """Whether the provider says the person used more than one factor: amr says "mfa", or names methods of two factors
    (FACTORS: knowledge, possession, inherence), or acr is the one the admin set. Methods of one factor (a password and
    a PIN; a one-time code alone; a password with risk-based or location checks) are one."""
    amr = claims.get("amr")
    said = [a for a in amr if isinstance(a, str)] if isinstance(amr, list) else []
    if "mfa" in said or len({FACTORS[a] for a in said if a in FACTORS}) >= 2:
        return True
    return bool(acr) and claims.get("acr") == acr


# ---------------------------------------------------------------- the flow

def _hash(v):
    return hashlib.sha256(v.encode()).hexdigest()


def address_group(ip):
    """Where sign-ins are counted from: an IPv4 address, or an IPv6 address's /64 (one household's, or one attacker's);
    one that carries an IPv4 address (mapped, 6to4's 2002::/16, Teredo's 2001::/32: #1111) by that address, as every
    /64 of it is the same host's."""
    try:
        a = ipaddress.ip_address(str(ip or "").split("%")[0])
    except ValueError:
        return str(ip or "?")[:64]
    if a.version == 6:
        v4 = a.ipv4_mapped or a.sixtofour or (a.teredo[1] if a.teredo else None)
        return str(v4) if v4 else str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


class _Pending:
    """Sign-ins started and not yet back: state hash -> what it was started with. Used up as it comes back.

    A sign-in's start (nobody signed in yet) is never refused (#1109's recheck: behind a reverse proxy every visitor
    shares one address, so refusing an address's starts would let anyone keep everyone out): its group is its address
    (address_group: an IPv6 one's /64), and once MAX are waiting, the adding group's own oldest goes first, then the
    oldest of all, so a flood drops only its own. A link or a fresh check at the provider (signed in) is the account's:
    at most STARTS_MOST in STATE_TTL seconds (else 429, which keeps out only that account) and PER_ACCOUNT waiting."""
    MAX, PER_ACCOUNT = 10000, 20
    SWEEP = 60   # (#1111) seconds between sweeps of the accounts' start times: never rebuilt on every start

    def __init__(self):
        self._p, self._lock, self._starts, self._swept = {}, threading.Lock(), {}, 0.0

    def _sweep(self, now):
        """The accounts' start times older than STATE_TTL gone, at most every SWEEP seconds; and never more than MAX
        accounts kept, the longest idle going first (#1111)."""
        if now - self._swept < self.SWEEP and len(self._starts) <= self.MAX:
            return
        self._swept = now
        live = {g: t for g, t in self._starts.items() if t and t[-1] > now - STATE_TTL}
        if len(live) > self.MAX:
            live = dict(sorted(live.items(), key=lambda gt: gt[1][-1])[-self.MAX:])
        self._starts = live

    def add(self, state, entry):
        now = time.time()
        group = f"account {entry['uid']}" if entry.get("uid") else address_group(entry.get("ip"))
        with self._lock:
            if entry.get("uid"):
                self._sweep(now)
                times = [t for t in self._starts.get(group, []) if t > now - STATE_TTL]
                if len(times) >= STARTS_MOST:
                    raise OidcError("Too many were started from your account. Try again in a few minutes.", 429)
                self._starts[group] = times + [now]
            entry = {**entry, "group": group}
            self._p = {k: v for k, v in self._p.items() if v["expires"] > now}
            mine = [k for k, v in self._p.items() if v.get("group") == group]   # (oldest first: the order added)
            if entry.get("uid"):
                for k in mine[:max(0, len(mine) - self.PER_ACCOUNT + 1)]:
                    del self._p[k]
                mine = [k for k in mine if k in self._p]
            while len(self._p) >= self.MAX:
                del self._p[mine.pop(0) if mine else next(iter(self._p))]
            self._p[_hash(state)] = entry

    def take(self, state):
        with self._lock:
            return self._p.pop(_hash(state), None) if isinstance(state, str) and 0 < len(state) <= 200 else None

    def clear(self):
        with self._lock:
            self._p.clear()
            self._starts.clear()


pending = _Pending()


def start(db, purpose, page_origin, uid=None, sid=None, who=None, ip=None):
    """(the provider's authorization address, the browser token to set as BROWSER_COOKIE)."""
    cfg = load(db)
    if not configured(cfg):
        raise OidcError("No sign-in provider is set up on this server.", 404)
    redirect = redirect_uri(page_origin)
    if not redirect:
        raise OidcError("This server has no address to come back to.", 409)
    conf = discover(db, client(db, who, ip))
    state, nonce, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = smart.pkce()
    pending.add(state, {"browser": _hash(browser), "nonce": nonce, "verifier": verifier, "purpose": purpose, "uid": uid,
                        "sid": sid, "issuer": cfg["issuer"], "client_id": cfg["client_id"], "redirect": redirect,
                        "expires": time.time() + STATE_TTL, "ip": ip, "started": time.time()})
    q = {"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": redirect, "scope": cfg["scopes"],
         "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    if purpose in ("link", "reauth"):
        q["prompt"] = "login"   # the identity linked is the one signed in now, not one a shared browser remembered
    if purpose == "reauth":
        q["max_age"] = "0"   # signed in again now: its ID token's auth_time says when (finish)
    ep = conf["authorization_endpoint"]
    return ep + ("&" if "?" in ep else "?") + urllib.parse.urlencode(q), browser


def _exchange(db, c, conf, cfg, code, entry):
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": entry["redirect"],
            "code_verifier": entry["verifier"]}
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    secret, methods = client_secret(db), conf.get("token_endpoint_auth_methods_supported")
    if secret and isinstance(methods, list) and "client_secret_basic" not in methods and "client_secret_post" in methods:
        form.update(client_id=cfg["client_id"], client_secret=secret)
    elif secret:   # client_secret_basic (RFC 6749 §2.3.1), each part form-encoded first
        pair = f"{urllib.parse.quote_plus(cfg['client_id'])}:{urllib.parse.quote_plus(secret)}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    else:
        form["client_id"] = cfg["client_id"]
    try:
        r = c.request("POST", conf["token_endpoint"], headers, urllib.parse.urlencode(form).encode(), max_bytes=MAX_BODY, retry=False)
    except smart.SmartError as e:
        raise OidcError(f"The sign-in provider couldn't be reached to finish the sign-in: {e}", 502) from None
    data = r.json()
    data = data if isinstance(data, dict) else {}
    if r.status != 200 or not isinstance(data.get("id_token"), str):
        err = str(data.get("error") or "")[:60]
        if err == "invalid_client" or r.status == 401:
            raise OidcError("The sign-in provider refused Sanguity's client ID or secret. Check Settings → Install → "
                            "Accounts and sign-in → Sign-in provider.", 502)
        raise OidcError("The sign-in provider didn't finish the sign-in" + (f" ({err})" if err else f" (it answered {r.status})")
                        + ". Start again.", 401)
    return data["id_token"]


def finish(db, state, code, browser, error=None, who=None, ip=None):
    """(what start() kept, the ID token's claims, whether the provider says more than one factor was used)."""
    entry = pending.take(state)   # used up now, whatever follows: a state is never good twice
    if not entry or entry["expires"] <= time.time():
        raise OidcError("That sign-in took too long, or was used already. Start it again.", 401)
    if not isinstance(browser, str) or not hmac.compare_digest(_hash(browser), entry["browser"]):
        raise OidcError("That sign-in was started in another browser, or this one's cookies were cleared. Start it again here.", 401)
    if error:
        raise OidcError("The sign-in provider didn't sign you in" + (f" ({str(error)[:60]})" if error != "access_denied" else "")
                        + ". Nothing changed here.", 401)
    cfg = load(db)
    if not configured(cfg) or (cfg["issuer"], cfg["client_id"]) != (entry["issuer"], entry["client_id"]):
        raise OidcError("The sign-in provider was changed meanwhile. Start again.", 409)
    if not isinstance(code, str) or not 0 < len(code) <= 2000:
        raise OidcError("The sign-in provider sent no code back. Start again.", 401)
    c = client(db, who, ip)
    conf = discover(db, c)
    token = _exchange(db, c, conf, cfg, code, entry)
    claims = verify(token, lambda force: _jwks(c, conf["jwks_uri"], force), conf["issuer"], cfg["client_id"], entry["nonce"])
    # signed in at the provider after this check was started (whole seconds): no skew back, so a provider that ignored
    # max_age=0 and prompt=login doesn't pass on a sign-in from minutes before (#1109's recheck)
    if entry["purpose"] == "reauth" and not (_number(claims.get("auth_time")) and claims["auth_time"] >= int(entry["started"])):
        raise OidcError("The sign-in provider didn't ask you to sign in again, so it can't confirm it's you now. Try again, "
                        "or use a code or a passkey.", 401)
    return entry, claims, multi_factor(claims, cfg["acr"])


# ---------------------------------------------------------------- linked identities

def shown_name(claims):
    for k in ("preferred_username", "email", "name"):
        if isinstance(claims.get(k), str) and claims[k].strip():
            return " ".join(claims[k].split())[:120]
    return None


def account_for(db, issuer, sub):
    r = db.execute("SELECT user_id FROM oidc_identities WHERE issuer=? AND subject=?", (issuer_key(issuer), sub)).fetchone()
    return r["user_id"] if r else None


def used(db, issuer, sub, name=None):
    """It signed in: when, and the name the provider gives it now (an admin's link by subject shows it from then on)."""
    db.execute("UPDATE oidc_identities SET last_used=?, name=COALESCE(?, name) WHERE issuer=? AND subject=?",
               (dbm.now(), crypto.enc(name) if name else None, issuer_key(issuer), sub))


def link(db, uid, issuer, sub, name=None, by=None):
    """Link identity (issuer, sub) to account uid. Raises OidcError: linked already, here or to another account; or
    this account has one at this provider already."""
    key = issuer_key(issuer)
    have = db.execute("SELECT user_id FROM oidc_identities WHERE issuer=? AND subject=?", (key, sub)).fetchone()
    if have:
        raise OidcError("That identity is linked to " + ("this account already." if have["user_id"] == uid else
                        "another account here. Its person, or an admin, unlinks it there first."), 409)
    if db.execute("SELECT 1 FROM oidc_identities WHERE issuer=? AND user_id=?", (key, uid)).fetchone():
        raise OidcError("This account has an identity at this provider linked already: unlink it first.", 409)
    return db.execute("INSERT INTO oidc_identities(issuer, subject, user_id, name, created, linked_by) VALUES(?, ?, ?, ?, ?, ?)",
                      (key, sub, uid, crypto.enc(name) if name else None, dbm.now(), by)).lastrowid


def listing(db, uid, cfg=None):
    cfg = cfg or load(db)
    return [{"id": r["id"], "subject": r["subject"], "name": crypto.dec(r["name"]) if r["name"] else None,
             "created": r["created"], "last_used": r["last_used"], "current": r["issuer"] == issuer_key(cfg["issuer"]),
             # (an admin's link by subject: who, said to its person until it's used, #1109's review)
             "linked_by_admin": r["by"] is not None and r["by_id"] != uid, "linked_by": r["by"]}
            for r in db.execute("""SELECT i.*, b.username AS by, b.id AS by_id FROM oidc_identities i
                                   LEFT JOIN users b ON b.id = i.linked_by WHERE i.user_id=? ORDER BY i.id""", (uid,))]


def works_for(db, uid):
    """Whether account uid's identity at the provider as set up now has signed in with it since it was set up so."""
    cfg = load(db)
    return configured(cfg) and db.execute(
        "SELECT 1 FROM oidc_identities WHERE user_id=? AND issuer=? AND last_used>=?",
        (uid, issuer_key(cfg["issuer"]), cfg["changed"])).fetchone() is not None


def brief(db):
    """What every page may know (/api/me, the sign-in page): the button's name and whether it's the only way in; None
    while no provider is set up."""
    cfg = load(db)
    return {"label": cfg["label"], "only": bool(cfg["only"])} if configured(cfg) else None


def current_count(db, uid):
    cfg = load(db)
    return db.execute("SELECT COUNT(*) FROM oidc_identities WHERE user_id=? AND issuer=?",
                      (uid, issuer_key(cfg["issuer"]))).fetchone()[0] if configured(cfg) else 0
