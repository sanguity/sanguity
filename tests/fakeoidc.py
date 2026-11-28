"""A fake OpenID Connect provider for the sign-in tests (#1109), as tests/fakefhir is SMART on FHIR's: discovery
(.well-known/openid-configuration), a JWKS, the authorization endpoint (which signs in whoever the test chose) and
the token endpoint (authorization code with PKCE S256, client_secret_basic or _post), with ID tokens it signs with its
own RSA and P-256 keys. Each part can be made wrong on purpose (`fake.token` claims and `fake.sign` how), and every
request is recorded. Standard library and cryptography; it runs in a thread on a free 127.0.0.1 port. Fictional
people only.

    with FakeOIDC(client_id="sanguity", secret="s3cret") as idp:
        idp.person = {"sub": "kim-1", "preferred_username": "kim"}
        location = idp.authorize(url_sanguity_made)        # -> the redirect URI with ?code=&state=
"""
import base64, hashlib, hmac, json, secrets, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _n(i, size=None):
    return b64url(i.to_bytes(size or (i.bit_length() + 7) // 8, "big"))


class FakeOIDC:
    def __init__(self, client_id="sanguity-test", secret=None, path="/realms/home", auth_method="client_secret_basic"):
        self.client_id, self.secret, self.path, self.auth_method = client_id, secret, path, auth_method
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)   # a key not in the JWKS
        self.person = {"sub": "kim-subject-1", "preferred_username": "kim (fictional)"}
        self.token = {}             # claims added to (or, with None, taken out of) the next ID tokens
        self.sign = "RS256"         # "RS256", "ES256", "none", "HS256" (with the RSA public key as its secret), "stranger"
        self.header = {}            # added to the JWS header
        self.discovery = {}         # added to (None: taken out of) the discovery document
        self.issuer_said = None     # the discovery document's issuer, when it should differ
        self.requests = []          # (method, path, form or query)
        self.codes = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the server
    def start(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self.issuer = self.base + self.path
        self._thread = threading.Thread(target=self._httpd.serve_forever, args=(0.05,), name="fakeoidc", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def conf(self):
        d = {"issuer": self.issuer_said or self.issuer, "authorization_endpoint": self.issuer + "/auth",
             "token_endpoint": self.issuer + "/token", "jwks_uri": self.issuer + "/certs",
             "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
             "id_token_signing_alg_values_supported": ["RS256", "ES256"],
             "token_endpoint_auth_methods_supported": [self.auth_method]}
        for k, v in self.discovery.items():
            if v is None:
                d.pop(k, None)
            else:
                d[k] = v
        return d

    def jwks(self):
        r, e = self.rsa.public_key().public_numbers(), self.ec.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": "rsa-1", "use": "sig", "alg": "RS256", "n": _n(r.n), "e": _n(r.e)},
                         {"kty": "EC", "kid": "ec-1", "use": "sig", "alg": "ES256", "crv": "P-256", "x": _n(e.x, 32), "y": _n(e.y, 32)}]}

    # ------------------------------------------------------------------ the person's browser at the provider
    def authorize(self, url):
        """What the provider's sign-in page does once the person has signed in: the redirect URI with a code and the
        state (or error=...). The code remembers what the authorization request asked."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.requests.append(("GET", "/auth", q))
        code = secrets.token_urlsafe(16)
        with self._lock:
            self.codes[code] = {"q": q, "person": dict(self.person)}
        return q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q.get("state", "")})

    # ------------------------------------------------------------------ tokens
    def id_token(self, q, person):
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": self.client_id, "exp": now + 300, "iat": now, "auth_time": now,
                  "nonce": q.get("nonce"), **person}
        for k, v in self.token.items():
            if v is None:
                claims.pop(k, None)
            else:
                claims[k] = v
        alg = {"stranger": "RS256"}.get(self.sign, self.sign)
        header = {"alg": alg, "typ": "JWT", "kid": "ec-1" if alg == "ES256" else "rsa-1", **self.header}
        signed = (b64url(json.dumps(header).encode()) + "." + b64url(json.dumps(claims).encode())).encode()
        if self.sign == "none":
            sig = b""
        elif self.sign == "HS256":   # the classic confusion: the provider's public key used as an HMAC secret
            pem = self.rsa.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            sig = hmac.new(pem, signed, hashlib.sha256).digest()
        elif alg == "ES256":
            r, s = decode_dss_signature(self.ec.sign(signed, ec.ECDSA(hashes.SHA256())))
            sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        else:
            key = self.stranger if self.sign == "stranger" else self.rsa
            sig = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        return signed.decode() + "." + b64url(sig)

    def exchange(self, form, auth):
        """The token endpoint: (status, body)."""
        if self.secret:
            cid, sec = form.get("client_id"), form.get("client_secret")
            if auth and auth.startswith("Basic "):
                cid, _, sec = base64.b64decode(auth[6:]).decode().partition(":")
                cid, sec = urllib.parse.unquote_plus(cid), urllib.parse.unquote_plus(sec)
            if cid != self.client_id or sec != self.secret:
                return 401, {"error": "invalid_client"}
        elif form.get("client_id") != self.client_id:
            return 401, {"error": "invalid_client"}
        with self._lock:
            got = self.codes.pop(form.get("code", ""), None)   # a code is good once
        if not got or form.get("grant_type") != "authorization_code" or form.get("redirect_uri") != got["q"].get("redirect_uri"):
            return 400, {"error": "invalid_grant"}
        q, verifier = got["q"], form.get("code_verifier")
        if not q.get("code_challenge") or q.get("code_challenge_method") != "S256" or not verifier \
                or b64url(hashlib.sha256(verifier.encode()).digest()) != q["code_challenge"]:
            return 400, {"error": "invalid_grant", "error_description": "PKCE verification failed"}
        return 200, {"access_token": secrets.token_urlsafe(16), "token_type": "Bearer", "expires_in": 300,
                     "id_token": self.id_token(q, got["person"])}


def _handler(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            fake.requests.append(("GET", path, {}))
            if path == fake.path + "/.well-known/openid-configuration":
                return self._send(200, fake.conf())
            if path == fake.path + "/certs":
                return self._send(200, fake.jwks())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
            fake.requests.append(("POST", path, form))
            if path == fake.path + "/token":
                return self._send(*fake.exchange(form, self.headers.get("Authorization")))
            self._send(404, {"error": "not found"})
    return H
