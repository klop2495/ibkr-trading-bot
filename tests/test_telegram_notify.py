"""Quick test for TelegramNotifier — sends a test message."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.notifications.telegram import TelegramNotifier


def test_send():
    notifier = TelegramNotifier()
    if not notifier.enabled:
        print("Notifier disabled — set TG_BOT_TOKEN and TG_ALLOWED_CHAT_IDS")
        return

    # Test basic message
    sent = notifier.broadcast("🧪 <b>Test message</b>\n\nTelegram notifications are working!")
    print(f"Basic test: sent={sent}")

    # Test signal notification
    sent = notifier.notify_new_signals(
        new_signals={"EURUSD:DOWN", "USDCAD:DOWN"},
        all_passed=[
            {
                "symbol": "EURUSD",
                "h30": {"passed": False, "confidence": "high", "aligned": 3, "total": 6, "strength": 1.0},
                "h60": {"passed": True, "confidence": "medium", "aligned": 4, "total": 7, "strength": 0.6},
            },
            {
                "symbol": "USDCAD",
                "h30": {"passed": False, "confidence": "medium", "aligned": 3, "total": 6, "strength": 0.5},
                "h60": {"passed": True, "confidence": "medium", "aligned": 4, "total": 7, "strength": 0.6},
            },
        ],
        current_hour=22,
    )
    print(f"Signal test: sent={sent}")


if __name__ == "__main__":
    test_send()
