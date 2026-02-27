# Signal Lifecycle Integration Guide
# ===================================
# 
# Files created:
#   1. app/forecast/signal_lifecycle.py  — lifecycle manager (DONE)
#   2. This guide
#
# Integration steps for app/main.py:

# STEP 1: Add import at top of main.py (around line 42, after other forecast imports)
# ---
# from app.forecast.signal_lifecycle import SignalLifecycleManager
# ---

# STEP 2: In main() function, after forecast_engine initialization, add:
# ---
# signal_lifecycle = SignalLifecycleManager()
# print(f"SignalLifecycleManager: enabled={signal_lifecycle.enabled} blacklist={sorted(signal_lifecycle.blacklist_hours)}")
# ---

# STEP 3: In the forecast generation section (Phase 8), BEFORE writing alt2/alt3 signals to DB,
# add lifecycle check. Find the section that does:
#   for fc in forecasts:
#     for strat, attr in [("alt2", "h30_alt2_direction"), ("alt3", "h30_alt3_direction")]:
#       alt_dir = getattr(fc, attr, None)
#       if alt_dir:
#         tg_notifier.notify_alt_signal(...)
#
# BEFORE that loop, add filtering:
# ---
# # Signal Lifecycle: filter out signals that shouldn't be emitted
# now_utc = datetime.now(timezone.utc)
# for fc in forecasts:
#     alt2_dir = getattr(fc, "h30_alt2_direction", None)
#     if alt2_dir:
#         allowed, reason = signal_lifecycle.can_signal(fc.symbol, alt2_dir, now_utc)
#         if not allowed:
#             # Block this signal — set to None so it won't be recorded
#             fc.h30_alt2_direction = None
#             logger.info(f"lifecycle_blocked {fc.symbol} alt2={alt2_dir} reason={reason}")
#         else:
#             signal_lifecycle.record_signal(fc.symbol, alt2_dir, now_utc)
#     # Same for alt3
#     alt3_dir = getattr(fc, "h30_alt3_direction", None)
#     if alt3_dir:
#         allowed, reason = signal_lifecycle.can_signal(fc.symbol, alt3_dir, now_utc)
#         if not allowed:
#             fc.h30_alt3_direction = None
#         # Note: alt3 shares lifecycle with alt2 for same symbol
# ---

# STEP 4: In the forecast verification section, after verifier.verify_pending(),
# feed results back to lifecycle manager.
# Find: verified = forecast_verifier.verify_pending(...)
# After that, add:
# ---
# # Feed verification results to lifecycle manager
# if verified > 0 and signal_lifecycle.enabled:
#     try:
#         # Get recently verified alt2 results
#         for sym in (getattr(settings, "symbols", None) or []):
#             latest = forecast_repo.get_latest(sym, limit=1) if forecast_repo else []
#             if latest:
#                 row = latest[0]
#                 alt2_correct = row.get("h30_alt2_correct")
#                 alt2_dir = row.get("h30_alt2_direction")
#                 if alt2_dir and alt2_correct is not None:
#                     state = signal_lifecycle._get_state(sym)
#                     if state.pending_direction == alt2_dir:
#                         signal_lifecycle.record_verification(sym, correct=bool(alt2_correct), now=datetime.now(timezone.utc))
#     except Exception as lc_exc:
#         logger.warning(f"lifecycle_verify_sync_error: {lc_exc}")
# ---

# STEP 5: Add lifecycle status to dashboard.py
# In the FastAPI app, add endpoint:
# ---
# @app.get("/api/forecast/lifecycle")
# def get_lifecycle_status():
#     if not hasattr(app.state, "signal_lifecycle"):
#         return {"enabled": False}
#     return app.state.signal_lifecycle.get_status()
# ---
# And set app.state.signal_lifecycle = signal_lifecycle in main()

# STEP 6: Add blacklist hours label to forecast DB rows
# In forecast_repo.insert_batch(), or in engine.py ForecastResult,
# add a field "hour_status" = "active" or "blacklisted" based on UTC hour.
# This allows the frontend to display the label.

# ENV VARS (add to .env on VPS):
# SIGNAL_LIFECYCLE_ENABLED=1
# SIGNAL_COOLDOWN_1=30
# SIGNAL_COOLDOWN_2=60
# SIGNAL_COOLDOWN_3=session
# SIGNAL_BLACKLIST_HOURS=06,08,13,14,18
