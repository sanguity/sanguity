"""A socket-level guard for test runs (#97): while it is installed, nothing in this process may connect to, or look
up, a host other than the test servers this process itself started (tests/fakefhir, tests/shl_server.py).

It works below server/net.py, on socket.socket itself, so it also catches code that goes around the gateway. A
blocked attempt raises ConnectionRefusedError (or socket.gaierror for a name lookup), as a host that isn't there
would, and is kept in `violations`; check() fails the run if there are any.

  netguard.install()     in setUpModule        netguard.check()    in tearDownModule (after uninstall())
"""
import ipaddress, socket, threading

_real = {}
_lock = threading.Lock()
bound = set()        # ports a socket of this process is bound to on a loopback address: the test servers
violations = []      # "host:port" or "host" (a lookup) that something tried to reach
LOCAL_NAMES = {"localhost"}


def _loopback(host):
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _bind(self, address):
    _real["bind"](self, address)
    if self.family in (socket.AF_INET, socket.AF_INET6) and _loopback(address[0]):
        with _lock:
            bound.add(self.getsockname()[1])


def _allowed(self, address):
    if self.family not in (socket.AF_INET, socket.AF_INET6):
        return True
    host, port = address[0], address[1]
    if _loopback(host) and port in bound:
        return True
    with _lock:
        violations.append(f"{host}:{port}")
    return False


def _connect(self, address):
    if not _allowed(self, address):
        raise ConnectionRefusedError(f"the test's network guard blocked a connection to {address[0]}:{address[1]}")
    return _real["connect"](self, address)


def _connect_ex(self, address):
    if not _allowed(self, address):
        return 111   # ECONNREFUSED
    return _real["connect_ex"](self, address)


def _numeric(name):
    try:
        ipaddress.ip_address(str(name).split("%")[0])
        return True
    except ValueError:
        return False


def _getaddrinfo(host, *a, **k):
    """Names other than localhost aren't looked up; an address is no lookup, and is checked when it's connected to."""
    name = host.decode() if isinstance(host, bytes) else host
    if name is not None and name.lower() not in LOCAL_NAMES and not _numeric(name):
        with _lock:
            violations.append(str(name))
        raise socket.gaierror(socket.EAI_NONAME, f"the test's network guard blocked a lookup of {name}")
    return _real["getaddrinfo"](host, *a, **k)


def install():
    if _real:
        return
    _real.update(bind=socket.socket.bind, connect=socket.socket.connect, connect_ex=socket.socket.connect_ex,
                 getaddrinfo=socket.getaddrinfo)
    socket.socket.bind, socket.socket.connect, socket.socket.connect_ex = _bind, _connect, _connect_ex
    socket.getaddrinfo = _getaddrinfo


def uninstall():
    if not _real:
        return
    socket.socket.bind, socket.socket.connect, socket.socket.connect_ex = _real["bind"], _real["connect"], _real["connect_ex"]
    socket.getaddrinfo = _real["getaddrinfo"]
    _real.clear()


def take():
    """The violations so far, cleared (for a test that makes one on purpose)."""
    with _lock:
        out = list(violations)
        violations.clear()
    return out


def check():
    if violations:
        raise AssertionError("Something contacted a host that isn't one of the test servers: " + ", ".join(sorted(set(violations))))
