from __future__ import annotations

from typing import Optional, Any
from app.storage.db import SupabaseDB
from app.models.bot_settings import BotSettings


class BotSettingsRepo:
    table = "bot_settings"

    def __init__(self, db: SupabaseDB) -> None:
        self.db = db

    def get_by_owner(self, owner_user_id: str) -> BotSettings:
        res = (
            self.db.client.table(self.table)
            .select("*")
            .eq("owner_user_id", owner_user_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise RuntimeError(f"bot_settings row not found for owner_user_id={owner_user_id}")
        return BotSettings.model_validate(res.data[0])

    def update_by_owner(self, owner_user_id: str, patch: dict[str, Any]) -> BotSettings:
        res = (
            self.db.client.table(self.table)
            .update(patch)
            .eq("owner_user_id", owner_user_id)
            .execute()
        )
        if not res.data:
            raise RuntimeError("bot_settings update returned no rows")
        return BotSettings.model_validate(res.data[0])
