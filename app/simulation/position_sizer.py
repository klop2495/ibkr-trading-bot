"""
PositionSizer - Calculate position size based on risk parameters
"""
from typing import Optional, Tuple

from app.simulation.models import (
    FOREX_PIP_SIZES, IDEALPRO_MIN_SIZES, 
    DEFAULT_MIN_SIZE, DEFAULT_PIP_SIZE,
    BlockReason
)


class MinSizePolicy:
    ROUND_UP = "ROUND_UP"   # Round up to minimum (for testing)
    BLOCK = "BLOCK"          # Block if below minimum (strict)


class PositionSizer:
    """
    Calculate position size based on:
    - equity
    - risk_per_trade (%)
    - stop_loss_pips
    - risk_modifier (from verdict)
    
    Formula:
    risk_cash = equity * risk_per_trade * risk_modifier
    qty = risk_cash / (sl_pips * pip_value_per_unit)
    """
    
    def __init__(
        self,
        min_size_policy: str = MinSizePolicy.ROUND_UP,
        lot_step: int = 1000,  # Round to nearest 1000
    ):
        self.min_size_policy = min_size_policy
        self.lot_step = lot_step
    
    def get_pip_size(self, symbol: str) -> float:
        """Get pip size for symbol"""
        return FOREX_PIP_SIZES.get(symbol, DEFAULT_PIP_SIZE)
    
    def get_min_size(self, symbol: str) -> int:
        """Get minimum lot size for IdealPro"""
        return IDEALPRO_MIN_SIZES.get(symbol, DEFAULT_MIN_SIZE)
    
    def calculate_pip_value(self, symbol: str, quantity: float, current_price: float) -> float:
        """
        Calculate pip value in account currency (USD assumed).
        
        For XXX/USD pairs (EURUSD, GBPUSD): pip_value = quantity * pip_size
        For USD/XXX pairs (USDJPY, USDCAD): pip_value = quantity * pip_size / current_price
        """
        pip_size = self.get_pip_size(symbol)
        
        # Check if quote currency is USD
        if symbol.endswith("USD"):
            # Direct quote: pip value = quantity * pip_size
            return quantity * pip_size
        else:
            # Indirect quote: pip value = quantity * pip_size / price
            if current_price > 0:
                return quantity * pip_size / current_price
            return quantity * pip_size  # Fallback
    
    def calculate_size(
        self,
        symbol: str,
        equity: float,
        risk_per_trade: float,
        sl_pips: float,
        risk_modifier: float = 1.0,
        current_price: Optional[float] = None,
    ) -> Tuple[float, float, Optional[BlockReason]]:
        """
        Calculate position size.
        
        Returns:
            (quantity, risk_cash, block_reason)
            block_reason is None if valid, otherwise BlockReason
        """
        if sl_pips <= 0:
            return 0, 0, BlockReason.INVALID_SL_TP
        
        if equity <= 0:
            return 0, 0, BlockReason.BELOW_MIN_SIZE
        
        # Calculate risk in account currency
        risk_cash = equity * risk_per_trade * risk_modifier
        
        # Get pip size for this pair
        pip_size = self.get_pip_size(symbol)
        
        # For initial calculation, estimate pip value
        # For XXX/USD: pip_value per unit ≈ pip_size
        # For USD/XXX: pip_value per unit ≈ pip_size / price
        if symbol.endswith("USD"):
            pip_value_per_unit = pip_size
        elif current_price and current_price > 0:
            pip_value_per_unit = pip_size / current_price
        else:
            # Fallback estimate
            pip_value_per_unit = pip_size
        
        # Calculate quantity
        # risk_cash = qty * sl_pips * pip_value_per_unit
        # qty = risk_cash / (sl_pips * pip_value_per_unit)
        qty = risk_cash / (sl_pips * pip_value_per_unit)
        
        # Round to lot step
        qty = round(qty / self.lot_step) * self.lot_step
        
        # Check minimum size
        min_size = self.get_min_size(symbol)
        
        if qty < min_size:
            if self.min_size_policy == MinSizePolicy.ROUND_UP:
                qty = min_size
            else:  # BLOCK
                return qty, risk_cash, BlockReason.BELOW_MIN_SIZE
        
        return qty, risk_cash, None
    
    def calculate_notional(self, symbol: str, quantity: float, entry_price: float) -> float:
        """Calculate notional value in account currency"""
        # For forex, notional = quantity (in base currency) * price
        # Assuming account in USD:
        if symbol.endswith("USD"):
            # EURUSD: notional in USD = quantity * price
            return quantity * entry_price
        else:
            # USDJPY: notional in USD = quantity (already in USD base)
            return quantity
    
    def check_leverage(
        self,
        total_exposure: float,
        equity: float,
        max_leverage: float,
    ) -> Tuple[bool, float]:
        """
        Check if total exposure exceeds max leverage.
        
        Returns:
            (is_allowed, current_leverage)
        """
        if equity <= 0:
            return False, 0
        
        current_leverage = total_exposure / equity
        return current_leverage <= max_leverage, current_leverage
