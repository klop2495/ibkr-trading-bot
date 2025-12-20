from typing import Any, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.bot_settings import BotSettings, SignalsParams
from app.storage.db import SupabaseDB
from app.storage.repositories import RiskEventsRepo


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

    def __init__(self, db: SupabaseDB, risk_events_repo: RiskEventsRepo | None = None) -> None:
        self.db = db
        self.risk_events_repo = risk_events_repo
        self.last_found_row: bool | None = None

    def _safe_settings(self, owner_user_id: UUID) -> BotSettings:
        return BotSettings(
            owner_user_id=owner_user_id,
            trading_enabled=False,
            symbols=[],
        )

    def _log_risk_event(self, event_type: str, severity: str, owner: str, message: str, data: dict | None = None) -> None:
        if not self.risk_events_repo:
            return
        try:
            payload = {"owner_user_id": owner}
            if data:
                payload.update(data)
            self.risk_events_repo.insert(
                event_type=event_type,
                severity=severity,
                message=message,
                data=payload,
            )
        except Exception:
            # best-effort; don't crash repo reads
            pass

    def _normalize_owner(self, owner_user_id: Union[str, UUID]) -> tuple[str | None, UUID | None]:
        try:
            if isinstance(owner_user_id, UUID):
                return str(owner_user_id), owner_user_id
            owner_str = str(owner_user_id).strip()
            owner_uuid = UUID(owner_str)
            return str(owner_uuid), owner_uuid
        except Exception:
            return None, None

    def get(self, owner_user_id: Union[str, UUID]) -> BotSettings:
        owner, owner_uuid = self._normalize_owner(owner_user_id)
        if not owner or not owner_uuid:
            self._log_risk_event("BOT_SETTINGS_READ_ERROR", "error", str(owner_user_id), "invalid owner_user_id")
            return self._safe_settings(owner_user_id if isinstance(owner_user_id, UUID) else UUID(int=0))
        self.last_found_row = False
        try:
            res = (
                self.db.client.table(self.table)
                .select("*")
                .eq("owner_user_id", owner)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            self._log_risk_event("BOT_SETTINGS", "ERROR", f"supabase error: {exc}", {"owner_user_id": owner})
            return self._safe_settings(owner_uuid)

        rows = (getattr(res, "data", None) or [])
        self.last_found_row = len(rows) > 0

        if not rows:
            print(f"bot_settings for owner {owner} not found; returning defaults (rows=0)")
            self._log_risk_event(
                "BOT_SETTINGS",
                "WARN",
                "bot_settings not found",
                {"owner_user_id": owner},
            )
            return self._safe_settings(owner_uuid)

        row = rows[0]
        if "symbols" in row and row["symbols"] is None:
            row["symbols"] = []
        if row.get("signals_params") is None:
            row["signals_params"] = {}
        # strip DB-only keys
        row.pop("id", None)
        row.pop("created_at", None)

        # sanitize signals_params shape/case quirks before validation
        if isinstance(row.get("signals_params"), dict):
            sp = row["signals_params"]
            structure = sp.get("structure")
            if isinstance(structure, dict):
                sl_mode = structure.get("sl_buffer_mode")
                if isinstance(sl_mode, str):
                    structure["sl_buffer_mode"] = sl_mode.upper()
            m15 = sp.get("m15_confirm")
            if isinstance(m15, dict) and "rule" in m15:
                m15.pop("rule", None)
        row["owner_user_id"] = row.get("owner_user_id") or owner
        try:
            settings = BotSettings.model_validate(row)
            return settings
        except Exception as exc:
            print(f"bot_settings validation error owner={owner} error={exc}")
            self._log_risk_event(
                "BOT_SETTINGS",
                "ERROR",
                "validation error",
                {"owner_user_id": owner, "error": str(exc)},
            )
            return self._safe_settings(owner_uuid)

    def update(self, owner_user_id: Union[str, UUID], patch: Any) -> BotSettings:
        owner, owner_uuid = self._normalize_owner(owner_user_id)
        if not owner or not owner_uuid:
            self._log_risk_event("BOT_SETTINGS_READ_ERROR", "error", str(owner_user_id), "invalid owner_user_id")
            return self._safe_settings(owner_user_id if isinstance(owner_user_id, UUID) else UUID(int=0))
        try:
            validated = patch if isinstance(patch, BotSettingsPatch) else BotSettingsPatch.model_validate(patch)
            payload = validated.model_dump(exclude_unset=True, exclude_none=True)
        except Exception:
            return self._safe_settings(owner_uuid)

        payload.pop("owner_user_id", None)
        res = (
            self.db.client.table(self.table)
            .update(payload)
            .eq("owner_user_id", owner)
            .execute()
        )
        if not res.data:
            return self._safe_settings(owner_uuid)
        row = res.data[0]
        if "symbols" in row and row["symbols"] is None:
            row["symbols"] = []
        if row.get("signals_params") is None:
            row["signals_params"] = {}
        row["owner_user_id"] = row.get("owner_user_id") or owner
        try:
            return BotSettings.model_validate(row)
        except Exception:
            return self._safe_settings(owner_uuid)
