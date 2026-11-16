"""Blue Cross Blue Shield claims: the claims.json a BCBS member-site export writes (build.py's "claims" format "bcbs").

claims.json holds `details`: one object per claim with claimId, claimType ("Pharmacy" or a medical type),
serviceFromDate, renderingProvider, the billed, plan-paid (bcbsnePaidAmount) and member-payable amounts,
claimStatus, isClaimDenied and the service lines. Medical claims become `claim` events and pharmacy fills `rx`
events, each with a Costs row: billed, plan paid, your share, status ("DENIED" for a denied claim) and the care
category, as build.py computes them.

Source keys: "bcbs:<claimId>" ("|<service date>" added when one claim number appears on two dates), else
"bcbs:id:<the claim's key in details>". How claims link to visits: see `_claims`.
"""
import posixpath

from ._claims import Claims, hkey, head, iso, json_candidates, load_json, money, claim_keys

NAME = "bcbs"
LABEL = "Blue Cross Blue Shield claims (claims.json)"
INSURER = "Blue Cross Blue Shield"

SIGNATURES = ('"memberPayableAmount"', '"billedChargeAmount"', '"serviceFromDate"')


def _find(files):
    for rel in json_candidates(files):
        h = head(files[rel])
        if h.lstrip().startswith("{") and '"details"' in h and sum(s in h for s in SIGNATURES) >= 2:
            return rel, h
    return None, ""


def detect(files):
    rel, h = _find(files)
    if not rel:
        return 0.0
    score = 0.95 if '"bcbsnePaidAmount"' in h or all(s in h for s in SIGNATURES) else 0.85
    return score if posixpath.basename(rel).lower() == "claims.json" else score - 0.05


def parse(files, insurer=None):
    """Claims from a BCBS claims.json. `insurer` names the plan on the Costs tab (default "Blue Cross Blue Shield")."""
    insurer = insurer or INSURER
    rel, _ = _find(files)
    if not rel:
        raise ValueError("This upload has no Blue Cross Blue Shield claims.json: no JSON file of claim details was found.")
    raw = load_json(files, rel)
    if not isinstance(raw, dict) or not isinstance(raw.get("details"), dict):
        raise ValueError(f"{rel} isn't a Blue Cross Blue Shield claims export: it has no \"details\" of claims.")
    out, no_date = Claims(NAME), 0
    claim_key = claim_keys(NAME, [(c.get("claimId"), (c.get("serviceFromDate") or "")[:10]) for c in raw["details"].values() if isinstance(c, dict)])
    for dk, c in raw["details"].items():
        if not isinstance(c, dict):
            continue
        date = (c.get("serviceFromDate") or "")[:10]
        if not date:
            no_date += 1
            continue
        billed, paid, owe = money(c.get("billedChargeAmount")), money(c.get("bcbsnePaidAmount")), money(c.get("memberPayableAmount"))
        status = c.get("claimStatus") or ""
        prov = ((c.get("renderingProvider") or {}).get("fullName") or "").title()
        cid = claim_key(c.get("claimId"), date)
        key = out.key(cid, f"{NAME}:id:{dk}", hkey(NAME, date, c.get("claimType"), prov, c.get("drugName"), billed, paid, owe))
        if not key:
            continue
        if c.get("claimType") == "Pharmacy":
            drug = (c.get("drugName") or "Prescription").title()
            i = out.add(iso(date), "rx", drug, f"Pharmacy fill · {insurer}",
                        {"type": "claim", "insurer": insurer, "kind": "Pharmacy", "drug": c.get("drugName"),
                         "billed": billed, "paid": paid, "owe": owe, "status": status}, key)
            out.cost(i, date, insurer, billed, paid, owe, status, "pharmacy", "Pharmacy", prov, drug)
            continue
        lines = [{"date": date, "desc": (l.get("serviceTitle") or l.get("serviceDescription") or "").capitalize(), "code": l.get("procedureCode") or "",
                  "billed": money(l.get("billedChargeAmount")), "denied": l.get("deniedReasons") or []} for l in c.get("serviceLines") or []]
        title = next((l["desc"] for l in lines if l["desc"]), "") or prov or "Medical claim"
        st = "DENIED" if c.get("isClaimDenied") else status
        i = out.add(iso(date), "claim", title, f"{prov} · {insurer}" + (" · denied" if c.get("isClaimDenied") else ""),
                    {"type": "claim", "insurer": insurer, "kind": "Medical", "provider": prov, "claimId": c.get("claimId"),
                     "billed": billed, "paid": paid, "owe": owe, "status": st, "lines": lines}, key,
                    abn=1 if c.get("isClaimDenied") else None)
        out.cost(i, date, insurer, billed, paid, owe, st, "medical", out.category(title, lines, prov, date), prov, title)
    if not out.events and not no_date:
        raise ValueError(f"{rel} has no claims in it.")
    return out.result(no_date)
