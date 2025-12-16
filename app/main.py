import os
import time
from dotenv import load_dotenv

from app.storage.db import SupabaseDB
from app.storage.bot_settings_repo import BotSettingsRepo


def main():
    # Always load explicit file to avoid python-dotenv edge cases
    load_dotenv(".env")

    owner_user_id = os.getenv("BOT_OWNER_USER_ID")
    if not owner_user_id:
        raise RuntimeError("BOT_OWNER_USER_ID is not set in .env (use your Supabase Auth UID)")

    poll_seconds = int(os.getenv("BOT_SETTINGS_POLL_SECONDS", "10"))

    db = SupabaseDB()
    print("Supabase ping:", db.ping())

    repo = BotSettingsRepo(db)

    last_updated_at = None
    print("Starting control-plane polling (bot_settings). No trading executed.")

    while True:
        s = repo.get_by_owner(owner_user_id)

        if last_updated_at is None or s.updated_at != last_updated_at:
            print("=== bot_settings updated ===")
            print("trading_enabled:", s.trading_enabled)
            print("mode:", s.mode)
            print("risk_per_trade:", s.risk_per_trade)
            print("max_open_positions:", s.max_open_positions)
            print("daily_loss_limit:", s.daily_loss_limit)
            print("loss_streak_breaker:", s.loss_streak_breaker)
            print("max_effective_leverage:", s.max_effective_leverage)
            print("max_margin_utilization:", s.max_margin_utilization)
            print("symbols:", s.symbols)
            print("updated_at:", s.updated_at)
            last_updated_at = s.updated_at

        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()

# --- runtime loop entrypoint (auto-generated) ---
def _run_runtime_loop_entrypoint():
    from app.runtime.runtime_loop import run_runtime_loop
    run_runtime_loop()

if __name__ == "__main__":
    _run_runtime_loop_entrypoint()

# --- runtime loop thread (auto-generated) ---
def _start_runtime_loop_thread_once():
    import threading
    try:
        from app.runtime.runtime_loop import run_runtime_loop
    except Exception as e:
        try:
            from app.storage.db import SupabaseDB
            from app.storage.repositories import RiskEventsRepo
            db = SupabaseDB()
            RiskEventsRepo(db).insert(
                event_type="RUNTIME_LOOP_IMPORT_ERROR",
                severity="error",
                message="Failed to import runtime_loop",
                data={"error": str(e)},
            )
        except Exception:
            pass
        return

    t = threading.Thread(target=run_runtime_loop, name="runtime-loop", daemon=True)
    t.start()
# --- end runtime loop thread (auto-generated) ---
