"""SMART on FHIR, the protocol side of connected sources (#32): discovery, PKCE, the authorization address, the
token endpoint's grants, Epic's dynamic client registration, revocation, and FHIR reads with paging and backoff.
Nothing here touches the database; connections.py keeps what it returns, encrypted.

Every request goes through a Client, which:
  - contacts only the hosts its permit allows (net.Permit, made by outside.py from the source's setup: the issuer's,
    and the hosts its discovery names for the token, revocation and registration endpoints) and refuses any other,
    redirects and "next" links included; the connection itself is made by net.py, which records each host (#97);
  - retries 429 and 5xx (and a server that doesn't answer) with backoff, honouring Retry-After, up to a cap;
  - says why it failed, so the page can tell the cases apart:
      Unavailable   the vendor didn't answer, or still answered 429 / 5xx after the retries: try again later
      AccessEnded   the vendor ended access: a refresh token or key it no longer accepts (invalid_grant),
                    revoked in the patient's portal or expired: sign in again
      Refused       anything else the vendor refused, or a setting that can't work: the message says what

Standard library and cryptography (already a dependency, for the RSA key of Epic's dynamic registration).
"""
import base64, hashlib, json, secrets, time, urllib.parse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import config, net

TRIES = 4            # attempts per request on 429 / 5xx / no answer
MAX_WAIT = 60        # seconds: a Retry-After longer than this isn't waited for in a pull; the next pull is later
MAX_BODY = 32 * 2**20   # the most one FHIR or OAuth answer may hold (attachments have their own limit)
UA = "Sanguity (self-hosted health records)"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
sleep = time.sleep   # tests may replace it


class SmartError(Exception):
    kind = "other"

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(SmartError):
    kind = "unavailable"


class AccessEnded(SmartError):
    kind = "access"


class Refused(SmartError):
    kind = "other"


class Forbidden(Refused):
    """403 on a FHIR read: the scopes granted don't cover it."""


class TokenRejected(SmartError):
    """401 on a FHIR read: the access token isn't accepted (expired or revoked); refresh and try once more."""
    kind = "access"


class TooBig(Refused):
    pass


# ---------------------------------------------------------------- addresses

def check_url(url, what="address"):
    """url, if it's a full https:// address (http:// only with CONNECT_ALLOW_HTTP, for a test server)."""
    if not isinstance(url, str) or len(url) > 2000:
        raise Refused(f"The {what} isn't an address.")
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.fragment:
        raise Refused(f"The {what} must be a full https:// address, like https://fhir.example.org/api/FHIR/R4.")
    if u.scheme == "http" and not config.CONNECT_ALLOW_HTTP:
        raise Refused(f"The {what} must use https://. Vendors' FHIR servers always do.")
    return url.strip()


def base_url(url, what="FHIR base address"):
    """A FHIR base or issuer, normalised: no trailing slash, no query."""
    url = check_url(url, what)
    u = urllib.parse.urlsplit(url)
    if u.query:
        raise Refused(f"The {what} can't have a query (?...).")
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), u.path.rstrip("/"), "", ""))


def host_of(url):
    """host[:port], lowercased: what a Client checks requests against."""
    return urllib.parse.urlsplit(url).netloc.lower()


def server_hosts(issuer, conf):
    """The hosts Sanguity's server contacts for this source: the FHIR base's, and the token, revocation and
    registration endpoints'. (The authorization endpoint is visited by the person's browser, not the server.)"""
    hosts = {host_of(issuer)}
    for k in ("token_endpoint", "revocation_endpoint", "registration_endpoint"):
        if isinstance(conf.get(k), str):
            hosts.add(host_of(conf[k]))
    return hosts


# ---------------------------------------------------------------- requests

class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8-sig") or "null")
        except (ValueError, UnicodeDecodeError):
            return None


def _retry_after(resp):
    v = resp.headers.get("retry-after", "").strip()
    if v.isdigit():
        return int(v)
    return None


class Client:
    def __init__(self, permit, timeout=None):
        self.permit = permit
        self.timeout = timeout or config.setting("CONNECT_TIMEOUT")

    @property
    def hosts(self):
        return self.permit.hosts()

    def request(self, method, url, headers=None, body=None, max_bytes=MAX_BODY, retry=True):
        url = check_url(url)
        u = urllib.parse.urlsplit(url)
        host = u.netloc.lower()
        if not self.permit.allows(host):
            raise Refused(f"Sanguity won't contact {host}: it isn't one of this source's hosts "
                          f"({', '.join(sorted(self.hosts)) or 'none, now'}).")
        headers = {"User-Agent": UA, **(headers or {})}
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        wait, err = 0, None
        for attempt in range(TRIES):
            try:
                resp = self._once(u, method, path, headers, body, max_bytes)
            except TooBig:
                raise
            except net.Refused as e:
                raise Refused(str(e)) from None
            except net.Errors as e:
                err = Unavailable(f"{host} didn't answer: {net.why(e)}.")
                wait = 2 ** attempt
            else:
                if resp.status != 429 and resp.status < 500:
                    return resp
                wait = _retry_after(resp)
                wait = 2 ** attempt if wait is None else wait
                err = Unavailable(f"{host} is unavailable just now (it answered {resp.status}"
                                  + (", too many requests" if resp.status == 429 else "") + ").")
            if not retry or attempt == TRIES - 1 or wait > MAX_WAIT:
                break
            sleep(wait)
        err.retry_after = wait
        raise err

    def _once(self, u, method, path, headers, body, max_bytes):
        conn = net.connection(self.permit, urllib.parse.urlunsplit(u), timeout=self.timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            r = conn.getresponse()
            length = r.getheader("content-length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise TooBig(f"{u.netloc} sent more than {max_bytes // 2**20} MB in one answer.")
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise TooBig(f"{u.netloc} sent more than {max_bytes // 2**20} MB in one answer.")
            return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, data)
        finally:
            conn.close()


# ---------------------------------------------------------------- discovery and the authorization address

def discover(client, issuer, pinned_issuer=None):
    """The issuer's .well-known/smart-configuration, checked: an authorization and a token endpoint, PKCE with
    S256, and, when it names an issuer, the one configured (or the one an admin's test saw before)."""
    r = client.request("GET", issuer + "/.well-known/smart-configuration", {"Accept": "application/json"})
    if r.status != 200:
        raise Refused(f"{issuer} has no SMART configuration (.well-known/smart-configuration answered {r.status}). "
                      "Check the FHIR base address.")
    conf = r.json()
    if not isinstance(conf, dict):
        raise Refused(f"{issuer}'s SMART configuration isn't JSON.")
    for k in ("authorization_endpoint", "token_endpoint"):
        if not isinstance(conf.get(k), str):
            raise Refused(f"{issuer}'s SMART configuration has no {k}.")
        check_url(conf[k], k.replace("_", " "))
    for k in ("revocation_endpoint", "registration_endpoint"):
        if conf.get(k) is not None:
            check_url(conf[k], k.replace("_", " "))
    said = conf.get("issuer")
    if isinstance(said, str) and said.rstrip("/") not in (issuer, (pinned_issuer or issuer).rstrip("/")):
        raise Refused(f"{issuer}'s SMART configuration says its issuer is {said[:200]}, not {issuer}. Sanguity "
                      "won't sign in there; check the address with the vendor.")
    methods = conf.get("code_challenge_methods_supported")
    if isinstance(methods, list) and "S256" not in methods:
        raise Refused(f"{issuer} doesn't support PKCE with S256, which SMART requires.")
    return conf


def pkce():
    """(code_verifier, code_challenge): 64 random bytes as the verifier (86 characters), S256 as the challenge."""
    verifier = secrets.token_urlsafe(64)
    return verifier, b64url(hashlib.sha256(verifier.encode()).digest())


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def authorize_url(conf, client_id, redirect_uri, scopes, state, challenge, aud):
    q = urllib.parse.urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
                                "scope": scopes, "state": state, "aud": aud, "code_challenge": challenge,
                                "code_challenge_method": "S256"})
    ep = conf["authorization_endpoint"]
    return ep + ("&" if "?" in ep else "?") + q


# ---------------------------------------------------------------- the token endpoint

def _token(client, url, form, client_id, secret=None):
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    form = dict(form)
    if secret:   # client_secret_basic (RFC 6749 §2.3.1): each part form-encoded first
        pair = f"{urllib.parse.quote_plus(client_id)}:{urllib.parse.quote_plus(secret)}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    elif client_id:
        form["client_id"] = client_id
    r = client.request("POST", url, headers, urllib.parse.urlencode(form).encode())
    data = r.json()
    data = data if isinstance(data, dict) else {}
    if r.status == 200 and isinstance(data.get("access_token"), str):
        return data
    err, desc = str(data.get("error") or "")[:60], str(data.get("error_description") or "")[:200]
    said = f" ({err}{': ' + desc if desc else ''})" if err else f" (it answered {r.status})"
    if err == "invalid_grant":
        raise AccessEnded("The vendor no longer accepts this connection's sign-in" + said + ".")
    if err == "invalid_client" or r.status == 401:
        raise Refused("The vendor refused the app's client ID or secret" + said + ". Check the source's setup.")
    raise Refused("The vendor refused the request" + said + ".")


def exchange_code(client, conf, client_id, secret, code, verifier, redirect_uri):
    return _token(client, conf["token_endpoint"], {"grant_type": "authorization_code", "code": code,
                                                   "redirect_uri": redirect_uri, "code_verifier": verifier},
                  client_id, secret)


def refresh(client, token_url, client_id, secret, refresh_token):
    return _token(client, token_url, {"grant_type": "refresh_token", "refresh_token": refresh_token}, client_id, secret)


def revoke(client, url, client_id, secret, token, hint):
    """RFC 7009. True when the vendor accepted it. Errors are the caller's to note, not to stop a disconnect."""
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    form = {"token": token, "token_type_hint": hint}
    if secret:
        pair = f"{urllib.parse.quote_plus(client_id)}:{urllib.parse.quote_plus(secret)}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    else:
        form["client_id"] = client_id
    r = client.request("POST", url, headers, urllib.parse.urlencode(form).encode(), retry=False)
    return r.status == 200


# ---------------------------------------------------------------- Epic's dynamic client registration

def new_key():
    """A new RSA key for a dynamic client: (private key PEM, public JWK)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()
    as_b64 = lambda n: b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))
    jwk = {"kty": "RSA", "n": as_b64(nums.n), "e": as_b64(nums.e), "alg": "RS384", "use": "sig"}
    jwk["kid"] = b64url(hashlib.sha256(json.dumps({k: jwk[k] for k in ("e", "kty", "n")}, sort_keys=True,
                                                  separators=(",", ":")).encode()).digest())[:16]
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem.decode(), jwk


def register(client, url, software_id, access_token, jwk):
    """Register a dynamic client (RFC 7591, as Epic does it) with the install's own public key, authorized by the
    access token of the first sign-in. Returns its client_id."""
    body = json.dumps({"software_id": software_id, "jwks": {"keys": [jwk]}}).encode()
    r = client.request("POST", url, {"Content-Type": "application/json", "Accept": "application/json",
                                     "Authorization": "Bearer " + access_token}, body)
    data = r.json() if r.status in (200, 201) else None
    if not isinstance(data, dict) or not isinstance(data.get("client_id"), str):
        err = (r.json() or {}) if isinstance(r.json(), dict) else {}
        raise Refused(f"The vendor refused to register this install's key (it answered {r.status}"
                      + (f", {str(err.get('error'))[:60]}" if err.get("error") else "") + ").")
    return data["client_id"]


def assertion(private_pem, kid, client_id, token_url):
    """A JWT for the JWT bearer grant (RFC 7523), signed RS384 with the install's key, valid for 4 minutes."""
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    now = int(time.time())
    head = {"alg": "RS384", "typ": "JWT", "kid": kid}
    claims = {"iss": client_id, "sub": client_id, "aud": token_url, "jti": secrets.token_urlsafe(24),
              "exp": now + 240, "nbf": now, "iat": now}
    part = lambda o: b64url(json.dumps(o, separators=(",", ":")).encode())
    msg = f"{part(head)}.{part(claims)}"
    sig = key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA384())
    return msg + "." + b64url(sig)


def jwt_grant(client, token_url, dyn_client_id, private_pem, kid):
    return _token(client, token_url, {"grant_type": JWT_BEARER, "assertion": assertion(private_pem, kid, dyn_client_id, token_url),
                                      "client_id": dyn_client_id}, None)


# ---------------------------------------------------------------- FHIR reads

class Reader:
    """FHIR reads at one base with an access token from tokens (an object with token() and renew()): on a 401
    it renews once and tries again."""

    def __init__(self, client, base, tokens):
        self.client, self.base, self.tokens = client, base, tokens

    def get(self, url, accept="application/fhir+json", max_bytes=MAX_BODY):
        if not url.startswith(("https://", "http://")):
            url = self.base + "/" + url.lstrip("/")
        for again in (False, True):
            r = self.client.request("GET", url, {"Accept": accept, "Authorization": "Bearer " + self.tokens.token()},
                                    max_bytes=max_bytes)
            if r.status == 401 and not again:
                self.tokens.renew()
                continue
            break
        if r.status == 401:
            raise TokenRejected("The vendor no longer accepts this connection's access token.")
        if r.status == 403:
            raise Forbidden("not allowed by the access granted")
        if r.status == 404:
            raise Refused("not found")
        if r.status != 200:
            raise Refused(f"the vendor answered {r.status}")
        return r

    def search(self, rtype, params, limit):
        """Every page of a search: (resources, the first page's Bundle.meta.lastUpdated or None, cut short)."""
        url = f"{self.base}/{rtype}?" + urllib.parse.urlencode(params, doseq=True)
        out, stamp, seen = [], None, set()
        while url:
            if url in seen:   # a server whose next link points back at a page already read
                break
            seen.add(url)
            bundle = self.get(url).json()
            if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
                raise Refused(f"the {rtype} search didn't return a Bundle")
            if stamp is None:
                stamp = (bundle.get("meta") or {}).get("lastUpdated") or ""
            for e in bundle.get("entry") or []:
                r = (e or {}).get("resource")
                if isinstance(r, dict) and r.get("resourceType") == rtype:
                    out.append(r)
                    if len(out) >= limit:
                        return out, stamp or None, True
            url = next((l.get("url") for l in bundle.get("link") or [] if isinstance(l, dict) and l.get("relation") == "next"), None)
        return out, stamp or None, False
