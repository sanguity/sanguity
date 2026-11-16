# fakefhir: a fake SMART on FHIR server for tests

CI can't sign in to live vendor sandboxes, so the connected-sources tests run against this fake instead
(issue #98). Sign-in (#32), sync (#91), the insurer APIs (#92), Health Links (#94) and the "no unlisted hosts"
test (#97) are its users. It uses only the standard library: an `http.server` in a thread on a free `127.0.0.1`
port, with its state kept in memory. It's a test tool and never part of the app.

Its own tests, with a short client written using only the standard library, are in
[`tests/test_fakefhir.py`](../test_fakefhir.py):

```bash
python tests/test_fakefhir.py
```

## Starting it

```python
import sys; sys.path.insert(0, ROOT)          # the repository root, as the other tests do
from tests.fakefhir import FakeFHIR, pkce

with FakeFHIR() as fake:                       # or fake = FakeFHIR().start(); ...; fake.stop()
    fake.register_client("app-1", ["http://127.0.0.1:8730/connect/return"])   # secret="..." makes it confidential
    fake.fhir_base                             # http://127.0.0.1:<port>/fhir: FHIR base, issuer and `aud`
```

Each `FakeFHIR` is its own server, so a test can run two (a provider and an insurer) on different ports.

| Address | What it is |
|---|---|
| `GET /fhir/.well-known/smart-configuration` | SMART discovery |
| `GET /fhir/metadata` | A minimal CapabilityStatement. Needs no token. |
| `GET\|POST /oauth2/authorize` | Authorization code with PKCE (`fake.authorize_url`) |
| `POST /oauth2/token` | `authorization_code`, `refresh_token`, and the JWT bearer grant when dynamic registration is on (`fake.token_url`) |
| `POST /oauth2/revoke` | RFC 7009 revocation (`fake.revoke_url`). Only when `revocation=True`. |
| `POST /oauth2/register` | Epic-style dynamic client registration (`fake.register_url`). Only when `dynamic_registration=True`. |
| `GET /fhir/{Type}`, `/fhir/{Type}/{id}`, `/fhir/Patient/{id}/$everything` | FHIR R4 reads and searches. They need a Bearer token. |

## Settings

These are keyword arguments, and attributes with the same names. They're read on every request, so a test can change
them while the fake is running (for example `fake.refresh = "omit"`).

| Setting | Default | Effect |
|---|---|---|
| `patients` | every fixture | Fixture names to load, for example `["sam-sample"]` |
| `access_ttl` | `3600` | Lifetime of an access token, in seconds |
| `refresh_ttl` | `None` | Lifetime of a refresh token. `None` means it never expires. |
| `refresh` | `"rotate"` | `"rotate"` issues a new refresh token on every refresh, and reusing an old one ends the whole grant. `"same"` returns the same refresh token. `"omit"` returns none, so the client keeps the original, as Oracle Health does. |
| `code_ttl` | `60` | Lifetime of an authorization code |
| `offline_for_public` | `True` | `False` means public clients don't get `offline_access`, so they get no refresh token |
| `require_aud` | `True` | The authorize request's `aud` must be `fhir_base` |
| `revocation` | `True` | Turns the revocation endpoint on or off, and whether discovery lists it |
| `dynamic_registration` | `False` | Turns on the registration endpoint and the JWT bearer grant, and adds them to discovery |
| `capabilities` | the SMART set | Replaces discovery's `capabilities` list |
| `scopes_supported` | `None` | When set, discovery lists these, and only these scopes are granted |
| `smart_config` | `{}` | Overrides discovery fields. A value of `None` removes the field. |
| `page_size`, `max_count` | `50`, `200` | The default page size, and the most `_count` can ask for |

## Signing in without a browser

The app builds its authorize URL as usual. The test then plays the patient's browser:

```python
verifier, challenge = pkce()                   # the fake's helper; the app has its own
url = fake.authorize_url + "?" + urlencode({"response_type": "code", "client_id": "app-1",
      "redirect_uri": RETURN, "scope": "launch/patient offline_access patient/*.rs", "state": state,
      "aud": fake.fhir_base, "code_challenge": challenge, "code_challenge_method": "S256"})
back = fake.authorize(url, patient="pat-1")    # approve=False denies; scopes=[...] unticks the rest
back.location, back.code, back.state, back.error   # the redirect to the app's return address (not followed)
```

`fake.authorize` adds `fake_patient`, `fake_decision` (`approve` or `deny`) and `fake_scopes` to the URL. A test can
also set `fake.signin_patient` and `fake.signin_approve` and open the URL some other way.

**The authorize endpoint refuses without redirecting** when the `client_id` is unknown or the `redirect_uri` isn't an
exact match for a registered one. `back.status` is then 400 and `back.body` gives the reason.

**It redirects with an error** for these cases:
- `response_type` isn't `code`;
- there's no `code_challenge`, or the method isn't `S256` (`plain` is refused);
- `aud` is wrong;
- no scope was requested;
- the patient denied.

**The token endpoint returns `invalid_grant`** when:
- the code is unknown, expired, or already used (a replayed code also revokes what its first exchange issued);
- the code was issued to another client;
- the `redirect_uri` differs from the authorize request's;
- the `code_verifier` is missing, malformed or doesn't match.

**Clients are authenticated like this:** a confidential client must send its secret, by HTTP Basic or by
`client_secret` in the form, at the token and revoke endpoints. Otherwise the endpoint returns `invalid_client` with
401. Public clients send only `client_id`. A token response carries `access_token`, `token_type`, `expires_in`,
`scope` and `patient`, and a `refresh_token` when `offline_access` or `online_access` was granted. There's no
`id_token`, because OpenID Connect isn't simulated.

## Tokens and time

The fake has its own clock: real time plus an offset.

| Call | What it does |
|---|---|
| `fake.advance(seconds)` | Moves the clock forward. Codes and tokens expire by it, and new resources are stamped by it. |
| `fake.expire_access_tokens()` | Expires every access token now and leaves refresh tokens alone |
| `fake.revoke_all(patient=None)` | Simulates the patient revoking the app in their portal: every grant ends, dynamic clients included |
| `fake.token_info(value)` | The fake's record of a token: kind, expiry, and the grant with its patient and scopes |

An expired, revoked or missing access token gets 401 with `WWW-Authenticate: Bearer error="invalid_token"`. An
expired, revoked or reused refresh token gets 400 `invalid_grant`. Revoking a refresh token also revokes the access
tokens from the same sign-in. Revoking an access token revokes only that token. A refresh may narrow `scope`, but not
widen it.

## Dynamic registration and the JWT bearer grant (Epic)

With `dynamic_registration=True`, the flow is the one Epic uses:
1. Sign in as a public client.
2. `POST /oauth2/register` with `Authorization: Bearer <that access token>` and JSON
   `{"software_id": "<client_id>", "jwks": {"keys": [<RSA public JWK>]}}`. The response is 201 with a new `client_id`
   bound to that patient and those scopes.
3. Get access tokens with `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` and an `assertion`, signed RS256,
   RS384 or RS512 with the matching private key. The assertion has `iss` and `sub` set to the new client ID, `aud`
   set to `token_url`, an `exp` within 5 minutes, and a `jti` that hasn't been used before.

No refresh tokens are issued in this flow. The signature check uses only the standard library. The self-test signs
with `cryptography`, which is already a dependency.

## FHIR

- **Patients.** Patients are fictional only, one fixture file per patient in
  [`tests/fixtures/fakefhir/`](../fixtures/fakefhir):
  - `pat-1`, Robin Example: the FHIR importer's fictional bundle, plus Coverage and two ExplanationOfBenefit claims.
  - `pat-2`, Sam Sample: diabetes follow-up, six Observations, a note held in a Binary, and a pharmacy claim.
  - `mem-1`, Casey Member, in [`payer/`](../fixtures/fakefhir/payer): a health plan's member for #92, loaded only with
    `FakeFHIR(fixtures=PAYER_FIXTURES)` (`from tests.fakefhir import PAYER_FIXTURES`), so the fake plays an
    insurer's patient-access API. Patient, Coverage and five CARIN Blue Button ExplanationOfBenefit claims: an
    office visit, an outpatient facility bill with a denied line, a denied MRI, a claim later adjusted, and a
    pharmacy fill. The default fixtures don't include it.

  Each type that #32 and #91 pull exists for both patients: Patient, Observation, Condition, MedicationRequest,
  Encounter, DocumentReference, DiagnosticReport and ExplanationOfBenefit. Every loaded resource has
  `meta.lastUpdated` `2025-10-01T00:00:00.000Z`.
- **Attachments.** DocumentReference attachments and DiagnosticReport `presentedForm` entries are served as Binary,
  with `url: Binary/<id>`. `GET Binary/<id>` returns the raw bytes with their content type. With
  `Accept: application/fhir+json`, it returns the Binary resource instead.
- **Search parameters.**
  - `patient`, `subject` and `beneficiary` (Coverage) accept `pat-1`, `Patient/pat-1` or an absolute URL.
  - `_id`.
  - `category`, `code` and `type` accept `code` or `system|code`, with commas for OR.
  - `_lastUpdated` with `gt`, `ge`, `lt`, `le`, `eq` and `ne`, repeatable. A date means the whole day.
  - `_count`.

  Other parameters are ignored. Results are sorted by `meta.lastUpdated`, then type and id. Each Bundle has `total`,
  `link: self`, and `link: next` while more remain. The next link adds `_offset` to the query. There's no snapshot,
  so a change between pages can shift them.
- **Incremental pulls.** `Bundle.meta.lastUpdated` is stamped strictly later than every resource so far. A client can
  save it and ask `_lastUpdated=gt<it>` next time to get exactly what changed since.
- **`$everything`.** `Patient/{id}/$everything` returns the patient's compartment, without Binary and without types
  the token can't read. It takes `_since`, `_type` and `_count`, and pages the same way.
- **Scopes.** SMART v1 (`patient/Observation.read`, `patient/*.read`) and v2 (`patient/Observation.rs`, `.r`, `.s`)
  are understood, and a `?query` suffix on a scope is ignored.
  - Search needs `s` or `read`. Read by id needs `r` or `read`.
  - Binary needs a read scope for Binary, DocumentReference or DiagnosticReport.
  - Anything outside the scopes gets 403. So does another patient's resource, a `patient=` naming someone else, or
    `$everything` for someone else.
  - A search without `patient=` returns only the signed-in patient's resources.

## Changing data between pulls

| Call | What it does |
|---|---|
| `fake.add(resource, patient="pat-1")` | Adds a resource and stamps it now. `patient` fills in its subject or patient reference. Returns what's stored, with its id. |
| `fake.update(type, id, {"status": "amended"})` | Changes top-level fields. A value of `None` removes the field. It bumps `versionId` and `lastUpdated`, even when there are no changes. |
| `fake.delete(type, id)` | Removes a resource. There's no `_history`, so deletions aren't announced. |
| `fake.get(type, id)`, `fake.resources(type=None, patient=None)`, `fake.patients` | Read what's stored |

## Injected errors

| Call | What it does |
|---|---|
| `fake.fail(429, retry_after=7)` | The next request gets 429 with `Retry-After: 7`. 429 and 503 default to `Retry-After: 1`. |
| `fake.fail(503, times=2, path="/fhir/Observation")` | Only requests whose path starts with `path` are affected. `times=None` means every matching request. |
| `fake.slow(2.0, times=1, path=None)` | Delays the response, then answers normally |
| `fake.clear_faults()` | Removes every fault |

Faults apply to every endpoint, the token endpoint included, so "vendor unavailable" can be tested apart from
"token revoked".

## The request record

`fake.requests` lists every request received, in order. Each one is a `Request` with `method`, `host`, `path`,
`query`, `authorization` and `status`:
- `host` is the `Host` header the client sent;
- `query` is a dict;
- `authorization` is `True` when an Authorization header was present (its value is never kept);
- `status` is the status the fake answered with.

`fake.hosts()` is the set of hosts. `fake.clear_requests()` empties the record. #97's test can compare the hosts with
the connections the app lists.

## What it doesn't do

- EHR launch;
- OpenID Connect (`id_token`);
- writes;
- `_history`;
- `_include`;
- bulk data;
- Health Link `shlink:/` manifests.

#94 can add a manifest endpoint here when it needs one. Keep the interface above stable, because the sign-in and sync
tests build on it.
