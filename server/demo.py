"""The fictional demo as a profile of its own, added on request (#1101).

A fresh install starts empty: the first account's own profile has no records, so the getting-started guide opens.
To look around first, setup (its "Add a demo profile" box) or Settings → Install → General → Demo profile adds one:
a separate profile named "Demo (fictional)", owned by whoever asked, its records the demo generator's
(demo/generate.py: a made-up person, providers, values and documents) loaded as one import, with the demo's watch
and scale summary kept as that import's own (watch_store.save_import). Everything it writes is in the profile's own
folder, sealed like the rest; the meta row "demo_profile" names it. Remove demo deletes that profile, and only it,
as Delete profile does (profiles.delete_profile): its records, documents, settings and folder. There is one at a time.

Before #1101 the Docker setup loaded the demo as the setup build when there was no sources.json, so the first
account's profile was the demo; an install whose build is still the demo keeps it (docker/setup.py).
"""
import os, secrets, shutil, subprocess, sys, threading

from . import crypto, db as dbm, profiles as prof, records, watch_store

NAME = "Demo (fictional)"
META = "demo_profile"
GENERATOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo", "generate.py")
_lock = threading.Lock()   # one Add at a time, so two clicks make one demo


class DemoError(Exception):
    pass


def available():
    """Whether this install has the demo generator (every image and checkout does)."""
    return os.path.isfile(GENERATOR)


def profile_id(db):
    """The demo profile's id, or None (none added, or deleted since as any profile can be)."""
    v = dbm.meta_get(db, META)
    if v and v.isdigit() and db.execute("SELECT 1 FROM profiles WHERE id=?", (int(v),)).fetchone():
        return int(v)
    return None


def _generate(out):
    """The demo, written by its generator into out (data.json, settings.json, files/), its watch summary into
    out/watch/daily.json; each file sealed with the app's key, as the profile's folder keeps them."""
    r = subprocess.run([sys.executable, "-I", GENERATOR, out, "--watch", os.path.join(out, "watch")],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise DemoError("The demo couldn't be made: " + (r.stderr.strip().splitlines() or ["its generator failed"])[-1])
    for root, dirs, files in os.walk(out):
        for n in files:
            p = os.path.join(root, n)
            with open(p, "rb") as f:
                body = f.read()
            if crypto.needs_resealing(body[:8]):
                crypto.write_file(p, body)


def add(db, owner_id):
    """Add the demo profile, owned by owner_id; returns its id (the one there already, if there is one)."""
    if not available():
        raise DemoError("This install has no demo generator (demo/generate.py).")
    with _lock:
        if (pid := profile_id(db)) is not None:
            return pid
        pid = prof.create_profile(db, NAME, owner_id=owner_id)
        base = prof.imported_dir(pid)
        os.makedirs(base, mode=0o700, exist_ok=True)
        new = os.path.join(base, prof.UNPACKING + secrets.token_hex(4))
        try:
            _generate(new)
            watch_file = os.path.join(new, "watch", "daily.json")
            summary = prof.read_json(watch_file) if os.path.isfile(watch_file) else None
            shutil.rmtree(os.path.join(new, "watch"), ignore_errors=True)

            def then(db, data, folder):   # the watch summary, as the import's own: undoing the import takes it too
                if summary:
                    watch_store.save_import(db, pid, summary, import_id=records.imports(db, pid)[-1])
            prof.import_dataset(db, pid, new, then=then, unpacked=True)
            dbm.meta_set(db, META, str(pid))
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            prof.delete_profile(db, pid)   # nothing half-made is left: it's this new profile's, and only its
            raise
        return pid


def remove(db):
    """Remove the demo profile whole (profiles.delete_profile), never any other. Returns what delete_profile
    returns, or None when there's no demo profile."""
    with _lock:
        pid = profile_id(db)
        if pid is None:
            dbm.meta_set(db, META, None)
            return None
        gone = prof.delete_profile(db, pid)
        dbm.meta_set(db, META, None)
        return {**gone, "id": pid}
