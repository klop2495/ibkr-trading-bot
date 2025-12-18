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
from app.signals.engine_v1 import SignalEngineV1
from app.agents.runner import AgentsAggregator, aggregate_decision
from app.storage.agent_reports_repo import AgentReportsRepo
from app.storage.repositories import SignalPreviewsRepo, DecisionsRepo
from app.models.decision import DecisionV1
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
    agent_reports_repo = AgentReportsRepo(db)
    signal_previews_repo = SignalPreviewsRepo(db)
    decisions_repo = DecisionsRepo(db)
    signal_engine = SignalEngine()
    preview_engine = None
    agents_aggregator = AgentsAggregator(reports_repo=agent_reports_repo)

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
                    warmup_ready, snapshots = md_service.process(end_dt_utc=datetime.now(timezone.utc))
                    params = getattr(settings, "signals_params", None)
                    preview_engine = SignalEngineV1(params) if params else None
                    if preview_engine and snapshots:
                        previews = preview_engine.compute_previews(snapshots, warmup_ready=warmup_ready)
                        for p in previews:
                            preview_id = None
                            if params and params.is_configured():
                                try:
                                    preview_id = signal_previews_repo.insert_preview_return_id(p)
                                except Exception as exc:
                                    if not ib_warning_printed:
                                        print(f"signal_preview persist failed (continuing): {exc}", file=sys.stderr)
                                        ib_warning_printed = True
                            print(
                                f"signal_preview symbol={p.symbol} dir={p.direction} setup={p.setup_type} rr={p.rr} flags={p.flags}"
                            )
                            if params and params.is_configured() and preview_id:
                                agent_results = agents_aggregator.run(p, params, signal_preview_id=preview_id)
                                agg = aggregate_decision(p, agent_results)
                                print(
                                    f"agents_decision symbol={p.symbol} allowed={agg['trade_allowed']} rr_mod={agg['risk_modifier']} flags={agg['flags']}"
                                )
                                try:
                                    decision = DecisionV1(
                                        ts_utc=p.ts_utc,
                                        symbol=p.symbol,
                                        signal_preview_id=preview_id,
                                        trade_allowed=agg["trade_allowed"],
                                        risk_modifier=agg["risk_modifier"],
                                        flags=agg["flags"],
                                        commentary=agg.get("commentary"),
                                    )
                                    decisions_repo.insert_decision(decision)
                                except Exception as exc:
                                    if not ib_warning_printed:
                                        print(f"decision persist failed (continuing): {exc}", file=sys.stderr)
                                        ib_warning_printed = True
                except Exception as exc:
                    if not ib_warning_printed:
                        print(f"IBKR market data error (continuing without crash): {exc}", file=sys.stderr)
                        ib_warning_printed = True
        if fetcher and md_service:
            try:
                params = getattr(settings, "signals_params", None)
                configured = params.is_configured() if params else False
                if not configured:
                    signal_engine.warn_rules_not_specified(risk_events_repo, params)
                    signals_warned = signal_engine._rules_warning_logged  # internal state: log once per missing config
                else:
                    signals_warned = False
            except Exception as exc:
                if not ib_warning_printed:
                    print(f"Signals warning log failed (continuing without crash): {exc}", file=sys.stderr)
                    ib_warning_printed = True
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
