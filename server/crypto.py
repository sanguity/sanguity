"""Encryption at rest: AES-256-GCM with keys from a key file kept apart from the data.

What it covers: every file Sanguity writes under DATA_DIR (each profile's records and settings, imported documents,
the setup build), the database fields that hold health details or secrets (document file names, activity
details, two-factor secrets, the pepper for password hashes), and everything the watch receiver writes under WATCH_DIR (its archive of uploads,
its database, kept in memory and saved as a sealed snapshot, and the daily.json the app reads; see
watch/receiver.py). So a copied data folder or a backup is unreadable without the key. Not covered: usernames and
profile names (identity, needed for sign-in and sorting), the original exports in RECORDS_DIR (your own files,
only read), and downloads from "Export everything" (portable on purpose). For a stolen disk, also use the
platform's disk encryption (ZFS, LUKS, FileVault, BitLocker).

The key file (SANGUITY_KEY_FILE, default auth/data_key, made on first start) holds one base64 key per line. The
first line encrypts; any line decrypts what was written with it, so a key can be rotated: add a new first line
(setup new-key), restart the app and the receiver (each re-encrypts its files with it as it starts), then drop
the old line.
server.manage reencrypt does the same with the app stopped.

Formats (both carry the id of the key that wrote them, so the right line is used):
  file   b"SGE1" + key id (4) + nonce (12) + ciphertext + tag (16)
  field  "enc:1:" + base64 of the same, after the magic
Anything without the marker is plaintext from before encryption and is read as such, then encrypted by the
start-up migration.
"""
import base64, hashlib, os, secrets, threading

from Crypto.Cipher import AES

from . import config

MAGIC, FIELD = b"SGE1", "enc:1:"


class KeyError_(Exception):
    """The key file is missing, unreadable, or lacks the key some data was written with."""


def _key_id(key):
    return hashlib.sha256(key).digest()[:4]


class Keyring:
    def __init__(self, path):
        self.path, self._keys, self._lock = path, None, threading.Lock()

    def load(self):
        with self._lock:
            if self._keys is None:
                try:
                    with open(self.path, encoding="ascii") as f:
                        keys = [base64.b64decode(l.strip()) for l in f if l.strip() and not l.startswith("#")]
                except FileNotFoundError:
                    raise KeyError_(f"No encryption key at {self.path}.") from None
                except (OSError, ValueError) as e:
                    raise KeyError_(f"Can't read the encryption key at {self.path}: {e}") from None
                if not keys or any(len(k) != 32 for k in keys):
                    raise KeyError_(f"{self.path} must hold one or more 256-bit keys, base64, one per line.")
                self._keys = {_key_id(k): k for k in keys}
                self._current = keys[0]
            return self._current, self._keys

    def reset(self):
        with self._lock:
            self._keys = None


keyring = Keyring(config.KEY_FILE)


def make_key_file(path):
    """Create a key file with one new key (mode 600). Returns True if it made one."""
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write("# Sanguity's encryption key. Back this file up somewhere other than the data: without it, the\n"
                "# records, documents and backups can't be read. One key per line; the first one encrypts.\n")
        f.write(base64.b64encode(secrets.token_bytes(32)).decode() + "\n")
    return True


def add_key(path):
    """Put a new key first in the key file (rotation); older keys stay for reading until re-encrypted."""
    with open(path, encoding="ascii") as f:
        lines = f.read().splitlines()
    comments = [l for l in lines if l.startswith("#")]
    keys = [l for l in lines if l.strip() and not l.startswith("#")]
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write("\n".join(comments + [base64.b64encode(secrets.token_bytes(32)).decode()] + keys) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- bytes and fields

def encrypted(data):
    return data[:4] == MAGIC


def _seal(data):
    key, _ = keyring.load()
    nonce = secrets.token_bytes(12)
    ct, tag = AES.new(key, AES.MODE_GCM, nonce=nonce).encrypt_and_digest(data)
    return _key_id(key) + nonce + ct + tag


def _open(blob):
    _, keys = keyring.load()
    key = keys.get(blob[:4])
    if key is None:
        raise KeyError_("This was encrypted with a key that isn't in the key file.")
    try:
        return AES.new(key, AES.MODE_GCM, nonce=blob[4:16]).decrypt_and_verify(blob[16:-16], blob[-16:])
    except ValueError:
        raise KeyError_("Encrypted data failed its integrity check (damaged, or not made with this key).") from None


def seal(data):
    return MAGIC + _seal(data)


def unseal(data):
    """Plaintext of a sealed blob; anything without the marker is returned as it is."""
    return _open(data[4:]) if encrypted(data) else data


def current_key_id():
    return _key_id(keyring.load()[0])


def needs_resealing(data):
    """Plaintext, or sealed with an older key."""
    return not encrypted(data) or data[4:8] != current_key_id()


def enc(text):
    """A text field, encrypted (None stays None)."""
    if text is None:
        return None
    return FIELD + base64.b64encode(_seal(text.encode("utf-8"))).decode()


def dec(text):
    """A text field's plaintext; values from before encryption pass through."""
    if not isinstance(text, str) or not text.startswith(FIELD):
        return text
    return _open(base64.b64decode(text[len(FIELD):])).decode("utf-8")


def field_needs_resealing(text):
    if not isinstance(text, str):
        return False
    return not text.startswith(FIELD) or base64.b64decode(text[len(FIELD):])[:4] != current_key_id()


# ---------------------------------------------------------------- files

def read_file(path):
    with open(path, "rb") as f:
        return unseal(f.read())


def write_file(path, data):
    """Write sealed bytes atomically (mode 600)."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(seal(data))
    os.replace(tmp, path)


def file_is_encrypted(path):
    try:
        with open(path, "rb") as f:
            return encrypted(f.read(4))
    except OSError:
        return False
