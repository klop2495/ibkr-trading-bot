"""
Tests for Phase 0: Parallel Decisions Shadow Mode.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.parallel_decision import ParallelDecisionV1


class TestParallelDecisionV1:
    """Tests for ParallelDecisionV1 model."""

    def test_create_shadow_long(self):
        """Test creating a shadow decision for LONG signal."""
        ts = datetime.now(timezone.utc)
        sp_id = uuid4()
        cd_id = uuid4()

        pd = ParallelDecisionV1.create_shadow(
            ts_utc=ts,
            symbol="EURUSD",
            rules_signal="LONG",
            rules_confidence="normal",
            rules_flags=["FLAG_A", "FLAG_B"],
            signal_preview_id=sp_id,
            control_decision_id=cd_id,
        )

        assert pd.ts_utc == ts
        assert pd.symbol == "EURUSD"
        assert pd.rules_signal == "LONG"
        assert pd.rules_confidence == "normal"
        assert pd.rules_flags == ["FLAG_A", "FLAG_B"]
        assert pd.gpt_signal == "HOLD"  # Stub
        assert pd.gpt_score == 0.0
        assert pd.gpt_consensus is False
        assert pd.hybrid_signal == "LONG"  # Mirrors rules
        assert pd.executed_strategy == "rules"
        assert pd.executed_signal == "LONG"
        assert pd.signal_preview_id == sp_id
        assert pd.control_decision_id == cd_id

    def test_create_shadow_hold(self):
        """Test creating a shadow decision for HOLD signal."""
        pd = ParallelDecisionV1.create_shadow(
            ts_utc=datetime.now(timezone.utc),
            symbol="GBPUSD",
            rules_signal="HOLD",
            rules_confidence="low",
            rules_flags=[],
        )

        assert pd.rules_signal == "HOLD"
        assert pd.gpt_signal == "HOLD"
        assert pd.hybrid_signal == "HOLD"
        assert pd.hybrid_score == 0.0
        assert pd.executed_strategy == "rules"
        assert pd.executed_signal == "HOLD"

    def test_to_db_row(self):
        """Test conversion to database row format."""
        ts = datetime.now(timezone.utc)
        sp_id = uuid4()
        cd_id = uuid4()

        pd = ParallelDecisionV1.create_shadow(
            ts_utc=ts,
            symbol="USDJPY",
            rules_signal="SHORT",
            rules_confidence="high",
            rules_flags=["FLAG_X"],
            signal_preview_id=sp_id,
            control_decision_id=cd_id,
        )

        row = pd.to_db_row()

        assert row["ts_utc"] == ts.isoformat()
        assert row["symbol"] == "USDJPY"
        assert row["rules_signal"] == "SHORT"
        assert row["rules_confidence"] == "high"
        assert row["rules_flags"] == ["FLAG_X"]
        assert row["gpt_signal"] == "HOLD"
        assert row["gpt_score"] == 0.0
        assert row["gpt_consensus"] is False
        assert row["gpt_consensus_count"] == 0
        assert row["gpt_agent_details"] == []
        assert row["hybrid_signal"] == "SHORT"
        assert row["executed_strategy"] == "rules"
        assert row["executed_signal"] == "SHORT"
        assert row["signal_preview_id"] == str(sp_id)
        assert row["control_decision_id"] == str(cd_id)
        assert row["budget_status"] == "OK"
        assert row["cache_hits"] == 0
        assert row["validation_failures"] == 0

    def test_default_safety_fields(self):
        """Test default values for safety-related fields."""
        pd = ParallelDecisionV1(
            ts_utc=datetime.now(timezone.utc),
            symbol="AUDUSD",
            rules_signal="LONG",
        )

        assert pd.budget_status == "OK"
        assert pd.cache_hits == 0
        assert pd.source_health == {}
        assert pd.validation_failures == 0
        assert pd.gpt_agent_details == []

    def test_extra_forbid(self):
        """Test that extra fields are rejected."""
        with pytest.raises(Exception):  # ValidationError
            ParallelDecisionV1(
                ts_utc=datetime.now(timezone.utc),
                symbol="EURUSD",
                rules_signal="LONG",
                unknown_field="should fail",
            )
