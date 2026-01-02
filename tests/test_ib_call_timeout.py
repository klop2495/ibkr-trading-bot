import time

import pytest

from app.broker.ib_utils import IBTimeoutError, ib_call_with_timeout


def test_ib_call_timeout_raises():
    def slow_call():
        time.sleep(0.2)
        return "ok"

    with pytest.raises(IBTimeoutError):
        ib_call_with_timeout(slow_call, 0.05, description="slow_call")
