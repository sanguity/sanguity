"""The one way out to the network (#97, #59). Every request Sanguity's server makes to another host goes through
here, and nothing in server/ opens a socket, resolves a name or imports an HTTP client any other way
(tests/test_outside.py checks the imports).

A request needs a Permit: what it is for, the hosts it may contact, and how a contact is recorded. Permits are made
in outside.py from what the install has been told to contact (a connected source's hosts, as an admin set it up and
tested it; the host of a Health Link someone confirmed), so a host nobody connected is refused here, whatever the
calling code asks for. A permit's hosts are read again before each request: disconnecting a source, or an admin
removing it, stops the next request even in the middle of a pull.

  Permit(purpose, hosts, record=None)     hosts: a set of "host[:port]", or a function giving the current set
  permit.also(host)                       a host the confirmed request itself names; only for one-off fetches, never
                                          for sources (a Health Link's other hosts are confirmed first, #919)
  resolve(permit, url) -> [ip]            the host's addresses (getaddrinfo), for a caller that checks them (shl.py)
  connection(permit, url, purpose=None, ip=None, timeout=..., context=None) -> http.client connection, not yet
                                          connected; with ip, it connects to that address (checked beforehand)
                                          while the certificate is still checked for the host
  Errors                                  what a host that didn't answer, or broke off, raises: (OSError, HTTPException)
  why(e) -> str                           one of those in plain words, for people ("its name wasn't found")

Each host is recorded once per permit and purpose (record(host, purpose)), before anything is sent to it, so an
attempt that fails is listed too. Only the host is recorded: never a path, query, token or anything else sent.
"""
import http.client, socket, ssl, urllib.parse

Errors = (OSError, http.client.HTTPException)
DEFAULT_PORTS = {"https": 443, "http": 80}


def why(e):
    """what went wrong reaching a host, in plain words (#528: a failed test said "didn't answer (gaierror)", the name
    of a Python exception)"""
    if isinstance(e, socket.gaierror):
        return "its name wasn't found (check the address)"
    if isinstance(e, TimeoutError):
        return "it took too long to answer"
    if isinstance(e, ConnectionRefusedError):
        return "it refused the connection"
    if isinstance(e, ssl.SSLCertVerificationError):
        return "its HTTPS certificate isn't valid"
    if isinstance(e, ssl.SSLError):
        return "the secure connection failed"
    return "the connection broke off"


class Refused(Exception):
    """Sanguity won't contact this host for this purpose."""


def norm(host):
    """host[:port] lowercased, without a port of 443 or 80: how hosts are compared and listed."""
    host = str(host or "").strip().lower()
    for p in (":443", ":80"):
        if host.endswith(p):
            return host[:-len(p)]
    return host


def host_of(url):
    """The host a URL names, as listed: lowercased, with the port unless it's the scheme's default."""
    u = urllib.parse.urlsplit(url)
    try:
        port = u.port
    except ValueError:
        port = None
    name = (u.hostname or "").lower()
    if ":" in name:
        name = f"[{name}]"
    return name if port in (None, DEFAULT_PORTS.get(u.scheme.lower())) else f"{name}:{port}"


class Permit:
    def __init__(self, purpose, hosts, record=None):
        self.purpose = purpose
        self._hosts = hosts
        self._record = record
        self._also = set()
        self._seen = set()

    def hosts(self):
        hs = self._hosts() if callable(self._hosts) else self._hosts
        return {norm(h) for h in hs or ()} | self._also

    def allows(self, host):
        return norm(host) in self.hosts()

    def also(self, host):
        self._also.add(norm(host))

    def contact(self, host, purpose=None):
        """Check host and record the contact (once per purpose). Raises Refused."""
        host = norm(host)
        if host not in self.hosts():
            raise Refused(f"Sanguity won't contact {host}: it isn't one of the hosts allowed for "
                          f"{(purpose or self.purpose).lower()}.")
        key = (host, purpose or self.purpose)
        if key not in self._seen:
            self._seen.add(key)
            if self._record:
                self._record(host, purpose or self.purpose)


NOTHING = Permit("nothing", set())   # allows no host


def _split(url):
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except (ValueError, TypeError, AttributeError):
        raise Refused("That isn't an address Sanguity can contact.") from None
    scheme = u.scheme.lower()
    if scheme not in DEFAULT_PORTS or not u.hostname:
        raise Refused("That isn't an address Sanguity can contact.")
    return scheme, u.hostname, port or DEFAULT_PORTS[scheme]


def resolve(permit, url):
    """The addresses of url's host, once the permit allows the host (no contact is recorded: nothing is sent to it)."""
    _, name, port = _split(url)
    if not permit.allows(host_of(url)):
        raise Refused(f"Sanguity won't contact {host_of(url)}: it isn't one of the hosts allowed for "
                      f"{permit.purpose.lower()}.")
    infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(i[4][0] for i in infos))


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to host, connecting to an address checked beforehand; the certificate is checked for host."""
    def __init__(self, host, port, ip, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def connection(permit, url, purpose=None, ip=None, timeout=20, context=None):
    """A connection to url's host, after the permit allows it and the contact is recorded. Raises Refused."""
    scheme, name, port = _split(url)
    permit.contact(host_of(url), purpose)
    if scheme == "https":
        ctx = context or ssl.create_default_context()
        if ip:
            return _Pinned(name, port, ip, timeout, ctx)
        return http.client.HTTPSConnection(name, port, timeout=timeout, context=ctx)
    return http.client.HTTPConnection(name, port, timeout=timeout)
