import time

from app.broker.connection_manager import ConnectionConfig, IBKRConnectionManager


class DummyIB:
    def __init__(self, fail_connects: int = 0):
        self._connected = False
        self._fail_connects = fail_connects
        self.connect_calls = 0

    def isConnected(self):
        return self._connected

    def connect(self, *args, **kwargs):
        self.connect_calls += 1
        if self._fail_connects > 0:
            self._fail_connects -= 1
            raise RuntimeError("connect_failed")
        self._connected = True

    def disconnect(self):
        self._connected = False

    def reqCurrentTime(self):
        return 123


def test_ensure_connected_retries(monkeypatch):
    ib = DummyIB(fail_connects=1)
    config = ConnectionConfig(max_reconnect_attempts=3, connection_timeout_seconds=1.0)
    manager = IBKRConnectionManager(config=config, ib=ib)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    assert manager.ensure_connected(max_attempts=3)
    assert ib.connect_calls >= 2


def test_ensure_connected_no_reconnect_when_healthy():
    ib = DummyIB()
    ib._connected = True
    config = ConnectionConfig(max_reconnect_attempts=1, connection_timeout_seconds=1.0)
    manager = IBKRConnectionManager(config=config, ib=ib)

    assert manager.ensure_connected(max_attempts=1)
    assert ib.connect_calls == 0
