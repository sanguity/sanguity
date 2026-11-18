"""A profile's records as the records page has them once every record has been opened (#217), for the tests that
look at records' details: GET /api/profiles/{pid}/data has only some of them (server/records.py, page()), and
GET .../details?ids= gives the rest, at most records.DETAILS_MAX at a time.

    from tests.whole_records import whole
    data = whole(client, pid)      # the records' data with every record's details, as served() has them
"""

CHUNK = 500   # server/records.py DETAILS_MAX


def details(client, pid, ids):
    """{event id (text): its details} for those of ids that have details."""
    ids, out = list(ids), {}
    for i in range(0, len(ids), CHUNK):
        r = client.get(f"/api/profiles/{pid}/details", params={"ids": ",".join(str(x) for x in ids[i:i + CHUNK])})
        assert r.status_code == 200, (r.status_code, r.text[:300])
        out.update(r.json()["details"])
    return out


def whole(client, pid):
    """GET .../data as JSON, its details completed from .../details."""
    r = client.get(f"/api/profiles/{pid}/data")
    assert r.status_code == 200, (r.status_code, r.text[:300])
    data = r.json()
    ids = [e["id"] for e in data.get("events") or []]
    data["details"] = details(client, pid, ids) if ids else {}
    return data
