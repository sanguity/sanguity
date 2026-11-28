"""UnitedHealthcare claims: the claims.json a UHC member-site export writes (build.py's "claims" format "uhc").

claims.json holds `list` (one entry per claim: claimKey, claimId, claimType, serviceDate, providerName, claimStatus,
and the `balance` amounts) and `details` (per claimKey, the service lines with their procedure codes, charges and
denials; the part toward the deductible where the file gives one, in `balance` or each line's adjudication:
_claims.deductible, #1163). Medical claims become `claim` events and pharmacy fills (claimType "RX") `rx` events, each with a Costs
row: billed, plan paid, your share, status and the care category, as build.py computes them.

Source keys: "uhc:<claimId>" ("|<service date>" added when one claim number appears on two dates), else
"uhc:key:<claimKey>", else "uhc:h:" + a hash of the date, type, provider, drug or prescription number and amounts.
How claims link to visits: see `_claims`.
"""
import posixpath

from ._claims import Claims, deductible, hkey, head, iso, json_candidates, load_json, money, claim_keys, nice_drug

NAME = "uhc"
LABEL = "UnitedHealthcare claims (claims.json)"
INSURER = "UnitedHealthcare"


def _find(files):
    for rel in json_candidates(files):
        h = head(files[rel])
        if h.lstrip().startswith("{") and '"claimKey"' in h and ('"healthCareClaimDetails"' in h or '"healthPlanPays"' in h
                                                                   or '"patientResponsibility"' in h):
            return rel, h
    return None, ""


def detect(files):
    rel, h = _find(files)
    if not rel:
        return 0.0
    score = 0.85
    if '"healthCareClaimDetails"' in h and '"healthPlanPays"' in h:
        score = 0.95
    return score if posixpath.basename(rel).lower() == "claims.json" else score - 0.05


def _details(raw):
    out = {}
    for k, v in (raw.get("details") or {}).items():
        first = list(v.values())[0] if isinstance(v, dict) and v else {}
        out[k] = (first or {}).get("healthCareClaimDetails") or {} if isinstance(first, dict) else {}
    return out


def parse(files, insurer=None):
    """Claims from a UHC claims.json. `insurer` names the plan on the Costs tab (default "UnitedHealthcare")."""
    insurer = insurer or INSURER
    rel, _ = _find(files)
    if not rel:
        raise ValueError("This upload has no UnitedHealthcare claims.json: no JSON file with a list of claims was found.")
    raw = load_json(files, rel)
    if not isinstance(raw, dict) or not isinstance(raw.get("list"), list):
        raise ValueError(f"{rel} isn't a UnitedHealthcare claims export: it has no \"list\" of claims.")
    det = _details(raw)
    out, no_date = Claims(NAME), 0
    claim_key = claim_keys(NAME, [(c.get("claimId"), (c.get("serviceDate") or "")[:10]) for c in raw["list"] if isinstance(c, dict)])
    for c in raw["list"]:
        if not isinstance(c, dict):
            continue
        d, b = det.get(c.get("claimKey")) or {}, c.get("balance") or {}
        billed, paid, owe = money(b.get("totalBilledAmount")), money(b.get("healthPlanPays")), money(b.get("patientResponsibility"))
        status, date = c.get("claimStatus") or "", c.get("serviceDate")
        if not date:
            no_date += 1
            continue
        cid = claim_key(c.get("claimId"), date[:10])
        key = out.key(cid, c.get("claimKey") and f"{NAME}:key:{c['claimKey']}",
                      hkey(NAME, date[:10], c.get("claimType"), c.get("providerName"), c.get("medicationName"),
                           c.get("prescriptionNumber"), billed, paid, owe))
        if not key:
            continue
        line_ded = [deductible(ln.get("serviceAdjudication")) for ln in d.get("serviceLines") or [] if isinstance(ln, dict)]
        ded = deductible(b)
        if ded is None and any(x is not None for x in line_ded):
            ded = round(sum(x or 0.0 for x in line_ded), 2)
        if c.get("claimType") == "RX":
            drug = nice_drug(c.get("medicationName"))
            i = out.add(iso(date), "rx", drug, f"Pharmacy fill · {c.get('providerName') or ''} · {insurer}".replace(" ·  ·", " ·"),
                        {"type": "claim", "insurer": insurer, "kind": "Pharmacy", "drug": c.get("medicationName"), "rxNumber": c.get("prescriptionNumber"),
                         "provider": c.get("providerName"), "billed": billed, "paid": paid, "owe": owe, "status": status,
                         **({"deductible": ded} if ded is not None else {})}, key)
            out.cost(i, date, insurer, billed, paid, owe, status, "pharmacy", "Pharmacy", c.get("providerName"), drug, ded=ded)
            continue
        lines = []
        for ln in d.get("serviceLines") or []:
            procs = [p for p in ln.get("procedures") or [] if p.get("code") not in (None, "MISC")]
            desc = (procs[0]["description"] if procs else (ln.get("providedServiceInfo") or {}).get("description")) or ""
            adj = ln.get("serviceAdjudication") or {}
            lines.append({"date": ln.get("lastServiceDate"), "desc": desc[:140], "code": procs[0]["code"] if procs else "",
                          "billed": money(adj.get("chargedAmount")), "denied": [x.get("description") or str(x) for x in ln.get("deniedClaimInfo") or []]})
        title = next((l["desc"] for l in lines if l["desc"] and len(l["desc"]) < 70), "") or (c.get("providerName") or "Medical claim").title()
        i = out.add(iso(date), "claim", title, f"{(c.get('providerName') or '').title()} · {insurer}" + (" · denied" if "DENIED" in status else ""),
                    {"type": "claim", "insurer": insurer, "kind": "Medical", "provider": c.get("providerName"), "claimId": c.get("claimId"),
                     "billed": billed, "paid": paid, "owe": owe, "status": status, "lines": lines,
                     "network": d.get("claimNetworkStatus"), "end": c.get("lastServiceDate"),
                     **({"deductible": ded} if ded is not None else {})}, key,
                    end=iso(c["lastServiceDate"]) if c.get("lastServiceDate") and c["lastServiceDate"] != date else None,
                    abn=1 if "DENIED" in status else None)
        out.cost(i, date, insurer, billed, paid, owe, status, "medical", out.category(title, lines, c.get("providerName"), date),
                 c.get("providerName"), title, ded=ded)
    if not out.events and not no_date:
        raise ValueError(f"{rel} has no claims in it.")
    return out.result(no_date)
