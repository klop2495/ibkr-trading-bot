"""
Telegram notification service for binary signals.

Sends alerts when quality-filtered signals appear or disappear.
Uses Telegram Bot API via simple HTTP requests (no extra dependencies).
"""

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


class TelegramNotifier:
    """Send trading signal notifications to Telegram."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_ids: Optional[List[str]] = None,
        web_base_url: Optional[str] = None,
        enabled: bool = True,
    ):
        self.bot_token = bot_token or os.getenv("TG_BOT_TOKEN", "")
        raw_ids = chat_ids or (os.getenv("TG_ALLOWED_CHAT_IDS", "")).split(",")
        self.chat_ids = [cid.strip() for cid in raw_ids if cid.strip()]
        self.web_base_url = (web_base_url or os.getenv("WEB_PUBLIC_BASE_URL", "")).rstrip("/")
        self.enabled = enabled and bool(self.bot_token) and bool(self.chat_ids)
        self._last_error: Optional[str] = None

        if self.enabled:
            print(f"TelegramNotifier ENABLED chats={self.chat_ids}")
        else:
            missing = []
            if not self.bot_token:
                missing.append("TG_BOT_TOKEN")
            if not self.chat_ids:
                missing.append("TG_ALLOWED_CHAT_IDS")
            print(f"TelegramNotifier DISABLED missing={missing}")

    def _send_message(self, chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message via Telegram Bot API. Returns True on success."""
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if not result.get("ok"):
                    self._last_error = f"API error: {result}"
                    print(f"tg_send_error chat={chat_id} error={result}")
                    return False
                return True
        except urllib.error.URLError as exc:
            self._last_error = str(exc)
            print(f"tg_send_error chat={chat_id} error={exc}")
            return False
        except Exception as exc:
            self._last_error = str(exc)
            print(f"tg_send_error chat={chat_id} error={exc}")
            return False

    def broadcast(self, text: str) -> int:
        """Send message to all configured chat IDs. Returns count of successful sends."""
        sent = 0
        for cid in self.chat_ids:
            if self._send_message(cid, text):
                sent += 1
        return sent

    def notify_new_signals(
        self,
        new_signals: Set[str],
        all_passed: List[Dict[str, Any]],
        current_hour: int,
    ) -> int:
        """
        Send notification about new quality-filtered signals.

        Args:
            new_signals: Set of "SYMBOL:DIRECTION" strings that are new this cycle
            all_passed: List of dicts with full signal info for all currently passed pairs
            current_hour: Current UTC hour
        """
        if not self.enabled or not new_signals:
            return 0

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

        lines = [f"🟢 <b>New Binary Signal{'s' if len(new_signals) > 1 else ''}</b>  •  {now_str}"]
        lines.append("")

        for sig_key in sorted(new_signals):
            parts = sig_key.split(":")
            symbol = parts[0] if parts else sig_key
            direction = parts[1] if len(parts) > 1 else "?"

            # Find full info from all_passed
            info = next((p for p in all_passed if p.get("symbol") == symbol), None)

            arrow = "🔴 " if direction == "DOWN" else "🟢 "
            dir_text = f"<b>{direction}</b>"

            detail_parts = []
            if info:
                # Show which horizons passed
                for hz_key in ["h30", "h60"]:
                    hz = info.get(hz_key)
                    if hz and hz.get("passed"):
                        hz_label = "30m" if hz_key == "h30" else "60m"
                        conf = hz.get("confidence", "?").upper()
                        aligned = hz.get("aligned", "?")
                        total = hz.get("total", "?")
                        strength = hz.get("strength")
                        str_pct = f"{strength * 100:.0f}%" if strength else "?"
                        detail_parts.append(
                            f"  {hz_label}: {conf} • {aligned}/{total} aligned • {str_pct}"
                        )

                # Advanced filter indicators
                adv_parts = []
                adx_val = info.get("adx_value")
                if adx_val is not None:
                    adx_emoji = "🟢" if adx_val >= 25 else "🟡" if adx_val >= 20 else "🔴"
                    adv_parts.append(f"ADX {adx_val:.0f}{adx_emoji}")
                if info.get("bb_squeeze"):
                    adv_parts.append("🔴 Squeeze")
                if info.get("mtf_conflict"):
                    h4d = (info.get("mtf_h4_direction") or "?").upper()
                    adv_parts.append(f"⚠️ H4={h4d}")
                elif info.get("mtf_h4_direction"):
                    h4d = info["mtf_h4_direction"].upper()
                    adv_parts.append(f"H4={h4d}")
                if adv_parts:
                    detail_parts.append(f"  {' • '.join(adv_parts)}")

            lines.append(f"{arrow}<b>{symbol}</b>  ▸  {dir_text}")
            for dp in detail_parts:
                lines.append(dp)
            lines.append("")

        # Link to dashboard
        if self.web_base_url:
            lines.append(f'<a href="{self.web_base_url}/admin/binary-signals">📊 Open Dashboard</a>')

        text = "\n".join(lines)
        sent = self.broadcast(text)
        print(f"tg_new_signals sent={sent} signals={len(new_signals)}")
        return sent

    def notify_lost_signals(self, lost_signals: Set[str]) -> int:
        """Send notification when signals are lost (optional, less urgent)."""
        if not self.enabled or not lost_signals:
            return 0

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        symbols = ", ".join(s.split(":")[0] for s in sorted(lost_signals))

        text = (
            f"🔴 <b>Signal{'s' if len(lost_signals) > 1 else ''} Expired</b>  •  {now_str}\n\n"
            f"{symbols}\n\n"
            f"Filter conditions no longer met."
        )
        sent = self.broadcast(text)
        print(f"tg_lost_signals sent={sent} lost={len(lost_signals)}")
        return sent

    def notify_trading_hours_start(self, hour: int) -> int:
        """Notify when trading hours window opens."""
        if not self.enabled:
            return 0

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        text = (
            f"⏰ <b>Trading Window Open</b>  •  {now_str}\n\n"
            f"Active hours started. Monitoring for quality signals.\n"
        )
        if self.web_base_url:
            text += f'\n<a href="{self.web_base_url}/admin/binary-signals">📊 Open Dashboard</a>'

        return self.broadcast(text)

    def notify_trading_hours_end(self, hour: int) -> int:
        """Notify when trading hours window closes."""
        if not self.enabled:
            return 0

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        text = (
            f"🔕 <b>Trading Window Closed</b>  •  {now_str}\n\n"
            f"Outside active hours. No signals will be generated."
        )
        return self.broadcast(text)
