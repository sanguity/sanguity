"""Outside contact: every request Sanguity makes to another server on someone's behalf (#59, #97).

By default Sanguity makes no outside requests. Some imports are opt-in exceptions, made only after the person
confirms: so far, fetching a pasted SMART Health Link (shl.py, #94) and, for a Health Card in it, its issuer's
public keys. Each one is recorded here, before the request is sent, as a profile activity entry "outside" with
{"host", "purpose"}: the host only, never the path, query, key or passcode. The outside-connections view (#97) lists
them with recent(); the profile's activity log shows them too.

  record(db, pid, host, purpose, who=None, ip=None)
  recent(db, pid, limit=100) -> [{"at", "host", "purpose", "actor"}]
"""
from . import activity

ACTION = "outside"


def record(db, pid, host, purpose, who=None, ip=None):
    """Note that Sanguity is about to contact host for purpose (a short phrase, like "SMART Health Link")."""
    activity.log(db, ACTION, who, pid, {"host": str(host)[:255], "purpose": str(purpose)[:80]}, ip)


def recent(db, pid, limit=100):
    return [{"at": r["at"], "host": r["detail"].get("host"), "purpose": r["detail"].get("purpose"), "actor": r["actor"]}
            for r in activity.for_profile(db, pid, action=ACTION, limit=limit)]
