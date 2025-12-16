import os
import sys
import time
from uuid import UUID

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
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
