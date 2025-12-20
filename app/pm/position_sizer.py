"""
Position Sizing Module

Calculates position size based on:
- Account equity
- Risk per trade (%)
- Stop loss distance
- Risk modifier from control plane
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class PositionSizeResult:
    """Result of position sizing calculation."""
    units: float
    risk_amount: float
    stop_distance_pips: float
    pip_value: float
    risk_percent_actual: float
    capped: bool = False  # True if position was reduced due to limits
    reason: Optional[str] = None


class PositionSizerConfig(BaseModel):
    """Configuration for position sizer."""
    model_config = ConfigDict(extra="forbid")
    
    # Risk limits
    max_risk_per_trade_pct: float = Field(default=1.0, ge=0.1, le=5.0)
    min_risk_per_trade_pct: float = Field(default=0.1, ge=0.01, le=1.0)
    
    # Position limits
    max_position_size: float = Field(default=100000.0)  # Max units per trade
    min_position_size: float = Field(default=1000.0)    # Min units (micro lot)
    
    # Lot sizing
    lot_step: float = Field(default=1000.0)  # Round to nearest 1000 units
    
    # Currency settings
    account_currency: str = "USD"


class PositionSizer:
    """
    Calculate position size based on risk parameters.
    
    Uses the formula:
    Position Size = (Account Equity × Risk%) / (Stop Loss Pips × Pip Value)
    """
    
    # Pip values for major pairs (per standard lot = 100,000 units)
    # These are approximate and should be updated with live rates
    PIP_VALUES = {
        "EURUSD": 10.0,    # $10 per pip per standard lot
        "GBPUSD": 10.0,
        "AUDUSD": 10.0,
        "NZDUSD": 10.0,
        "USDCAD": 7.5,     # Varies with CAD rate
        "USDCHF": 10.5,    # Varies with CHF rate
        "USDJPY": 6.5,     # Varies with JPY rate (per 0.01 move)
        "EURGBP": 12.5,    # Varies with GBP rate
        "EURJPY": 6.5,
        "GBPJPY": 6.5,
    }
    
    # Default pip value for unknown pairs
    DEFAULT_PIP_VALUE = 10.0
    
    def __init__(self, config: Optional[PositionSizerConfig] = None):
        self.config = config or PositionSizerConfig()
    
    def get_pip_value(self, symbol: str, units: float = 100000.0) -> float:
        """
        Get pip value for a symbol.
        
        Args:
            symbol: Currency pair (e.g., "EURUSD")
            units: Position size in base currency units
            
        Returns:
            Pip value in account currency per pip move
        """
        symbol_upper = symbol.upper()
        base_pip_value = self.PIP_VALUES.get(symbol_upper, self.DEFAULT_PIP_VALUE)
        
        # Scale to position size (base values are for 100k units)
        return base_pip_value * (units / 100000.0)
    
    def calculate(
        self,
        equity: float,
        stop_loss_pips: float,
        symbol: str,
        risk_modifier: float = 1.0,
        custom_risk_pct: Optional[float] = None,
    ) -> PositionSizeResult:
        """
        Calculate position size.
        
        Args:
            equity: Account equity in account currency
            stop_loss_pips: Distance to stop loss in pips
            symbol: Currency pair
            risk_modifier: Multiplier from control plane (0.0 to 2.0)
            custom_risk_pct: Override default risk percentage
            
        Returns:
            PositionSizeResult with calculated position size
        """
        # Validate inputs
        if equity <= 0:
            return PositionSizeResult(
                units=0,
                risk_amount=0,
                stop_distance_pips=stop_loss_pips,
                pip_value=0,
                risk_percent_actual=0,
                capped=True,
                reason="invalid_equity",
            )
        
        if stop_loss_pips <= 0:
            return PositionSizeResult(
                units=0,
                risk_amount=0,
                stop_distance_pips=stop_loss_pips,
                pip_value=0,
                risk_percent_actual=0,
                capped=True,
                reason="invalid_stop_loss",
            )
        
        # Determine risk percentage
        base_risk_pct = custom_risk_pct or self.config.max_risk_per_trade_pct
        
        # Apply risk modifier (clamp to 0.0 - 2.0)
        risk_modifier = max(0.0, min(2.0, risk_modifier))
        adjusted_risk_pct = base_risk_pct * risk_modifier
        
        # Clamp to configured limits
        adjusted_risk_pct = max(
            self.config.min_risk_per_trade_pct,
            min(self.config.max_risk_per_trade_pct, adjusted_risk_pct)
        )
        
        # Calculate risk amount in account currency
        risk_amount = equity * (adjusted_risk_pct / 100.0)
        
        # Get pip value per standard lot
        pip_value_per_lot = self.PIP_VALUES.get(symbol.upper(), self.DEFAULT_PIP_VALUE)
        
        # Calculate position size
        # Position = Risk Amount / (Stop Loss Pips × Pip Value per standard lot / 100000)
        if pip_value_per_lot <= 0:
            return PositionSizeResult(
                units=0,
                risk_amount=risk_amount,
                stop_distance_pips=stop_loss_pips,
                pip_value=0,
                risk_percent_actual=0,
                capped=True,
                reason="invalid_pip_value",
            )
        
        # Pip value per unit
        pip_value_per_unit = pip_value_per_lot / 100000.0
        
        # Raw position size
        raw_units = risk_amount / (stop_loss_pips * pip_value_per_unit)
        
        # Round down to lot step
        units = (raw_units // self.config.lot_step) * self.config.lot_step
        
        # Apply position limits
        capped = False
        cap_reason = None
        
        if units > self.config.max_position_size:
            units = self.config.max_position_size
            capped = True
            cap_reason = "max_position_limit"
        
        if units < self.config.min_position_size:
            if raw_units >= self.config.min_position_size * 0.5:
                # Allow minimum if we're close
                units = self.config.min_position_size
            else:
                units = 0
                capped = True
                cap_reason = "below_min_position"
        
        # Calculate actual pip value and risk
        actual_pip_value = self.get_pip_value(symbol, units)
        actual_risk_amount = stop_loss_pips * actual_pip_value
        actual_risk_pct = (actual_risk_amount / equity) * 100 if equity > 0 else 0
        
        return PositionSizeResult(
            units=units,
            risk_amount=actual_risk_amount,
            stop_distance_pips=stop_loss_pips,
            pip_value=actual_pip_value,
            risk_percent_actual=actual_risk_pct,
            capped=capped,
            reason=cap_reason,
        )
    
    def calculate_from_prices(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        symbol: str,
        risk_modifier: float = 1.0,
        custom_risk_pct: Optional[float] = None,
    ) -> PositionSizeResult:
        """
        Calculate position size from entry and stop prices.
        
        Args:
            equity: Account equity
            entry_price: Entry price
            stop_price: Stop loss price
            symbol: Currency pair
            risk_modifier: Risk modifier from control plane
            custom_risk_pct: Override default risk percentage
            
        Returns:
            PositionSizeResult with calculated position size
        """
        # Calculate stop distance in pips
        # For JPY pairs, 1 pip = 0.01; for others, 1 pip = 0.0001
        is_jpy_pair = symbol.upper().endswith("JPY")
        pip_size = 0.01 if is_jpy_pair else 0.0001
        
        stop_distance = abs(entry_price - stop_price)
        stop_loss_pips = stop_distance / pip_size
        
        return self.calculate(
            equity=equity,
            stop_loss_pips=stop_loss_pips,
            symbol=symbol,
            risk_modifier=risk_modifier,
            custom_risk_pct=custom_risk_pct,
        )
