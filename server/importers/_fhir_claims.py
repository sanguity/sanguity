"""ExplanationOfBenefit -> Costs, for the FHIR importer (fhir.py, #92).

Insurers' patient-access APIs and Medicare Blue Button 2.0 give claims as CARIN Blue Button ExplanationOfBenefit
(EOB) resources (https://hl7.org/fhir/us/carin-bb/). Each EOB becomes the same records the claims importers
(claims_uhc, claims_bcbs, claims_csv; see _claims.py) make from an insurer's own export:

    a `claim` event (medical: professional, institutional, oral, vision) or an `rx` event (pharmacy; Blue Button's
    Part D events), keyed "fhir:ExplanationOfBenefit/<id>" like any other FHIR resource;
    its `claim` detail: insurer, kind, provider, claimId, billed, paid, owe, status, lines (date, desc, code,
    billed, denied), network, plan, and the `visit` hint the claims importers give;
    a `costs` row: d, ins, billed, paid, owe, status, kind, cat, prov, title.

Amounts come from the EOB's `total` where it has them, else the sum over its items' `adjudication`, matched by
the adjudication category's code in any code system (CARIN's C4BBAdjudication and the base FHIR adjudication codes
share them): billed = submitted; paid = benefit (else paidtoprovider + paidtopatient, else payment.amount); owe =
memberliability (else copay + deductible + coinsurance). A negative amount (an adjustment) counts as it is.

Status: "Denied" when the payer denied the whole claim (a denialreason, or C4BBPayerAdjudicationStatus "denied",
on the claim or on every line, and nothing paid), "Pending" while it is queued or partly processed, else "Paid"
when the plan paid something and "Processed" when it paid nothing. Denied lines on a claim that was otherwise paid
are listed on the line, as the claims importers do.

Adjusted claims: an EOB that names an earlier claim as its `related` "prior" claim replaces it, and one marked
"replacedby" (with the newer claim in the same import) is replaced. A replaced claim is kept, hidden from the
timeline and with status "Replaced" and amounts of 0 on Costs (its own amounts under `original`), so a sync that
reads the adjustment later can update the one it saved before in place (sync._touched) and nothing counts twice.

Coverage is read for the insurer's and plan's names (insurance.coverage -> payor, class "plan"); it makes no
records of its own.

Linking to visits: as the claims importers, a facility bill (revenue-code lines) inside a hospital stay from the
same import (an Encounter) is "Hospital stay (facility)", and every claim's detail carries `visit`.
"""
import re

from . import _claims
from .fhir import Index, iso, text_of

PAID_PARTS = ("paidtoprovider", "paidtopatient")
OWE_PARTS = ("copay", "deductible", "coinsurance")
PHARMACY = {"pharmacy", "pde"}           # CARIN claim type; Blue Button's Part D event type


def _codes(cc):
    """Lower-cased codes of a CodeableConcept (any system), and its text."""
    if not isinstance(cc, dict):
        return set()
    out = {str(c.get("code")).lower() for c in cc.get("coding") or [] if isinstance(c, dict) and c.get("code")}
    if cc.get("text"):
        out.add(cc["text"].strip().lower())
    return out


def _amounts(adjudications):
    """{category code: summed amount} over a list of adjudication or total entries."""
    out = {}
    for a in adjudications or []:
        if not isinstance(a, dict) or not isinstance(a.get("amount"), dict):
            continue
        for code in _codes(a.get("category")):
            out[code] = round(out.get(code, 0.0) + _claims.money(a["amount"]), 2)
    return out


def _denials(adjudications):
    """Why a claim or line was denied: the reasons of its denialreason entries (or a status of "denied")."""
    out = []
    for a in adjudications or []:
        if not isinstance(a, dict):
            continue
        cats = _codes(a.get("category"))
        if "denialreason" in cats or "denied" in cats or "denied" in _codes(a.get("reason")):
            out.append(text_of(a.get("reason")) or "Denied")
    return out


def _date(x):
    return (x or "")[:10] if isinstance(x, str) else ""


def _name(s):
    """Names as the source gives them; an all-capitals one in title case, as the claims importers show them."""
    s = " ".join((s or "").split())
    return s.title() if s.isupper() else s


def _revenue(cc):
    """A revenue code as the claims importers carry it: five digits, 0-padded ("0450" -> "00450")."""
    for c in (cc or {}).get("coding") or [] if isinstance(cc, dict) else []:
        code = str(c.get("code") or "")
        if re.fullmatch(r"\d{3,5}", code):
            return code.zfill(5)
    return ""


def _words(cc):
    """A CodeableConcept's text or a coding's display, not a bare code."""
    if not isinstance(cc, dict):
        return ""
    return cc.get("text") or next((c["display"] for c in cc.get("coding") or [] if isinstance(c, dict) and c.get("display")), "")


def _code(cc):
    for c in (cc or {}).get("coding") or [] if isinstance(cc, dict) else []:
        if c.get("code"):
            return str(c["code"])
    return ""


def _identifiers(r):
    return {str(i["value"]) for i in r.get("identifier") or [] if isinstance(i, dict) and i.get("value")}


def names_of(r):
    """What other claims may call this one by: its identifiers, and "id:<resource id>"."""
    return _identifiers(r) | {f"id:{r.get('id')}"}


def replaces(r, idx=None):
    """The names (as names_of gives them) of the claims this EOB says it replaces: for sync.py, which marks one
    it saved in an earlier pull."""
    return _related(r, idx or Index([]))[0]


def _claim_id(r):
    ids = [i for i in r.get("identifier") or [] if isinstance(i, dict) and i.get("value")]
    uc = next((i["value"] for i in ids if "uc" in _codes(i.get("type"))), None)
    return str(uc or (ids[0]["value"] if ids else r.get("id") or ""))


def _related(r, idx):
    """(identifiers or ids of the claims this EOB replaces, those of the claims it says replace it)."""
    replaces, replaced = set(), set()
    for rel in r.get("related") or []:
        if not isinstance(rel, dict):
            continue
        how = _codes(rel.get("relationship"))
        names = set()
        claim = rel.get("claim") if isinstance(rel.get("claim"), dict) else {}
        if isinstance(claim.get("identifier"), dict) and claim["identifier"].get("value"):
            names.add(str(claim["identifier"]["value"]))
        target = idx.get(claim) if claim.get("reference") else None
        if target:
            names.add(f"id:{target.get('id')}")
        if isinstance(rel.get("reference"), dict) and rel["reference"].get("value"):
            names.add(str(rel["reference"]["value"]))
        if "prior" in how or "replaces" in how:
            replaces |= names
        elif "replacedby" in how:
            replaced |= names
    return replaces, replaced


def _coverage(r, idx):
    """(insurer name, plan name) from the EOB's focal Coverage."""
    ins = sorted(r.get("insurance") or [], key=lambda i: not (isinstance(i, dict) and i.get("focal")))
    for i in ins:
        cov = idx.get((i or {}).get("coverage"), r) if isinstance(i, dict) else None
        if not cov or cov.get("resourceType") != "Coverage":
            continue
        payor = next((idx.name(p, cov) for p in cov.get("payor") or [] if idx.name(p, cov)), "")
        plan = next((c.get("name") or c.get("value") for c in cov.get("class") or []
                     if isinstance(c, dict) and ({"plan", "group"} & _codes(c.get("type"))) and (c.get("name") or c.get("value"))), "")
        return payor, plan
    return "", ""


def _support(r, code):
    for s in r.get("supportingInfo") or []:
        if isinstance(s, dict) and code in _codes(s.get("category")):
            for k in ("valueQuantity", "valueString", "valueCode", "code", "valueBoolean", "timingDate"):
                v = s.get(k)
                if isinstance(v, dict):
                    return v.get("value") if "value" in v else text_of(v)
                if v not in (None, ""):
                    return v
    return None


def add_claims(eobs, idx, rkey, b, warnings):
    """Add a claim or fill per ExplanationOfBenefit in `eobs` (resources) to Builder `b`; returns the Costs rows.
    `b.events` already holds the import's stays, for filing facility bills."""
    live, skipped = [], 0
    for r in eobs:
        if r.get("status") in ("entered-in-error", "cancelled"):
            skipped += 1
            continue
        live.append(r)
    claim_of = {n: _claim_id(r) for r in live for n in names_of(r)}
    replaced_by = {}   # a replaced claim's identifier or id -> the claim number of the one replacing it
    for r in live:
        older, newer = _related(r, idx)
        for n in older:
            replaced_by.setdefault(n, _claim_id(r))
        for n in newer:   # "replacedby": only when that newer claim is in this import too
            if n in claim_of and n not in names_of(r):
                for own in names_of(r):
                    replaced_by.setdefault(own, claim_of[n])
    costs, superseded, no_date = [], 0, 0
    for r in sorted(live, key=lambda x: rkey(x)):
        gone = next((replaced_by[n] for n in sorted(names_of(r)) if n in replaced_by), None)
        items = [i for i in r.get("item") or [] if isinstance(i, dict)]
        bp = r.get("billablePeriod") or {}
        first = items[0] if items else {}
        date = _date(bp.get("start")) or _date(first.get("servicedDate")) or _date((first.get("servicedPeriod") or {}).get("start")) \
            or _date(r.get("created"))
        if not date:
            no_date += 1
            continue
        end = _date(bp.get("end")) or max([_date(i.get("servicedDate")) or _date((i.get("servicedPeriod") or {}).get("end")) for i in items] or [""])
        end = end if end and end > date else ""
        kinds = _codes(r.get("type"))
        pharmacy = bool(kinds & PHARMACY)
        institutional = "institutional" in kinds or bool(kinds & {"inpatient", "outpatient", "snf", "hha", "hospice"})
        payor, plan = _coverage(r, idx)
        insurer = _name(idx.name(r.get("insurer"), r) or payor) or "Insurance"
        prov = _name(idx.name(r.get("provider"), r) or idx.name(r.get("facility"), r))

        totals, line_sums = _amounts(r.get("total")), _amounts([a for i in items for a in i.get("adjudication") or []])

        def pick(*codes_):
            for src in (totals, line_sums):
                hit = [src[c] for c in codes_ if c in src]
                if hit:
                    return round(sum(hit), 2)
            return None
        billed = pick("submitted") or 0.0
        paid = pick("benefit")
        if paid is None:
            paid = pick(*PAID_PARTS)
        if paid is None:
            paid = _claims.money((r.get("payment") or {}).get("amount")) if isinstance(r.get("payment"), dict) else 0.0
        owe = pick("memberliability")
        if owe is None:
            owe = pick(*OWE_PARTS) or 0.0

        lines = []
        for it in items:
            adj = it.get("adjudication") or []
            code = (_revenue(it.get("revenue")) if institutional else "") or _code(it.get("productOrService")) or _revenue(it.get("revenue"))
            desc = _words(it.get("productOrService")) or _words(it.get("revenue")) or text_of(it.get("productOrService")) or ""
            lines.append({"date": _date(it.get("servicedDate")) or _date((it.get("servicedPeriod") or {}).get("start")) or date,
                          "desc": desc[:140], "code": code, "billed": _amounts(adj).get("submitted", 0.0), "denied": _denials(adj)})
        header_denied = _denials(r.get("adjudication"))
        all_lines_denied = bool(lines) and all(l["denied"] for l in lines)
        denied = (bool(header_denied) or all_lines_denied) and not paid and not gone
        outcome = (r.get("outcome") or "").lower()
        status = ("Replaced" if gone else "Denied" if denied else "Pending" if outcome in ("queued", "partial") or r.get("status") == "draft"
                  else "Paid" if paid else "Processed")
        original = None
        if gone:   # kept, so a later sync can mark one it saved before, but hidden and with nothing to count twice
            superseded += 1
            original = {"billed": billed, "paid": paid, "owe": owe}
            billed = paid = owe = 0.0
        network = _support(r, "benefitpaymentstatus") or _support(r, "billingnetworkcontractingstatus")
        detail = {"type": "claim", "insurer": insurer, "kind": "Pharmacy" if pharmacy else "Medical", "provider": prov,
                  "claimId": _claim_id(r), "billed": billed, "paid": paid, "owe": owe, "status": status}
        if plan:
            detail["plan"] = plan
        if network:
            detail["network"] = str(network)
        if header_denied:
            detail["denied"] = header_denied
        if gone:
            detail.update({"replacedBy": gone, "original": original})
        if pharmacy:
            raw = text_of(first.get("productOrService")) or "Prescription"
            drug = _claims.nice_drug(raw) if raw.isupper() else raw
            detail.update({"drug": raw, "ndc": _code(first.get("productOrService")) or None,
                           "rxNumber": _support(r, "rxnumber"), "daysSupply": _support(r, "dayssupply")})
            detail = {k: v for k, v in detail.items() if v is not None}
            title, kind, cat = drug, "rx", "Pharmacy"
            sub = " · ".join(x for x in ["Pharmacy fill", prov, insurer] if x) + (" · denied" if denied else "") + (" · replaced" if gone else "")
        else:
            detail["lines"] = lines
            if end:
                detail["end"] = end
            title = next((l["desc"] for l in lines if l["desc"] and len(l["desc"]) < 70), "") or prov or "Medical claim"
            kind = "claim"
            cat = _claims.care_category("medical", title, lines, prov, date, b.events)
            sub = " · ".join(x for x in [prov, insurer] if x) + (" · denied" if denied else "") + (" · replaced" if gone else "")
        hint = {"date": date}
        if end:
            hint["end"] = end
        if prov:
            hint["provider"] = prov
        if not pharmacy and _claims.is_facility(lines):
            hint["facility"] = True
        detail["visit"] = hint
        eid = b.add(iso(date), kind, title, sub, detail, end=iso(end) if end and not pharmacy else None,
                    abn=1 if denied else None, hidden=1 if gone else None, key=rkey(r))
        costs.append({"id": eid, "d": date, "ins": insurer, "billed": billed, "paid": paid, "owe": owe, "status": status,
                      "kind": "pharmacy" if pharmacy else "medical", "cat": cat, "prov": prov, "title": title})
    if skipped:
        warnings.append(f"Skipped {skipped} claim{'s' if skipped != 1 else ''} the insurer marked cancelled or entered in error.")
    if superseded:
        warnings.append(f"{superseded} claim{'s were' if superseded != 1 else ' was'} replaced by an adjusted claim: "
                        f"{'they are' if superseded != 1 else 'it is'} kept as \"Replaced\", with no amounts, so the "
                        "Costs tab counts only the adjusted one.")
    if no_date:
        warnings.append(f"{no_date} claim{'s' if no_date != 1 else ''} had no service date and "
                        f"{'were' if no_date != 1 else 'was'} left out.")
    return costs


__all__ = ["add_claims", "names_of", "replaces"]
