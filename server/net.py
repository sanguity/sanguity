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
                                          while the certificate is still checked for the host (https), or the
                                          Host header still names it (http: #1130, so a name checked once isn't
                                          looked up again to another address)
  Errors                                  what a host that didn't answer, or broke off, raises: (OSError, HTTPException)
  why(e) -> str                           one of those in plain words, for people ("its name wasn't found")

  open_socket(name, port, ip=None, timeout)   (#1159) a socket to name, through the outbound proxy (PROXY, set by
                                          outside.py from Needs a restart's) unless the host is local or on its bypass
                                          list: HTTP CONNECT or SOCKS5, with its username and password if it has them;
                                          a name only the proxy looks up, an address checked beforehand as it is
  proxy_answers()                         whether the proxy in force takes a connection (its settings are kept on it)

Each host is recorded once per permit and purpose (record(host, purpose)), before anything is sent to it, so an
attempt that fails is listed too. Only the host is recorded: never a path, query, token or anything else sent.
"""
import base64, http.client, ipaddress, socket, ssl, urllib.parse

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
    if isinstance(e, ProxyError):   # (#1159) its own words
        return str(e)
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
        policy(self, host)
        if host not in self.hosts():
            raise Refused(f"Sanguity won't contact {host}: it isn't one of the hosts allowed for "
                          f"{(purpose or self.purpose).lower()}.")
        key = (host, purpose or self.purpose)
        if key not in self._seen:
            self._seen.add(key)
            if self._record:
                self._record(host, purpose or self.purpose)


NOTHING = Permit("nothing", set())   # allows no host
POLICY = None   # (#1167) outside.py sets it: (purpose, host) -> why it's refused, or None
ADDRESS_POLICY = None   # (#1167's review) outside.py sets it: (ip) -> why that address is refused, or None


def policy(permit, host):
    """(#1167) Outside connections' switches and blocks, before anything else, whatever the permit says: the master
    switch, a blocked host, the permit's kind switched off. Read at each request (outside.refusal)."""
    if POLICY is None:   # (outside.py not imported yet: it sets it as it is)
        from . import outside   # noqa: F401
    why = POLICY(permit.purpose, host)
    if why:
        raise Refused(why)


def address_policy(ips):
    """(#1167's review) The addresses a name resolved to (or one checked beforehand): refused when one is blocked."""
    if ADDRESS_POLICY is None:
        from . import outside   # noqa: F401
    for ip in ips:
        why = ADDRESS_POLICY(ip)
        if why:
            raise Refused(why)


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
    policy(permit, host_of(url))
    if not permit.allows(host_of(url)):
        raise Refused(f"Sanguity won't contact {host_of(url)}: it isn't one of the hosts allowed for "
                      f"{permit.purpose.lower()}.")
    infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(i[4][0] for i in infos))
    address_policy(ips)
    return ips


# ---------------------------------------------------------------- (#1159) the outbound proxy

class ProxyError(OSError):
    """The outbound proxy didn't take the connection: said in plain words (never its password)."""


class Proxy:
    """An HTTP (CONNECT) or SOCKS5 proxy every outside connection goes through, but the local ones and the bypass
    list's. The permit, the contact's record and an address checked beforehand apply as without one: the proxy is
    asked for that address then (and the certificate still checked for the host); else for the name, which only the
    proxy looks up."""
    def __init__(self, scheme, host, port, user="", password="", bypass=()):
        self.scheme, self.host, self.port, self.user, self.password = scheme, host, int(port), user or "", password or ""
        self.bypass = [b for b in bypass if b]

    def bypasses(self, name, ip=None):
        name = (name or "").lower().strip("[]").rstrip(".")
        for addr in (name, ip):
            try:
                a = ipaddress.ip_address(str(addr).split("%")[0]) if addr else None
            except ValueError:
                a = None
            if a is not None:
                if a.is_private or a.is_loopback or a.is_link_local:
                    return True
                for b in self.bypass:
                    try:
                        if a in ipaddress.ip_network(b, strict=False):
                            return True
                    except ValueError:
                        continue
        if name == "localhost" or "." not in name or name.endswith(LOCAL_SUFFIXES):
            return True
        return any(name == b or (b.startswith(".") and (name.endswith(b) or name == b[1:])) for b in self.bypass)


LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa")
PROXY = None   # outside.py sets it: () -> the Proxy in force, or None; read before each connection


def proxy_for(name, ip=None):
    p = PROXY() if PROXY else None
    return None if p is None or p.bypasses(name, ip) else p


def _read_until(sock, end, most=16384):
    data = b""
    while end not in data:
        chunk = sock.recv(1024)
        if not chunk:
            raise ProxyError("the outbound proxy broke off")
        data += chunk
        if len(data) > most:
            raise ProxyError("the outbound proxy's answer was too long")
    return data


def _exactly(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ProxyError("the outbound proxy broke off")
        data += chunk
    return data


def _http_connect(sock, p, host, port):
    target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    head = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
    if p.user:
        head += "Proxy-Authorization: Basic " + base64.b64encode(f"{p.user}:{p.password}".encode()).decode() + "\r\n"
    sock.sendall((head + "\r\n").encode())
    status = _read_until(sock, b"\r\n\r\n").split(b"\r\n", 1)[0].split()
    code = status[1] if len(status) > 1 else b""
    if code == b"407":
        raise ProxyError("the outbound proxy wants a username and password it wasn't given")
    if code != b"200":
        raise ProxyError(f"the outbound proxy refused it ({code.decode(errors='replace')[:3] or 'no answer'})")


def _socks5(sock, p, host, port):
    sock.sendall(b"\x05\x02\x00\x02" if p.user else b"\x05\x01\x00")
    ver, method = _exactly(sock, 2)
    if ver != 5 or method == 0xFF:
        raise ProxyError("the outbound proxy (SOCKS5) took none of the ways to sign in Sanguity offers")
    if method == 2:
        if not p.user:
            raise ProxyError("the outbound proxy wants a username and password it wasn't given")
        u, w = p.user.encode()[:255], p.password.encode()[:255]
        sock.sendall(bytes([1, len(u)]) + u + bytes([len(w)]) + w)
        if _exactly(sock, 2)[1] != 0:
            raise ProxyError("the outbound proxy refused its username and password")
    try:
        a = ipaddress.ip_address(host)
        addr = (b"\x01" if a.version == 4 else b"\x04") + a.packed
    except ValueError:
        name = host.encode("idna")[:255]
        addr = b"\x03" + bytes([len(name)]) + name   # the proxy looks the name up, never this server
    sock.sendall(b"\x05\x01\x00" + addr + port.to_bytes(2, "big"))
    _, rep, _, kind = _exactly(sock, 4)
    _exactly(sock, {1: 4, 4: 16}.get(kind) or _exactly(sock, 1)[0])
    _exactly(sock, 2)
    if rep != 0:
        raise ProxyError({2: "the outbound proxy's rules don't allow it", 3: "the outbound proxy can't reach that network",
                          4: "the outbound proxy can't reach that host", 5: "the host refused the outbound proxy",
                          6: "it took too long to answer the outbound proxy"}.get(rep, "the outbound proxy refused it"))


def open_socket(name, port, ip=None, timeout=20):
    """A connected socket to name (at ip, checked beforehand, when given), through the outbound proxy unless it's
    bypassed. Only connection() and probe() use it, once their permit allowed the host."""
    p = proxy_for(name, ip)
    if ip:
        address_policy([ip])
    if p is None:
        if ip:
            return socket.create_connection((ip, port), timeout)
        # (#1167's review) looked up here, each address checked against the blocks, and connected to as checked
        ips = list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)))
        address_policy(ips)
        err = None
        for a in ips:
            try:
                return socket.create_connection((a, port), timeout)
            except OSError as e:
                err = e
        raise err or OSError(f"{name} has no address")
    sock = socket.create_connection((p.host, p.port), timeout)
    try:
        (_socks5 if p.scheme == "socks5" else _http_connect)(sock, p, ip or name, port)
    except BaseException:
        sock.close()
        raise
    return sock


def proxy_answers(timeout=5):
    """(#1159) Whether the outbound proxy in force takes a connection (nothing is sent to it): tried and kept on that."""
    p = PROXY() if PROXY else None
    if p is None:
        return True
    try:
        socket.create_connection((p.host, p.port), timeout).close()
        return True
    except OSError:
        return False


class _Conn(http.client.HTTPSConnection):
    """HTTPS to host (at an address checked beforehand, when given), through the outbound proxy unless bypassed; the
    certificate is checked for host."""
    def __init__(self, host, port, ip, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        self.sock = self._context.wrap_socket(open_socket(self.host, self.port, self._ip, self.timeout), server_hostname=self.host)


class _ConnHTTP(http.client.HTTPConnection):
    """Plain HTTP to host, as _Conn; the Host header still names host."""
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = open_socket(self.host, self.port, self._ip, self.timeout)


def connection(permit, url, purpose=None, ip=None, timeout=20, context=None):
    """A connection to url's host, after the permit allows it and the contact is recorded. Raises Refused."""
    scheme, name, port = _split(url)
    permit.contact(host_of(url), purpose)
    if ip:   # (#1167's review) an address checked beforehand: not a blocked one
        address_policy([ip])
    if scheme == "https":
        return _Conn(name, port, ip, timeout, context or ssl.create_default_context())
    return _ConnHTTP(name, port, ip, timeout)


def probe(permit, url, timeout=10):
    """(#1167) Whether url's host is reachable, step by step: [{"step": "name" | "connect" | "tls", "ok", "said"}],
    stopping at the first that fails. The name is looked up here, or by the outbound proxy; nothing is sent beyond the
    TLS handshake (for https). Under the permit, its contact recorded first. Raises Refused."""
    scheme, name, port = _split(url)
    permit.contact(host_of(url))
    steps, p = [], proxy_for(name)

    def step(what, ok, said):
        steps.append({"step": what, "ok": ok, "said": said})
        return ok
    if p:
        step("name", True, f"looked up by the outbound proxy ({p.host}:{p.port})")
    else:
        try:
            ips = list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)))
        except Errors as e:
            step("name", False, why(e))
            return steps
        step("name", True, ", ".join(ips[:4]))
    try:
        sock = open_socket(name, port, None, timeout)
    except Errors as e:
        step("connect", False, why(e))
        return steps
    try:
        step("connect", True, f"port {port}" + (" through the outbound proxy" if p else ""))
        if scheme == "https":
            try:
                tls = ssl.create_default_context().wrap_socket(sock, server_hostname=name)
                step("tls", True, f"{tls.version()}, its certificate valid for {name}")
                sock = tls
            except Errors as e:
                step("tls", False, why(e))
    finally:
        sock.close()
    return steps
