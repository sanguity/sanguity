#!/usr/bin/env python3
"""The fake SMART on FHIR server (tests/fakefhir, issue #98) that the sign-in, sync and Health Link tests run
against: discovery, PKCE sign-in and its failures, confidential clients, refresh (rotating, reused, expired,
revoked), revocation, scopes, paging, `_lastUpdated`, `$everything`, Binary attachments, injected 429/5xx/slow
responses, the request record, and Epic-style dynamic registration. Fictional patients only.

  python3 tests/test_fakefhir.py
"""
import base64, json, os, sys, time, unittest, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.fakefhir import FakeFHIR, pkce   # noqa: E402

RETURN = "http://127.0.0.1:8730/connect/return"
SCOPES = ["launch/patient", "offline_access", "patient/*.rs"]


def call(method, url, form=None, headers=None, body=None):
    """(status, headers, parsed JSON or bytes), without raising on 4xx/5xx."""
    data = urllib.parse.urlencode(form).encode() if form is not None else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, h, raw = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        with e:
            status, h, raw = e.code, e.headers, e.read()
    try:
        return status, h, json.loads(raw) if raw else None
    except ValueError:
        return status, h, raw


def bearer(token):
    return {"Authorization": "Bearer " + token}


class Base(unittest.TestCase):
    settings = {}

    def setUp(self):
        self.fake = FakeFHIR(**self.settings).start()
        self.addCleanup(self.fake.stop)
        self.client = self.fake.register_client("app-1", [RETURN])

    def authorize_url(self, challenge, *, scopes=SCOPES, client_id="app-1", redirect_uri=RETURN, method="S256", **extra):
        q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": " ".join(scopes),
             "state": "st-123", "aud": self.fake.fhir_base, "code_challenge": challenge, "code_challenge_method": method}
        q.update(extra)
        return self.fake.authorize_url + "?" + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None})

    def sign_in(self, patient="pat-1", scopes=SCOPES, **form):
        """A whole sign-in: authorize, then exchange the code. Returns the token response."""
        verifier, challenge = pkce()
        r = self.fake.authorize(self.authorize_url(challenge, scopes=scopes), patient=patient)
        self.assertEqual(r.state, "st-123")
        status, _, tok = call("POST", self.fake.token_url, dict(
            {"grant_type": "authorization_code", "code": r.code, "redirect_uri": RETURN, "code_verifier": verifier,
             "client_id": "app-1"}, **form))
        self.assertEqual(status, 200, tok)
        return tok

    def refresh(self, refresh_token, **form):
        return call("POST", self.fake.token_url, dict(
            {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": "app-1"}, **form))

    def search(self, token, path):
        return call("GET", f"{self.fake.fhir_base}/{path}", headers=bearer(token))


class ClientFlowExample(Base):
    """What #32's sign-in and #91's sync do, written with only the standard library."""

    def test_sign_in_pull_refresh_disconnect(self):
        # 1. discovery, from the FHIR base the user picked
        status, _, smart = call("GET", self.fake.fhir_base + "/.well-known/smart-configuration")
        self.assertEqual(status, 200)
        self.assertIn("S256", smart["code_challenge_methods_supported"])

        # 2. the app builds the authorize URL with PKCE, state and aud; the "browser" signs in as Robin and approves
        verifier, challenge = pkce()
        url = smart["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": "app-1", "redirect_uri": RETURN, "scope": " ".join(SCOPES),
            "state": "st-123", "aud": self.fake.fhir_base, "code_challenge": challenge, "code_challenge_method": "S256"})
        back = self.fake.authorize(url, patient="pat-1")
        self.assertTrue(back.location.startswith(RETURN + "?"))
        self.assertEqual(back.state, "st-123")

        # 3. the install's /connect/return exchanges the code
        status, _, tok = call("POST", smart["token_endpoint"], {
            "grant_type": "authorization_code", "code": back.code, "redirect_uri": RETURN,
            "code_verifier": verifier, "client_id": "app-1"})
        self.assertEqual(status, 200, tok)
        self.assertEqual(tok["patient"], "pat-1")
        self.assertEqual(tok["token_type"], "Bearer")

        # 4. a pull, following `next` links
        ids, url = [], f"{self.fake.fhir_base}/Observation?patient={tok['patient']}&_count=3"
        while url:
            status, _, bundle = call("GET", url, headers=bearer(tok["access_token"]))
            self.assertEqual(status, 200, bundle)
            ids += [e["resource"]["id"] for e in bundle["entry"]]
            url = next((link["url"] for link in bundle["link"] if link["relation"] == "next"), None)
        self.assertEqual(sorted(ids), ["obs-bp", "obs-hgb", "obs-ldl", "obs-plt", "obs-smoke", "obs-ua", "obs-wt"])
        since = bundle["meta"]["lastUpdated"]

        # 5. later: the access token has expired; refresh, then pull only what changed since
        self.fake.advance(3601)
        self.assertEqual(self.search(tok["access_token"], "Observation")[0], 401)
        status, _, tok2 = self.refresh(tok["refresh_token"])
        self.assertEqual(status, 200, tok2)
        self.fake.add({"resourceType": "Observation", "status": "final", "code": {"text": "Hemoglobin"},
                       "valueQuantity": {"value": 13.0, "unit": "g/dL"}}, patient="pat-1")
        _, _, bundle = self.search(tok2["access_token"], "Observation?_lastUpdated=gt" + urllib.parse.quote(since))
        self.assertEqual(bundle["total"], 1)

        # 6. disconnect: revoke the refresh token; nothing issued from the sign-in works any more
        status, _, _ = call("POST", smart["revocation_endpoint"], {"token": tok2["refresh_token"], "client_id": "app-1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.search(tok2["access_token"], "Observation")[0], 401)
        self.assertEqual(self.refresh(tok2["refresh_token"])[0], 400)


class Discovery(Base):
    settings = {"capabilities": ["launch-standalone", "client-public"], "smart_config": {"revocation_endpoint": None}}

    def test_configurable(self):
        _, _, smart = call("GET", self.fake.fhir_base + "/.well-known/smart-configuration")
        self.assertEqual(smart["capabilities"], ["launch-standalone", "client-public"])
        self.assertNotIn("revocation_endpoint", smart)
        self.assertNotIn("registration_endpoint", smart)
        self.assertEqual(smart["issuer"], self.fake.fhir_base)
        self.fake.capabilities = None   # settings are read on each request
        _, _, smart = call("GET", self.fake.fhir_base + "/.well-known/smart-configuration")
        self.assertIn("permission-offline", smart["capabilities"])

    def test_metadata_without_a_token(self):
        status, _, cs = call("GET", self.fake.fhir_base + "/metadata")
        self.assertEqual(status, 200)
        self.assertEqual(cs["fhirVersion"], "4.0.1")


class Authorize(Base):
    def test_deny(self):
        _, challenge = pkce()
        r = self.fake.authorize(self.authorize_url(challenge), approve=False)
        self.assertEqual((r.status, r.error, r.state, r.code), (302, "access_denied", "st-123", None))

    def test_refused_without_redirect(self):
        _, challenge = pkce()
        r = self.fake.authorize(self.authorize_url(challenge, redirect_uri=RETURN + "/"))   # not an exact match
        self.assertEqual((r.status, r.location), (400, None))
        r = self.fake.authorize(self.authorize_url(challenge, client_id="someone-else"))
        self.assertEqual((r.status, r.location), (400, None))

    def test_pkce_and_aud_required(self):
        _, challenge = pkce()
        for url in (self.authorize_url(None), self.authorize_url(challenge, method="plain"),
                    self.authorize_url(challenge, method=None), self.authorize_url(challenge, aud="https://elsewhere.test/fhir"),
                    self.authorize_url(challenge, response_type="token")):
            r = self.fake.authorize(url)
            self.assertEqual(r.status, 302)
            self.assertIn(r.error, ("invalid_request", "unsupported_response_type"), url)
            self.assertIsNone(r.code)

    def test_patient_unticks_scopes(self):
        verifier, challenge = pkce()
        r = self.fake.authorize(self.authorize_url(challenge, scopes=["launch/patient", "offline_access",
                                                                      "patient/Observation.rs", "patient/Condition.rs"]),
                                patient="pat-2", scopes=["launch/patient", "patient/Observation.rs"])
        _, _, tok = call("POST", self.fake.token_url, {"grant_type": "authorization_code", "code": r.code,
                                                       "redirect_uri": RETURN, "code_verifier": verifier, "client_id": "app-1"})
        self.assertEqual(tok["scope"], "launch/patient patient/Observation.rs")
        self.assertNotIn("refresh_token", tok)   # no offline_access granted


class TokenExchange(Base):
    def code(self, challenge):
        return self.fake.authorize(self.authorize_url(challenge)).code

    def exchange(self, code, verifier, **over):
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": RETURN, "code_verifier": verifier,
                "client_id": "app-1"}
        form.update(over)
        return call("POST", self.fake.token_url, {k: v for k, v in form.items() if v is not None})

    def test_pkce_failures(self):
        verifier, challenge = pkce()
        other, _ = pkce()
        for bad in (other, None, "short", verifier[:-1] + "!"):
            status, _, err = self.exchange(self.code(challenge), bad)
            self.assertEqual((status, err["error"]), (400, "invalid_grant"), bad)

    def test_redirect_uri_and_client_must_match(self):
        verifier, challenge = pkce()
        self.fake.register_client("app-2", [RETURN, "http://127.0.0.1:9999/connect/return"])
        status, _, err = self.exchange(self.code(challenge), verifier, redirect_uri="http://127.0.0.1:9999/connect/return")
        self.assertEqual((status, err["error"]), (400, "invalid_grant"))
        status, _, err = self.exchange(self.code(challenge), verifier, client_id="app-2")
        self.assertEqual((status, err["error"]), (400, "invalid_grant"))
        status, _, err = self.exchange(self.code(challenge), verifier, client_id="nobody")
        self.assertEqual((status, err["error"]), (401, "invalid_client"))

    def test_code_is_single_use_and_short_lived(self):
        verifier, challenge = pkce()
        code = self.code(challenge)
        status, _, tok = self.exchange(code, verifier)
        self.assertEqual(status, 200)
        status, _, err = self.exchange(code, verifier)
        self.assertEqual((status, err["error"]), (400, "invalid_grant"))
        # a replayed code ends what the first exchange got
        self.assertEqual(self.search(tok["access_token"], "Patient/pat-1")[0], 401)
        code = self.code(challenge)
        self.fake.advance(61)
        self.assertEqual(self.exchange(code, verifier)[0], 400)

    def test_confidential_client(self):
        self.fake.register_client("app-c", [RETURN], secret="fictional-secret")
        verifier, challenge = pkce()

        def code():
            return self.fake.authorize(self.authorize_url(challenge, client_id="app-c")).code
        self.assertEqual(self.exchange(code(), verifier, client_id="app-c")[0], 401)   # no secret
        self.assertEqual(self.exchange(code(), verifier, client_id="app-c", client_secret="wrong")[0], 401)
        self.assertEqual(self.exchange(code(), verifier, client_id="app-c", client_secret="fictional-secret")[0], 200)
        basic = "Basic " + base64.b64encode(b"app-c:fictional-secret").decode()
        status, _, tok = call("POST", self.fake.token_url, {"grant_type": "authorization_code", "code": code(),
                                                            "redirect_uri": RETURN, "code_verifier": verifier},
                              headers={"Authorization": basic})
        self.assertEqual(status, 200, tok)
        # refresh needs the secret too
        status, _, _ = call("POST", self.fake.token_url, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                                          "client_id": "app-c"})
        self.assertEqual(status, 401)
        status, _, _ = call("POST", self.fake.token_url, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                            headers={"Authorization": basic})
        self.assertEqual(status, 200)

    def test_no_refresh_token_for_public_clients_when_configured(self):
        self.fake.offline_for_public = False
        tok = self.sign_in()
        self.assertNotIn("refresh_token", tok)
        self.assertNotIn("offline_access", tok["scope"])


class Refresh(Base):
    def test_rotation(self):
        tok = self.sign_in()
        status, _, tok2 = self.refresh(tok["refresh_token"])
        self.assertEqual(status, 200)
        self.assertNotEqual(tok2["refresh_token"], tok["refresh_token"])
        self.assertEqual(self.search(tok2["access_token"], "Patient/pat-1")[0], 200)
        # reusing the rotated token is refused and ends the grant, the new tokens included
        status, _, err = self.refresh(tok["refresh_token"])
        self.assertEqual((status, err["error"]), (400, "invalid_grant"))
        self.assertEqual(self.refresh(tok2["refresh_token"])[0], 400)
        self.assertEqual(self.search(tok2["access_token"], "Patient/pat-1")[0], 401)

    def test_same_and_omit(self):
        self.fake.refresh = "same"
        tok = self.sign_in()
        _, _, tok2 = self.refresh(tok["refresh_token"])
        self.assertEqual(tok2["refresh_token"], tok["refresh_token"])
        self.fake.refresh = "omit"
        _, _, tok3 = self.refresh(tok["refresh_token"])
        self.assertNotIn("refresh_token", tok3)
        self.assertEqual(self.refresh(tok["refresh_token"])[0], 200)   # the original keeps working

    def test_expired_and_revoked(self):
        self.fake.refresh_ttl = 600
        tok = self.sign_in()
        self.fake.advance(601)
        status, _, err = self.refresh(tok["refresh_token"])
        self.assertEqual((status, err["error"]), (400, "invalid_grant"))
        tok = self.sign_in()
        self.fake.revoke_all("pat-1")   # the patient revokes the app in their portal
        self.assertEqual(self.refresh(tok["refresh_token"])[0], 400)
        self.assertEqual(self.search(tok["access_token"], "Patient/pat-1")[0], 401)

    def test_access_expiry(self):
        tok = self.sign_in()
        self.fake.expire_access_tokens()
        status, headers, _ = self.search(tok["access_token"], "Patient/pat-1")
        self.assertEqual(status, 401)
        self.assertIn('error="invalid_token"', headers["WWW-Authenticate"])
        status, _, tok2 = self.refresh(tok["refresh_token"])
        self.assertEqual(self.search(tok2["access_token"], "Patient/pat-1")[0], 200)

    def test_scope_can_narrow_not_widen(self):
        tok = self.sign_in(scopes=["offline_access", "patient/Observation.rs"])
        self.assertEqual(self.refresh(tok["refresh_token"], scope="offline_access patient/*.rs")[0], 400)

    def test_revoking_an_access_token_only(self):
        tok = self.sign_in()
        self.assertEqual(call("POST", self.fake.revoke_url, {"token": tok["access_token"], "client_id": "app-1"})[0], 200)
        self.assertEqual(self.search(tok["access_token"], "Patient/pat-1")[0], 401)
        self.assertEqual(self.refresh(tok["refresh_token"])[0], 200)
        self.assertEqual(call("POST", self.fake.revoke_url, {"token": "never-issued", "client_id": "app-1"})[0], 200)


class Searches(Base):
    settings = {"page_size": 4}

    def test_paging(self):
        tok = self.sign_in("pat-2")
        status, _, first = self.search(tok["access_token"], "Observation?patient=pat-2&_count=2")
        self.assertEqual((status, first["total"], len(first["entry"])), (200, 6, 2))
        seen, bundle = [], first
        while True:
            seen += [e["resource"]["id"] for e in bundle["entry"]]
            nxt = [link["url"] for link in bundle["link"] if link["relation"] == "next"]
            if not nxt:
                break
            bundle = call("GET", nxt[0], headers=bearer(tok["access_token"]))[2]
        self.assertEqual(sorted(seen), [f"obs-s{i}" for i in range(1, 7)])
        _, _, page = self.search(tok["access_token"], "Observation")   # default page size from settings
        self.assertEqual(len(page["entry"]), 4)
        _, _, labs = self.search(tok["access_token"], "Observation?category=laboratory&code=http://loinc.org|2345-7")
        self.assertEqual(labs["total"], 4)

    def test_last_updated(self):
        tok = self.sign_in("pat-2")
        _, _, full = self.search(tok["access_token"], "Observation")
        since = full["meta"]["lastUpdated"]
        q = "Observation?_lastUpdated=gt" + urllib.parse.quote(since)
        self.assertEqual(self.search(tok["access_token"], q)[2]["total"], 0)
        added = self.fake.add({"resourceType": "Observation", "status": "final", "code": {"text": "Glucose"},
                               "valueQuantity": {"value": 110, "unit": "mg/dL"}}, patient="pat-2")
        changed = self.fake.update("Observation", "obs-s2", {"status": "amended"})
        self.fake.add({"resourceType": "Observation", "status": "final", "code": {"text": "Not Sam's"}}, patient="pat-1")
        _, _, inc = self.search(tok["access_token"], q)
        self.assertEqual(sorted(e["resource"]["id"] for e in inc["entry"]), sorted([added["id"], "obs-s2"]))
        self.assertEqual(changed["meta"]["versionId"], "2")
        self.assertEqual(self.search(tok["access_token"], "Observation?_lastUpdated=lt2025-10-02")[2]["total"], 5)
        self.assertEqual(self.search(tok["access_token"], "Observation?_lastUpdated=ge2025-10-01&_lastUpdated=le2025-10-01")[2]["total"], 5)
        self.fake.delete("Observation", "obs-s3")
        self.assertEqual(self.search(tok["access_token"], "Observation")[2]["total"], 6)

    def test_everything(self):
        tok = self.sign_in("pat-1")
        types, url = {}, f"{self.fake.fhir_base}/Patient/pat-1/$everything?_count=5"
        while url:
            _, _, b = call("GET", url, headers=bearer(tok["access_token"]))
            for e in b["entry"]:
                types[e["resource"]["resourceType"]] = types.get(e["resource"]["resourceType"], 0) + 1
            url = next((link["url"] for link in b["link"] if link["relation"] == "next"), None)
        self.assertEqual(types["ExplanationOfBenefit"], 2)
        self.assertEqual(types["Patient"], 1)
        self.assertNotIn("Organization", types)   # outside the patient's compartment
        _, _, b = self.search(tok["access_token"], "Patient/pat-1/$everything?_type=Condition,Encounter")
        self.assertEqual(b["total"], 5)
        self.assertEqual(self.search(tok["access_token"], "Patient/pat-2/$everything")[0], 403)

    def test_every_resource_type(self):
        tok = self.sign_in("pat-2")
        for t in ("Patient", "Observation", "Condition", "MedicationRequest", "Encounter", "DocumentReference",
                  "DiagnosticReport", "ExplanationOfBenefit"):
            for patient in ("pat-1", "pat-2"):
                self.assertTrue(self.fake.resources(t, patient), (t, patient))
            status, _, b = self.search(tok["access_token"], f"{t}?patient=pat-2")
            self.assertEqual(status, 200, t)
            self.assertTrue(b["entry"], t)
            self.assertTrue(all(e["resource"].get("subject", e["resource"].get("patient", {})).get("reference", "Patient/pat-2")
                                == "Patient/pat-2" for e in b["entry"]), t)

    def test_binary_attachments(self):
        tok = self.sign_in("pat-1")
        doc = self.search(tok["access_token"], "DocumentReference/doc-1")[2]
        url = doc["content"][0]["attachment"]["url"]
        self.assertTrue(url.startswith("Binary/"))
        status, headers, raw = self.search(tok["access_token"], url)
        with open(os.path.join(ROOT, "tests", "fixtures", "importers", "fhir", "bundle", "attachments",
                               "discharge-summary.pdf"), "rb") as f:
            self.assertEqual(raw, f.read())
        self.assertEqual(headers["Content-Type"], "application/pdf")
        status, _, b = call("GET", f"{self.fake.fhir_base}/{url}",
                            headers=dict(bearer(tok["access_token"]), Accept="application/fhir+json"))
        self.assertEqual(b["resourceType"], "Binary")
        report = self.search(tok["access_token"], "DiagnosticReport/dr-xr")[2]
        self.assertEqual([p["url"] for p in report["presentedForm"]], ["Binary/bin-dr-xr-0", "Binary/bin-dr-xr-1"])
        tok2 = self.sign_in("pat-2")
        self.assertEqual(self.search(tok2["access_token"], url)[0], 403)   # Robin's document, Sam's token
        self.assertEqual(self.search(tok2["access_token"], "Binary/bin-doc-s1-0")[2][:10], b"Fictional ")


class Scopes(Base):
    def test_enforced(self):
        tok = self.sign_in("pat-2", scopes=["patient/Observation.rs", "patient/Patient.r"])
        self.assertEqual(self.search(tok["access_token"], "Observation")[0], 200)
        self.assertEqual(self.search(tok["access_token"], "Condition")[0], 403)
        self.assertEqual(self.search(tok["access_token"], "Patient/pat-2")[0], 200)
        self.assertEqual(self.search(tok["access_token"], "Patient")[0], 403)          # read, not search
        self.assertEqual(self.search(tok["access_token"], "Observation?patient=pat-1")[0], 403)
        self.assertEqual(self.search(tok["access_token"], "Observation/obs-hgb")[0], 403)  # Robin's
        self.assertEqual(self.search(tok["access_token"], "Binary/bin-doc-s1-0")[0], 403)
        tok = self.sign_in("pat-2", scopes=["patient/Condition.read"])                   # SMART v1 style
        self.assertEqual(self.search(tok["access_token"], "Condition?patient=Patient/pat-2")[0], 200)

    def test_no_token(self):
        status, headers, _ = call("GET", self.fake.fhir_base + "/Patient/pat-1")
        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers["WWW-Authenticate"])


class Faults(Base):
    def test_rate_limit(self):
        tok = self.sign_in()
        self.fake.fail(429, retry_after=7, path="/fhir/Observation")
        status, headers, _ = self.search(tok["access_token"], "Observation")
        self.assertEqual((status, headers["Retry-After"]), (429, "7"))
        self.assertEqual(self.search(tok["access_token"], "Condition")[0], 200)   # other paths untouched
        self.assertEqual(self.search(tok["access_token"], "Observation")[0], 200)  # only once

    def test_server_errors_and_slow(self):
        tok = self.sign_in()
        self.fake.fail(503, times=2)
        self.assertEqual(self.search(tok["access_token"], "Observation")[0], 503)
        self.assertEqual(self.refresh(tok["refresh_token"])[0], 503)               # the token endpoint too
        self.assertEqual(self.search(tok["access_token"], "Observation")[0], 200)
        self.fake.fail(500, times=None, path="/fhir/Condition")
        for _ in range(3):
            self.assertEqual(self.search(tok["access_token"], "Condition")[0], 500)
        self.fake.clear_faults()
        self.assertEqual(self.search(tok["access_token"], "Condition")[0], 200)
        self.fake.slow(0.5)
        t = time.monotonic()
        self.assertEqual(self.search(tok["access_token"], "Observation")[0], 200)
        self.assertGreaterEqual(time.monotonic() - t, 0.45)


class Recording(Base):
    def test_requests_are_recorded(self):
        tok = self.sign_in()
        self.fake.clear_requests()
        # a client that thinks it's talking to a vendor's host (as #97's test can arrange, by Host header or a proxy)
        call("GET", self.fake.fhir_base + "/Observation?_count=1",
             headers=dict(bearer(tok["access_token"]), Host="fhir.vendor.example"))
        call("GET", self.fake.fhir_base + "/.well-known/smart-configuration")
        first, second = self.fake.requests
        self.assertEqual((first.method, first.host, first.path, first.query, first.authorization, first.status),
                         ("GET", "fhir.vendor.example", "/fhir/Observation", {"_count": "1"}, True, 200))
        self.assertEqual((second.host, second.authorization), (f"127.0.0.1:{self.fake.port}", False))
        self.assertEqual(self.fake.hosts(), {"fhir.vendor.example", f"127.0.0.1:{self.fake.port}"})
        self.assertNotIn(tok["access_token"], repr(self.fake.requests))   # never the credential itself

    def test_two_fakes_are_separate_hosts(self):
        with FakeFHIR(patients=["sam-sample"]) as insurer:
            self.assertNotEqual(insurer.port, self.fake.port)
            self.assertEqual(insurer.patients, ["pat-2"])
            call("GET", insurer.fhir_base + "/metadata")
            self.assertEqual(len(insurer.requests), 1)
            self.assertEqual(self.fake.requests, [])


try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:   # pragma: no cover
    rsa = None


@unittest.skipIf(rsa is None, "cryptography isn't installed")
class DynamicRegistration(Base):
    """Epic's offline access for apps without a secret: register a key after a standalone sign-in, then get
    access tokens with JWTs signed by it."""
    settings = {"dynamic_registration": True}

    def jwt(self, key, claims, kid="k1"):
        def enc(o):
            return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
        head = enc({"alg": "RS384", "typ": "JWT", "kid": kid}) + "." + enc(claims)
        sig = key.sign(head.encode(), padding.PKCS1v15(), hashes.SHA384())
        return head + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    def test_register_and_jwt_bearer(self):
        _, _, smart = call("GET", self.fake.fhir_base + "/.well-known/smart-configuration")
        self.assertEqual(smart["registration_endpoint"], self.fake.register_url)
        tok = self.sign_in(scopes=["launch/patient", "patient/*.rs"])
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = key.public_key().public_numbers()

        def b64int(i):
            return base64.urlsafe_b64encode(i.to_bytes((i.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
        jwk = {"kty": "RSA", "kid": "k1", "alg": "RS384", "n": b64int(pub.n), "e": b64int(pub.e)}
        status, _, reg = call("POST", self.fake.register_url, body=json.dumps({"software_id": "app-1", "jwks": {"keys": [jwk]}}).encode(),
                              headers=dict(bearer(tok["access_token"]), **{"Content-Type": "application/json"}))
        self.assertEqual(status, 201, reg)
        self.assertEqual(call("POST", self.fake.register_url, body=b"{}")[0], 401)

        def claims(**over):
            c = {"iss": reg["client_id"], "sub": reg["client_id"], "aud": self.fake.token_url,
                 "jti": os.urandom(8).hex(), "exp": int(self.fake.now()) + 240}
            c.update(over)
            return c

        def grant(assertion):
            return call("POST", self.fake.token_url, {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                      "assertion": assertion, "client_id": reg["client_id"]})
        self.fake.advance(7200)   # long after the first sign-in's access token expired
        status, _, t = grant(self.jwt(key, claims()))
        self.assertEqual(status, 200, t)
        self.assertEqual((t["patient"], self.search(t["access_token"], "Condition")[0]), ("pat-1", 200))
        self.assertNotIn("refresh_token", t)
        c = claims()
        self.assertEqual(grant(self.jwt(key, c))[0], 200)
        self.assertEqual(grant(self.jwt(key, c))[0], 400)                       # jti reused
        self.assertEqual(grant(self.jwt(key, claims(aud="https://elsewhere.test")))[0], 400)
        self.assertEqual(grant(self.jwt(key, claims(exp=int(self.fake.now()) + 3600)))[0], 400)
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertEqual(grant(self.jwt(other, claims()))[0], 400)               # not the registered key
        self.fake.revoke_all("pat-1")
        self.assertEqual(grant(self.jwt(key, claims()))[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
