from app.signals.engine import SignalEngine


class _FakeRiskRepo:
    def __init__(self):
        self.calls = 0
        self.last_event = None

    def insert(self, event_type, severity="info", message=None, **kwargs):
        self.calls += 1
        self.last_event = (event_type, severity, message)


def test_warn_rules_not_specified_logs_once_and_skips_duplicates():
    engine = SignalEngine()
    repo = _FakeRiskRepo()

    engine.warn_rules_not_specified(repo)
    engine.warn_rules_not_specified(repo)

    assert repo.calls == 1
    assert repo.last_event[0] == "SIGNALS_RULES_NOT_SPECIFIED"
    assert repo.last_event[1] == "warn"
