# Connected sources: how OAuth sign-in works for a self-hosted install

**Status: decided (option 1, for now).** This document is for issue #90. It sets out the options, the vendor rules
that limit them, the decision and its threat model. The analysis of option 2, a project registration with a relay
page, stays below for when it's added. Option 1 is built (#32, #91: `server/connections.py`, `server/smart.py`,
`server/sync.py`); the project has registered no app with any vendor.

**Why it has to be decided first.** Sign-in to a provider's or insurer's FHIR API (#32, #92), scheduled sync with
stored tokens (#91) and the provider directory (#93) all depend on how an install gets the authorization code back
from the vendor. Vendors only send codes to redirect URIs that were registered in advance. Every Sanguity install
has its own address, and many have no public address at all.

**Sources.** Each vendor fact below links to the vendor's or HL7's own page. A claim marked *(unverified)* comes
from forum posts, third-party guides, or nowhere; check it before relying on it. The pages were read in
November 2026. Epic's documentation is one very long page, so section names are given.

## Decision: option 1, each user registers their own app

Sanguity starts with **option 1**: each install connects through an app its owner registers with each vendor, and
the vendor sends the sign-in straight back to the install's own address. Option 2 may be added later if enough
people want live connections; the code is built so that adding it changes only where a sign-in returns to.

**Why.** The project takes on no vendor registrations, terms, privacy duties or hosting, and SMART's rule against
forwarding authorization codes (see [SMART App Launch](#smart-app-launch-2x-hl7)) doesn't arise, because nothing
forwards them. Each install can also keep a client secret on its server where a vendor issues one. File imports
remain the main way records come in; live connections are an extra for those who can set them up.

**What it reaches, and what it doesn't** (from the vendor facts below):

| Source | Under option 1 |
|---|---|
| Epic sandbox | Works. Enough to build and test sign-in and sync completely (#32, #91), and for #32's done-when. |
| Epic production (real hospitals) | Possible for installs with a proper HTTPS address. Whether Epic accepts and distributes an app registered by one person, and accepts a tailnet name such as `*.ts.net`, is *unverified*: check before promising it. |
| Oracle Health | Effectively out for home installs: it wants a valid certificate and a DNS TXT record the user controls on the redirect host, which a LAN address or a `ts.net` name can't provide. |
| Medicare Blue Button 2.0 | Sandbox only. Its production review is impractical for an individual. |
| Other insurers' patient-access APIs (#92) | Only those that let individuals register, one by one. |
| Installs on a LAN address or plain HTTP | Unlikely to be accepted by vendors in production. The app says so. |

**The open questions, answered:**

1. *Option 2 or option 1?* Option 1 for now; option 2 if there's demand.
2. *SMART's "SHALL NOT forward" rule:* doesn't arise under option 1. Revisit it, with the community and Epic, before
   building option 2.
3. *Blue Button and other confidential-only payers:* through option 1, which can hold a client secret. In
   practice that means their sandboxes until a payer's review is practical for an individual. No token-exchange
   server.
4. *A commercial aggregator:* not planned.
5. *Who hosts a relay:* not needed until option 2.
6. *Release timing:* built in Phase 2b, as an advanced setting next to file imports.
7. *Forks:* each install, fork or not, registers its own app.

### What gets built

- **One sign-in flow** (#32), with its settings per vendor: client ID, client secret where the vendor issues one,
  scopes, and the return address. Today the return address is the install's own `/connect/return`; with option 2
  it would be the relay. Nothing else depends on which.
  - Discovery from the issuer's `.well-known/smart-configuration`.
  - PKCE (S256) and a single-use `state` bound to the signed-in session.
  - Checks on return: `state`, issuer, `aud`, and the token exchange against the stored issuer.
  - Epic's dynamic client registration where available, so long-lived access uses a key the install generates.
- **A guided setup screen per vendor:** where to register, the exact return address to paste (from
  `PUBLIC_ORIGINS`), the scopes to request, and a "test this connection" button that works against Epic's sandbox.
  It warns when the install's address is unlikely to be accepted (LAN, plain HTTP). Built as a numbered
  walk-through per vendor (#165): what to look for on the vendor's developer site (described, not exact button
  names, since those sites change), the values to paste with Copy buttons, the form, and the test as the last step.
  An admin can mark a source as waiting for the vendor's review; people then see it's coming, and are told to upload
  the portal's export meanwhile, in place of a connect button.
- **Connections and sync** (#91), as the issue describes: a connection per profile and source, tokens and secrets
  encrypted with `crypto.enc`, a reconnect prompt when access ends, and disconnecting revokes and deletes them.
- **Choosing what a connection pulls** (#99): kinds of records and a start date, chosen before signing in. The
  sign-in asks only for the scopes those kinds need: a type-specific scope the admin set up stays only if a chosen
  kind reads that type, and a wildcard (`patient/*.read`) becomes one scope per type, unless the vendor's discovery
  lists the wildcard but not the narrower scopes. Kinds the granted scopes don't cover aren't offered later without
  signing in again. Narrower SMART v2 scopes with a `?category=` query (labs only, say) aren't asked for yet: the
  category goes in the search instead.
- **Attachment limits** (#101) and **a warning when a connection stops syncing** (#102), on the profile's main page
  and in the admins' notices, never by email or push.
- **A local count of connections per vendor**, shown to admins and never sent anywhere, so demand for option 2 is
  something the project can ask about rather than guess.
- **The directory** (#93) says per entry what option 1 needs there (the vendor's developer site, whether a client
  ID is enough), rather than "enabled".

### Threat model (option 1)

| Party | Sees | Doesn't see |
|---|---|---|
| Vendor | The user's sign-in and consent, the install's IP, every FHIR call, and the registered return address (the install's own address). | Anything else on the install. |
| Network between the user's browser and the install | On plain HTTP, the authorization code in transit. It's useless without the PKCE verifier, which stays on the server, and redeeming it needs the session cookie. | The verifier, tokens, secrets. |
| Someone with a copy of the database | Encrypted tokens, client secrets and keys. | Their plaintext, without `auth/data_key`. |

- **Forged or replayed returns:** `state` is random, single use, short lived and bound to the session that started
  the sign-in, so a link crafted by someone else (to attach their records, or to fish for a code) is refused. The
  code is bound to the install's PKCE challenge.
- **A wrong or hostile FHIR server:** the issuer comes from the user's choice or the directory and is shown before
  sign-in; tokens are only ever sent to that issuer's token endpoint, and the `aud` check stops a token being used
  elsewhere.
- **A leaked client secret:** it identifies the user's own app, not a patient. Someone holding it still needs the
  patient's sign-in and consent. It's encrypted at rest, and rotating it is a setting.
- **Disconnecting** revokes the token where the vendor supports revocation, deletes the token, secret and key, and
  stops all contact with that host. What happens to the records already pulled is the user's choice.
- **Outside requests** stay opt-in: nothing contacts a vendor until the user connects it, and every connection is
  listed with its host (#97, #59): Admin → Outside connections, enforced by `server/net.py`.

## Summary of the proposal (before the decision)

- **Recommendation at the time (not chosen for now):** the project registers one public client with each vendor that allows it. A static relay page
  at a project-owned HTTPS address (no server, nothing stored) is the registered redirect URI. It passes the code to
  the install's own address, which it reads from `state`. PKCE keeps the code useless to anyone but the install that
  started the sign-in. On Epic, the install then registers its own dynamic client with a key pair it generates and
  keeps, so long-lived access never depends on a shared secret or on the relay.
- **What it costs:** the project takes on vendor registrations and their terms, and runs a small page whose
  integrity matters (see [If the relay page is replaced](#if-the-relay-page-is-replaced-or-compromised)). The relay
  forwards the code to an address taken from the request. SMART says apps shall not do this, so the relay has to
  confine and show where it sends it. That is the main risk to weigh.
- **What it doesn't cover:** vendors that only accept confidential clients with a shared secret. Medicare Blue
  Button 2.0 is one. A static page can't keep a secret, so these need either a per-user registration or a server.
  See [Medicare Blue Button 2.0](#medicare-blue-button-20).
- **"No outside requests by default" still holds:** nothing is contacted until the user connects a source. The
  relay is a page the user's browser loads during sign-in. The install's server never contacts it.

## Vendor facts that constrain the choice

### SMART App Launch 2.x (HL7)

The current release is SMART App Launch 2.2.0
([App Launch](https://hl7.org/fhir/smart-app-launch/app-launch.html),
[Conformance](https://hl7.org/fhir/smart-app-launch/conformance.html)).

- **Redirect URIs.** Apps register "one or more fixed, fully-specified `redirect_uri`s", and the authorization
  request's `redirect_uri` "Must match one of the client's pre-registered redirect URIs" (*Register App with EHR*,
  *Obtain authorization code*). The token request repeats the same `redirect_uri`.
- **No forwarding to arbitrary URLs.** *Security and Privacy Considerations > App protection*: "An app SHALL NOT
  forward values passed back to its redirect URL to any other arbitrary or user-provided URL". A relay that forwards
  codes to the address in `state` breaks this unless the forwarding is confined. The recommended design confines it
  in three ways: it forwards only to an address the user has confirmed in this browser, the code can't be used
  without the install's PKCE verifier, and the relay shows where it is sending the code. Whether a vendor's review
  accepts that is an [open question](#open-questions-for-you).
- **PKCE.** "All SMART apps SHALL support Proof Key for Code Exchange (PKCE)". Servers support `S256` and "SHALL NOT
  support the `plain` method" (*Considerations for PKCE Support*). `code_challenge_methods_supported` is required
  in `.well-known/smart-configuration`.
- **`state`.** It is unpredictable, "with at least 122 bits of entropy", and the app "SHALL validate the value of
  the state parameter upon return" (*Obtain authorization code*).
- **`aud`.** Servers "SHALL support the `aud` parameter". The app sends the FHIR base URL it means to call, which
  "prevents leaking a genuine bearer token to a counterfeit resource server".
- **Public and confidential clients.** Which one to use depends on "whether the execution environment within which
  the app runs enables the app to protect secrets". For public apps, client authentication on refresh "is not
  possible (and thus not required)". A server "can decide which client types (public or confidential) are eligible
  for offline access" (*Refresh access token*). So refresh tokens for public clients are allowed but not promised.
- **Discovery.** `capabilities` may include `client-public`, `client-confidential-symmetric`,
  `client-confidential-asymmetric` and `permission-offline`. `revocation_endpoint` is recommended, not required, and
  `registration_endpoint` (dynamic registration) is optional
  ([Conformance](https://hl7.org/fhir/smart-app-launch/conformance.html)).
- **Loopback and custom schemes.** SMART says nothing on these. The rules come from
  [RFC 8252](https://www.rfc-editor.org/rfc/rfc8252) (OAuth for native apps). Loopback redirects are
  `http://127.0.0.1:{port}`, and the server "MUST allow any port" (§7.3). Private-use schemes must be reverse-DNS
  names such as `com.example.app:/` (§7.1). Neither helps a self-hosted server: a loopback redirect lands on the
  machine running the browser, not on the install, and a custom scheme needs a native app.

### Epic

From [Epic on FHIR documentation](https://fhir.epic.com/Documentation?docId=oauth2) (one long page, many
sections), and the page the issue cites, [test patients and sandbox](https://fhir.epic.com/Documentation?docId=testpatients).

- **Redirect URIs are an allowlist.** *Limit Use of Localhost URIs*: apps "must register a list of URLs that Epic
  will use as an allowlist", and "Epic will only deliver authorization codes to a URL that is listed on the
  corresponding app." Redirect URIs are one of the few things that can still be added after an app is marked Ready
  for Production. "With the exception of adding redirect URIs and modifying your JWK Set URLs, all technical changes
  will require you to build a new app."
- **Localhost is discouraged** "as a security best practice", except for native apps that are built to use it.
  Epic suggests a separate test app for localhost URIs "that will never be activated in customer environments".
  Custom schemes aren't covered. Native apps are pointed to claimed HTTPS links (Universal Links, App Links, App URI
  Handlers) or PKCE (*Considerations for Native Apps*).
- **PKCE** has been supported since the August 2019 version, `S256` only.
- **Refresh tokens need the confidential profile.** The app creation form asks "Whether you'll use the confidential
  client profile (required for refresh tokens)". For native and browser-based apps, *Offline Access for Native and
  Browser-Based Applications* describes **dynamic client registration**:
  1. a standalone launch as a public client, with no secret, which yields a one-time initial access token;
  2. the app generates "a public-private key pair on the user's phone or computer" and registers a dynamic client
     (RFC 7591) with `software_id` set to Epic's client ID and its public key in `jwks`;
  3. for the duration the patient chose, the app gets access tokens with the JWT bearer grant (RFC 7523), signed
     with its own private key. The dynamic client is bound to that user.

  For Sanguity this fits the relay design closely. The install is the "device", its private key never leaves it,
  and after the first sign-in it never needs the relay again for that connection.
- **The patient picks the duration.** *Refresh Tokens and Persistent Access Periods*: "In patient-facing contexts,
  it can be set by the patient through MyChart."
- **Per-health-system enablement.** Each Epic customer still has to have the client ID. For patient-facing apps
  that use only qualifying APIs (USCDI v1/v3, a CCDS subset, or "CMS Payer Apps using only ExplanationOfBenefit"),
  client IDs are distributed automatically. "All US provider organizations currently receive client IDs that meet
  the automatic distribution criteria", and qualifying payer apps are "automatically made available to Epic payer
  environments" (*auto-download* sections). Apps outside the criteria, or a health system that has turned
  auto-download off, go through the App Request Process with each organization. Direct access "can be granted only
  with approval from both an Epic Community Member and Epic"
  ([testpatients](https://fhir.epic.com/Documentation?docId=testpatients), *Terms, For Developers*). This applies
  to every option. It is why #93 must say when a health system hasn't enabled the app. Sync after registration
  reportedly takes from an hour to about 12 hours *(unverified: third-party guides)*.
- **Terms for persistent access.** A patient app that keeps a persistent connection (for example with a refresh
  token) must either have a BAA with the health system or tell the patient how long the access lasts (Epic terms,
  quoted in the OAuth section). Sanguity would take the second route.
- **Revocation.** Patients can revoke access in MyChart "at any time". A token revocation endpoint for patient
  apps isn't described in the parts read *(unverified)*. Check `revocation_endpoint` in each server's
  `.well-known/smart-configuration`.
- **Can an individual register a production patient app?** The open.epic terms address "developers" without
  requiring an organization. Whether a private person's app is accepted for production and auto-download is
  *(unverified)*.

### Oracle Health (Cerner Millennium)

From [Oracle Health Authorization Framework](https://docs.oracle.com/en/industries/health/millennium-platform-apis/authorization-framework).

- **Redirect URIs.** The request's `redirect_uri` "should match the redirect URI provided during the registration
  phase", and it "must be an absolute URI". Validation is strict about the path, "which includes trailing slashes".
  Extra query parameters are allowed. Several redirect URIs are possible, but "not yet exposed in our developer
  tooling", so they go through Oracle's operations team.
- **HTTPS and localhost.** Development environments may use "private domains (such as localhost), unsecured
  connections (non-HTTPS), or nonstandard ports". For general release, apps "SHOULD support TLS", use port 443,
  and have a valid certificate from a trusted authority. "Oracle Health does not support the use of self-signed
  certificates." Native apps "SHOULD" use claimed `https` redirects rather than private-use schemes. Windows apps:
  "only explicit internet hosts or explicit URI activation schemes".
- **The app is named only with a DNS record.** The site hosting the redirect "SHOULD host a DNS TXT record"
  `cerner-client-id=…` on its eTLD+1. Without it, users see "**Unknown Application**". A project domain can do this
  once; a household install can't.
- **Tokens can be suspended when TLS or DNS changes.** "The automatic suspension of a token can occur when the TLS
  or DNS information has changed since the original authorization" (*Considerations for Handling
  `offline_access`*). With a per-install redirect, a renewed certificate or a moved dynamic-DNS name could cut
  connections. With a fixed relay address it shouldn't *(unverified that the check concerns the redirect host)*.
- **Refresh tokens.** `offline_access` "Grants a refresh token that can be used until a user or administrator
  revokes it, or until the token is revoked automatically because access is unused for more than three months."
  Access tokens last under ten minutes. A refresh "does not contain a new refresh token", so the original must be
  kept. Confidential clients get secrets through Cerner Central system accounts "If an application has its own
  server". Whether `offline_access` is granted to public patient apps is *(unverified)*. The page sets no rule, and
  forum reports differ.
- **Revocation endpoint:** `{base-url}/tenants/{id}/protocols/oauth2/profiles/smart-v1/token/revoke`.
- **PKCE** for the code flow isn't mentioned on the page *(unverified; check `code_challenge_methods_supported`,
  which SMART 2 requires)*.
- **Per-organization enablement.** "Oracle Health customers may then enable client applications to access their
  protected resources", and access ends when "The given organization terminates your application's access."
  Whether patient apps reach Oracle Health domains automatically, as Epic's auto-download does, is *(unverified)*.

### Insurers' patient-access APIs (CARIN Blue Button)

From the [CARIN IG for Blue Button 2.2.0, Security and Privacy](https://hl7.org/fhir/us/carin-bb/STU2.2/en/Security_And_Privacy_Considerations.html).

- Payers must publish SMART discovery and support standalone launch. Public clients, confidential clients and
  refresh tokens (`offline_access`) are all "MAY". UDAP dynamic registration is also "MAY". So each payer decides,
  and each runs its own developer portal, terms and review.
- Many payers run on Epic's payer platform. There, the "CMS Payer Apps" auto-download category applies (see Epic
  above).
- In practice this is payer by payer: a checklist in #92, not one rule.

**In Sanguity (#92).** An admin sets a payer up under Connected sources with the vendor type "Insurer
(patient-access API)". Its guide asks for the CARIN Blue Button scopes, `patient/ExplanationOfBenefit.read`,
`patient/Coverage.read` and `patient/Patient.read` (`.rs` on SMART v2), with `launch/patient` and `offline_access`,
and a sync then searches only the types the granted scopes cover (Coverage by `beneficiary`). Where a payer issues a
client secret, option 1 handles it: the install keeps it encrypted on its own server. Claims land on the Costs tab
(docs/importers.md, "Claims as FHIR"). Where individuals may be able to register, read November 2026; only Blue
Button's terms come from its own documentation, the rest are *(unverified)*:

| Payer | Developer portal | What we found |
|---|---|---|
| Medicare Blue Button 2.0 | [bluebutton.cms.gov/developers](https://bluebutton.cms.gov/developers/) | Self-service sandbox with synthetic beneficiaries; confidential clients only; PKCE S256. Production review impractical for one person (below). |
| Humana | [developers.humana.com](https://developers.humana.com/apis/registerapp) | A FHIR sandbox with synthetic members; apps register through its Data Exchange portal. Production approval for an individual *(unverified)*. |
| Cigna | [developer.cigna.com](https://developer.cigna.com) | Developer registration, then a sandbox client on request *(unverified: a Cigna sandbox guide shared on chat.fhir.org)*. |
| Aetna | [developerportal.aetna.com](https://developerportal.aetna.com) | Self-registration asks for a company email *(unverified: a third-party guide)*. |
| Elevance Health (Anthem) | [anthem.com/developers](https://anthem.com/developers) | Sandbox, then a separate production request form *(unverified for individuals)*. |
| UnitedHealthcare | [uhc.com, interoperability APIs](https://www.uhc.com/legal/interoperability-apis) | Its registration route wasn't confirmed: the page refused automated reading *(unverified)*. |
| VA Lighthouse Patient Health API | [developer.va.gov](https://developer.va.gov) | Sandbox by application; the client type for patient apps *(unverified)*. Not tried with Sanguity. |

None of these has been registered with or contacted by the project: the tests use the fake FHIR server
(`tests/fakefhir`, its payer fixtures) in Blue Button's place.

### Medicare Blue Button 2.0

From [Blue Button authorization](https://bluebutton.cms.gov/api-documentation/authorization),
[API documentation](https://bluebutton.cms.gov/api-documentation/) and
[production access](https://bluebutton.cms.gov/production-access/).

- **Confidential only:** "we do not support the implicit grant or public client types". The token request
  includes `client_secret`.
- **PKCE is required,** "Only `S256` is supported".
- **Refresh tokens** go only to apps in the "13 months" and "Research" categories, and "A refresh token can only be
  used one time." "One-time use" apps get 1 hour and no refresh.
- **Revocation** is `POST /revoke` with `client_id`, `client_secret` and `token`.
- **Production access:** a public privacy policy and terms of service, an application form, a one-hour demo to
  CMS, and listing in Medicare's connected-apps directory. It is written for organizations.

So: a shared secret can't ship in open-source installs or sit in a static page, and few individuals will pass
production review. Blue Button doesn't fit any option here without a server.

## The options

| | 1. Each user registers their own app | 2. Project registration + static relay (recommended) | 3. Commercial aggregator |
|---|---|---|---|
| **User** | Creates a developer account with each vendor, registers an app with their install's redirect URI, gets it through production review, pastes the client ID (and secret) into Sanguity. Repeats for each vendor and payer. | Picks a hospital or insurer, signs in, approves. Confirms their install's address once on the relay page. | Signs up with the aggregator (often with identity proofing; Health Gorilla's patient access requires NIST IAL2 *(unverified: vendor comparison pages)*), then links portals through it. |
| **Project** | Writes setup guides per vendor and keeps them current. No accounts or obligations. | Holds one registration per vendor and accepts its terms. Owns a domain and a static page; keeps them secure. Answers vendor reviews. | Contracts with an aggregator, pays per patient or per year (pricing from ~$1–2 per patient per year to ~$60k/year flat *(unverified: third-party listings)*). Needs a server-side key, which an open-source install can't hold. |
| **Epic** | Each user's app needs auto-download or each health system's approval. A LAN or tailnet redirect over HTTP is discouraged and probably refused for production *(unverified)*. | One public app, auto-downloaded to US provider organizations if it uses only qualifying APIs. Dynamic client registration gives each install its own key and long-lived access. | Through the aggregator's own Epic connections. Coverage depends on them. |
| **Oracle Health** | Wants a valid TLS certificate and a DNS TXT record for the redirect host, which most homes can't provide. Users see "Unknown Application" without them. | One registration on a domain the project controls, with the TXT record. Public patient apps' refresh tokens are unverified. | As above. |
| **Insurers / Blue Button** | Possible where a payer lets individuals register. Blue Button production review is impractical for an individual. | Works for payers that accept public clients. Not for confidential-only payers such as Blue Button. | Some aggregators cover payers. |
| **"No outside requests by default"** | Kept. Outside contact only with the vendors, after opt-in. | Kept. The relay page is loaded by the user's browser only during a sign-in they start. The vendor and the relay host see the install's address (see the threat model). | Broken in spirit: every connection goes through a third party that sees the records. It could only be an opt-in plugin, never the default. |
| **Who sees health data** | Only the vendor and the install. | Only the vendor and the install. The relay sees a code it can't use. | The aggregator sees and usually stores the records. |

**A variant of option 2, for confidential-only vendors:** a relay with a token-exchange server that holds the
secret. It would see access tokens, so it is a server holding health-data credentials. It isn't recommended as a
default. If you want Blue Button, the realistic routes are option 1 for the few willing users (sandbox is easy;
production isn't), or this variant as a separately opted-in service.

## Option 2 in detail (for later): project registration and a relay, with Epic's dynamic registration

**Why.** It is the only option where a typical user can connect a hospital in a few clicks without becoming an app
developer. Health data still goes only between the vendor and the install. Epic's auto-download covers the
per-health-system step for most US providers, and its dynamic registration removes the main weakness of a shared
public client: long-lived access is bound to a key that only the install holds. Oracle Health's naming and TLS
rules favour one stable, project-owned redirect host. Option 1 stays available as an advanced setting ("use my
own client ID"). It costs little to support: it is the same flow without the relay.

### The flow

1. **Start (at the install).** The signed-in user picks a source. The install reads the server's
   `.well-known/smart-configuration` and stores, per pending sign-in:
   - the issuer and FHIR base;
   - a PKCE `code_verifier` (encrypted with `crypto.enc`);
   - a hash of `state`, bound to the user's session, single use, expiring after 10 minutes.

   `state` is 128+ random bits (SMART needs ≥122), followed by the install's return address.
2. **Authorize (at the vendor).** The browser goes straight to the vendor's authorize endpoint. The request
   carries the project's `client_id`, `redirect_uri=https://<relay>/callback`, `state`, `code_challenge` (S256),
   `aud` = the FHIR base, and the scopes (#91 lists them). The relay isn't involved yet, so it can't change what
   the user is asked to approve.
3. **Return (at the relay).** The vendor redirects to the relay with `code` and `state`. The relay page:
   - removes `code` from the address bar and history at once;
   - reads the return address from `state` and allows only a fixed path (`/connect/return`) on that origin;
   - if this browser has confirmed that origin before (a list in the relay origin's `localStorage`), continues. If
     not, it shows the full origin and asks the user to confirm it is their own Sanguity, with a warning for a
     first-time address. Declining drops the code;
   - navigates to `<origin>/connect/return#code=…&state=…`. A fragment isn't sent to any server, so the code isn't
     in the install's proxy logs or in a `Referer`.

   The relay has no scripts but its own, a strict CSP, `Referrer-Policy: no-referrer`, no analytics, and nothing
   stored beyond the confirmed-origins list.
4. **Finish (at the install).** The session cookie is `SameSite=Strict`, so it isn't sent on the cross-site
   navigation from the relay. `/connect/return` is therefore a small static page that reads the fragment and POSTs
   it same-origin, with the session cookie and CSRF token. The server then:
   - checks `state` against its pending sign-ins for this session (hash match, unexpired, unused), then deletes it;
   - exchanges the code at the token endpoint of the **stored** issuer (never one taken from the request), with the
     stored verifier and the same `redirect_uri`;
   - checks the token response: `patient` is present, scopes are as requested or recorded as narrower (#91 reports
     gaps), and, if `openid` was requested, the `id_token` signature, its `iss` (the stored issuer) and its `aud`
     (the client ID);
   - on Epic, registers a dynamic client with a newly generated key pair and from then on uses the JWT bearer
     grant. Elsewhere it keeps the refresh token, if one was issued;
   - stores the refresh token or private key encrypted with `crypto.enc`, per profile and source (#91).

### Threat model (option 2)

**What each party sees**

| Party | Sees | Doesn't see |
|---|---|---|
| Vendor | What it always sees for a patient app: the user's sign-in, consent, IP, every FHIR call. It also sees `state`, which includes the install's return address (often a LAN IP or tailnet name). | Anything else from the install. |
| Relay page (code in the user's browser) | `code` and `state` for an instant; the confirmed-origins list in that browser. | The PKCE verifier, tokens, records, the install's session. |
| Relay host (e.g. GitHub Pages / its CDN) | Request logs for `/callback?code=…&state=…`: the user's IP, the install's address, a code that has usually been redeemed within seconds. | Everything the relay page doesn't see. |
| Network between browser and install | On plain HTTP, the code in transit. It is useless without the verifier, and redeeming it needs the session cookie and CSRF token. | The verifier, which stays on the server. |

The code is single use, short lived, and bound to the install's PKCE challenge. Anyone holding it without the
verifier can't redeem it. On Epic, long-lived access needs the install's private key as well. A copy of the
database without `auth/data_key` gives neither.

**If the relay page is replaced or compromised** (a stolen repository or hosting account, a hijacked domain, a
malicious change that gets merged):
- **Existing connections are unaffected.** Refresh and JWT bearer grants go from the install straight to the
  vendor and never touch the relay.
- **Codes captured from now on are useless** without the verifiers.
- **The real danger is new sign-ins.** A malicious relay runs in a browser that is signed in to the vendor. It
  could start a fresh authorization with an attacker's PKCE challenge and an attacker's return address, hoping the
  user approves the second consent screen as a retry. Codes from that would go to the attacker's install, and the
  attacker could redeem them. Defences:
  - keep the page small enough to review in a minute;
  - deploy only from a protected branch with signed commits, and lock down the domain and registrar (2FA, a
    registrar lock);
  - publish the page's hash in the repo, so anyone can compare it with what is served;
  - have the install say after each sign-in "you should have seen one consent screen", and list new connections
    under outside connections (#59), so an unexpected one is noticed.

  The project can also ask vendors to suspend the client ID. That stops new sign-ins for everyone, which is the
  emergency brake.
- **What can't be prevented:** a browser can't pin a page's contents, so the install can't check the relay before
  it runs. That residual trust in the project's hosting is the cost of option 2. Option 1 doesn't have it.

**Phishing via a forged `state`.** An attacker starts a sign-in at their own install, so they hold the verifier.
They send the victim the authorize link, with `state` pointing at the attacker's install. The victim sees a genuine
MyChart page asking to connect "Sanguity", a name they trust, and approves. PKCE doesn't help here, and neither do
the install's own checks, because the attacker's install doesn't run them. The relay is the only party that can
step in:
- it never forwards silently to an origin this browser hasn't confirmed;
- for a new origin it shows the full address and asks "Is this your Sanguity?";
- it offers a "this isn't mine" path that explains what happened and drops the code.

For a user who already has an install, an unknown address stands out. A new user being phished on their first sign-in
is the weakest case. The warning there is all there is, as with any OAuth consent phishing. Under option 1 the same
attack shows the attacker's own app name instead of "Sanguity", so the warning is weaker in option 2.

**Checks the install makes on return:** listed in step 4. In short:
- `state` is single use, short lived, matched by hash, and bound to the signed-in session;
- the PKCE verifier is kept server-side;
- the token endpoint comes from the stored issuer;
- `aud` is the stored FHIR base;
- an `id_token` is checked for signature, `iss` and `aud`;
- secrets are stored with `crypto.enc`.

A code that arrives with no matching pending sign-in is refused, and the refusal is logged as a security event.

**Disconnecting.**
1. The install calls the server's `revocation_endpoint`, where discovery lists one: Oracle Health has one, Blue
   Button needs the secret, and Epic's is unverified.
2. On Epic it also stops using, and deletes, the dynamic client's private key. Without the key the dynamic client
   can't get tokens again.
3. It deletes the stored refresh token in every case.
4. If the vendor has no revoke endpoint, it tells the user where to revoke access themselves (MyChart's linked apps
   page, the payer's portal).

What happens to records already pulled is the user's choice (#91).

## Deployment cases

The relay forwards by a **browser navigation**. Neither the vendor nor the relay ever connects to the install, so
the install only has to be reachable from the user's browser. The install's server needs outbound HTTPS to the
vendor's token and FHIR endpoints; that is the outside connection listed under #59.

| Install reached at | Option 2 (relay) | Option 1 (own registration) |
|---|---|---|
| **Behind a reverse proxy with a public HTTPS name** (`https://health.example.com`) | Works. The return address is the origin in `PUBLIC_ORIGINS` (or `X-Forwarded-Host`), and it must be the one the user opens, so the session cookie applies. | Works, if the vendor accepts the user's registration. Oracle Health also wants a valid certificate and a DNS TXT record. |
| **Home network, no public name** (`http://192.168.1.20:8080`, `http://nas.local`) | Works: the browser is on the same network and the navigation is top level. Chrome's Local Network Access rules aim at subresource requests from public sites to private addresses. Top-level navigations aren't covered *(unverified as of November 2026; test it before release)*. | Unlikely: Epic discourages non-native localhost/private redirects, and Oracle Health wants TLS and a public CA certificate for anything beyond development. |
| **Plain HTTP** (anywhere) | Works. The code crosses HTTP, but it is useless without the server-side verifier, and redeeming it needs the session and CSRF token. The install shows its existing plain-HTTP warning outside the home network. The relay warns too when the return address is plain HTTP on a public IP. | Vendors are unlikely to accept an HTTP redirect for production *(unverified)*. |
| **Tailscale or other private DNS** (`https://sanguity.tail1234.ts.net`, with MagicDNS and HTTPS certs) | Works. The relay never resolves the name, and only the user's browser, on the tailnet, needs to. | Registrable as a redirect URI, but the vendor can't reach it, and Oracle Health's DNS TXT step needs the user to control the eTLD+1, which they don't on `ts.net`. |
| **Several addresses for one install** (LAN and tailnet) | Each address is confirmed once in each browser. The return address is the one the sign-in started from. | One registration per address, or pick one. |

## What #32 and #91 would build under option 2 (for later)

**Project setup (one-off, by you, outside the code):**
- a domain for the relay;
- an Epic on FHIR public patient app (USCDI-only scopes, for auto-download), with dynamic client registration and
  the relay `redirect_uri`;
- an Epic payer app (`ExplanationOfBenefit`, the CMS payer category);
- an Oracle Health code Console registration, with the DNS TXT record;
- sandbox registrations of each, plus separate test apps for localhost;
- payer registrations one by one, as #92's checklist.

**#32, SMART on FHIR patient access:**
- the relay page in the repo: static, its own CSP, tests, and a published hash;
- the install's `/connect/start` and `/connect/return`, and the server-side pending sign-in table;
- discovery from `.well-known/smart-configuration`, PKCE (S256), `state` bound to the session, `aud`, and token
  exchange against the stored issuer;
- Epic dynamic client registration and the JWT bearer grant, with a per-connection key pair;
- a fallback to "use my own client ID" (option 1) under advanced settings;
- data pulled through the FHIR R4 importer.

  Done when an Epic sandbox patient connects, from an install on a LAN address, through the relay.

**#91, Connections and scheduled sync:**
- a connection record per profile and source, holding:
  - the issuer, FHIR base and patient ID;
  - the credential type (refresh token or DCR key);
  - the secret itself, encrypted with `crypto.enc`;
  - the granted scopes and the expiry the patient chose;
- getting tokens: Epic's JWT bearer grant, or a refresh, keeping Oracle Health's non-rotating refresh token and
  Blue Button's single-use one;
- a reconnect prompt when the grant ends (patient-chosen period, 3 months unused, 13 months);
- disconnecting: revoke, delete the key and token, and let the user choose what happens to the records;
- listing in Settings under outside connections (#59).

  The scheduler and incremental pulls are as the issue describes. None of it touches the relay.

**#93, the directory:** a bundled list, showing for each entry whether the project's client is enabled there (Epic
auto-download status), and "not enabled here" otherwise.

## Open questions (as asked before the decision; answered above)

1. **Option 2 or option 1?** Option 2 asks you to run the relay domain and hold vendor registrations in the
   project's name, which means accepting Epic's, Oracle's and payers' terms, privacy-policy obligations, and being
   the contact for vendor reviews. Is the project willing to be that party?
2. **SMART's "SHALL NOT forward" rule.** Ship the confined relay and disclose the design in vendor reviews, or
   first ask the SMART community and Epic whether it is acceptable? This session didn't contact anyone.
3. **Medicare Blue Button and other confidential-only payers:** leave them to option 1 (self-registration),
   run a token-exchange server as a separate opt-in, or drop them for now?
4. **Should a commercial aggregator appear at all,** as an opt-in plugin, or be ruled out?
5. **Who hosts the relay:** GitHub Pages under the project, a dedicated domain on a static host, or both? The host
   sees request logs containing install addresses.
6. **Release timing (#32's own question):** ship sign-in in Phase 2b, or wait until file imports have been in use
   for a while?
7. **Forks:** should forks reuse the project's registrations through the same relay? That is technically possible.
   Or should they be told to register their own?
