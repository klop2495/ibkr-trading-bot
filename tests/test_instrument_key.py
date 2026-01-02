from types import SimpleNamespace

from app.broker.keys import instrument_key


def test_key_builder_cfd_conid():
    contract = SimpleNamespace(secType="CFD", conId=12345)
    assert instrument_key(contract) == "CFD:12345"


def test_key_builder_missing_conid_returns_none():
    contract = SimpleNamespace(secType="CFD", conId=None)
    assert instrument_key(contract) is None
