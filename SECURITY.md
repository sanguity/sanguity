# Security

Sanguity holds medical records, so security reports are taken seriously.

**Please don't open a public issue for a vulnerability.** Report it privately instead:

- GitHub: **Security → Report a vulnerability** on this repository, or
- email **security@sanguity.com**

Include what you found, how to reproduce it, and which version or commit you tested. You'll get an
acknowledgement within a week. Please give a reasonable time to ship a fix before disclosing publicly.

## Scope

Sanguity is self-hosted: each install is run by its owner, and there is no hosted service. In scope are the
code in this repository and its default Docker setup (login and sessions, document serving, the watch
receiver, the build scripts). Weaknesses in a particular install's reverse proxy, network or host are not,
but tell us if our defaults make a common setup unsafe.
