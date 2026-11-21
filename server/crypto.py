"""Encryption at rest: AES-256-GCM with keys from a key file kept apart from the data.

What it covers: every file Sanguity writes under DATA_DIR (imported documents, uploaded files, the setup build),
the database fields that hold health details or secrets (the records themselves, settings that name labs, document file names,
activity details, two-factor secrets, the pepper for password hashes), and everything the watch receiver writes under WATCH_DIR (its archive of uploads,
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

Formats (all carry the id of the key that wrote them, so the right line is used):
  file   b"SGE1" + key id (4) + nonce (12) + ciphertext + tag (16)
  field  "enc:1:" + base64 of the same, after the magic
  stream b"SGS1" + key id (4) + nonce prefix (7) + chunk size (4, big-endian), then chunks of that much plaintext
         (the last one shorter, possibly empty), each ciphertext + tag (16). A chunk's nonce is the prefix, its
         number (4, big-endian) and 1 for the last chunk or 0, and the header is its associated data, so chunks
         can't be reordered, dropped, cut short or moved to another file. For large files (uploads), written and
         read a chunk at a time (StreamWriter, iter_file) instead of whole.
Anything without the marker is plaintext from before encryption and is read as such, then encrypted by the
start-up migration.
"""
import base64, contextlib, hashlib, os, secrets, threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config

MAGIC, FIELD, STREAM = b"SGE1", "enc:1:", b"SGS1"
CHUNK = 1 << 20   # plaintext bytes per chunk of a stream


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
            _ciphers.clear()


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


_ciphers = {}   # key -> AESGCM: made once per key, as a database row is encrypted or decrypted one field at a time


def _cipher(key):
    c = _ciphers.get(key)
    if c is None:
        c = _ciphers[key] = AESGCM(key)
    return c


def _seal(data):
    key, _ = keyring.load()
    nonce = secrets.token_bytes(12)
    return _key_id(key) + nonce + _cipher(key).encrypt(nonce, data, None)   # ciphertext, then the 16-byte tag


def _open(blob):
    _, keys = keyring.load()
    key = keys.get(blob[:4])
    if key is None:
        raise KeyError_("This was encrypted with a key that isn't in the key file.")
    try:
        return _cipher(key).decrypt(blob[4:16], blob[16:], None)
    except (InvalidTag, ValueError):
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


def keyed_name(text):
    """A file name for text that doesn't reveal it (HMAC-SHA256 with a key derived from the current data key)."""
    import hmac
    sub = hashlib.sha256(b"sanguity file names\x00" + keyring.load()[0]).digest()
    return hmac.new(sub, text.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def field_needs_resealing(text):
    if not isinstance(text, str):
        return False
    return not text.startswith(FIELD) or base64.b64decode(text[len(FIELD):])[:4] != current_key_id()


# ---------------------------------------------------------------- files

def read_file(path):
    with open(path, "rb") as f:
        if f.read(4) == STREAM:
            f.seek(0)
            return b"".join(_stream_chunks(f))
        f.seek(0)
        return unseal(f.read())


def write_file(path, data):
    """Write sealed bytes atomically (mode 600), through a temporary name of its own beside path (still ending in
    ".tmp", which the at-rest walk and backups leave out), so two writers of one file can't take each other's (#521)."""
    tmp = f"{path}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(seal(data))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise


def file_is_encrypted(path):
    """Sealed whole (SGE1) or as a stream (SGS1)."""
    try:
        with open(path, "rb") as f:
            return f.read(4) in (MAGIC, STREAM)
    except OSError:
        return False


def file_is_current(head):
    """The first 8 bytes of a file say it's sealed, either way, with the current key."""
    return head[:4] in (MAGIC, STREAM) and head[4:8] == current_key_id()


# ---------------------------------------------------------------- streams (large files, a chunk at a time)

def _nonce(prefix, n, last):
    return prefix + n.to_bytes(4, "big") + (b"\x01" if last else b"\x00")


class StreamWriter:
    """Write a file sealed as a stream, atomically (mode 600): to path + ".tmp", which replaces path on close().
    As a context manager, an exception leaves nothing behind. size: plaintext bytes written so far."""

    def __init__(self, path, chunk=CHUNK):
        key, _ = keyring.load()
        self.path, self.chunk, self.size, self._n, self._buf = path, chunk, 0, 0, bytearray()
        self._cipher, self._prefix = _cipher(key), secrets.token_bytes(7)
        self._head = STREAM + _key_id(key) + self._prefix + chunk.to_bytes(4, "big")
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self._f = os.fdopen(fd, "wb")
        self._f.write(self._head)

    def _emit(self, data, last):
        self._f.write(self._cipher.encrypt(_nonce(self._prefix, self._n, last), bytes(data), self._head))
        self._n += 1

    def write(self, data):
        self._buf += data
        self.size += len(data)
        while len(self._buf) > self.chunk:   # keep the remainder: the last chunk is only known at close()
            self._emit(self._buf[:self.chunk], False)
            del self._buf[:self.chunk]

    def close(self):
        self._emit(self._buf, True)
        self._buf.clear()
        self._f.close()
        os.replace(self.path + ".tmp", self.path)

    def abort(self):
        self._f.close()
        try:
            os.remove(self.path + ".tmp")
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, kind, *exc):
        self.abort() if kind else self.close()


def _stream_chunks(f):
    head = f.read(19)
    if len(head) < 19 or head[:4] != STREAM:
        raise KeyError_("Not an encrypted stream.")
    _, keys = keyring.load()
    key = keys.get(head[4:8])
    if key is None:
        raise KeyError_("This was encrypted with a key that isn't in the key file.")
    cipher, prefix, size = _cipher(key), head[8:15], int.from_bytes(head[15:19], "big") + 16
    n, block = 0, f.read(size)
    while True:
        after = f.read(size) if len(block) == size else b""
        last = not after
        try:
            yield cipher.decrypt(_nonce(prefix, n, last), block, head)
        except (InvalidTag, ValueError):
            raise KeyError_("Encrypted data failed its integrity check (damaged, cut short, or not made with this "
                            "key).") from None
        if last:
            return
        block, n = after, n + 1


def iter_file(path, size=CHUNK):
    """A file's plaintext, a piece at a time: a stream chunk by chunk, a whole-sealed file in one piece, and
    plaintext (from before encryption) as it is."""
    with open(path, "rb") as f:
        magic = f.read(4)
        f.seek(0)
        if magic == STREAM:
            yield from _stream_chunks(f)
        elif magic == MAGIC:
            yield unseal(f.read())
        else:
            while piece := f.read(size):
                yield piece


def reseal_stream(path):
    """Re-encrypt a file as a stream with the current key (after a key rotation, or from another format)."""
    with StreamWriter(path) as w:
        for piece in iter_file(path):
            w.write(piece)


def decrypt_to(path, dest):
    """Write path's plaintext to dest (mode 600), a piece at a time."""
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        for piece in iter_file(path):
            out.write(piece)
