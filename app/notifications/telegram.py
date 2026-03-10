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
        self.alt_only = os.getenv("TG_ALT_ONLY", "1") != "0"
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
        Send one Telegram message PER new signal.

        Each message contains: symbol, direction, price, time,
        horizon details, and advanced filter indicators.
        Only quality-confirmed signals are sent.
        """
        if self.alt_only:
            return 0
        if not self.enabled or not new_signals:
            return 0

        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Paris"))
        # CET (winter) / CEST (summer)
        tz_abbr = "CEST" if now.dst() else "CET"
        now_str = now.strftime(f"%H:%M {tz_abbr}")
        total_sent = 0

        for sig_key in sorted(new_signals):
            parts = sig_key.split(":")
            symbol = parts[0] if parts else sig_key
            direction = parts[1] if len(parts) > 1 else "?"

            # Find matching pair_info
            info = next((p for p in all_passed if p.get("symbol") == symbol), None)
            # Use direction from pair_info if available (more reliable)
            if info and info.get("direction"):
                direction = info["direction"]

            # Direction
            if direction == "DOWN":
                dir_text = "🟥⬇ PUT"
            else:
                dir_text = "🟩⬆ CALL"

            # Price
            price = info.get("base_price") if info else None
            if price is not None:
                is_jpy = "JPY" in symbol
                price_str = f"{price:.3f}" if is_jpy else f"{price:.5f}"
            else:
                price_str = "—"

            # Build message
            lines = [
                f"{dir_text}  <b>{symbol}</b>",
                f"💰 {price_str}  •  {now_str}",
            ]

            # Horizon details (compact)
            if info:
                hz_parts = []
                for hz_key in ["h30", "h60"]:
                    hz = info.get(hz_key)
                    if hz and hz.get("passed"):
                        hz_label = "30m" if hz_key == "h30" else "60m"
                        aligned = hz.get("aligned", "?")
                        total = hz.get("total", "?")
                        hz_parts.append(f"{hz_label}:{aligned}/{total}")
                if hz_parts:
                    lines.append(" • ".join(hz_parts))

                # Advanced filters (compact line)
                adv_parts = []
                adx_val = info.get("adx_value")
                if adx_val is not None:
                    adx_emoji = "🟢" if adx_val >= 25 else "🟡" if adx_val >= 20 else "🔴"
                    adv_parts.append(f"ADX {adx_val:.0f}{adx_emoji}")
                if info.get("bb_squeeze"):
                    adv_parts.append("⚠️Squeeze")
                if info.get("mtf_conflict"):
                    h4d = (info.get("mtf_h4_direction") or "?").upper()
                    adv_parts.append(f"⚠️H4={h4d}")
                elif info.get("mtf_h4_direction"):
                    h4d = info["mtf_h4_direction"].upper()
                    adv_parts.append(f"H4={h4d}")
                if adv_parts:
                    lines.append(" • ".join(adv_parts))

            text = "\n".join(lines)
            sent = self.broadcast(text)
            total_sent += sent

        print(f"tg_new_signals sent={total_sent} signals={len(new_signals)}")
        return total_sent

    def notify_alt_signal(
        self,
        symbol: str,
        strategy: str,  # "alt2" | "alt3" | "alt4" | "alt5"
        direction: str,  # "up" or "down"
        base_price: float,
        adx_value: float | None = None,
        votes: dict | None = None,
        h4_direction: str | None = None,
        mode: str | None = None,
        hour_utc: int | None = None,
    ) -> int:
        """Send Alt2/Alt3 signal notification to Telegram."""
        if not self.enabled:
            return 0

        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Paris"))
        tz_abbr = "CEST" if now.dst() else "CET"
        now_str = now.strftime(f"%H:%M {tz_abbr}")

        if direction == "down":
            dir_text = "\U0001f7e5\u2b07 PUT"
        else:
            dir_text = "\U0001f7e9\u2b06 CALL"

        is_jpy = "JPY" in symbol
        price_str = f"{base_price:.3f}" if is_jpy else f"{base_price:.5f}"

        if strategy == "alt3":
            strat_label = "\U0001f52c ALT3"
            strat_desc = "Alt2+momentum"
        elif strategy == "alt4":
            strat_label = "\U0001f9ea ALT4"
            strat_desc = "hybrid original/inverted"
        elif strategy == "alt5":
            strat_label = "\U0001f6e1 ALT5"
            strat_desc = "anti-alt3 (hours+ADX)"
        else:
            strat_label = "\U0001f3af ALT2"
            strat_desc = "ma\u2260pv + ADX\u226530 + top16"

        lines = [
            f"{dir_text}  <b>{symbol}</b>  [{strat_label}]",
            f"\U0001f4b0 {price_str}  \u2022  {now_str}",
            f"Strategy: {strat_desc}",
        ]
        if strategy == "alt4" and mode:
            mode_label = str(mode).upper()
            if isinstance(hour_utc, int) and 0 <= hour_utc <= 23:
                lines.append(f"Mode: {mode_label} (h{hour_utc:02d})")
            else:
                lines.append(f"Mode: {mode_label}")

        # ADX
        if adx_value is not None:
            adx_emoji = "\U0001f7e2" if adx_value >= 30 else "\U0001f7e1" if adx_value >= 25 else "\U0001f534"
            lines.append(f"ADX {adx_value:.0f}{adx_emoji}")

        # Votes
        if votes:
            ma = votes.get("ma_cross_inv", "?")
            pv = votes.get("price_vs_ma", "?")
            mom = votes.get("momentum", "?")
            lines.append(f"Votes: ma={ma} pv={pv} mom={mom}")

        # H4
        if h4_direction:
            lines.append(f"H4={h4_direction.upper()}")

        if self.web_base_url:
            lines.append(f'\n<a href="{self.web_base_url}/admin/binary-signals">\U0001f4ca Open Signals</a>')

        text = "\n".join(lines)
        sent = self.broadcast(text)
        print(f"tg_alt_signal sent={sent} strategy={strategy} symbol={symbol} dir={direction}")
        return sent

    def notify_lost_signals(self, lost_signals: Set[str]) -> int:
        """Send notification when signals are lost (optional, less urgent)."""
        if self.alt_only:
            return 0
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
        if self.alt_only:
            return 0
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
        if self.alt_only:
            return 0
        if not self.enabled:
            return 0

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        text = (
            f"🔕 <b>Trading Window Closed</b>  •  {now_str}\n\n"
            f"Outside active hours. No signals will be generated."
        )
        return self.broadcast(text)

    def notify_daily_report(self, db_client) -> int:
        """
        Send daily performance report with accuracy breakdown.

        Queries last 24h verified forecasts and computes:
        - Overall H30 accuracy
        - Quality filtered (MED + aligned>=4) accuracy
        - Breakdown by time period (early vs late session)
        - ADX correlation
        - Squeeze correlation
        - Top/worst performing pairs
        """
        if self.alt_only:
            return 0
        if not self.enabled or not db_client:
            return 0

        try:
            from zoneinfo import ZoneInfo
            now_paris = datetime.now(ZoneInfo("Europe/Paris"))
            date_str = now_paris.strftime("%d %b %Y")

            cutoff = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)).isoformat()
            res = db_client.table("price_forecasts").select(
                "symbol, ts_utc, h30_direction, h30_confidence, h30_aligned, h30_correct, "
                "h60_direction, h60_confidence, h60_aligned, h60_correct, "
                "adx_value, bb_squeeze, mtf_conflict"
            ).gte("ts_utc", cutoff).order("ts_utc", desc=True).limit(500).execute()

            all_rows = res.data or []

            # H30 verified rows
            h30_rows = [r for r in all_rows if r.get("h30_correct") is not None]
            if not h30_rows:
                return 0  # Nothing to report

            h30_ok = sum(1 for r in h30_rows if r["h30_correct"])
            h30_total = len(h30_rows)

            # Quality filtered (MED + aligned>=4)
            qf = [r for r in h30_rows
                  if (r.get("h30_confidence") or "").lower() == "medium"
                  and (r.get("h30_aligned") or 0) >= 4]
            qf_ok = sum(1 for r in qf if r["h30_correct"])
            qf_total = len(qf)

            # H60 stats
            h60_rows = [r for r in all_rows if r.get("h60_correct") is not None]
            h60_ok = sum(1 for r in h60_rows if r["h60_correct"])
            h60_total = len(h60_rows)

            # Time breakdown (on quality filtered)
            early = [r for r in qf if int(r["ts_utc"][11:13]) < 10]
            late = [r for r in qf if int(r["ts_utc"][11:13]) >= 10]
            e_ok = sum(1 for r in early if r["h30_correct"])
            l_ok = sum(1 for r in late if r["h30_correct"])

            # ADX breakdown (on quality filtered)
            adx_high = [r for r in qf if r.get("adx_value") and r["adx_value"] >= 25]
            adx_low = [r for r in qf if r.get("adx_value") and r["adx_value"] < 25]
            ah_ok = sum(1 for r in adx_high if r["h30_correct"])
            al_ok = sum(1 for r in adx_low if r["h30_correct"])

            # Squeeze breakdown (on quality filtered)
            sq = [r for r in qf if r.get("bb_squeeze")]
            nosq = [r for r in qf if not r.get("bb_squeeze")]
            sq_ok = sum(1 for r in sq if r["h30_correct"])
            nosq_ok = sum(1 for r in nosq if r["h30_correct"])

            # Per-symbol stats (on quality filtered)
            sym_stats: dict = {}
            for r in qf:
                s = r["symbol"]
                if s not in sym_stats:
                    sym_stats[s] = {"ok": 0, "total": 0}
                sym_stats[s]["total"] += 1
                if r["h30_correct"]:
                    sym_stats[s]["ok"] += 1

            # Accuracy emoji
            def acc_emoji(pct: int) -> str:
                if pct >= 75: return "🟢"
                if pct >= 55: return "🟡"
                return "🔴"

            def fmt_acc(ok: int, total: int) -> str:
                if total == 0: return "—"
                pct = round(ok / total * 100)
                return f"{acc_emoji(pct)} {ok}/{total} = {pct}%"

            # Build message
            lines = [
                f"📊 <b>Daily Report</b>  •  {date_str}",
                "",
                f"<b>H30 Overall:</b> {fmt_acc(h30_ok, h30_total)}",
                f"<b>H30 Quality:</b> {fmt_acc(qf_ok, qf_total)}",
                f"<b>H60 Overall:</b> {fmt_acc(h60_ok, h60_total)}",
                "",
                "<b>⏰ By Session (quality):</b>",
                f"  08-10 UTC: {fmt_acc(e_ok, len(early))}",
                f"  10+  UTC: {fmt_acc(l_ok, len(late))}",
                "",
                "<b>📈 By ADX (quality):</b>",
                f"  ADX≥25 trend: {fmt_acc(ah_ok, len(adx_high))}",
                f"  ADX&lt;25 flat: {fmt_acc(al_ok, len(adx_low))}",
                "",
                "<b>📉 By Squeeze (quality):</b>",
                f"  Squeeze: {fmt_acc(sq_ok, len(sq))}",
                f"  Normal: {fmt_acc(nosq_ok, len(nosq))}",
            ]

            # Top/worst pairs
            if sym_stats:
                sorted_syms = sorted(sym_stats.items(), key=lambda x: x[1]["ok"] / max(x[1]["total"], 1), reverse=True)
                best = [(s, d) for s, d in sorted_syms if d["total"] >= 2 and d["ok"] / d["total"] >= 0.75]
                worst = [(s, d) for s, d in sorted_syms if d["total"] >= 2 and d["ok"] / d["total"] < 0.55]
                if best:
                    lines.append("")
                    lines.append("<b>🏆 Best pairs:</b>")
                    for s, d in best[:5]:
                        pct = round(d["ok"] / d["total"] * 100)
                        lines.append(f"  {s}: {d['ok']}/{d['total']} = {pct}%")
                if worst:
                    lines.append("")
                    lines.append("<b>⚠️ Weak pairs:</b>")
                    for s, d in worst[:5]:
                        pct = round(d["ok"] / d["total"] * 100)
                        lines.append(f"  {s}: {d['ok']}/{d['total']} = {pct}%")

            text = "\n".join(lines)
            sent = self.broadcast(text)
            print(f"tg_daily_report sent={sent} h30={h30_ok}/{h30_total} qf={qf_ok}/{qf_total}")
            return sent

        except Exception as exc:
            print(f"tg_daily_report_error: {exc}")
            return 0
