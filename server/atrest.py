"""Keeping everything at rest encrypted with the current key: the start-up check and migration, and
server.manage reencrypt after a key rotation (see crypto.py). Also the pepper for password and recovery-code
hashes (load_pepper)."""
import base64, glob, os, secrets

from . import config, crypto, db as dbm, security as sec


def _files():
    """Every file Sanguity wrote that holds records: each profile's folder (with its uploads) and the setup build."""
    for top in (os.path.join(config.DATA_DIR, "profiles"), config.BUILD_DIR):
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not d.startswith(".deleting-")]
            for n in files:
                if not n.endswith(".tmp"):
                    yield os.path.join(root, n)


FIELDS = (("documents", "id", "path"), ("activity", "id", "detail"), ("users", "id", "totp_secret"), ("users", "id", "totp_pending"),
          ("imports", "id", "meta"), ("events", "id", "body"), ("events", "id", "detail"), ("events", "id", "cost"),
          ("series", "id", "body"), ("results", "id", "body"), ("uploads", "id", "manifest"), ("upload_files", "rowid", "path"),
          ("jobs", "id", "message"), ("jobs", "id", "warnings"), ("jobs", "id", "summary"),
          ("import_undos", "id", "entry"),
          ("edits", "id", "target_key"), ("edits", "id", "other_key"), ("edits", "id", "value"),
          ("labmaps", "id", "source"), ("labmaps", "id", "target"), ("jobs", "id", "labs"))


def anything_encrypted(db):
    """Whether this install already holds encrypted data (so a missing key must not be replaced by a new one)."""
    for table, _, col in FIELDS:
        if db.execute(f"SELECT 1 FROM {table} WHERE {col} LIKE ? LIMIT 1", (crypto.FIELD + "%",)).fetchone():
            return True
    if (dbm.meta_get(db, "pepper") or "").startswith(crypto.FIELD):
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
    sample = sample["path"] if sample else dbm.meta_get(db, "pepper")
    try:
        if sample:
            crypto.dec(sample)
    except crypto.KeyError_ as e:
        raise SystemExit(f"{e} The key file at {config.KEY_FILE} isn't the one this install's records were encrypted with.")


def load_pepper(db):
    """Load the pepper (security.py), making it on the first start: 32 random bytes kept in the meta table,
    encrypted with the data key like any secret field. It never changes, so password hashes don't either; a key
    rotation only re-encrypts it (seal_everything). Call after check_key."""
    stored = dbm.meta_get(db, "pepper")
    if stored is None:
        peppered = db.execute("""SELECT 1 FROM users WHERE pw_hash LIKE ? UNION ALL
                                 SELECT 1 FROM recovery_codes WHERE code_hash LIKE 'h1:%' LIMIT 1""", (sec.PEPPERED + "%",)).fetchone()
        if peppered:   # a new pepper would quietly lock everyone out
            raise SystemExit("The database's pepper (meta table, key 'pepper') is missing, but passwords were hashed with "
                             "it. Restore the database from a backup.")
        db.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('pepper', ?)",   # another process may have just made one
                   (crypto.enc(base64.b64encode(secrets.token_bytes(32)).decode()),))
        stored = dbm.meta_get(db, "pepper")
    try:
        pepper = base64.b64decode(crypto.dec(stored))
    except crypto.KeyError_ as e:
        raise SystemExit(f"{e} The password pepper can't be opened: the key file at {config.KEY_FILE} isn't the one "
                         "this database was written with.") from None
    sec.set_pepper(pepper)
    return pepper


def seal_everything(db):
    """Encrypt whatever is still plaintext, or encrypted with an older key, with the current key. Safe to repeat.
    Returns {"files": n, "fields": n} changed."""
    files = 0
    for path in _files():
        with open(path, "rb") as f:
            head = f.read(8)
        if crypto.file_is_current(head):
            continue
        if head[:4] == crypto.STREAM:   # an upload's file, possibly large: a chunk at a time
            crypto.reseal_stream(path)
        else:
            crypto.write_file(path, crypto.read_file(path))
        files += 1
    fields = 0
    for table, key, col in FIELDS:
        for r in db.execute(f"SELECT {key}, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall():
            if crypto.field_needs_resealing(r[col]):
                db.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?", (crypto.enc(crypto.dec(r[col])), r[key]))
                fields += 1
    pepper = dbm.meta_get(db, "pepper")
    if pepper is not None and crypto.field_needs_resealing(pepper):
        dbm.meta_set(db, "pepper", crypto.enc(crypto.dec(pepper)))
        fields += 1
    return {"files": files, "fields": fields}
