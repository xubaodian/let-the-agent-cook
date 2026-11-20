import selectors
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv6Address, ip_address
from urllib.parse import urlsplit

from agent_cook.sandbox.urls import public_https_origin


def _connect_public(host: str, port: int) -> socket.socket:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for family, kind, protocol, _, address in addresses:
        ip = ip_address(address[0])
        if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            continue
        upstream = socket.socket(family, kind, protocol)
        try:
            upstream.settimeout(8)
            upstream.connect(address)
        except OSError:
            upstream.close()
        else:
            return upstream
    raise OSError("Could not connect to a public address for the requested site.")


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    client.settimeout(8)
    with selectors.DefaultSelector() as selector:
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)
        while ready := selector.select(timeout=15):
            for key, _ in ready:
                data = key.fileobj.recv(65536)
                if not data:
                    return
                key.data.sendall(data)


@contextmanager
def browser_proxy(origins: frozenset[str]) -> Iterator[str]:
    """Limit the browser's HTTPS connections to explicitly approved public origins."""
    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self) -> None:
            try:
                origin = public_https_origin(f"https://{self.path}", origin_only=True)
            except ValueError:
                self.send_error(403)
                return
            if origin not in origins:
                self.send_error(403)
                return
            target = urlsplit(origin)
            try:
                upstream = _connect_public(target.hostname, target.port or 443)
            except OSError:
                self.send_error(502)
                return
            with upstream:
                self.send_response_only(200, "Connection Established")
                self.end_headers()
                try:
                    _relay(self.connection, upstream)
                except OSError:
                    return

        def log_message(self, *_args: object) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=2)
