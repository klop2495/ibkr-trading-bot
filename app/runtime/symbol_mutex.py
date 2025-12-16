from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict


class SymbolMutex:
    def __init__(self) -> None:
        self._locks: Dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def _get(self, symbol: str) -> threading.RLock:
        key = symbol or "__GLOBAL__"
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.RLock()
            return self._locks[key]

    @contextmanager
    def lock(self, symbol: str):
        lk = self._get(symbol)
        lk.acquire()
        try:
            yield
        finally:
            lk.release()
