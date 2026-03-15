from datetime import datetime, timezone

from app.models.decision import DecisionV1
from app.models.bot_settings import BotSettings
from app.models.risk_verdict import RiskVerdictV1

FLAG_TRADING_DISABLED = "TRADING_DISABLED"
FLAG_RISK_ERROR = "RISK_ERROR"
FLAG_MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
FLAG_ACTIVE_SYMBOL_POSITION = "ACTIVE_SYMBOL_POSITION"
FLAG_MAX_DAILY_TRADES_PORTFOLIO = "MAX_DAILY_TRADES_PORTFOLIO"
FLAG_MAX_DAILY_TRADES_SYMBOL = "MAX_DAILY_TRADES_SYMBOL"
FLAG_DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
FLAG_EXPOSURE_LIMIT = "EXPOSURE_LIMIT"
FLAG_MARGIN_UTILIZATION = "MARGIN_UTILIZATION"


class RiskEngineV1:
    def __init__(self, trades_history_repo=None, broker_state_service=None) -> None:
        self._trades_history_repo = trades_history_repo
        self._broker_state_service = broker_state_service

    def set_trades_history_repo(self, trades_history_repo) -> None:
        self._trades_history_repo = trades_history_repo

    def set_broker_state_service(self, broker_state_service) -> None:
        self._broker_state_service = broker_state_service

    def _count_active_trades(self) -> int:
        if not self._trades_history_repo:
            return 0
        if hasattr(self._trades_history_repo, "count_active_trades"):
            return int(self._trades_history_repo.count_active_trades())
        return 0

    def _has_active_symbol_trade(self, symbol: str) -> bool:
        if not self._trades_history_repo or not symbol:
            return False
        if hasattr(self._trades_history_repo, "count_active_trades"):
            return int(self._trades_history_repo.count_active_trades(symbol=symbol)) > 0
        if hasattr(self._trades_history_repo, "get_active_trades"):
            return bool(self._trades_history_repo.get_active_trades(symbol=symbol))
        return False

    def _count_trades_since(self, since: datetime, symbol: str | None = None) -> int:
        if not self._trades_history_repo:
            return 0
        if hasattr(self._trades_history_repo, "count_trades_opened_since"):
            return int(self._trades_history_repo.count_trades_opened_since(since=since, symbol=symbol))
        return 0

    def _sum_closed_pnl_since(self, since: datetime) -> float:
        if not self._trades_history_repo:
            return 0.0
        if hasattr(self._trades_history_repo, "sum_closed_pnl_since"):
            return float(self._trades_history_repo.sum_closed_pnl_since(since=since))
        return 0.0

    def _get_broker_state(self):
        if not self._broker_state_service:
            return None
        if hasattr(self._broker_state_service, "get_state"):
            return self._broker_state_service.get_state()
        return None

    def _calculate_active_exposure(self) -> float:
        if not self._trades_history_repo:
            return 0.0
        rows = []
        if hasattr(self._trades_history_repo, "get_active_trades_full"):
            rows = self._trades_history_repo.get_active_trades_full()
        elif hasattr(self._trades_history_repo, "get_all_open_trades"):
            rows = self._trades_history_repo.get_all_open_trades()
        total = 0.0
        for row in rows or []:
            try:
                qty = abs(float(row.get("quantity") or 0.0))
                px = float(row.get("entry_price") or 0.0)
                if qty > 0 and px > 0:
                    total += qty * px
            except Exception:
                continue
        return total

    def evaluate(self, decision: DecisionV1, settings: BotSettings) -> RiskVerdictV1:
        dec_id = decision.id
        if dec_id is None:
            raise ValueError("decision.id is required for risk evaluation")

        try:
            ts = decision.ts_utc or datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            trading_enabled = getattr(settings, "trading_enabled")
            if isinstance(trading_enabled, property):
                raise RuntimeError("invalid_trading_enabled")
            if not trading_enabled:
                return RiskVerdictV1(
                    ts_utc=ts,
                    symbol=decision.symbol,
                    decision_id=dec_id,
                    signal_preview_id=decision.signal_preview_id,
                    trade_allowed=False,
                    risk_modifier=min(decision.risk_modifier, 1.0),
                    flags=decision.flags + [FLAG_TRADING_DISABLED],
                    commentary="trading disabled",
                )
            if not decision.trade_allowed:
                return RiskVerdictV1(
                    ts_utc=ts,
                    symbol=decision.symbol,
                    decision_id=dec_id,
                    signal_preview_id=decision.signal_preview_id,
                    trade_allowed=False,
                    risk_modifier=min(decision.risk_modifier, 1.0),
                    flags=list(decision.flags),
                    commentary=decision.commentary,
                )
            max_open_positions = int(getattr(settings, "max_open_positions", 0) or 0)
            if max_open_positions > 0 and self._count_active_trades() >= max_open_positions:
                return RiskVerdictV1(
                    ts_utc=ts,
                    symbol=decision.symbol,
                    decision_id=dec_id,
                    signal_preview_id=decision.signal_preview_id,
                    trade_allowed=False,
                    risk_modifier=min(decision.risk_modifier, 1.0),
                    flags=list(decision.flags) + [FLAG_MAX_OPEN_POSITIONS],
                    commentary=f"risk_blocked:max_open_positions={max_open_positions}",
                )
            if self._has_active_symbol_trade(decision.symbol):
                return RiskVerdictV1(
                    ts_utc=ts,
                    symbol=decision.symbol,
                    decision_id=dec_id,
                    signal_preview_id=decision.signal_preview_id,
                    trade_allowed=False,
                    risk_modifier=min(decision.risk_modifier, 1.0),
                    flags=list(decision.flags) + [FLAG_ACTIVE_SYMBOL_POSITION],
                    commentary=f"risk_blocked:active_symbol_trade={decision.symbol}",
                )
            day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
            max_daily_portfolio = int(getattr(settings, "max_trades_per_day_portfolio", 0) or 0)
            if max_daily_portfolio > 0 and self._count_trades_since(day_start) >= max_daily_portfolio:
                return RiskVerdictV1(
                    ts_utc=ts,
                    symbol=decision.symbol,
                    decision_id=dec_id,
                    signal_preview_id=decision.signal_preview_id,
                    trade_allowed=False,
                    risk_modifier=min(decision.risk_modifier, 1.0),
                    flags=list(decision.flags) + [FLAG_MAX_DAILY_TRADES_PORTFOLIO],
                    commentary=f"risk_blocked:max_trades_per_day_portfolio={max_daily_portfolio}",
                )
            max_daily_symbol = int(getattr(settings, "max_trades_per_day_per_symbol", 0) or 0)
            if max_daily_symbol > 0 and self._count_trades_since(day_start, symbol=decision.symbol) >= max_daily_symbol:
                return RiskVerdictV1(
                    ts_utc=ts,
                    symbol=decision.symbol,
                    decision_id=dec_id,
                    signal_preview_id=decision.signal_preview_id,
                    trade_allowed=False,
                    risk_modifier=min(decision.risk_modifier, 1.0),
                    flags=list(decision.flags) + [FLAG_MAX_DAILY_TRADES_SYMBOL],
                    commentary=f"risk_blocked:max_trades_per_day_per_symbol={decision.symbol}:{max_daily_symbol}",
                )
            broker_state = self._get_broker_state()
            if broker_state is not None:
                net_liquidation = float(getattr(broker_state, "net_liquidation", 0.0) or 0.0)
                available_funds = float(getattr(broker_state, "available_funds", 0.0) or 0.0)
                if net_liquidation > 0:
                    daily_loss_limit = float(getattr(settings, "daily_loss_limit", 0.0) or 0.0)
                    if daily_loss_limit > 0:
                        daily_realized_pnl = self._sum_closed_pnl_since(day_start)
                        if daily_realized_pnl < 0 and abs(daily_realized_pnl) >= net_liquidation * daily_loss_limit:
                            return RiskVerdictV1(
                                ts_utc=ts,
                                symbol=decision.symbol,
                                decision_id=dec_id,
                                signal_preview_id=decision.signal_preview_id,
                                trade_allowed=False,
                                risk_modifier=min(decision.risk_modifier, 1.0),
                                flags=list(decision.flags) + [FLAG_DAILY_LOSS_LIMIT],
                                commentary=f"risk_blocked:daily_loss_limit={daily_loss_limit}",
                            )
                    max_leverage = float(getattr(settings, "max_effective_leverage", 0.0) or 0.0)
                    current_exposure = self._calculate_active_exposure()
                    if max_leverage > 0 and current_exposure >= net_liquidation * max_leverage:
                        return RiskVerdictV1(
                            ts_utc=ts,
                            symbol=decision.symbol,
                            decision_id=dec_id,
                            signal_preview_id=decision.signal_preview_id,
                            trade_allowed=False,
                            risk_modifier=min(decision.risk_modifier, 1.0),
                            flags=list(decision.flags) + [FLAG_EXPOSURE_LIMIT],
                            commentary=f"risk_blocked:max_effective_leverage={max_leverage}",
                        )
                    max_margin_utilization = float(getattr(settings, "max_margin_utilization", 0.0) or 0.0)
                    if max_margin_utilization > 0:
                        utilization = max(0.0, min(1.0, 1.0 - (available_funds / net_liquidation)))
                        if utilization >= max_margin_utilization:
                            return RiskVerdictV1(
                                ts_utc=ts,
                                symbol=decision.symbol,
                                decision_id=dec_id,
                                signal_preview_id=decision.signal_preview_id,
                                trade_allowed=False,
                                risk_modifier=min(decision.risk_modifier, 1.0),
                                flags=list(decision.flags) + [FLAG_MARGIN_UTILIZATION],
                                commentary=f"risk_blocked:max_margin_utilization={max_margin_utilization}",
                            )
            modifier = decision.risk_modifier
            if modifier < 0:
                modifier = 0.0
            return RiskVerdictV1(
                ts_utc=ts,
                symbol=decision.symbol,
                decision_id=dec_id,
                signal_preview_id=decision.signal_preview_id,
                trade_allowed=True,
                risk_modifier=modifier,
                flags=list(decision.flags),
                commentary=decision.commentary,
            )
        except Exception as exc:
            ts = decision.ts_utc or datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return RiskVerdictV1(
                ts_utc=ts,
                symbol=decision.symbol,
                decision_id=dec_id,
                signal_preview_id=decision.signal_preview_id,
                trade_allowed=False,
                risk_modifier=0.0,
                flags=decision.flags + [FLAG_RISK_ERROR],
                commentary=f"risk_error:{exc.__class__.__name__}",
            )
