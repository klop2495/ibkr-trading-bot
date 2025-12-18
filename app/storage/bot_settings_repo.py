from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.bot_settings import BotSettings, SignalsParams
from app.storage.db import SupabaseDB


class BotSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    trading_enabled: bool | None = None
    mode: str | None = None
    risk_per_trade: float | None = None
    max_open_positions: int | None = None
    max_trades_per_day_portfolio: int | None = None
    max_trades_per_day_per_symbol: int | None = None
    max_usd_side_positions: int | None = None
    daily_loss_limit: float | None = None
    loss_streak_breaker: int | None = None
    breaker_pause_hours: int | None = None
    max_effective_leverage: float | None = None
    max_margin_utilization: float | None = None
    warmup_bars_min: int | None = None
    symbols: list[str] | None = None
    signals_params: SignalsParams | dict | None = None


class BotSettingsRepo:
    table = "bot_settings"

    def __init__(self, db: SupabaseDB) -> None:
        self.db = db

    def _safe_settings(self, owner_user_id: UUID) -> BotSettings:
        return BotSettings(
            owner_user_id=owner_user_id,
            trading_enabled=False,
            symbols=[],
        )

    def get(self, owner_user_id: UUID) -> BotSettings:
        try:
            res = (
                self.db.client.table(self.table)
                .select("*")
                .eq("owner_user_id", str(owner_user_id))
                .limit(1)
                .execute()
            )
            if not res.data:
                return self._safe_settings(owner_user_id)
            row = res.data[0]
            if "symbols" in row and row["symbols"] is None:
                row["symbols"] = []
            if row.get("signals_params") is None:
                row["signals_params"] = {}
            row["owner_user_id"] = row.get("owner_user_id") or str(owner_user_id)
            return BotSettings.model_validate(row)
        except Exception:
            return self._safe_settings(owner_user_id)

    def update(self, owner_user_id: UUID, patch: Any) -> BotSettings:
        try:
            validated = patch if isinstance(patch, BotSettingsPatch) else BotSettingsPatch.model_validate(patch)
            payload = validated.model_dump(exclude_unset=True, exclude_none=True)
        except Exception:
            return self._safe_settings(owner_user_id)

        payload.pop("owner_user_id", None)
        res = (
            self.db.client.table(self.table)
            .update(payload)
            .eq("owner_user_id", str(owner_user_id))
            .execute()
        )
        if not res.data:
            return self._safe_settings(owner_user_id)
        row = res.data[0]
        if "symbols" in row and row["symbols"] is None:
            row["symbols"] = []
        if row.get("signals_params") is None:
            row["signals_params"] = {}
        row["owner_user_id"] = row.get("owner_user_id") or str(owner_user_id)
        try:
            return BotSettings.model_validate(row)
        except Exception:
            return self._safe_settings(owner_user_id)
