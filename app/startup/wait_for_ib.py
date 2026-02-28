from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from datetime import datetime, timezone

from ib_insync import IB


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _tcp_check(host: str, port: int, timeout_s: float = 2.0) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout_s)
    try:
        sock.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _ib_probe(host: str, port: int, client_id: int, timeout_s: float = 5.0) -> bool:
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout_s)
        ib.reqCurrentTime()
        return True
    except Exception:
        return False
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for IB Gateway API readiness")
    parser.add_argument("--once", action="store_true", help="single probe and exit")
    args = parser.parse_args()

    enabled = os.getenv("IB_STARTUP_WAIT_ENABLED", "1") == "1"
    if not enabled:
        print("wait_for_ib: disabled via IB_STARTUP_WAIT_ENABLED=0")
        return 0

    host = os.getenv("IBKR_HOST") or os.getenv("IB_GATEWAY_HOST", "ib-gateway")
    port = _env_int("IBKR_PORT", _env_int("IB_GATEWAY_PORT", 4004))
    timeout_s = _env_float("IB_STARTUP_WAIT_TIMEOUT_SECONDS", 180.0)
    interval_s = _env_float("IB_STARTUP_WAIT_PROBE_INTERVAL_SECONDS", 2.0)
    connect_timeout_s = _env_float("IB_STARTUP_WAIT_CONNECT_TIMEOUT_SECONDS", 5.0)
    base_client_id = _env_int("IB_STARTUP_WAIT_CLIENT_ID", 199)

    if args.once:
        cid = base_client_id
        tcp_ok = _tcp_check(host, port)
        ib_ok = _ib_probe(host, port, cid, timeout_s=connect_timeout_s) if tcp_ok else False
        print(f"wait_for_ib once: tcp={tcp_ok} ib={ib_ok} host={host} port={port} clientId={cid}")
        return 0 if ib_ok else 1

    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        cid = base_client_id + (attempt % 100)

        tcp_ok = _tcp_check(host, port)
        if not tcp_ok:
            print(
                f"wait_for_ib attempt={attempt} status=tcp_not_ready host={host} port={port} "
                f"ts={datetime.now(timezone.utc).isoformat()}"
            )
            time.sleep(interval_s)
            continue

        ib_ok = _ib_probe(host, port, cid, timeout_s=connect_timeout_s)
        if ib_ok:
            print(
                f"wait_for_ib attempt={attempt} status=ready host={host} port={port} clientId={cid} "
                f"ts={datetime.now(timezone.utc).isoformat()}"
            )
            return 0

        print(
            f"wait_for_ib attempt={attempt} status=ib_not_ready host={host} port={port} clientId={cid} "
            f"ts={datetime.now(timezone.utc).isoformat()}"
        )
        time.sleep(interval_s)

    print(f"wait_for_ib timeout host={host} port={port} timeout_s={timeout_s}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

