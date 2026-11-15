"""Two-factor sign-in: time-based one-time codes (TOTP, RFC 6238, as authenticator apps make them) and one-time
recovery codes. Standard library only, apart from drawing the QR code (segno), which happens on the server so the
browser fetches nothing from elsewhere.

TOTP here: SHA-1, 6 digits, 30-second steps, accepting the step before and after for clock drift, and never the
same step twice for an account (so a code seen over someone's shoulder can't be replayed).
Recovery codes: 10 per account, 8 characters each (about 40 bits), each usable once. Each is stored as
"h1:" + a random 128-bit salt + HMAC-SHA256(pepper, salt + code) (ASVS 2.6.2: a unique salt per code when it has
under 112 bits; the pepper, see security.py, means a copied database alone can't be searched for them). Codes made
before that are plain SHA-256 and keep working until the account's codes are replaced.
"""
import base64, hashlib, hmac, io, re, secrets, struct, time, urllib.parse

import segno

from . import security as sec

ISSUER, DIGITS, PERIOD, DRIFT = "Sanguity", 6, 30, 1
RECOVERY_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/i/l to misread


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def looks_like_code(text):
    return re.fullmatch(r"\d{%d}" % DIGITS, re.sub(r"\s", "", text or "")) is not None


def verify(secret, code, last_step=0, now=None):
    """The time step the code belongs to, or None. Steps at or before last_step are refused (already used)."""
    code = re.sub(r"\s", "", code or "")
    if not secret or not looks_like_code(code):
        return None
    current = int((now or time.time()) // PERIOD)
    for step in range(current - DRIFT, current + DRIFT + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(secret, username):
    label = urllib.parse.quote(f"{ISSUER}:{username}", safe=":")
    return f"otpauth://totp/{label}?" + urllib.parse.urlencode(
        {"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": DIGITS, "period": PERIOD})


def qr_data_uri(text):
    """The QR code as an SVG data: URI, which the page's CSP allows for images."""
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=5, border=2, dark="#000", light="#fff", xmldecl=False)
    return "data:image/svg+xml;base64," + base64.b64encode(out.getvalue()).decode()


def new_recovery_codes():
    """Plain codes to show once, as xxxx-xxxx."""
    codes = ["".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8)) for _ in range(RECOVERY_COUNT)]
    return [f"{c[:4]}-{c[4:]}" for c in codes]


def _plain(code):
    return re.sub(r"[\s-]", "", (code or "").lower()).encode()


def recovery_hash(code):
    """How a new recovery code is stored: salted and peppered."""
    salt = secrets.token_bytes(16)
    return "h1:" + salt.hex() + ":" + sec.pepper_mac(salt + _plain(code)).hex()


def recovery_matches(stored, code):
    """Whether code is the one stored (as recovery_hash makes it, or plain SHA-256 from before the pepper)."""
    if stored.startswith("h1:"):
        _, salt, mac = stored.split(":", 2)
        expected = sec.pepper_mac(bytes.fromhex(salt) + _plain(code)).hex()
    else:
        mac, expected = stored, hashlib.sha256(_plain(code)).hexdigest()
    return hmac.compare_digest(mac, expected)
