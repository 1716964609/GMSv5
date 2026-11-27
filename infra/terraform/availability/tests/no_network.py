"""Block all non-loopback network access for the test process. No AWS calls.

Imported by every test module, so it is active for a single file and for the
whole suite. It does not rely on fake credentials: any attempt to resolve or
connect to a non-loopback address raises NetworkBlocked and is recorded in
ATTEMPTS. botocore's Stubber answers before any connection is made.
"""
import socket

ATTEMPTS = []
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class NetworkBlocked(OSError):
    pass


def _host(address):
    return address[0] if isinstance(address, tuple) else address


def _guard(name, host):
    if host is None or str(host) in LOOPBACK:
        return
    ATTEMPTS.append((name, str(host)))
    raise NetworkBlocked(f"network access blocked in tests: {name} {host}")


if not getattr(socket, "_gmsv5_network_blocked", False):
    _real_getaddrinfo = socket.getaddrinfo
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex
    _real_create_connection = socket.create_connection

    def getaddrinfo(host, *args, **kwargs):
        _guard("getaddrinfo", host)
        return _real_getaddrinfo(host, *args, **kwargs)

    def connect(self, address):
        _guard("connect", _host(address))
        return _real_connect(self, address)

    def connect_ex(self, address):
        _guard("connect_ex", _host(address))
        return _real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        _guard("create_connection", _host(address))
        return _real_create_connection(address, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
    socket._gmsv5_network_blocked = True
