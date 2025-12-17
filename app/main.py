import os
import sys
import time
from uuid import UUID
from datetime import datetime, timezone

from app.broker.ibkr_client import IBKRClient
from app.market_data.ibkr_fetcher import IBKRFetcher
from app.market_data.service import MarketDataService
from app.models.ibkr import IBKRConnectionConfig
from app.signals.engine import SignalEngine
from app.storage.repositories import RiskEventsRepo
from app.storage.bot_settings_repo import BotSettingsRepo
from app.storage.db import SupabaseDB


def main():
    owner_user_id_raw = os.getenv("BOT_OWNER_USER_ID")
    if not owner_user_id_raw:
        print("BOT_OWNER_USER_ID is not set; exiting", file=sys.stderr)
        sys.exit(1)
    try:
        owner_user_id = UUID(owner_user_id_raw)
    except Exception:
        print("BOT_OWNER_USER_ID is invalid UUID; exiting", file=sys.stderr)
        sys.exit(1)

    try:
        poll_seconds = int(os.getenv("BOT_SETTINGS_POLL_SECONDS", "10"))
    except Exception:
        poll_seconds = 10

    db = SupabaseDB()
    print("Supabase ping:", db.ping())

    repo = BotSettingsRepo(db)
    risk_events_repo = RiskEventsRepo(db)
    signal_engine = SignalEngine()

    # Market data wiring (env-gated)
    ibkr_enabled = os.getenv("IBKR_ENABLED") == "1"
    fetcher = None
    md_service = None
    md_last_symbols = None
    md_last_warmup = None
    signals_warned = False
    ib_warning_printed = False
    if not ibkr_enabled:
        print("IBKR market data disabled (IBKR_ENABLED != 1)")
    else:
        ib_host = os.getenv("IBKR_HOST")
        ib_port = os.getenv("IBKR_PORT")
        ib_client_id = os.getenv("IBKR_CLIENT_ID")
        missing = []
        ib_port_int = None
        ib_client_int = None
        if not ib_host:
            missing.append("IBKR_HOST")
        if not ib_port:
            missing.append("IBKR_PORT")
        if not ib_client_id:
            missing.append("IBKR_CLIENT_ID")
        try:
            ib_port_int = int(ib_port) if ib_port else None
            ib_client_int = int(ib_client_id) if ib_client_id else None
        except Exception:
            missing.append("IBKR_PORT/IBKR_CLIENT_ID invalid")

        if missing:
            print(f"IBKR market data disabled: missing/invalid {', '.join(missing)}")
        else:
            try:
                ib_config = IBKRConnectionConfig(host=ib_host, port=ib_port_int, client_id=ib_client_int)
                ib_client = IBKRClient(config=ib_config)
                ib_client.connect()
                fetcher = IBKRFetcher(ib=ib_client.ib)
                print("IBKR market data enabled and connected")
            except Exception as exc:
                print(f"IBKR market data disabled: connect failed ({exc})")
                fetcher = None

    last_updated_at = None
    settings_snapshot = None
    last_trading_enabled = None
    print("Starting control-plane polling (bot_settings). No trading executed.")

    while True:
        settings = repo.get(owner_user_id)
        if settings_snapshot is None or settings.updated_at != last_updated_at:
            settings_snapshot = settings
            last_updated_at = settings.updated_at
            print("=== bot_settings updated ===")
            print("trading_enabled:", settings.trading_enabled)
            print("mode:", settings.mode)
            print("symbols:", settings.symbols)
        if settings.trading_enabled != last_trading_enabled:
            last_trading_enabled = settings.trading_enabled
            if not settings.trading_enabled:
                print("trading disabled by settings; no new entries will be processed")
        if fetcher:
            if md_service is None or md_last_symbols != settings.symbols or md_last_warmup != settings.warmup_bars_min:
                md_last_symbols = settings.symbols
                md_last_warmup = settings.warmup_bars_min
                md_service = MarketDataService(
                    symbols=settings.symbols,
                    timeframes=["M15", "H1", "H4"],
                    warmup_bars_min=settings.warmup_bars_min,
                    fetcher=fetcher,
                )
            if md_service:
                try:
                    md_service.process(end_dt_utc=datetime.now(timezone.utc))
                except Exception as exc:
                    if not ib_warning_printed:
                        print(f"IBKR market data error (continuing without crash): {exc}", file=sys.stderr)
                        ib_warning_printed = True
        if fetcher and md_service and not signals_warned:
            try:
                signal_engine.warn_rules_not_specified(risk_events_repo)
                signals_warned = True
            except Exception as exc:
                if not ib_warning_printed:
                    print(f"Signals warning log failed (continuing without crash): {exc}", file=sys.stderr)
                    ib_warning_printed = True
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
