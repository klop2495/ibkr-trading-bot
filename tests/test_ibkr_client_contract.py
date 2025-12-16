from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from app.broker.ibkr_client import IBKRClient
from app.models.ibkr import IBKRAccountSummary, IBKRConnectionConfig, IBKRPositionsSnapshot


class FakeIB:
    def __init__(self):
        self.connected = False
        self.connect_calls = []
        self.summary_payload = []
        self.positions_payload = []

    def connect(self, host, port, clientId=None, readonly=None):
        self.connect_calls.append((host, port, clientId, readonly))
        self.connected = True

    def disconnect(self):
        self.connected = False

    def isConnected(self):
        return self.connected

    def accountSummary(self):
        return self.summary_payload

    def positions(self):
        return self.positions_payload


def test_connect_calls_ib_with_expected_args():
    fake_ib = FakeIB()
    config = IBKRConnectionConfig(host="127.0.0.1", port=4002, client_id=1)
    client = IBKRClient(config=config, ib=fake_ib)

    client.connect()

    assert fake_ib.connected is True
    assert fake_ib.connect_calls == [("127.0.0.1", 4002, 1, True)]


def test_get_account_summary_maps_values():
    fake_ib = FakeIB()
    fake_ib.summary_payload = [
        SimpleNamespace(tag="NetLiquidation", value="100000", currency="USD", account="DU123"),
        SimpleNamespace(tag="AvailableFunds", value="50000", currency="USD", account="DU123"),
    ]
    config = IBKRConnectionConfig(host="127.0.0.1", port=4002, client_id=1)
    client = IBKRClient(config=config, ib=fake_ib)

    ts = datetime.now(timezone.utc)
    summary = client.get_account_summary(ts_utc=ts)

    assert isinstance(summary, IBKRAccountSummary)
    assert len(summary.values) == 2
    assert summary.values[0].tag == "NetLiquidation"
    assert summary.values[1].value == "50000"


def test_get_positions_snapshot_maps_contract_fields_and_normalizes_symbol():
    fake_ib = FakeIB()
    fake_ib.positions_payload = [
        SimpleNamespace(
            account="DU123",
            contract=SimpleNamespace(symbol="eurusd", secType="CASH", currency="USD", exchange="IDEALPRO"),
            position=1.0,
            avgCost=1.2345,
        )
    ]
    config = IBKRConnectionConfig(host="127.0.0.1", port=4002, client_id=1)
    client = IBKRClient(config=config, ib=fake_ib)

    ts = datetime.now(timezone.utc)
    snap = client.get_positions_snapshot(ts_utc=ts)

    assert isinstance(snap, IBKRPositionsSnapshot)
    assert snap.positions[0].symbol == "EURUSD"
    assert snap.positions[0].sec_type == "CASH"
    assert snap.positions[0].position == 1.0


def test_connect_failure_raises_runtime_error():
    fake_ib = FakeIB()
    fake_ib.connect = mock.Mock(side_effect=Exception("boom"))
    config = IBKRConnectionConfig(host="127.0.0.1", port=4002, client_id=1)
    client = IBKRClient(config=config, ib=fake_ib)

    with pytest.raises(RuntimeError, match="ibkr_connect_failed"):
        client.connect()


def test_fetch_failure_raises_runtime_error():
    fake_ib = FakeIB()
    fake_ib.positions = mock.Mock(side_effect=Exception("fail"))
    config = IBKRConnectionConfig(host="127.0.0.1", port=4002, client_id=1)
    client = IBKRClient(config=config, ib=fake_ib)

    with pytest.raises(RuntimeError, match="ibkr_fetch_failed"):
        client.get_positions_snapshot(ts_utc=datetime.now(timezone.utc))
