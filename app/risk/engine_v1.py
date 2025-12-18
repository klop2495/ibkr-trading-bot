from datetime import datetime, timezone

from app.models.decision import DecisionV1
from app.models.bot_settings import BotSettings
from app.models.risk_verdict import RiskVerdictV1

FLAG_TRADING_DISABLED = "TRADING_DISABLED"
FLAG_RISK_ERROR = "RISK_ERROR"


class RiskEngineV1:
    def evaluate(self, decision: DecisionV1, settings: BotSettings) -> RiskVerdictV1:
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
                    decision_id=decision.id or decision.signal_preview_id,
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
                    decision_id=decision.id or decision.signal_preview_id,
                    signal_preview_id=decision.signal_preview_id,
                    trade_allowed=False,
                    risk_modifier=min(decision.risk_modifier, 1.0),
                    flags=list(decision.flags),
                    commentary=decision.commentary,
                )
            modifier = decision.risk_modifier
            if modifier < 0:
                modifier = 0.0
            return RiskVerdictV1(
                ts_utc=ts,
                symbol=decision.symbol,
                decision_id=decision.id or decision.signal_preview_id,
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
                decision_id=decision.id or decision.signal_preview_id,
                signal_preview_id=decision.signal_preview_id,
                trade_allowed=False,
                risk_modifier=0.0,
                flags=decision.flags + [FLAG_RISK_ERROR],
                commentary=f"risk_error:{exc.__class__.__name__}",
            )
