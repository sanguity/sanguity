"""Find your provider (#93): search the bundled directory of FHIR endpoints. Public data, so any signed-in account
may search it; nothing here touches anyone's records, and nothing is looked up outside the install (directory/).

  GET /api/directory?q=…        {"results": [entry…], "vendors": {…}, "sources": [...], "built", "return_addresses"}
  GET /api/directory/entry?id=… one entry with all its names and places, its vendor, and the return addresses

Admins only (#992), Settings → Install → Provider directory: adding and updating Epic's and Oracle Health's lists,
which the server downloads itself (directory/update.py). Not under /api/directory: nothing of these is anyone's.
  GET  /api/provider-directory          each list: loaded or not, its count, when it was read, its terms and host;
                                        the update going on (its progress), the last one's outcome, the weekly update
  POST /api/provider-directory/update   Add the lists / Update now, in the background (409 while one runs)

An entry: id, name, vendor, kind (provider · payer · sandbox), fhir_base, fhir_version, org (the health system),
places [[city, state]…], aliases (its locations' names), logo and portal (URLs as the vendor published them; the
app never fetches them), sandbox, source, retrieved. A result also says which of its names matched, so a search for
a clinic shows the health system it belongs to.

What connecting needs comes from directory.VENDORS (option 1 of docs/connected-sources-oauth.md: each install
registers its own app with the vendor), with the return address to register: each of PUBLIC_ORIGINS, and the
address this page was opened at, + /connect/return, with whether a vendor is likely to accept it outside its sandbox.
"""
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import config, directory
from .directory import update
from .main import _client_ip, _request_host, admin, connected, get_db, user

router = APIRouter(dependencies=[Depends(connected)])   # refused while connected sources are off (#1096)
LIST_MAX = 60   # names and places shown for one entry


def _local(host):
    h = host.split("]")[0].lstrip("[") if host.startswith("[") else host.rsplit(":", 1)[0]
    try:
        return not ipaddress.ip_address(h).is_global
    except ValueError:
        return h == "localhost" or "." not in h or h.endswith((".localhost", ".local", ".lan", ".home.arpa",
                                                                 ".internal", ".ts.net"))


def return_addresses(request):
    seen = list(config.setting("PUBLIC_ORIGINS"))
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip().lower()
    host = _request_host(request).lower()
    here = f"{proto}://{host}" if host else None
    if here and here not in seen:
        seen.append(here)
    out = []
    for origin in seen:
        scheme, _, host = origin.partition("://")
        why = ("plain HTTP" if scheme != "https" else "a home-network or tailnet address" if _local(host) else None)
        out.append({"url": origin + directory.RETURN_PATH, "current": origin == here, "likely_accepted": why is None,
                    "why": f"Vendors are unlikely to accept {why} outside their sandboxes." if why else None})
    return out


def _summary(e, query_words=None):
    out = {k: e.get(k) for k in ("id", "name", "vendor", "kind", "fhir_base", "org", "logo", "sandbox")}
    places = e.get("places") or []
    first = next((p for p in places if p and p[0]), places[0] if places else None)   # one with a city, if any
    out["place"] = ", ".join(x for x in first if x) if first else None
    out["places"] = len(places)
    if query_words:   # matched through one of its locations: say which
        name = set(directory.words(e["name"]))
        if not any(w in name or any(n.startswith(w) for n in name) for w in query_words):
            out["matched"] = next((a for a in e.get("aliases") or [] if any(w in directory.words(a) for w in query_words)), None)
    return out


def _context(ix, request):
    return {"vendors": directory.VENDORS, "sources": ix.sources, "built": ix.built,
            "return_addresses": return_addresses(request)}


@router.get("/api/directory")
def search(request: Request, q: str = Query("", max_length=directory.QUERY_MAX), s=Depends(user)):
    ix = directory.index()
    qw = [w for w in directory.words(q) if w not in directory.STOP]
    return {"query": q, "results": [_summary(e, qw) for e in ix.search(q)], "total": len(ix.entries), **_context(ix, request)}


@router.get("/api/directory/entry")
def entry(request: Request, id: str = Query(..., max_length=200), s=Depends(user)):
    ix = directory.index()
    i = ix.by_id.get(id)
    if i is None:
        raise HTTPException(404, "No such provider in the directory.")
    e = dict(ix.entries[i])
    for k in ("aliases", "places"):
        if e.get(k):
            e[k + "_total"] = len(e[k])
            e[k] = e[k][:LIST_MAX]
    return {"entry": e, **_context(ix, request)}


@router.get("/api/provider-directory")
def lists(s=Depends(admin), db=Depends(get_db)):
    return update.status(db)


@router.post("/api/provider-directory/update", status_code=202)
def update_now(request: Request, s=Depends(admin), db=Depends(get_db)):
    try:
        update.submit({"id": s["id"], "username": s["username"]}, _client_ip(request))
    except update.Busy as e:
        raise HTTPException(409, str(e))
    return update.status(db)
