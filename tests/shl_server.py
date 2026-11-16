"""A small local HTTPS server that hands out SMART Health Links, for tests/test_shl.py and tests/browser_check.py.

It has its own certificate (made here, for localhost and 127.0.0.1), and serves whatever routes a test puts in
`routes`: {path: handler(method, query, body) -> (status, content type, bytes)}. Every request it gets is appended
to `seen` as (method, path, query, body), so a test can say nothing was fetched. Fictional data only.

  srv = Server(); srv.start()                 https://localhost:<srv.port>
  srv.trust()                                 lets server.shl fetch from it (loopback, its certificate)
  srv.share(bundle, passcode=None, flags="")  a manifest serving that FHIR bundle: the shlink:/ text
"""
import datetime, http.server, ipaddress, json, os, secrets, ssl, tempfile, threading, urllib.parse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def bundle(day="2026-03-04", value=12.1, name="Hemoglobin"):
    """A fictional FHIR Bundle: one patient and one lab result."""
    return {"resourceType": "Bundle", "type": "collection", "entry": [
        {"fullUrl": "resource:0", "resource": {"resourceType": "Patient", "id": "p1", "name": [{"family": "Example", "given": ["Robin"]}],
                                                "birthDate": "1980-01-01"}},
        {"fullUrl": "resource:1", "resource": {
            "resourceType": "Observation", "id": f"obs-{day}", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "718-7", "display": "Hemoglobin [Mass/volume] in Blood"}], "text": name},
            "subject": {"reference": "resource:0"}, "effectiveDateTime": f"{day}T09:45:00-06:00",
            "valueQuantity": {"value": value, "unit": "g/dL", "system": "http://unitsofmeasure.org", "code": "g/dL"},
            "referenceRange": [{"low": {"value": 13.5, "unit": "g/dL"}, "high": {"value": 17.5, "unit": "g/dL"}}]}}]}


def _cert(folder):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    cp, kp = os.path.join(folder, "cert.pem"), os.path.join(folder, "key.pem")
    with open(cp, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(kp, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cp, kp


class Server:
    def __init__(self):
        self.folder = tempfile.mkdtemp(prefix="sanguity-shl-server-")
        self.cert, self.keyfile = _cert(self.folder)
        self.routes, self.seen = {}, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, method):
                u = urllib.parse.urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else b""
                outer.seen.append((method, u.path, urllib.parse.parse_qs(u.query), body))
                h = outer.routes.get(u.path)
                status, ctype, data = h(method, urllib.parse.parse_qs(u.query), body) if h else (404, "text/plain", b"not here")
                self.send_response(status)
                if status in (301, 302, 303, 307, 308):
                    self.send_header("Location", data.decode())
                    data = b""
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert, self.keyfile)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.base = f"https://localhost:{self.port}"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()

    def trust(self):
        from server import shl
        shl.ALLOW_PRIVATE, shl.CA_FILE = True, self.cert

    def manifest(self, path, files, passcode=None, attempts=3):
        """A manifest at path listing files ([(content type, JWE)], embedded), behind passcode if given."""
        left = [attempts]

        def h(method, query, body):
            if method != "POST":
                return 405, "text/plain", b""
            ask = json.loads(body)
            if passcode is not None and ask.get("passcode") != passcode:
                left[0] -= 1
                return 401, "application/json", json.dumps({"remainingAttempts": max(left[0], 0)}).encode()
            return 200, "application/json", json.dumps({"files": [{"contentType": c, "embedded": j} for c, j in files]}).encode()
        self.routes[path] = h

    def share(self, data, passcode=None, flags="", label="Fictional records", exp=None, cty="application/fhir+json", viewer=None):
        from server import shl
        key, path = secrets.token_bytes(32), "/shl/" + secrets.token_hex(8)
        jwe = shl.encrypt(data if isinstance(data, bytes) else json.dumps(data).encode(), key, cty, zip_=True)
        if "U" in flags:
            self.routes[path] = lambda m, q, b: (200, "application/jose", jwe.encode())
        else:
            self.manifest(path, [(cty, jwe)], passcode)
        flags = flags + ("P" if passcode is not None else "")
        return shl.encode(self.base + path, key, flags, label, exp, viewer)

    def requests(self, path_prefix="/"):
        return [s for s in self.seen if s[1].startswith(path_prefix)]
