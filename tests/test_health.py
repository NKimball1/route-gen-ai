"""Routing-server liveness probe (routes/providers.brouter_reachable)."""
import socket
import threading

from routes.providers import brouter_reachable


def test_closed_port_is_down():
    assert brouter_reachable("http://127.0.0.1:1", timeout_s=0.5) is False


def test_listening_port_is_up():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    # accept in the background so the connect completes cleanly
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    try:
        assert brouter_reachable(f"http://127.0.0.1:{port}/brouter") is True
    finally:
        srv.close()


def test_unresolvable_host_is_down_not_an_exception():
    assert brouter_reachable("http://no-such-host.invalid:17777",
                             timeout_s=0.5) is False
