"""A fake outbound proxy for the tests (#1159): HTTP CONNECT or SOCKS5 on 127.0.0.1, optionally asking for a username and
password. Whatever host it's asked for, it connects to `to` (a test server's port on 127.0.0.1) and keeps the target it
was asked for in `asked` ("host:port"), so a test can see that a name was handed to the proxy, never looked up here.

  p = FakeProxy("socks5", to=port, user="u", password="p").start();  p.port;  p.asked;  p.stop()
"""
import base64, socket, threading


class FakeProxy:
    def __init__(self, kind, to, user=None, password=None):
        self.kind, self.to, self.user, self.password = kind, to, user, password
        self.asked, self.refused = [], 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]

    def start(self):
        self.sock.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def stop(self):
        self.sock.close()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    @staticmethod
    def _exactly(c, n):
        data = b""
        while len(data) < n:
            chunk = c.recv(n - len(data))
            if not chunk:
                raise OSError("closed")
            data += chunk
        return data

    def _one(self, c):
        try:
            target = self._socks(c) if self.kind == "socks5" else self._connect(c)
            if target is None:
                self.refused += 1
                return
            self.asked.append(target)
            up = socket.create_connection(("127.0.0.1", self.to), 5)
            threading.Thread(target=self._pipe, args=(up, c), daemon=True).start()
            self._pipe(c, up)
        except OSError:
            pass
        finally:
            c.close()

    @staticmethod
    def _pipe(a, b):
        try:
            while (data := a.recv(65536)):
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _connect(self, c):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = c.recv(1024)
            if not chunk:
                return None
            head += chunk
        lines = head.decode().split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        if self.user:
            want = "Proxy-Authorization: Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            if want not in lines:
                c.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                return None
        if method != "CONNECT":
            c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return None
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        return target

    def _socks(self, c):
        ver, n = self._exactly(c, 2)
        methods = self._exactly(c, n)
        want = 2 if self.user else 0
        if want not in methods:
            c.sendall(b"\x05\xff")
            return None
        c.sendall(bytes([5, want]))
        if want == 2:
            self._exactly(c, 1)
            user = self._exactly(c, self._exactly(c, 1)[0]).decode()
            password = self._exactly(c, self._exactly(c, 1)[0]).decode()
            if (user, password) != (self.user, self.password):
                c.sendall(b"\x01\x01")
                return None
            c.sendall(b"\x01\x00")
        _, cmd, _, kind = self._exactly(c, 4)
        if kind == 1:
            host = socket.inet_ntop(socket.AF_INET, self._exactly(c, 4))
        elif kind == 4:
            host = socket.inet_ntop(socket.AF_INET6, self._exactly(c, 16))
        else:
            host = self._exactly(c, self._exactly(c, 1)[0]).decode()
        port = int.from_bytes(self._exactly(c, 2), "big")
        c.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01" + self.port.to_bytes(2, "big"))
        return f"{host}:{port}"
