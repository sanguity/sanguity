"""SMART Health Links and SMART Health Cards (issue #94): reading them, fetching what a link points to, decrypting it.

A SMART Health Link (https://docs.smarthealthit.org/smart-health-links/spec/) is "shlink:/" and a base64url JSON
payload, alone or after "#" in a viewer's address:

  {"url": manifest or file address, "key": 32 bytes base64url, "exp": seconds, "flag": "L" "P" "U", "label": ≤80 chars}

  L  long-term: the link may be used again later (Sanguity fetches once, when asked)
  P  a passcode is needed, sent with the manifest request
  U  url is the one encrypted file itself (GET ?recipient=...), not a manifest

The manifest (POST {"recipient", "passcode"}) lists files, each embedded or at a short-lived location; each file is a
compact JWE (alg "dir", enc "A256GCM", optionally "zip": "DEF") encrypted with the link's key. Its content type is
application/fhir+json (a FHIR resource, usually a Bundle) or application/smart-health-card ({"verifiableCredential":
[JWS, ...]}: SMART Health Cards, https://spec.smarthealth.cards/, signed ES256 by their issuer, whose keys are at
<iss>/.well-known/jwks.json). A Health Card can also be pasted on its own, as its QR code's "shc:/" numeric text.

This module knows nothing of the web routes (routes_shl.py) or of uploads, so share links (#47) can make and read
the same format:

  parse(text) -> Link                 a shlink:/ or shc:/ text; no network
  describe(link) -> dict              what to show before anything is fetched: host, label, flags; never the key
  encode(url, key, ...) -> str        a shlink:/ text           encrypt(data, key, cty) -> compact JWE
  decrypt(jwe, key) -> (bytes, cty)
  fetch(link, passcode, permit) -> [{"cty", "data"}]   the one outbound fetch (plus a GET per file the manifest
                                                       doesn't embed), decrypted
  read_cards(data, permit) -> [card]   Health Cards: payload, FHIR bundle, signature verified / invalid / unchecked
  issuers(data) -> {host}             the hosts a Health Card file's cards would have their issuers' keys fetched from
  sign_card(payload, private_key, kid) -> JWS   for tests and #47

Outbound requests (_request) go through net.py under a permit (outside.for_link: the hosts the person confirmed).
The page names the link's host (or a pasted card's issuer) before Fetch and preview; a host the link itself names
beyond those (a file's location on another server, a Health Card's issuer, whose /.well-known/jwks.json would learn
this server's address and when the link was read) is never contacted unconfirmed: the fetch stops before it with Ask,
naming each, and the page asks, then fetches again with them confirmed (#919). HTTPS only; the host's addresses
must all be public (no loopback, private, link-local or reserved address: SSRF), and the connection goes to the
address that was checked, so a DNS answer can't change in between; redirects only to the same host; a timeout and a
size cap (config.SHL_*). net.py records each host before anything is sent to it (outside.record). The key and the
passcode are never logged, never stored and never part of an error message.
"""
import base64, ipaddress, json, os, re, ssl, time, urllib.parse, zlib

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config, net

RECIPIENT = "Sanguity (self-hosted personal health records)"   # who is asking; never the person's name


def max_bytes():
    """SHL_MAX_MB, read at each use: what one link may bring in all (the manifest and every file in it)."""
    return config.setting("SHL_MAX_MB") * config.MB


FHIR, CARD = "application/fhir+json", "application/smart-health-card"
FLAGS = {"L": "long-term link", "P": "passcode needed", "U": "a single file"}
# tests only: a local test server (loopback address, its own certificate). Never set from the environment.
ALLOW_PRIVATE = False
CA_FILE = None


class Refused(Exception):
    """A plain-language reason, safe to show and to log (no key, no passcode). status: the HTTP status to answer."""
    def __init__(self, message, status=400, remaining=None):
        super().__init__(message)
        self.status = status
        self.remaining = remaining   # passcode attempts left, when the link's server says


class Ask(Refused):
    """The fetch stopped before contacting hosts the person hasn't confirmed (#919): files: the hosts of files the
    manifest lists by location; issuers: of Health Cards' issuers, whose keys check their signatures. Nothing has been
    sent to any of them. 409: the page names them and asks, then asks for the link again with them confirmed."""
    def __init__(self, files=(), issuers=()):
        self.files, self.issuers = sorted(set(files)), sorted(set(issuers) - set(files))
        self.hosts = self.files + self.issuers
        super().__init__(f"This link also names {_listed(self.hosts)}, which Sanguity hasn't contacted: confirm "
                         f"{'it' if len(self.hosts) == 1 else 'them'} first.", 409)


def _listed(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


# ---------------------------------------------------------------- base64url

def b64d(s):
    if not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", s):
        raise ValueError("not base64url")
    s = s.rstrip("=")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------- the link

class Link:
    """A parsed link. kind "link" (a SMART Health Link) or "card" (a Health Card's shc:/ text, in jws)."""
    __slots__ = ("kind", "url", "key", "exp", "flags", "label", "jws")

    def __init__(self, kind, url=None, key=None, exp=None, flags="", label=None, jws=None):
        self.kind, self.url, self.key, self.exp, self.flags, self.label, self.jws = kind, url, key, exp, flags, label, jws

    def __repr__(self):   # never the key
        return f"Link({self.kind}, host={host_of(self.url) if self.url else None!r}, flags={self.flags!r})"


_SHLINK = re.compile(r"shlink:/([A-Za-z0-9_=-]+)")


def parse(text):
    """A pasted SMART Health Link (shlink:/..., or a viewer address with #shlink:/...) or Health Card (shc:/...)
    as a Link, or Refused. Nothing is fetched."""
    if not isinstance(text, str) or not text.strip():
        raise Refused("Paste a SMART Health Link: it starts with shlink:/ or holds #shlink:/.")
    text = text.strip()
    if len(text) > 20000:
        raise Refused("That's too long to be a SMART Health Link.")
    if text.lower().startswith("shc:/"):
        return Link("card", jws=_shc_numeric(text))
    m = _SHLINK.search(text)
    if not m or not (text.startswith("shlink:/") or "#shlink:/" in text):
        raise Refused("That isn't a SMART Health Link: it starts with shlink:/, or a viewer's address holds #shlink:/.")
    try:
        p = json.loads(b64d(m.group(1)))
    except (ValueError, UnicodeDecodeError):
        raise Refused("This SMART Health Link is damaged: its contents can't be read.") from None
    if not isinstance(p, dict):
        raise Refused("This SMART Health Link is damaged: its contents can't be read.")
    url, key, flag, label, exp = p.get("url"), p.get("key"), p.get("flag", ""), p.get("label"), p.get("exp")
    if not isinstance(url, str) or not url:
        raise Refused("This SMART Health Link has no address to fetch from.")
    try:
        raw = b64d(key) if isinstance(key, str) and len(key) == 43 else b""
    except ValueError:
        raw = b""
    if len(raw) != 32:
        raise Refused("This SMART Health Link's key isn't valid (it should be 32 bytes).")
    if not isinstance(flag, str) or not re.fullmatch(r"[A-Z]*", flag):
        raise Refused("This SMART Health Link's flags aren't valid.")
    if "P" in flag and "U" in flag:
        raise Refused("This SMART Health Link asks for a passcode and is a single file: the two can't go together.")
    if exp is not None and (isinstance(exp, bool) or not isinstance(exp, (int, float))):
        raise Refused("This SMART Health Link's expiry isn't valid.")
    if p.get("v", 1) != 1:
        raise Refused(f"This SMART Health Link is version {str(p.get('v'))[:10]}, which Sanguity can't read yet.")
    label = label[:80] if isinstance(label, str) else None
    return Link("link", url=url, key=raw, exp=exp, flags="".join(sorted(set(flag) & set(FLAGS))), label=label)


def _shc_numeric(text):
    """A Health Card's QR text, shc:/ and two digits per character (each its code minus 45), as the JWS."""
    body = text[5:]
    if re.match(r"\d+/\d+/", body):
        raise Refused("This Health Card is in several QR codes. Scan or paste it as one (most issuers offer that).")
    if not re.fullmatch(r"(\d\d)+", body):
        raise Refused("That isn't a SMART Health Card: after shc:/ come only digits.")
    try:
        jws = "".join(chr(int(body[i:i + 2]) + 45) for i in range(0, len(body), 2))
    except ValueError:
        jws = ""
    if jws.count(".") != 2:
        raise Refused("That SMART Health Card is damaged: it can't be read.")
    return jws


def host_of(url):
    """The host a URL names, as listed under outside connections: lower case, with the port unless it's 443."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    try:
        port = u.port
    except ValueError:
        port = None
    return host if port in (None, 443) else f"{host}:{port}"


def describe(link, now=None):
    """What a person sees before confirming: no key, nothing fetched."""
    now = time.time() if now is None else now
    if link.kind == "card":
        try:
            iss = _jws_payload(link.jws).get("iss")
        except Refused:
            iss = None
        return {"kind": "card", "host": host_of(iss) if isinstance(iss, str) else None, "issuer": iss if isinstance(iss, str) else None,
                "label": None, "flags": [], "passcode": False, "long_term": False, "direct": False, "exp": None,
                "expired": False, "https": True}
    return {"kind": "link", "host": host_of(link.url), "issuer": None, "label": link.label,
            "flags": [FLAGS[f] for f in link.flags], "passcode": "P" in link.flags, "long_term": "L" in link.flags,
            "direct": "U" in link.flags, "exp": link.exp, "expired": link.exp is not None and link.exp < now,
            "https": urllib.parse.urlsplit(link.url).scheme.lower() == "https"}


def encode(url, key, flags="", label=None, exp=None, viewer=None):
    """A shlink:/ text for a manifest or file at url, encrypted with key (32 bytes). viewer: an address to put it
    after, as viewer#shlink:/..."""
    p = {"url": url, "key": b64e(key)}
    if flags:
        p["flag"] = "".join(sorted(set(flags)))
    if label:
        p["label"] = label[:80]
    if exp is not None:
        p["exp"] = exp
    text = "shlink:/" + b64e(json.dumps(p, separators=(",", ":")).encode())
    return f"{viewer}#{text}" if viewer else text


# ---------------------------------------------------------------- JWE: dir + A256GCM

def encrypt(data, key, cty=FHIR, zip_=False):
    """data (bytes) as a compact JWE for the link's key."""
    header = {"alg": "dir", "enc": "A256GCM", "cty": cty}
    if zip_:
        header["zip"] = "DEF"
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        data = c.compress(data) + c.flush()
    prot = b64e(json.dumps(header, separators=(",", ":")).encode())
    iv = os.urandom(12)
    sealed = AESGCM(key).encrypt(iv, data, prot.encode("ascii"))
    return ".".join([prot, "", b64e(iv), b64e(sealed[:-16]), b64e(sealed[-16:])])


def _inflate(data, limit):
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(data, limit + 1)
    except zlib.error:
        raise Refused("A file in this link is damaged: it can't be unpacked.") from None
    if len(out) > limit or d.unconsumed_tail:
        raise Refused("A file in this link unpacks to more than Sanguity takes from a link.", 413)
    return out


def decrypt(jwe, key, limit=None):
    """A compact JWE (alg dir, enc A256GCM) decrypted with the link's key: (bytes, content type or None)."""
    limit = limit or max_bytes()
    parts = jwe.strip().split(".") if isinstance(jwe, str) else []
    if len(parts) != 5:
        raise Refused("A file in this link isn't encrypted the way SMART Health Links are.")
    try:
        header = json.loads(b64d(parts[0]))
        iv, ct, tag = b64d(parts[2]), b64d(parts[3]), b64d(parts[4])
    except (ValueError, UnicodeDecodeError):
        raise Refused("A file in this link is damaged: it can't be read.") from None
    if not isinstance(header, dict) or header.get("alg") != "dir" or header.get("enc") != "A256GCM" or parts[1]:
        raise Refused("A file in this link is encrypted in a way Sanguity can't read (only dir + A256GCM).")
    if len(iv) != 12 or len(tag) != 16:
        raise Refused("A file in this link is damaged: it can't be read.")
    try:
        plain = AESGCM(key).decrypt(iv, ct + tag, parts[0].encode("ascii"))
    except InvalidTag:
        raise Refused("A file in this link couldn't be decrypted with the link's key: the link may be damaged or "
                      "the file replaced.") from None
    if header.get("zip") == "DEF":
        plain = _inflate(plain, limit)
    cty = header.get("cty")
    return plain, cty if isinstance(cty, str) else None


# ---------------------------------------------------------------- outbound requests

def _allowed(ip):
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def _context():
    ctx = ssl.create_default_context(cafile=CA_FILE) if CA_FILE else ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _addresses(permit, url, host):
    try:
        ips = net.resolve(permit, url)
    except net.Refused as e:
        raise Refused(str(e)) from None
    except (OSError, UnicodeError):
        raise Refused(f"{host} couldn't be found (no such host, or no connection to the internet).", 502) from None
    if not ips:
        raise Refused(f"{host} couldn't be found.", 502)
    if not ALLOW_PRIVATE and not all(_allowed(ip) for ip in ips):
        raise Refused(f"{host} is a private or local address. Sanguity only fetches links from public internet "
                      "hosts, so a link can't be used to reach machines on your own network.")
    return sorted(ips, key=lambda ip: ":" in ip)   # IPv4 first


def check_url(url):
    """(host, port, path with query) of an address Sanguity may fetch from, or Refused."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port or 443
    except ValueError:
        raise Refused("The link's address isn't valid.") from None
    if u.scheme.lower() != "https":
        raise Refused("The link's address isn't HTTPS, so it was refused: health records are only fetched over an "
                      "encrypted connection.")
    if not u.hostname or u.username or u.password:
        raise Refused("The link's address isn't valid.")
    if not ALLOW_PRIVATE:
        try:
            if not _allowed(u.hostname.strip("[]")):
                raise Refused(f"{u.hostname} is a private or local address. Sanguity only fetches links from public "
                              "internet hosts, so a link can't be used to reach machines on your own network.")
        except ValueError:
            pass   # a name, not an address: checked once resolved
    return u.hostname.lower(), port, (u.path or "/") + (f"?{u.query}" if u.query else "")


def _request(method, url, body=None, headers=None, limit=None, permit=None, purpose="SMART Health Link"):
    """One HTTPS request under the rules above: (status, content type, body bytes). permit: a net.Permit for the
    host (none: nothing is allowed)."""
    limit = limit or max_bytes()
    permit = permit or net.NOTHING
    for _ in range(4):   # the request and up to three redirects on the same host
        host, port, path = check_url(url)
        ips = _addresses(permit, url, host)
        conn, err = None, None
        for ip in ips:
            try:   # net records the host once the address is allowed, before anything is sent
                conn = net.connection(permit, url, purpose, ip, config.setting("SHL_TIMEOUT"), _context())
            except net.Refused as e:
                raise Refused(str(e)) from None
            try:
                conn.request(method, path, body=body, headers={"User-Agent": "Sanguity", "Accept-Encoding": "identity",
                                                               **(headers or {})})
                resp = conn.getresponse()
                break
            except ssl.SSLCertVerificationError:
                conn.close()
                raise Refused(f"{host}'s certificate couldn't be verified, so nothing was fetched.", 502) from None
            except net.Errors as e:
                conn.close()
                conn, err = None, e
        if conn is None:
            timed_out = isinstance(err, TimeoutError)
            raise Refused(f"{host} {'took too long to answer' if timed_out else 'could not be reached'}. Try again later.", 502)
        try:
            if resp.status in (301, 302, 303, 307, 308):
                to = urllib.parse.urljoin(url, resp.getheader("Location") or "")
                if host_of(to) != host_of(url) or urllib.parse.urlsplit(to).scheme.lower() != "https":
                    raise Refused(f"{host} sent the request on to another address ({host_of(to) or 'unknown'}), "
                                  "which Sanguity doesn't follow.", 502)
                if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                    method, body = "GET", None
                url = to
                continue
            data = b""
            while len(data) <= limit:
                piece = resp.read(min(65536, limit + 1 - len(data)))
                if not piece:
                    break
                data += piece
            if len(data) > limit:
                raise Refused(f"{host} sent more than Sanguity takes from a link ({limit // config.MB} MB).", 413)
            return resp.status, (resp.getheader("Content-Type") or "").split(";")[0].strip().lower(), data
        except net.Errors:
            raise Refused(f"The connection to {host} broke off. Try again later.", 502) from None
        finally:
            conn.close()
    raise Refused(f"{host_of(url)} redirected too many times.", 502)


# ---------------------------------------------------------------- fetching a link's files

def fetch(link, passcode=None, permit=None, now=None):
    """Fetch and decrypt what a SMART Health Link holds: [{"cty", "data"}]. One request to the link's address (the
    manifest, or the file itself for a U link), then a GET for each file the manifest lists by location instead
    of embedding it. Refused: expired links, a missing or wrong passcode, more files or bytes than config.SHL_*."""
    if link.kind != "link":
        raise Refused("That's a Health Card, not a link: there's nothing to fetch.")
    now = time.time() if now is None else now
    if link.exp is not None and link.exp < now:
        raise Refused("This SMART Health Link has expired. Ask whoever shared it for a new one.", 410)
    if "P" in link.flags and not passcode:
        raise Refused("This link needs its passcode.", 401)
    budget = [max_bytes()]

    def get(method, url, body=None, headers=None, purpose="SMART Health Link"):
        status, ctype, data = _request(method, url, body, headers, budget[0], permit, purpose)
        budget[0] -= len(data)
        return status, ctype, data

    if "U" in link.flags:
        sep = "&" if "?" in link.url else "?"
        status, _, data = get("GET", link.url + sep + urllib.parse.urlencode({"recipient": RECIPIENT}))
        _status_ok(status, link)
        plain, cty = decrypt(data.decode("ascii", "replace"), link.key)
        return [{"cty": cty, "data": plain}]

    ask = {"recipient": RECIPIENT, "embeddedLengthMax": max_bytes()}
    if passcode:
        ask["passcode"] = str(passcode)[:200]
    status, _, data = get("POST", link.url, json.dumps(ask).encode(), {"Content-Type": "application/json"})
    if status == 401 and "P" in link.flags:
        try:
            left = json.loads(data).get("remainingAttempts")
        except (ValueError, AttributeError):
            left = None
        left = left if isinstance(left, int) and not isinstance(left, bool) else None
        raise Refused("That passcode isn't right." + (f" {left} attempt{'' if left == 1 else 's'} left before the "
                      "link stops working." if left is not None else ""), 401, left)
    _status_ok(status, link)
    try:
        man = json.loads(data)
        files = man["files"]
        assert isinstance(files, list)
    except (ValueError, KeyError, TypeError, AssertionError):
        raise Refused(f"{host_of(link.url)} didn't answer with a SMART Health Link manifest.", 502) from None
    if len(files) > config.setting("SHL_MAX_FILES"):
        raise Refused(f"This link holds {len(files)} files; Sanguity takes at most {config.setting('SHL_MAX_FILES')} from a link.", 413)
    out, elsewhere = [], set()
    for f in files:
        if not isinstance(f, dict):
            continue
        cty = f.get("contentType") if isinstance(f.get("contentType"), str) else None
        if isinstance(f.get("embedded"), str):
            jwe = f["embedded"]
        elif isinstance(f.get("location"), str):
            where = host_of(f["location"])
            if where and not (permit or net.NOTHING).allows(where):   # a host not confirmed: asked about first (#919)
                elsewhere.add(where)
                continue
            status, _, body = get("GET", f["location"], purpose="SMART Health Link file")
            _status_ok(status, link)
            jwe = body.decode("ascii", "replace")
        else:
            continue
        plain, inner = decrypt(jwe, link.key, budget[0] + len(jwe))
        out.append({"cty": cty or inner, "data": plain})
    if elsewhere:   # with the issuers of the cards fetched so far, so it's asked once for all it knows of
        raise Ask(elsewhere, _unconfirmed(out, permit))
    if not out:
        raise Refused("This link's manifest lists no files.", 404)
    return out


def _status_ok(status, link):
    if status == 200:
        return
    if status in (404, 410):
        raise Refused("This SMART Health Link is no longer available (it was withdrawn, used up or has expired).", 410)
    if status == 401:
        raise Refused("The link's server refused the request (401).", 502)
    if status == 429:
        raise Refused("The link's server is busy (too many requests). Try again later.", 502)
    raise Refused(f"{host_of(link.url)} answered {int(status)}, so nothing was fetched.", 502)


# ---------------------------------------------------------------- SMART Health Cards: JWS, ES256

def _jws_parts(jws):
    parts = jws.strip().split(".") if isinstance(jws, str) else []
    if len(parts) != 3:
        raise Refused("A Health Card in this link is damaged: it can't be read.")
    try:
        header = json.loads(b64d(parts[0]))
        payload = b64d(parts[1])
        sig = b64d(parts[2])
    except (ValueError, UnicodeDecodeError):
        raise Refused("A Health Card in this link is damaged: it can't be read.") from None
    if not isinstance(header, dict):
        raise Refused("A Health Card in this link is damaged: it can't be read.")
    if header.get("zip") == "DEF":
        payload = _inflate(payload, max_bytes())
    return header, payload, sig, (parts[0] + "." + parts[1]).encode("ascii")


def _jws_payload(jws):
    _, payload, _, _ = _jws_parts(jws)
    try:
        p = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise Refused("A Health Card in this link is damaged: it can't be read.") from None
    if not isinstance(p, dict):
        raise Refused("A Health Card in this link is damaged: it can't be read.")
    return p


def sign_card(payload, private_key, kid):
    """A Health Card's JWS (ES256, zip DEF) for payload, signed with an EC P-256 private key."""
    header = b64e(json.dumps({"alg": "ES256", "zip": "DEF", "kid": kid}, separators=(",", ":")).encode())
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = b64e(c.compress(json.dumps(payload, separators=(",", ":")).encode()) + c.flush())
    r, s = decode_dss_signature(private_key.sign(f"{header}.{body}".encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return f"{header}.{body}.{b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def jwk_of(public_key, kid):
    n = public_key.public_numbers()
    return {"kty": "EC", "crv": "P-256", "kid": kid, "use": "sig", "alg": "ES256",
            "x": b64e(n.x.to_bytes(32, "big")), "y": b64e(n.y.to_bytes(32, "big"))}


def _verify(header, signed, sig, jwks):
    """True when one of the issuer's keys (a JWKS dict) signed it; False when none did."""
    if header.get("alg") != "ES256" or len(sig) != 64:
        return False
    for k in jwks.get("keys") or [] if isinstance(jwks, dict) else []:
        if not isinstance(k, dict) or k.get("kty") != "EC" or k.get("crv") != "P-256":
            continue
        if header.get("kid") and k.get("kid") != header["kid"]:
            continue
        try:
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(b64d(k["x"]), "big"), int.from_bytes(b64d(k["y"]), "big"),
                                                ec.SECP256R1()).public_key()
            pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                       signed, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError, KeyError, TypeError):
            continue
    return False


def check_card(jws, permit=None, jwks_cache=None):
    """A Health Card read and its signature checked against its issuer's keys when they can be fetched:
    {"issuer", "bundle", "signature": "verified" | "invalid" | "unchecked", "why"}."""
    header, _, sig, signed = _jws_parts(jws)
    p = _jws_payload(jws)
    iss = p.get("iss") if isinstance(p.get("iss"), str) else None
    vc = p.get("vc") if isinstance(p.get("vc"), dict) else {}
    subject = vc.get("credentialSubject") if isinstance(vc.get("credentialSubject"), dict) else {}
    bundle = subject.get("fhirBundle")
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise Refused("A Health Card in this link holds no FHIR bundle.")
    card = {"issuer": iss, "host": host_of(iss) if iss else None, "bundle": bundle, "signature": "unchecked", "why": None}
    if not iss:
        card["why"] = "it names no issuer"
        return card
    cache = jwks_cache if jwks_cache is not None else {}
    if iss not in cache:
        try:   # (an issuer the person confirmed: prepare asks about one they haven't first, #919)
            status, _, data = _request("GET", iss.rstrip("/") + "/.well-known/jwks.json", limit=256 * 1024,
                                       permit=permit, purpose="Health Card issuer's keys")
            cache[iss] = json.loads(data) if status == 200 else f"its issuer's keys weren't available ({int(status)})"
        except Refused as e:
            cache[iss] = f"its issuer's keys couldn't be fetched: {e}"
        except ValueError:
            cache[iss] = "its issuer's keys couldn't be read"
    keys = cache[iss]
    if isinstance(keys, str):
        card["why"] = keys
    elif _verify(header, signed, sig, keys):
        card["signature"] = "verified"
    else:
        card["signature"], card["why"] = "invalid", "the signature doesn't match the issuer's keys"
    return card


def read_cards(data, permit=None, jwks_cache=None):
    """The Health Cards in an application/smart-health-card file ({"verifiableCredential": [JWS, ...]})."""
    try:
        doc = json.loads(data)
        vcs = doc["verifiableCredential"]
        assert isinstance(vcs, list) and all(isinstance(v, str) for v in vcs)
    except (ValueError, KeyError, TypeError, AssertionError, UnicodeDecodeError):
        raise Refused("A Health Card file in this link is damaged: it can't be read.") from None
    if len(vcs) > config.setting("SHL_MAX_FILES"):
        raise Refused(f"A Health Card file holds {len(vcs)} cards; Sanguity takes at most {config.setting('SHL_MAX_FILES')}.", 413)
    cache = {} if jwks_cache is None else jwks_cache
    return [check_card(v, permit, cache) for v in vcs]


def issuers(data):
    """The hosts of the issuers of the Health Cards in an application/smart-health-card file, read without checking
    anything or contacting anyone: where check_card would fetch their keys. Empty for a file that can't be read."""
    try:
        vcs = json.loads(data)["verifiableCredential"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return set()
    out = set()
    for v in vcs[:config.setting("SHL_MAX_FILES")] if isinstance(vcs, list) else []:
        try:
            iss = _jws_payload(v).get("iss") if isinstance(v, str) else None
        except Refused:
            continue
        if isinstance(iss, str) and host_of(iss):
            out.add(host_of(iss))
    return out


def _is_card(it):
    return (it.get("cty") or "").split(";")[0].strip().lower() == CARD


def _unconfirmed(items, permit):
    """The hosts the Health Cards among items would have their issuers' keys fetched from that permit doesn't allow."""
    permit = permit or net.NOTHING
    return {h for it in items if _is_card(it) for h in issuers(it["data"]) if not permit.allows(h)}


# ---------------------------------------------------------------- what a link brought, as files to import

def prepare(items, permit=None):
    """The decrypted files of a link (fetch) as files for an import: {"files": {path: bytes}, "cards": [{"issuer",
    "host", "signature", "why"}], "notes": [str]}. A FHIR file is kept as it came; a Health Card file is kept too
    (as .smart-health-card, which the FHIR importer doesn't read) and the bundle of each card whose signature isn't
    invalid goes beside it as JSON. A card with an invalid signature is left out: its contents were changed after
    it was signed, or it wasn't signed by its issuer. Other content types are left out, with a note."""
    if permit is not None and (ask := _unconfirmed(items, permit)):   # an issuer the person hasn't confirmed (#919)
        raise Ask((), ask)
    files, cards, notes, cache = {}, [], [], {}
    for n, it in enumerate(items, 1):
        cty, data = (it.get("cty") or "").split(";")[0].strip().lower(), it["data"]
        if cty == FHIR or (not cty and data.lstrip()[:1] == b"{" and b'"resourceType"' in data[:4096]):
            try:
                doc = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                raise Refused(f"File {n} in this link should be FHIR JSON but can't be read.") from None
            if not isinstance(doc, dict) or not isinstance(doc.get("resourceType"), str):
                raise Refused(f"File {n} in this link should be FHIR JSON but isn't.")
            files[f"smart-health-link/file-{n}.json"] = data
        elif cty == CARD:
            files[f"smart-health-link/file-{n}.smart-health-card"] = data
            for m, c in enumerate(read_cards(data, permit, cache), 1):
                cards.append({k: c[k] for k in ("issuer", "host", "signature", "why")})
                if c["signature"] == "invalid":
                    notes.append(f"A Health Card from {c['host'] or 'an unnamed issuer'} was left out: {c['why']}.")
                    continue
                files[f"smart-health-link/file-{n}-card-{m}.json"] = json.dumps(c["bundle"], ensure_ascii=False).encode("utf-8")
        else:
            notes.append(f"File {n} in this link ({cty or 'no content type'}) isn't health records Sanguity reads, so it was left out.")
    if not any(p.endswith(".json") for p in files):
        raise Refused(" ".join(notes) or "This link holds nothing Sanguity can import.", 422)
    return {"files": files, "cards": cards, "notes": notes}


def card_items(link):
    """A pasted Health Card (shc:/) as the one file fetch() would have brought."""
    return [{"cty": CARD, "data": json.dumps({"verifiableCredential": [link.jws]}).encode()}]
