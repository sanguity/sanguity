"""Keeping everything at rest encrypted with the current key: the start-up check and migration, and
server.manage reencrypt after a key rotation (see crypto.py)."""
import glob, os

from . import config, crypto


def _files():
    """Every file Sanguity wrote that holds records: each profile's folder and the setup build."""
    for top in (os.path.join(config.DATA_DIR, "profiles"), config.BUILD_DIR):
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not d.startswith(".deleting-")]
            for n in files:
                if not n.endswith(".tmp"):
                    yield os.path.join(root, n)


FIELDS = (("documents", "id", "path"), ("activity", "id", "detail"), ("users", "id", "totp_secret"), ("users", "id", "totp_pending"))


def anything_encrypted(db):
    """Whether this install already holds encrypted data (so a missing key must not be replaced by a new one)."""
    for table, _, col in FIELDS:
        if db.execute(f"SELECT 1 FROM {table} WHERE {col} LIKE ? LIMIT 1", (crypto.FIELD + "%",)).fetchone():
            return True
    return any(crypto.file_is_encrypted(p) for p in glob.glob(os.path.join(config.DATA_DIR, "profiles", "*", "data.json")))


def check_key(db):
    """Load the key, making one on a first start where that's possible. Raises SystemExit with what to do when the
    key is missing or doesn't open what's there."""
    try:
        crypto.keyring.load()
    except crypto.KeyError_ as e:
        if anything_encrypted(db):
            raise SystemExit(f"{e} This install's records are encrypted, and can't be read without the key they were "
                             f"written with. Put that key file back at {config.KEY_FILE} (or set SANGUITY_KEY_FILE).")
        try:
            crypto.make_key_file(config.KEY_FILE)
        except OSError:
            raise SystemExit(f"{e} In Docker the setup service makes it (docker compose up); otherwise set "
                             "SANGUITY_KEY_FILE to a file outside the data folder and start again.") from None
        crypto.keyring.reset()
        crypto.keyring.load()
        print(f"Made a new encryption key at {config.KEY_FILE}. Back it up somewhere other than the data: "
              "without it, the records and backups can't be read.", flush=True)
    sample = db.execute("SELECT path FROM documents WHERE path LIKE ? LIMIT 1", (crypto.FIELD + "%",)).fetchone()
    try:
        if sample:
            crypto.dec(sample["path"])
    except crypto.KeyError_ as e:
        raise SystemExit(f"{e} The key file at {config.KEY_FILE} isn't the one this install's records were encrypted with.")


def seal_everything(db):
    """Encrypt whatever is still plaintext, or encrypted with an older key, with the current key. Safe to repeat.
    Returns {"files": n, "fields": n} changed."""
    files = 0
    for path in _files():
        with open(path, "rb") as f:
            head = f.read(8)
        if crypto.encrypted(head[:4]) and head[4:8] == crypto.current_key_id():
            continue
        crypto.write_file(path, crypto.read_file(path))
        files += 1
    fields = 0
    for table, key, col in FIELDS:
        for r in db.execute(f"SELECT {key}, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall():
            if crypto.field_needs_resealing(r[col]):
                db.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?", (crypto.enc(crypto.dec(r[col])), r[key]))
                fields += 1
    return {"files": files, "fields": fields}
