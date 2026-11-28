#!/usr/bin/env python3
"""Sign in with OIDC (#1109), against the fake identity provider (tests/fakeoidc.py): a linked identity signs in, an
unknown one is refused; a bad signature, alg none or HS256, the wrong aud, azp or iss, an expired token, a time of
issue that isn't now, a replayed state or nonce, another browser's state and a PKCE verifier that isn't the one sent
are refused; MFA by amr or acr counts, else the account's own second factor is asked for; linking and unlinking need
the fresh check; Sign in with OIDC only is refused before the admin's own sign-in works, refuses passwords and
passkeys on their own once on, and server.manage turns it off; the client secret never reaches a page; nothing is
contacted before a provider is set up. Fictional people only; nothing real is contacted.

  python3 tests/test_oidc.py
"""
import io, json, logging, os, sys, tempfile, time, unittest, urllib.parse
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-oidc-test-")
ORIGIN = "http://sanguity.test:8730"
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_RECORDS=os.path.join(TMP, "records"), SANGUITY_BUILD=os.path.join(TMP, "no-build"),
                  SANGUITY_WATCH_FILE=os.path.join(TMP, "watch", "daily.json"), SANGUITY_LEGACY_WWW=os.path.join(TMP, "www"),
                  SANGUITY_KEY_FILE=os.path.join(TMP, "keys", "data_key"), PUBLIC_ORIGINS=ORIGIN, CONNECT_ALLOW_HTTP="1")
os.environ.pop("OIDC_CLIENT_SECRET", None)
os.makedirs(os.path.join(TMP, "records"))
os.makedirs(os.path.join(TMP, "keys"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient   # noqa: E402

from server import accounts, atrest, crypto, db as dbm, main, manage, oidc, profiles as prof, twofactor   # noqa: E402
from tests import netguard   # noqa: E402
from tests.fakeoidc import FakeOIDC   # noqa: E402

H = {"X-Sanguity": "1"}
PW = {"ada": "a fictional admin's long passphrase", "kim": "seventeen quiet orange kites", "lee": "maple tunnel brisk saddle"}
SECRET = "fictional client secret, never shown"
logging.getLogger("sanguity.security").setLevel(logging.ERROR)   # (assertLogs reads what it needs)


def client():
    return TestClient(main.app, base_url="http://testserver")


def q(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def code_now(secret):
    return twofactor._code(secret, int(time.time() // twofactor.PERIOD))


def setUpModule():
    netguard.install()


def tearDownModule():
    netguard.uninstall()
    netguard.check()


class OIDC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbm.init()
        db = dbm.connect()
        atrest.check_key(db); atrest.load_pepper(db); prof.bootstrap(db)
        cls.uid = {n: accounts.create(db, n, is_admin=n == "ada", password=pw)[0] for n, pw in PW.items()}
        cls.lee_secret = twofactor.new_secret()
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(cls.lee_secret), cls.uid["lee"]))
        db.execute("UPDATE users SET totp_secret=? WHERE id=?", (crypto.enc(twofactor.new_secret()), cls.uid["ada"]))
        db.close()
        cls.idp = FakeOIDC(client_id="sanguity-test", secret=SECRET).start()

    @classmethod
    def tearDownClass(cls):
        cls.idp.stop()

    def setUp(self):
        self.idp.person = {"sub": "kim-subject-1", "preferred_username": "kim (fictional)"}
        self.idp.token, self.idp.sign, self.idp.header, self.idp.discovery, self.idp.issuer_said = {}, "RS256", {}, {}, None
        self.idp.secret = SECRET
        main.sec.throttle._fails.clear()   # (each test from the same address)
        db = dbm.connect()
        db.execute("DELETE FROM oidc_identities")
        db.execute("DELETE FROM meta WHERE k IN ('oidc', 'oidc.secret', 'require_2fa')")
        db.close()
        oidc._cache.clear()
        oidc.pending.clear()

    # ------------------------------------------------------------ helpers
    def signed_in(self, name):
        c = client()
        r = c.post("/api/login", json={"username": name, "password": PW[name]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        if r.json().get("two_factor"):
            r = c.post("/api/login/code", json={"ticket": r.json()["ticket"], "code": self.code(name)}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        return c

    def code(self, name):
        db = dbm.connect()
        secret = crypto.dec(db.execute("SELECT totp_secret FROM users WHERE username=?", (name,)).fetchone()[0])
        db.execute("UPDATE users SET totp_last_step=0 WHERE username=?", (name,))   # (a second code in the same 30 s)
        db.close()
        return code_now(secret)

    def set_up(self, **more):
        """The provider set up, as an admin does under General → Sign-in provider."""
        ada = self.signed_in("ada")
        r = ada.put("/api/settings/install/oidc", json={"issuer": self.idp.issuer, "client_id": "sanguity-test",
                                                        "secret": SECRET, "label": "Authentik (fictional)", **more}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return ada

    def link(self, name, sub):
        db = dbm.connect()
        oidc.link(db, self.uid[name], self.idp.issuer, sub)
        db.close()

    def start(self, c=None):
        c = c or client()
        r = c.post("/api/login/oidc/start", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return c, r.json()["url"]

    def come_back(self, c, url, **body):
        back = q(self.idp.authorize(url))
        return c.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"], **body}, headers=H)

    def oidc_sign_in(self):
        c, url = self.start()
        return c, self.come_back(c, url)

    def refused(self, why_in, **token):
        main.sec.throttle._fails.clear()   # (many refusals from one address in a row)
        self.idp.token = token
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 401, r.text)
        self.assertIn(why_in, r.json()["detail"])
        self.assertEqual(c.get("/api/me").status_code, 401, "no session made")
        return r

    # ------------------------------------------------------------ not set up
    def test_nothing_is_offered_or_contacted_before_a_provider_is_set_up(self):
        before = len(self.idp.requests)
        self.assertEqual(client().get("/api/login/oidc").json(), {"label": None, "only": False})
        self.assertEqual(client().post("/api/login/oidc/start", headers=H).status_code, 404)
        self.assertEqual(oidc.hosts(dbm.connect()), [])
        self.assertEqual(len(self.idp.requests), before, "nothing was contacted")

    # ------------------------------------------------------------ signing in
    def test_a_linked_identity_signs_in_as_a_password_does(self):
        self.set_up()
        self.link("kim", "kim-subject-1")
        with self.assertLogs("sanguity.security", "INFO") as seen:
            c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["username"], "kim")
        self.assertEqual(c.get("/api/me").json()["user"]["username"], "kim")
        self.assertTrue(any('"event":"login.ok"' in m and '"method":"oidc"' in m for m in seen.output), seen.output)
        url = next(x for x in reversed(self.idp.requests) if x[1] == "/auth")[2]   # what the authorization request asked
        self.assertEqual(url["code_challenge_method"], "S256")
        self.assertEqual(url["redirect_uri"], ORIGIN + "/login.html", "the redirect URI from the public address, exactly")
        self.assertEqual(url["scope"], "openid email profile")
        self.assertTrue(url["nonce"] and url["state"] and url["code_challenge"])
        token = next(x for x in reversed(self.idp.requests) if x[1].endswith("/token"))[2]
        self.assertNotIn("client_secret", token, "client_secret_basic, as the provider says it takes")
        db = dbm.connect()
        contacts = {crypto.dec(r["host"]) for r in db.execute("SELECT host FROM outside_contacts WHERE purpose=?", (oidc.PURPOSE,))}
        self.assertEqual(contacts, {urllib.parse.urlsplit(self.idp.base).netloc}, "recorded in Outside connections")
        self.assertIsNotNone(db.execute("SELECT last_used FROM oidc_identities").fetchone()[0])
        self.assertEqual(db.execute("SELECT idp_mfa FROM sessions ORDER BY id DESC LIMIT 1").fetchone()[0], 0)
        db.close()

    def test_an_identity_linked_to_no_account_is_refused_and_no_account_is_made(self):
        self.set_up()
        self.idp.person = {"sub": "a-stranger"}
        db = dbm.connect()
        n = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        db.close()
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 401)
        self.assertIn("isn't linked to an account here", r.json()["detail"])
        db = dbm.connect()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], n)
        db.close()

    def test_a_disabled_account_cant_sign_in(self):
        self.set_up()
        self.link("kim", "kim-subject-1")
        db = dbm.connect()
        db.execute("UPDATE users SET disabled=1 WHERE id=?", (self.uid["kim"],))
        db.close()
        try:
            c, r = self.oidc_sign_in()
            self.assertEqual(r.status_code, 401)
        finally:
            db = dbm.connect()
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (self.uid["kim"],))
            db.close()

    def test_every_bad_token_is_refused(self):
        self.set_up()
        self.link("kim", "kim-subject-1")
        now = int(time.time())
        for sign, why in (("stranger", "signature isn't the provider's"), ("none", "accepts only RS256 and ES256"),
                          ("HS256", "accepts only RS256 and ES256")):
            self.idp.sign = sign
            self.refused(why)
        self.idp.sign = "RS256"
        self.refused("for another client", aud="someone-else")
        self.refused("another client (azp)", aud=["sanguity-test", "someone-else"])
        self.refused("another client (azp)", azp="someone-else")
        self.refused("issued by", iss="https://idp.elsewhere.test")
        self.refused("expired", exp=now - oidc.SKEW - 5)
        self.refused("time of issue", iat=now + oidc.SKEW + 60)
        self.refused("time of issue", iat=now - oidc.IAT_MOST - oidc.SKEW - 60)
        self.refused("isn't valid yet", nbf=now + oidc.SKEW + 60)
        self.refused("nonce", nonce="not-this-sign-ins")
        self.refused("nonce", nonce=None)
        self.refused("names no subject", sub=None)
        self.idp.header = {"kid": "a-key-it-doesnt-have"}
        self.refused("signature isn't the provider's")
        self.idp.header = {"jku": "https://keys.elsewhere.test/jwks"}
        self.refused("names keys")
        self.idp.header = {}
        self.idp.sign = "ES256"   # its P-256 key: accepted
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 200, r.text)
        self.idp.sign = "RS256"
        self.idp.token = {"aud": ["sanguity-test", "other"], "azp": "sanguity-test"}   # several audiences, azp ours: accepted
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 200, r.text)

    def test_a_state_is_good_once_in_the_browser_it_was_started_in(self):
        self.set_up()
        self.link("kim", "kim-subject-1")
        c, url = self.start()
        back = q(self.idp.authorize(url))
        body = {"code": back["code"], "state": back["state"]}
        self.assertEqual(c.post("/api/login/oidc/return", json=body, headers=H).status_code, 200)
        again = c.post("/api/login/oidc/return", json=body, headers=H)
        self.assertEqual(again.status_code, 401, "a replayed state")
        self.assertIn("used already", again.json()["detail"])
        # started in one browser, finished in another: refused, and the state is used up for the first too
        a, url = self.start()
        back = q(self.idp.authorize(url))
        other = client()
        r = other.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertIn("another browser", r.json()["detail"])
        self.assertEqual(a.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H).status_code, 401)
        # a state that expired
        c, url = self.start()
        with mock.patch.object(oidc.time, "time", lambda: time.time_ns() / 1e9 + oidc.STATE_TTL + 1):
            r = self.come_back(c, url)
        self.assertEqual(r.status_code, 401)
        # the provider's own refusal: nothing signed in
        c, url = self.start()
        back = q(self.idp.authorize(url))
        r = c.post("/api/login/oidc/return", json={"state": back["state"], "error": "access_denied"}, headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertIn("didn't sign you in", r.json()["detail"])

    def test_sign_ins_started_from_one_address_are_kept_to_a_few(self):
        """Starting many from one place drops its own oldest, never another's: nobody else is kept from signing in."""
        self.set_up()
        self.link("kim", "kim-subject-1")
        oidc.pending.add("someone-elses", {"expires": time.time() + 60, "ip": "203.0.113.9"})
        c, first = self.start()
        for _ in range(oidc.pending.PER_ADDRESS):
            c, last = self.start(c)
        self.assertEqual(self.come_back(c, first).status_code, 401, "its oldest went")
        self.assertEqual(self.come_back(c, last).status_code, 200)
        self.assertIsNotNone(oidc.pending.take("someone-elses"))

    def test_a_replayed_nonce_is_refused(self):
        self.set_up()
        self.link("kim", "kim-subject-1")
        c, url = self.start()
        first = q(url)["nonce"]
        self.assertEqual(self.come_back(c, url).status_code, 200)
        self.refused("nonce", nonce=first)   # an ID token from that sign-in, brought to a new one

    def test_pkce_s256_is_required_and_its_verifier_is_the_one_sent(self):
        self.set_up()
        self.link("kim", "kim-subject-1")
        self.idp.discovery = {"code_challenge_methods_supported": ["plain"]}
        r = client().post("/api/login/oidc/start", headers=H)
        self.assertEqual(r.status_code, 502)
        self.assertIn("PKCE with S256", r.json()["detail"])
        self.idp.discovery = {}
        oidc._cache.clear()
        c, url = self.start()
        entry = next(iter(oidc.pending._p.values()))
        entry["verifier"] = "x" * 64   # not the one the challenge was made from
        r = self.come_back(c, url)
        self.assertEqual(r.status_code, 401)
        self.assertIn("didn't finish the sign-in (invalid_grant)", r.json()["detail"])

    def test_the_discovery_document_must_name_the_issuer_set_up(self):
        self.set_up()
        self.idp.issuer_said = "https://idp.elsewhere.test"
        r = client().post("/api/login/oidc/start", headers=H)
        self.assertEqual(r.status_code, 502)
        self.assertIn("names its issuer as", r.json()["detail"])

    # ------------------------------------------------------------ two factors
    def test_the_providers_two_factors_count_otherwise_the_accounts_own_is_asked_for(self):
        self.set_up(acr="gold")
        self.link("lee", "lee-subject-1")
        self.idp.person = {"sub": "lee-subject-1"}
        self.idp.token = {"amr": ["pwd"]}
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["two_factor"], "one factor at the provider: the account's code next")
        self.assertEqual(c.get("/api/me").status_code, 401)
        with self.assertLogs("sanguity.security", "INFO") as seen:
            r = c.post("/api/login/code", json={"ticket": r.json()["ticket"], "code": self.code("lee")}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(any('"method":"oidc + code"' in m for m in seen.output), seen.output)
        for token in ({"amr": ["pwd", "mfa"]}, {"amr": ["hwk"]}, {"acr": "gold"}):
            self.idp.token = token
            c, r = self.oidc_sign_in()
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("two_factor", r.json(), token)
            self.assertEqual(c.get("/api/me").json()["user"]["username"], "lee")
        self.idp.token = {"acr": "silver"}
        c, r = self.oidc_sign_in()
        self.assertTrue(r.json()["two_factor"], "an acr that isn't the one set")

    def test_where_two_factor_is_required_the_providers_counts_for_its_session(self):
        self.set_up()
        self.link("kim", "kim-subject-1")
        db = dbm.connect()
        dbm.meta_set(db, "require_2fa", "1")
        db.close()
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(c.get("/api/me").json()["user"]["needs_two_factor"], "one factor: kim sets up her own, as now")
        self.assertEqual(c.get("/api/lab-names").status_code, 403)
        self.idp.token = {"amr": ["mfa"]}
        c, r = self.oidc_sign_in()
        me = c.get("/api/me").json()["user"]
        self.assertFalse(me["needs_two_factor"])
        self.assertFalse(me["two_factor"], "the account itself still has none")
        self.assertEqual(c.get("/api/lab-names").status_code, 200)

    # ------------------------------------------------------------ linking
    def test_linking_needs_the_fresh_check_and_unlinking_works(self):
        self.set_up()
        kim = self.signed_in("kim")
        db = dbm.connect()
        db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uid["kim"],))
        db.close()
        self.assertEqual(kim.post("/api/me/oidc/start", headers=H).json()["detail"], "reauth_required")
        self.assertEqual(kim.post("/api/me/reauth", json={"secret": PW["kim"]}, headers=H).status_code, 200)
        r = kim.post("/api/me/oidc/start", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(q(r.json()["url"])["prompt"], "login")
        back = q(self.idp.authorize(r.json()["url"]))
        r = kim.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["name"], "kim (fictional)")
        mine = kim.get("/api/me/oidc").json()
        self.assertEqual([i["name"] for i in mine["identities"]], ["kim (fictional)"])
        self.assertEqual(mine["provider"]["label"], "Authentik (fictional)")
        c, r = self.oidc_sign_in()
        self.assertEqual(r.json()["username"], "kim")
        # the same identity can't be linked to another account
        lee = self.signed_in("lee")
        r = lee.post("/api/me/oidc/start", headers=H)
        back = q(self.idp.authorize(r.json()["url"]))
        r = lee.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("another account", r.json()["detail"])
        # unlinking: the fresh check, then it no longer signs in; the other sign-in (c) ends
        iid = mine["identities"][0]["id"]
        db = dbm.connect()
        db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uid["kim"],))
        db.close()
        self.assertEqual(kim.request("DELETE", f"/api/me/oidc/{iid}", headers=H).json()["detail"], "reauth_required")
        kim.post("/api/me/reauth", json={"secret": PW["kim"]}, headers=H)
        r = kim.request("DELETE", f"/api/me/oidc/{iid}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get("/api/me").status_code, 401, "every other sign-in ends")
        self.assertEqual(kim.get("/api/me/oidc").json()["identities"], [])
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 401)

    def test_a_link_finishes_only_for_the_sign_in_that_started_it(self):
        self.set_up()
        kim = self.signed_in("kim")
        r = kim.post("/api/me/oidc/start", headers=H)
        back = q(self.idp.authorize(r.json()["url"]))
        kim.post("/api/logout", headers=H)
        r = kim.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H)
        self.assertEqual(r.status_code, 401)
        self.assertIn("signed out", r.json()["detail"])

    def test_an_admin_links_and_unlinks_an_accounts_identity_after_the_fresh_check(self):
        ada = self.set_up()
        db = dbm.connect()
        db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uid["ada"],))
        db.close()
        r = ada.post(f"/api/users/{self.uid['kim']}/oidc", json={"subject": "kim-subject-1"}, headers=H)
        self.assertEqual(r.json()["detail"], "reauth_required")
        ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        self.assertEqual(ada.post(f"/api/users/{self.uid['kim']}/oidc", json={"subject": "has space"}, headers=H).status_code, 400)
        r = ada.post(f"/api/users/{self.uid['kim']}/oidc", json={"subject": "kim-subject-1"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        listed = {u["username"]: u["oidc"] for u in ada.get("/api/users").json()}
        self.assertEqual([i["subject"] for i in listed["kim"]], ["kim-subject-1"])
        c, r = self.oidc_sign_in()
        self.assertEqual(r.json()["username"], "kim")
        r = ada.request("DELETE", f"/api/users/{self.uid['kim']}/oidc/{listed['kim'][0]['id']}", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get("/api/me").status_code, 401, "the account is signed out everywhere")

    # ------------------------------------------------------------ the provider's settings
    def test_the_client_secret_is_encrypted_and_never_given_to_a_page(self):
        ada = self.set_up()
        got = ada.get("/api/settings/install/oidc")
        self.assertEqual(got.json()["secret"], "install")
        self.assertEqual(got.json()["redirect_uri"], ORIGIN + "/login.html")
        for path in ("/api/settings/install/oidc", "/api/settings/install", "/api/users", "/api/activity", "/api/me", "/api/login/oidc"):
            self.assertNotIn(SECRET, ada.get(path).text, path)
        db = dbm.connect()
        stored = dbm.meta_get(db, oidc.SECRET_META)
        self.assertTrue(stored.startswith(crypto.FIELD) and SECRET not in stored)
        self.assertNotIn(SECRET, dbm.meta_get(db, oidc.OIDC_META))
        db.close()
        # the environment's wins, and then it can't be changed here
        with mock.patch.dict(os.environ, {"OIDC_CLIENT_SECRET": "the environment's (fictional)"}):
            self.assertEqual(ada.get("/api/settings/install/oidc").json()["secret"], "environment")
            self.assertEqual(ada.put("/api/settings/install/oidc", json={"secret": "another"}, headers=H).status_code, 409)
            self.idp.secret = "the environment's (fictional)"
            self.link("kim", "kim-subject-1")
            c, r = self.oidc_sign_in()
            self.assertEqual(r.status_code, 200, r.text)

    def test_each_setting_is_checked_and_each_change_needs_the_fresh_check(self):
        ada = self.set_up()
        for body, said in (({"issuer": "https://idp.test/?x=1"}, "https://"), ({"scopes": "email profile"}, "openid"),
                           ({"client_id": "has space"}, "spaces"), ({"label": "x" * 41}, "40")):
            r = ada.put("/api/settings/install/oidc", json=body, headers=H)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(said, r.json()["detail"])
        with mock.patch.object(oidc.config, "CONNECT_ALLOW_HTTP", False):
            self.assertIn("https://", ada.put("/api/settings/install/oidc", json={"issuer": "http://idp.test"}, headers=H).json()["detail"])
        db = dbm.connect()
        db.execute("UPDATE sessions SET verified_at=0 WHERE user_id=?", (self.uid["ada"],))
        db.close()
        self.assertEqual(ada.put("/api/settings/install/oidc", json={"label": "Keycloak"}, headers=H).json()["detail"], "reauth_required")
        r = ada.post("/api/settings/install/oidc/test", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["keys"], 2)
        self.assertEqual(self.signed_in("kim").get("/api/settings/install/oidc").status_code, 403)

    # ------------------------------------------------------------ OIDC only
    def test_oidc_only_needs_the_admins_own_sign_in_and_server_manage_turns_it_off(self):
        ada = self.set_up()
        r = ada.put("/api/settings/install/oidc", json={"only": True}, headers=H)
        self.assertEqual(r.status_code, 409)
        self.assertIn("yourself first", r.json()["detail"])
        self.assertFalse(ada.get("/api/settings/install/oidc").json()["may_go_only"])
        # ada links her own (signing in with the provider as she does), then it may go on
        self.idp.person = {"sub": "ada-subject-1"}
        r = ada.post("/api/me/oidc/start", headers=H)
        back = q(self.idp.authorize(r.json()["url"]))
        self.assertEqual(ada.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H).status_code, 200)
        self.assertTrue(ada.get("/api/settings/install/oidc").json()["may_go_only"])
        self.assertEqual(ada.put("/api/settings/install/oidc", json={"only": True}, headers=H).status_code, 200)
        self.assertEqual(client().get("/api/login/oidc").json(), {"label": "Authentik (fictional)", "only": True})
        r = client().post("/api/login", json={"username": "kim", "password": PW["kim"]}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn("signs in with Authentik (fictional) only", r.json()["detail"])
        self.assertEqual(client().post("/api/login/passkey/options", json={}, headers=H).status_code, 403)
        # the provider can't be changed under it, nor ada's only identity unlinked
        self.assertEqual(ada.put("/api/settings/install/oidc", json={"issuer": "https://idp.elsewhere.test"}, headers=H).status_code, 409)
        self.assertEqual(ada.put("/api/settings/install/oidc", json={"secret": "another"}, headers=H).status_code, 409)
        iid = ada.get("/api/me/oidc").json()["identities"][0]["id"]
        self.assertEqual(ada.request("DELETE", f"/api/me/oidc/{iid}", headers=H).status_code, 409)
        # her OIDC sign-in still works, with her code after it
        self.idp.person = {"sub": "ada-subject-1"}
        c, r = self.oidc_sign_in()
        self.assertTrue(r.json()["two_factor"])
        # the way back: python -m server.manage oidc-only off
        with redirect_stdout(io.StringIO()) as out:
            manage.run(["oidc-only", "off"])
        self.assertIn("is off", out.getvalue())
        self.assertEqual(client().post("/api/login", json={"username": "kim", "password": PW["kim"]}, headers=H).status_code, 200)
        self.assertFalse(client().get("/api/login/oidc").json()["only"])

    def test_restoring_keeps_todays_provider_and_identities(self):   # (the access tables, test_backups has the restore)
        from server import backup
        self.assertIn("oidc_identities", backup.ACCESS_TABLES)
        self.assertTrue({"oidc", "oidc.secret"} <= set(backup.ACCESS_META))


if __name__ == "__main__":
    unittest.main(verbosity=2)
