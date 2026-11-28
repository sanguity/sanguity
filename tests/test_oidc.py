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
        """The provider set up, as an admin does under Accounts and sign-in → Sign-in provider."""
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

    def test_a_full_store_drops_the_flooding_addresss_own_never_anothers(self):
        """Starting many from one place (behind a reverse proxy, every visitor's) is never refused (#1109's recheck), and
        once the store is full drops its own oldest, never someone else's sign-in."""
        self.set_up()
        self.link("kim", "kim-subject-1")
        with mock.patch.object(oidc.pending, "MAX", 40):
            oidc.pending.add("someone-elses", {"expires": time.time() + 60, "ip": "203.0.113.9"})
            c, first = self.start()
            for _ in range(oidc.STARTS_MOST + 20):   # past the old per-address refusal, and past MAX
                c, last = self.start(c)
            self.assertEqual(self.come_back(c, first).status_code, 401, "its own oldest went")
            self.assertEqual(self.come_back(c, last).status_code, 200)
            self.assertIsNotNone(oidc.pending.take("someone-elses"), "nobody else's")

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
        for token in ({"amr": ["pwd", "mfa"]}, {"amr": ["pwd", "hwk"]}, {"amr": ["otp", "fpt"]}, {"acr": "gold"}):
            self.idp.token = token
            c, r = self.oidc_sign_in()
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("two_factor", r.json(), token)
            self.assertEqual(c.get("/api/me").json()["user"]["username"], "lee")
        self.idp.token = {"acr": "silver"}
        c, r = self.oidc_sign_in()
        self.assertTrue(r.json()["two_factor"], "an acr that isn't the one set")
        for token in ({"amr": ["otp"]}, {"amr": ["hwk"]}, {"amr": ["otp", "otp"]}, {"amr": ["pwd", "made-up"]}):   # one method: one factor
            self.idp.token = token
            c, r = self.oidc_sign_in()
            self.assertTrue(r.json().get("two_factor"), token)

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
    def test_linking_needs_the_password_and_unlinking_works(self):
        """Linking asks the password, as adding a passkey does (#1109's review): a sign-in's own fresh check isn't enough."""
        self.set_up()
        kim = self.signed_in("kim")   # (fresh: just signed in)
        for body in ({}, {"password": "not kim's password at all"}):
            r = kim.post("/api/me/oidc/start", json=body, headers=H)
            self.assertEqual(r.status_code, 403, body)
            self.assertEqual(r.json()["detail"], "Your password is wrong.")
        r = kim.post("/api/me/oidc/start", json={"password": PW["kim"]}, headers=H)
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
        r = lee.post("/api/me/oidc/start", json={"password": PW["lee"]}, headers=H)
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
        r = kim.post("/api/me/oidc/start", json={"password": PW["kim"]}, headers=H)
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
        r = ada.post("/api/me/oidc/start", json={"password": PW["ada"]}, headers=H)
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

    def test_with_oidc_only_nobody_is_held_at_a_password_change_and_no_password_is_reset(self):
        """A password that signs in nowhere needn't be replaced: an account marked to change its password signs in with
        the provider and isn't held; an admin's Reset password says passwords are off. OIDC only off, the mark holds again."""
        ada = self.set_up()
        self.idp.person = {"sub": "ada-subject-1"}
        r = ada.post("/api/me/oidc/start", json={"password": PW["ada"]}, headers=H)
        back = q(self.idp.authorize(r.json()["url"]))
        self.assertEqual(ada.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H).status_code, 200)
        self.assertEqual(ada.put("/api/settings/install/oidc", json={"only": True}, headers=H).status_code, 200)
        self.link("kim", "kim-subject-1")
        db = dbm.connect()
        db.execute("UPDATE users SET must_change_pw=1 WHERE id=?", (self.uid["kim"],))
        db.close()
        try:
            self.idp.person = {"sub": "kim-subject-1"}
            c, r = self.oidc_sign_in()
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["must_change_pw"])
            self.assertFalse(c.get("/api/me").json()["user"]["must_change_pw"])
            self.assertEqual(c.get("/api/me/oidc").status_code, 200, "not held at a password change")
            self.assertEqual(c.get("/api/lab-names").status_code, 200)
            r = ada.patch(f"/api/users/{self.uid['kim']}", json={"reset_password": True}, headers=H)
            self.assertEqual(r.status_code, 409)
            self.assertIn("Passwords are off while Sign in with Authentik (fictional) only is on", r.json()["detail"])
            with redirect_stdout(io.StringIO()):
                manage.run(["oidc-only", "off"])
            self.assertEqual(c.get("/api/me/oidc").json()["detail"], "password_change_required", "off again: the mark holds")
            self.assertTrue(c.get("/api/me").json()["user"]["must_change_pw"])
        finally:
            db = dbm.connect()
            db.execute("UPDATE users SET must_change_pw=0 WHERE id=?", (self.uid["kim"],))
            db.close()

    # ------------------------------------------------------------ #1109's security review
    def only_on(self, ada):
        """ada's own identity linked (her sign-in there works), then Sign in with OIDC only on."""
        self.idp.person = {"sub": "ada-subject-1"}
        r = ada.post("/api/me/oidc/start", json={"password": PW["ada"]}, headers=H)
        back = q(self.idp.authorize(r.json()["url"]))
        self.assertEqual(ada.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H).status_code, 200)
        self.assertEqual(ada.put("/api/settings/install/oidc", json={"only": True}, headers=H).status_code, 200)

    def test_oidc_only_the_provider_stands_in_for_the_password_and_the_password_confirms_nothing(self):
        """With OIDC only on, two-factor sign-in required and a provider that doesn't say two factors, an account with no
        second factor can still set one up: the provider's fresh check (signed in again now) stands in for the password;
        a sign-in's own freshness doesn't, and the password passes no fresh check."""
        ada = self.set_up()
        self.only_on(ada)
        self.link("kim", "kim-subject-1")
        db = dbm.connect()
        dbm.meta_set(db, "require_2fa", "1")
        db.close()
        self.idp.person = {"sub": "kim-subject-1"}
        c, r = self.oidc_sign_in()
        self.assertTrue(c.get("/api/me").json()["user"]["needs_two_factor"])
        self.assertEqual(c.post("/api/me/2fa/start", json={"password": ""}, headers=H).json()["detail"], "reauth_required",
                         "just signed in isn't a fresh check that stands in for the password")
        self.assertEqual(c.post("/api/me/2fa/start", json={"password": PW["kim"]}, headers=H).json()["detail"], "reauth_required")
        r = ada.post("/api/me/reauth", json={"secret": PW["ada"]}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn("Passwords are off", r.json()["detail"])
        # the provider's fresh check: signed in again there now
        r = c.post("/api/me/reauth/oidc/start", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((q(r.json()["url"])["prompt"], q(r.json()["url"])["max_age"]), ("login", "0"))
        self.idp.token = {"auth_time": int(time.time()) - 3600}   # a provider that didn't ask again: refused
        r = self.come_back(c, r.json()["url"])
        self.assertEqual(r.status_code, 401)
        self.assertIn("didn't ask you to sign in again", r.json()["detail"])
        url = c.post("/api/me/reauth/oidc/start", headers=H).json()["url"]
        self.idp.token = {"auth_time": int(time.time()) - 60}   # (#1109's recheck) a sign-in a minute before the check: refused too
        r = self.come_back(c, url)
        self.assertEqual(r.status_code, 401)
        self.idp.token = {}
        self.idp.person = {"sub": "ada-subject-1"}   # someone else's identity: refused
        r = self.come_back(c, c.post("/api/me/reauth/oidc/start", headers=H).json()["url"])
        self.assertEqual(r.status_code, 403)
        self.idp.person = {"sub": "kim-subject-1"}
        r = self.come_back(c, c.post("/api/me/reauth/oidc/start", headers=H).json()["url"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["reauth"])
        r = c.post("/api/me/2fa/start", json={"password": ""}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("secret", r.json())
        with redirect_stdout(io.StringIO()):
            manage.run(["oidc-only", "off"])
        self.assertEqual(c.post("/api/me/2fa/start", json={"password": ""}, headers=H).status_code, 403, "off: the password again")

    def test_over_https_the_browser_cookie_is_a_host_cookie_a_plain_one_isnt_read(self):
        """(#1109's review) A sanguity_oidc cookie planted for /api/login/oidc (by a sibling name, or plain HTTP) can't bind
        someone's sign-in: over HTTPS the cookie is __Host-sanguity_oidc at /, Secure, and only it is read."""
        from server import routes_oidc
        self.set_up()
        self.link("kim", "kim-subject-1")
        with mock.patch.object(routes_oidc, "_secure", lambda request: True):
            c = client()
            r = c.post("/api/login/oidc/start", headers=H)
            cookie = r.headers["set-cookie"]
            self.assertTrue(cookie.startswith("__Host-sanguity_oidc="), cookie)
            self.assertIn("Path=/;", cookie)
            self.assertIn("Secure", cookie)
            value = cookie.split(";")[0].split("=", 1)[1]
            back = q(self.idp.authorize(r.json()["url"]))
            planted = client()
            planted.cookies.set("sanguity_oidc", value, path="/api/login/oidc")
            r = planted.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H)
            self.assertEqual(r.status_code, 401)
            self.assertIn("another browser", r.json()["detail"])
            c, url = self.start(client())
            value = c.cookies.get("__Host-sanguity_oidc")
            back = q(self.idp.authorize(url))
            ok = client()
            ok.cookies.set("__Host-sanguity_oidc", value)
            self.assertEqual(ok.post("/api/login/oidc/return", json={"code": back["code"], "state": back["state"]}, headers=H).status_code, 200)

    def test_starts_group_by_address_and_only_an_accounts_own_are_throttled(self):
        """(#1109's recheck) A sign-in's start, from anyone, is never refused, from however many at one address (behind
        a reverse proxy, every visitor's); an IPv6 /64 is one address. Links and fresh checks at the provider are an
        account's own, throttled per account: past STARTS_MOST that account waits, not the address."""
        self.assertEqual(oidc.address_group("2001:db8:1:2::1"), oidc.address_group("2001:db8:1:2:ffff::9"))
        self.assertNotEqual(oidc.address_group("2001:db8:1:2::1"), oidc.address_group("2001:db8:1:3::1"))
        self.assertEqual(oidc.address_group("::ffff:203.0.113.5"), "203.0.113.5")
        p = oidc._Pending()
        p.MAX = 31
        e = lambda ip, uid=None: {"expires": time.time() + 60, "ip": ip, "uid": uid}
        p.add("real", e("198.51.100.7"))
        for i in range(oidc.STARTS_MOST + 5):   # anonymous, one /64: none refused
            p.add(f"v6-{i}", e(f"2001:db8:1:2::{i + 1:x}"))
        self.assertIsNotNone(p.take("real"), "the flood dropped only its own")
        self.assertIsNotNone(p.take(f"v6-{oidc.STARTS_MOST + 4}"))
        p.MAX = 1000   # (room: what follows is the account's own limits)
        for i in range(oidc.STARTS_MOST):
            p.add(f"kim-{i}", e("198.51.100.7", uid=2))
        with self.assertRaises(oidc.OidcError) as got:
            p.add("kim-again", e("203.0.113.1", uid=2))   # another address: still kim's
        self.assertEqual(got.exception.status, 429)
        p.add("lee", e("198.51.100.7", uid=3))   # the same address, another account: not held up
        self.assertEqual(sum(1 for v in p._p.values() if v["group"] == "account 2"), p.PER_ACCOUNT)
        # and the routes: anonymous starts never refused; an account's links and checks its own
        self.set_up()
        oidc.pending.clear()
        for _ in range(oidc.STARTS_MOST + 5):
            self.assertEqual(client().post("/api/login/oidc/start", headers=H).status_code, 200)
        self.link("kim", "kim-subject-1")
        kim = self.signed_in("kim")
        for _ in range(oidc.STARTS_MOST):
            self.assertEqual(kim.post("/api/me/reauth/oidc/start", headers=H).status_code, 200)
        self.assertEqual(kim.post("/api/me/reauth/oidc/start", headers=H).status_code, 429)
        self.assertEqual(client().post("/api/login/oidc/start", headers=H).status_code, 200, "the address isn't held up")

    def test_an_address_carrying_an_ipv4_one_is_grouped_by_it(self):   # #1111
        """6to4 (2002::/16) and Teredo (2001::/32) addresses carry their host's IPv4 address: grouped by it, as a mapped
        one is, not by /64 (a host has a /48 of 6to4 /64s)."""
        self.assertEqual(oidc.address_group("2002:cb00:7105::1"), "203.0.113.5")
        self.assertEqual(oidc.address_group("2002:cb00:7105:ffff::9"), "203.0.113.5")
        self.assertEqual(oidc.address_group("2001:0:4136:e378:8000:63bf:3fff:fdd2"), "192.0.2.45")   # RFC 4380's example
        self.assertEqual(oidc.address_group("2001:db8:1:2::1"), "2001:db8:1:2::/64")

    def test_the_accounts_start_times_are_swept_not_rebuilt_on_every_start(self):   # #1111
        """The start times kept per account are swept of old ones at most every SWEEP seconds, and never more than MAX
        accounts' are kept, the longest idle going first."""
        p = oidc._Pending()
        p.MAX = 50
        now = [1_000_000.0]
        e = lambda uid: {"expires": now[0] + 600, "ip": "198.51.100.7", "uid": uid}
        with mock.patch.object(oidc.time, "time", lambda: now[0]):
            for uid in range(1, 41):
                p.add(f"s{uid}", e(uid))
            rebuilt = []
            real = p._sweep
            p._sweep = lambda t: (rebuilt.append(t), real(t))
            now[0] += oidc.STATE_TTL + 1   # every one of those old now
            for uid in range(41, 61):
                p.add(f"t{uid}", e(uid))
            self.assertEqual(len(rebuilt), 20)   # (asked each time; it does the work once)
            self.assertEqual(set(p._starts), {f"account {u}" for u in range(41, 61)}, "the old ones went, at the first sweep")
            for uid in range(61, 101):   # within SWEEP of the last: kept to MAX all the same, the longest idle going
                now[0] += 0.01
                p.add(f"u{uid}", e(uid))
            self.assertLessEqual(len(p._starts), p.MAX + 1)
            self.assertIn("account 100", p._starts)
            self.assertNotIn("account 41", p._starts)

    def test_only_two_factors_count_as_two(self):
        """(#1109's recheck) Methods of two factors (knowledge, possession, inherence) count; two of one factor, or one
        with methods that say nothing of factors (user, pop, rba, geo, mca, wia, unknown), don't."""
        for amr in (["pwd", "rba"], ["pwd", "geo"], ["pwd", "user"], ["swk", "pop"], ["pwd", "pin"], ["otp", "sms"],
                    ["pwd", "mca", "wia"], ["otp"], ["fpt", "face"], ["pwd", "made-up"]):
            self.assertFalse(oidc.multi_factor({"amr": amr}, ""), amr)
        for amr in (["pwd", "otp"], ["pin", "hwk"], ["swk", "fpt"], ["kba", "sc", "rba"], ["mfa"]):
            self.assertTrue(oidc.multi_factor({"amr": amr}, ""), amr)
        self.assertTrue(oidc.multi_factor({"amr": ["pwd"], "acr": "gold"}, "gold"))
        self.assertFalse(oidc.multi_factor({"amr": ["pwd"], "acr": "gold"}, ""))

    def test_an_admins_link_is_told_to_its_person_and_shows_the_providers_name_once_used(self):
        ada = self.set_up()
        with self.assertLogs("sanguity.security", "INFO") as seen:
            self.assertEqual(ada.post(f"/api/users/{self.uid['kim']}/oidc", json={"subject": "kim-subject-1"}, headers=H).status_code, 200)
        self.assertTrue(any('"event":"oidc.link"' in m and '"user":"kim"' in m and '"by":"ada"' in m for m in seen.output), seen.output)
        kim = self.signed_in("kim")
        got = kim.get("/api/me/oidc").json()["identities"][0]
        self.assertEqual((got["linked_by_admin"], got["linked_by"], got["name"], got["last_used"]), (True, "ada", None, None))
        self.idp.person = {"sub": "kim-subject-1", "email": "kim@idp.example (fictional)"}
        c, r = self.oidc_sign_in()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(kim.get("/api/me/oidc").json()["identities"][0]["name"], "kim@idp.example (fictional)")
        listed = {u["username"]: u["oidc"] for u in ada.get("/api/users").json()}
        self.assertEqual(listed["kim"][0]["name"], "kim@idp.example (fictional)", "the admin sees whose it turned out to be")

    def test_the_client_secret_is_resealed_by_a_key_rotation(self):
        from server import config
        self.set_up()
        db = dbm.connect()
        before = dbm.meta_get(db, oidc.SECRET_META)
        crypto.add_key(config.KEY_FILE)   # (kept: everything is sealed with it below, as the app does as it starts)
        crypto.keyring.reset()
        self.assertTrue(crypto.field_needs_resealing(before))
        atrest.seal_everything(db)
        after = dbm.meta_get(db, oidc.SECRET_META)
        self.assertNotEqual(after, before)
        self.assertFalse(crypto.field_needs_resealing(after))
        self.assertEqual(oidc.client_secret(db), SECRET)
        db.close()

    def test_restoring_keeps_todays_provider_and_identities(self):   # (the access tables, test_backups has the restore)
        from server import backup
        self.assertIn("oidc_identities", backup.ACCESS_TABLES)
        self.assertTrue({"oidc", "oidc.secret"} <= set(backup.ACCESS_META))


if __name__ == "__main__":
    unittest.main(verbosity=2)
