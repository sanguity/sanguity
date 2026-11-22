"""Passkeys (WebAuthn): Face ID, Windows Hello, a phone or a security key, checked with py_webauthn.

A passkey is bound to the site's name (the relying-party ID, the host the browser shows), so:
  - it needs HTTPS, or http://localhost; browsers offer no passkeys to a plain-HTTP address
  - it can't be an IP address; open Sanguity by a name (a reverse proxy's, Tailscale's, ...)
  - one made at one name only works there, so each is stored with its name and only offered there.
The host comes from the request's Origin, which the CSRF guard has already matched to this site (see
main._same_site and PUBLIC_ORIGINS).

Each passkey checks the person too (fingerprint, face, PIN: "user verification", required when one is made), so
it signs in on its own, or as the step after the password. Only the public key is stored. No attestation is
asked for: any authenticator is accepted, and none is told apart.
"""
import ipaddress, json, secrets, threading, urllib.parse

import webauthn
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, AuthenticatorTransport, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from . import db as dbm, security as sec

RP_NAME = "Sanguity"
NAME_MAX = 60
PER_ACCOUNT = 20
TRANSPORTS = {t.value for t in AuthenticatorTransport}


# a passkey the device still has but Sanguity doesn't (removed under Account, or from another install)
STALE = ("That passkey isn't registered here: it was probably removed under Account. Delete it from this device's "
         "passkeys (or password manager), then try again; you can pick a passkey on another device, such as your phone.")


class PasskeyError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _own(host):
    """A page at this computer's own address: localhost (or a name under it), 127.x or ::1."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost" or host.endswith(".localhost")


def where(rp_id, page):
    """The address to open for passkeys made at rp_id, said to a page at page (urlsplit): another name at https:// (its
    port isn't kept); this computer's own name with the page's scheme and port from a page on this computer, else in
    words (#549: from another device, localhost is that device, and a proxy's scheme and port aren't the server's)."""
    if rp_id == "localhost" or rp_id.endswith(".localhost"):
        if not _own((page.hostname or "").lower()):
            return f"{rp_id} (on the computer it runs on)"
        return f"{page.scheme}://{rp_id}{f':{page.port}' if page.port else ''}"
    return f"https://{rp_id}"


def site(origin, made=()):
    """(rp_id, origin) for a browser Origin, or PasskeyError when passkeys can't work at that address. made: the names
    the account's passkeys were made at, which the refusal names (#535; #549: at any IP address, and on plain HTTP)."""
    parts = urllib.parse.urlsplit((origin or "").strip())
    host = (parts.hostname or "").lower()
    if not host:
        raise PasskeyError("Passkeys need the browser to say which site this is; reload the page and try again.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    at = lambda: " or ".join(where(h, parts) for h in made or ["localhost"])   # noqa: E731
    if ip:   # this computer's own address (127.x, ::1): its name, localhost, works without HTTPS (#531)
        raise PasskeyError("Passkeys don't work at an IP address. Open Sanguity " + (
            f"at {at()}" if ip.is_loopback or made else "by its name (with HTTPS)") + " to use one.")
    if parts.scheme != "https" and not _own(host):
        raise PasskeyError(f"Passkeys need HTTPS. Open Sanguity at {at() if made else 'its https:// address'} to use one.")
    return host, f"{parts.scheme}://{parts.netloc}".lower()


class _Challenges:
    """Each ceremony's challenge, server-side and single-use, under a random state token the page sends back."""
    TTL, MAX = 300, 10000

    def __init__(self):
        self._c, self._lock = {}, threading.Lock()

    def issue(self, **kw):
        token, now = sec.new_token(), dbm.now()
        with self._lock:
            if len(self._c) >= self.MAX:
                self._c = {k: v for k, v in self._c.items() if v["expires"] > now}
                if len(self._c) >= self.MAX:   # still full: drop the oldest
                    self._c.pop(next(iter(self._c)))
            self._c[sec.token_hash(token)] = {**kw, "expires": now + self.TTL}
        return token

    def take(self, token):
        with self._lock:
            c = self._c.pop(sec.token_hash(token or ""), None)
        if not c or c["expires"] <= dbm.now():
            raise PasskeyError("That took too long. Try again.", 401)
        return c


challenges = _Challenges()


def _handle(db, uid):
    """The account's WebAuthn user handle: random, so it says nothing about the account."""
    h = db.execute("SELECT webauthn_handle FROM users WHERE id=?", (uid,)).fetchone()[0]
    if not h:
        h = bytes_to_base64url(secrets.token_bytes(32))
        db.execute("UPDATE users SET webauthn_handle=? WHERE id=? AND webauthn_handle IS NULL", (h, uid))
        h = db.execute("SELECT webauthn_handle FROM users WHERE id=?", (uid,)).fetchone()[0]
    return h


def _descriptors(db, uid, rp_id):
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["credential_id"]),
                                          transports=[AuthenticatorTransport(t) for t in json.loads(r["transports"] or "[]") if t in TRANSPORTS] or None)
            for r in db.execute("SELECT credential_id, transports FROM passkeys WHERE user_id=? AND rp_id=?", (uid, rp_id))]


def count(db, uid):
    return db.execute("SELECT COUNT(*) FROM passkeys WHERE user_id=?", (uid,)).fetchone()[0]


def sites(db, uid):
    """The names the account's passkeys were made at, the first made first."""
    return list(dict.fromkeys(r[0] for r in db.execute("SELECT rp_id FROM passkeys WHERE user_id=? ORDER BY created", (uid,))))


def listing(db, uid, rp_id=None):
    return [{**dict(r), "here": r["rp_id"] == rp_id} for r in db.execute(
        "SELECT id, name, rp_id, credential_id, created, last_used FROM passkeys WHERE user_id=? ORDER BY created", (uid,))]


def user_handle(db, uid):
    """The handle, if the account has ever made a passkey (for telling the device which ones still count)."""
    return db.execute("SELECT webauthn_handle FROM users WHERE id=?", (uid,)).fetchone()[0]


# ---------------------------------------------------------------- adding one

def registration_options(db, u, origin):
    """Options for navigator.credentials.create, and the state token to send back with its result."""
    rp_id, origin = site(origin)
    if count(db, u["id"]) >= PER_ACCOUNT:
        raise PasskeyError(f"An account can have at most {PER_ACCOUNT} passkeys. Remove one first.", 409)
    opts = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_name=u["username"], user_display_name=u["username"],
        user_id=base64url_to_bytes(_handle(db, u["id"])),
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=_descriptors(db, u["id"], rp_id))   # the authenticator says so if it already has one
    state = challenges.issue(kind="register", uid=u["id"], challenge=opts.challenge, rp_id=rp_id, origin=origin)
    return state, json.loads(webauthn.options_to_json(opts))


def register(db, u, state, credential, name):
    """Check the authenticator's answer and store the passkey. Returns its row id."""
    c = challenges.take(state)
    if c["kind"] != "register" or c["uid"] != u["id"]:
        raise PasskeyError("That took too long. Try again.", 401)
    try:
        v = webauthn.verify_registration_response(credential=credential, expected_challenge=c["challenge"],
                                                  expected_rp_id=c["rp_id"], expected_origin=c["origin"],
                                                  require_user_verification=True)
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        raise PasskeyError(f"The passkey couldn't be checked ({e}).") from None
    transports = [t for t in ((credential.get("response") or {}).get("transports") or []) if t in TRANSPORTS]
    cid = bytes_to_base64url(v.credential_id)
    if db.execute("SELECT 1 FROM passkeys WHERE credential_id=?", (cid,)).fetchone():
        raise PasskeyError("That passkey is already registered.", 409)
    name = " ".join((name or "").split())[:NAME_MAX] or "Passkey"
    return db.execute("""INSERT INTO passkeys(user_id, credential_id, public_key, sign_count, rp_id, name, transports, created)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (u["id"], cid, v.credential_public_key, v.sign_count, c["rp_id"], name, json.dumps(transports), dbm.now())).lastrowid


# ---------------------------------------------------------------- signing in with one

def authentication_options(db, origin, uid=None):
    """Options for navigator.credentials.get. With uid (after the password): that account's passkeys at this
    address. Without: any passkey the browser holds for this site, which must verify the person."""
    rp_id, origin = site(origin, sites(db, uid) if uid is not None else ())
    allow = None
    if uid is not None:
        allow = _descriptors(db, uid, rp_id)
        if not allow:   # where they were made, and a code only to an account with one (#540)
            at = " or ".join(where(h, urllib.parse.urlsplit(origin)) for h in sites(db, uid))
            code = db.execute("SELECT totp_secret FROM users WHERE id=?", (uid,)).fetchone()[0]
            raise PasskeyError("None of your passkeys was made at this address." + (f" Open Sanguity at {at} to use one" if at else "")
                               + (", or use your authenticator code." if at and code else " Use your authenticator code." if code
                                  else "." if at else ""), 409)
    opts = webauthn.generate_authentication_options(
        rp_id=rp_id, allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED if uid is not None else UserVerificationRequirement.REQUIRED)
    state = challenges.issue(kind="sign-in", uid=uid, challenge=opts.challenge, rp_id=rp_id, origin=origin)
    return state, json.loads(webauthn.options_to_json(opts))


def authenticate(db, state, credential):
    """Check a sign-in; returns (user id, passkey name). With a state issued for an account, only its passkeys do."""
    c = challenges.take(state)
    if c["kind"] != "sign-in":
        raise PasskeyError("That took too long. Try again.", 401)
    cid = credential.get("id") if isinstance(credential, dict) else None
    row = db.execute("SELECT * FROM passkeys WHERE credential_id=? AND rp_id=?", (str(cid)[:1400], c["rp_id"])).fetchone() if cid else None
    if not row or (c["uid"] is not None and row["user_id"] != c["uid"]):
        raise PasskeyError(STALE, 401)
    handle = (credential.get("response") or {}).get("userHandle")
    if handle and handle != db.execute("SELECT webauthn_handle FROM users WHERE id=?", (row["user_id"],)).fetchone()[0]:
        raise PasskeyError(STALE, 401)
    try:
        v = webauthn.verify_authentication_response(credential=credential, expected_challenge=c["challenge"],
                                                    expected_rp_id=c["rp_id"], expected_origin=c["origin"],
                                                    credential_public_key=row["public_key"],
                                                    credential_current_sign_count=row["sign_count"],
                                                    require_user_verification=c["uid"] is None)
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        raise PasskeyError(f"The passkey didn't check out ({e}).", 401) from None
    db.execute("UPDATE passkeys SET sign_count=?, last_used=? WHERE id=?", (v.new_sign_count, dbm.now(), row["id"]))
    return row["user_id"], row["name"]
