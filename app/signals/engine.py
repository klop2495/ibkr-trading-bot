from datetime import datetime, timezone
from typing import List

from app.models.snapshot import MarketSnapshot
from app.signals.models import FeatureBins, SignalPreview, SignalSummary


class SignalEngine:
    def __init__(self):
        self._rules_warning_logged = False

    def compute_signals(self, snapshots: List[MarketSnapshot]) -> List[SignalPreview]:
        signals: List[SignalPreview] = []
        for snap in snapshots:
            signals.append(self._compute_single(snap))
        return signals

    def _compute_single(self, snap: MarketSnapshot) -> SignalPreview:
        ts = snap.timestamp or datetime.utcnow().replace(tzinfo=timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        symbol = snap.symbol
        feature_bins = FeatureBins(trend="unknown", volatility="unknown", momentum="unknown")
        signal_summary = SignalSummary(direction="flat", setup_present=False, entry_triggered=False)
        flags: List[str] = ["binning_not_specified", "signals_rules_not_specified"]
        data_quality = getattr(snap, "data_quality", "unknown")
        spread_quality = getattr(snap, "spread_quality", "unknown")
        return SignalPreview(
            ts_utc=ts,
            symbol=symbol,
            feature_bins=feature_bins,
            signal_summary=signal_summary,
            data_quality=data_quality,
            spread_quality=spread_quality,
            flags=flags,
        )

    def warn_rules_not_specified(self, risk_repo) -> None:
        if self._rules_warning_logged:
            return
        try:
            risk_repo.insert(
                event_type="SIGNALS_RULES_NOT_SPECIFIED",
                severity="warn",
                message="signals persistence disabled until rules specified",
            )
        except Exception:
            # fail-safe: do not break caller if logging fails
            return
        self._rules_warning_logged = True
