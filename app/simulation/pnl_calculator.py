"""
PnL Calculator - Calculate profit/loss for forex trades
"""
from typing import Tuple

from app.simulation.models import SimSide, FOREX_PIP_SIZES, DEFAULT_PIP_SIZE


class PnLCalculator:
    """Calculate P&L for forex trades"""
    
    @staticmethod
    def get_pip_size(symbol: str) -> float:
        """Get pip size for symbol"""
        return FOREX_PIP_SIZES.get(symbol, DEFAULT_PIP_SIZE)
    
    @staticmethod
    def calculate_pips(
        symbol: str,
        side: SimSide,
        entry_price: float,
        exit_price: float,
    ) -> float:
        """
        Calculate P&L in pips.
        
        BUY: profit when price goes up -> (exit - entry) / pip_size
        SELL: profit when price goes down -> (entry - exit) / pip_size
        """
        pip_size = PnLCalculator.get_pip_size(symbol)
        
        if side == SimSide.BUY:
            pips = (exit_price - entry_price) / pip_size
        else:  # SELL
            pips = (entry_price - exit_price) / pip_size
        
        return pips
    
    @staticmethod
    def calculate_pnl(
        symbol: str,
        side: SimSide,
        entry_price: float,
        exit_price: float,
        quantity: float,
    ) -> Tuple[float, float]:
        """
        Calculate P&L in pips and account currency.
        
        Returns:
            (pnl_pips, pnl_cash)
        
        For XXX/USD pairs: pnl = pips * pip_size * quantity
        For USD/XXX pairs: pnl = pips * pip_size * quantity / exit_price
        """
        pips = PnLCalculator.calculate_pips(symbol, side, entry_price, exit_price)
        pip_size = PnLCalculator.get_pip_size(symbol)
        
        # Calculate cash P&L
        if symbol.endswith("USD"):
            # Direct quote (EURUSD, GBPUSD, etc.)
            # P&L in USD = pips * pip_size * quantity
            pnl_cash = pips * pip_size * quantity
        else:
            # Indirect quote (USDJPY, USDCAD, etc.)
            # P&L in USD = pips * pip_size * quantity / exit_price
            if exit_price > 0:
                pnl_cash = pips * pip_size * quantity / exit_price
            else:
                pnl_cash = pips * pip_size * quantity
        
        return pips, pnl_cash
    
    @staticmethod
    def calculate_unrealized_pnl(
        symbol: str,
        side: SimSide,
        entry_price: float,
        current_bid: float,
        current_ask: float,
        quantity: float,
    ) -> Tuple[float, float]:
        """
        Calculate unrealized P&L (mark-to-market).
        
        For BUY positions: use bid price (what you'd get if you sold now)
        For SELL positions: use ask price (what you'd pay to buy back)
        
        Returns:
            (unrealized_pips, unrealized_cash)
        """
        if side == SimSide.BUY:
            exit_price = current_bid  # To close a BUY, you sell at bid
        else:
            exit_price = current_ask  # To close a SELL, you buy at ask
        
        return PnLCalculator.calculate_pnl(
            symbol, side, entry_price, exit_price, quantity
        )
    
    @staticmethod
    def calculate_sl_tp_prices(
        symbol: str,
        side: SimSide,
        entry_price: float,
        sl_pips: float,
        tp_pips: float,
    ) -> Tuple[float, float]:
        """
        Calculate stop loss and take profit prices from pip distances.
        
        BUY:
            SL = entry - sl_pips * pip_size
            TP = entry + tp_pips * pip_size
        
        SELL:
            SL = entry + sl_pips * pip_size
            TP = entry - tp_pips * pip_size
        
        Returns:
            (stop_loss_price, take_profit_price)
        """
        pip_size = PnLCalculator.get_pip_size(symbol)
        
        if side == SimSide.BUY:
            sl_price = entry_price - sl_pips * pip_size
            tp_price = entry_price + tp_pips * pip_size
        else:  # SELL
            sl_price = entry_price + sl_pips * pip_size
            tp_price = entry_price - tp_pips * pip_size
        
        return sl_price, tp_price
    
    @staticmethod
    def check_sl_hit(
        side: SimSide,
        stop_loss: float,
        current_bid: float,
        current_ask: float,
    ) -> bool:
        """
        Check if stop loss is hit.
        
        BUY: SL hit when bid <= stop_loss (selling at bid)
        SELL: SL hit when ask >= stop_loss (buying at ask)
        """
        if side == SimSide.BUY:
            return current_bid <= stop_loss
        else:  # SELL
            return current_ask >= stop_loss
    
    @staticmethod
    def check_tp_hit(
        side: SimSide,
        take_profit: float,
        current_bid: float,
        current_ask: float,
    ) -> bool:
        """
        Check if take profit is hit.
        
        BUY: TP hit when bid >= take_profit (selling at bid)
        SELL: TP hit when ask <= take_profit (buying at ask)
        """
        if side == SimSide.BUY:
            return current_bid >= take_profit
        else:  # SELL
            return current_ask <= take_profit
    
    @staticmethod
    def get_exit_price_for_close(
        side: SimSide,
        current_bid: float,
        current_ask: float,
    ) -> float:
        """
        Get the exit price for closing a position.
        
        BUY: close by selling at bid
        SELL: close by buying at ask
        """
        if side == SimSide.BUY:
            return current_bid
        else:
            return current_ask
