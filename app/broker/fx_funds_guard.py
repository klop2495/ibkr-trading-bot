"""
FX Funds Guard

Pre-check available currency balance before placing FX spot orders.
Prevents orders from going Inactive due to insufficient funds.

Features:
- Check CashBalance in required currency before order
- Auto-reduce order size if insufficient funds
- Skip order if size becomes too small
- Log all decisions to risk_events
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class FundsPolicy(str, Enum):
    """Policy for handling insufficient funds."""
    AUTO_REDUCE = "auto_reduce"  # Reduce qty to max available
    SKIP = "skip"  # Skip the trade entirely


@dataclass
class FundsCheckResult:
    """Result of funds pre-check."""
    can_trade: bool
    original_qty: float
    adjusted_qty: float
    reason: str
    currency_needed: str
    available_cash: float
    required_cash: float
    price_used: float
    price_source: str  # "bid", "ask", "last", "midpoint"
    was_adjusted: bool = False
    
    # Config fields for logging
    buffer: float = 0.0
    qty_step: int = 0
    min_idealpro: int = 0
    min_trade_qty: int = 0
    allow_odd_lots: bool = True
    policy: str = "auto_reduce"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "can_trade": self.can_trade,
            "original_qty": self.original_qty,
            "adjusted_qty": self.adjusted_qty,
            "reason": self.reason,
            "currency_needed": self.currency_needed,
            "available_cash": self.available_cash,
            "required_cash": self.required_cash,
            "price_used": self.price_used,
            "price_source": self.price_source,
            "was_adjusted": self.was_adjusted,
            # Config for transparency
            "buffer": self.buffer,
            "qty_step": self.qty_step,
            "min_idealpro": self.min_idealpro,
            "min_trade_qty": self.min_trade_qty,
            "allow_odd_lots": self.allow_odd_lots,
            "policy": self.policy,
        }


class FXFundsGuard:
    """
    Guard for FX spot orders - ensures sufficient funds before placing.
    
    For FX spot (IDEALPRO/CASH):
    - BUY base/quote requires quote currency (e.g., BUY EURUSD needs USD)
    - SELL base/quote requires base currency (e.g., SELL EURUSD needs EUR)
    
    IB rejects orders that would create negative balance without explicit error.
    """
    
    # Configuration defaults (can be overridden via env)
    DEFAULT_BUFFER = 0.02  # 2% safety buffer (only for BUY)
    DEFAULT_QTY_STEP = 100  # Round to nearest 100
    DEFAULT_MIN_IDEALPRO = 20000  # IB minimum for IDEALPRO routing
    DEFAULT_MIN_TRADE_QTY = 5000  # Minimum trade size to avoid micro-trades
    DEFAULT_ALLOW_ODD_LOTS = True  # Allow below IDEALPRO minimum (warning 399)
    DEFAULT_POLICY = FundsPolicy.AUTO_REDUCE
    
    def __init__(
        self,
        ib: Any,
        account: Optional[str] = None,
        buffer: Optional[float] = None,
        qty_step: Optional[int] = None,
        min_idealpro: Optional[int] = None,
        min_trade_qty: Optional[int] = None,
        allow_odd_lots: Optional[bool] = None,
        policy: Optional[FundsPolicy] = None,
    ):
        """
        Initialize FX Funds Guard.
        
        Args:
            ib: ib_insync IB instance (must be connected)
            account: Account ID (optional, uses default if not specified)
            buffer: Safety buffer percentage for BUY orders (default 2%)
            qty_step: Quantity rounding step (default 100)
            min_idealpro: Minimum qty for IDEALPRO routing (default 20000)
            min_trade_qty: Minimum trade size to execute (default 5000)
            allow_odd_lots: Allow qty below IDEALPRO minimum (default True)
            policy: Policy for insufficient funds (default AUTO_REDUCE)
        """
        self.ib = ib
        self.account = account
        
        # Load config from env or use defaults
        self.buffer = buffer if buffer is not None else float(os.getenv("FX_FUNDS_BUFFER", self.DEFAULT_BUFFER))
        self.qty_step = qty_step if qty_step is not None else int(os.getenv("FX_QTY_STEP", self.DEFAULT_QTY_STEP))
        self.min_idealpro = min_idealpro if min_idealpro is not None else int(os.getenv("FX_MIN_IDEALPRO", self.DEFAULT_MIN_IDEALPRO))
        self.min_trade_qty = min_trade_qty if min_trade_qty is not None else int(os.getenv("FX_MIN_TRADE_QTY", self.DEFAULT_MIN_TRADE_QTY))
        self.allow_odd_lots = allow_odd_lots if allow_odd_lots is not None else os.getenv("FX_ALLOW_ODD_LOTS", "true").lower() == "true"
        
        policy_str = os.getenv("FX_FUNDS_POLICY", "auto_reduce")
        self.policy = policy if policy is not None else FundsPolicy(policy_str)
        
        # Cache for cash balances
        self._cash_cache: Dict[str, float] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 5.0  # Cache for 5 seconds
    
    def _parse_fx_pair(self, symbol: str) -> Tuple[str, str]:
        """
        Parse FX pair into base and quote currencies.
        
        Args:
            symbol: FX pair (e.g., "EURUSD", "EUR.USD", "EUR/USD")
        
        Returns:
            Tuple of (base, quote) currencies
        """
        # Remove separators
        clean = symbol.replace(".", "").replace("/", "").replace(" ", "").upper()
        
        if len(clean) == 6:
            return clean[:3], clean[3:]
        
        # Fallback for non-standard
        logger.warning(f"Non-standard FX symbol: {symbol}, assuming {symbol}/USD")
        return symbol.upper(), "USD"
    
    def _get_required_currency(self, symbol: str, side: str) -> str:
        """
        Determine which currency is needed for the trade.
        
        Args:
            symbol: FX pair
            side: "BUY" or "SELL"
        
        Returns:
            Currency code needed for the trade
        """
        base, quote = self._parse_fx_pair(symbol)
        
        # BUY base/quote = buying base, paying with quote
        # SELL base/quote = selling base, receiving quote
        if side.upper() == "BUY":
            return quote  # Need quote currency to buy base
        else:
            return base  # Need base currency to sell
    
    def get_cash_balance(self, currency: str) -> float:
        """
        Get available cash balance for a currency.
        
        Args:
            currency: Currency code (e.g., "USD", "EUR")
        
        Returns:
            Available cash balance
        """
        import time
        
        # Check cache
        now = time.time()
        if now - self._cache_time < self._cache_ttl and currency in self._cash_cache:
            return self._cash_cache[currency]
        
        # Refresh cache
        try:
            self._cash_cache = {}
            account_values = self.ib.accountValues(account=self.account)
            
            for av in account_values:
                if av.tag == "CashBalance":
                    self._cash_cache[av.currency] = float(av.value)
            
            self._cache_time = now
            
        except Exception as e:
            logger.error(f"Failed to get cash balances: {e}")
            return 0.0
        
        return self._cash_cache.get(currency, 0.0)
    
    def get_fx_price(self, symbol: str, side: str) -> Tuple[float, str]:
        """
        Get current price for FX pair with reliable data fetching.
        
        Uses polling loop instead of fixed sleep for better reliability.
        
        Args:
            symbol: FX pair
            side: "BUY" or "SELL"
        
        Returns:
            Tuple of (price, source) where source is "ask", "bid", "last", or "midpoint"
        """
        try:
            from app.broker.contracts import create_cfd_fx_contract
            contract = create_cfd_fx_contract(self.ib, symbol)
            
            ticker = self.ib.reqMktData(contract, snapshot=True)
            
            # Poll for data up to 3 seconds (30 x 0.1s)
            for _ in range(30):
                self.ib.sleep(0.1)
                
                # For BUY, prefer ask; for SELL, prefer bid
                if side.upper() == "BUY":
                    if ticker.ask and ticker.ask > 0:
                        return ticker.ask, "ask"
                else:
                    if ticker.bid and ticker.bid > 0:
                        return ticker.bid, "bid"
                
                # Accept any valid price
                if ticker.last and ticker.last > 0:
                    return ticker.last, "last"
                
                if ticker.bid and ticker.ask and ticker.bid > 0 and ticker.ask > 0:
                    midpoint = (ticker.bid + ticker.ask) / 2
                    return midpoint, "midpoint"
            
            # Last resort - use close
            if ticker.close and ticker.close > 0:
                return ticker.close, "close"
            
            logger.warning(f"No price data for {symbol} after 3s polling")
            return 0.0, "none"
            
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return 0.0, "error"
    
    def compute_max_qty(
        self,
        symbol: str,
        side: str,
        price: float,
    ) -> float:
        """
        Compute maximum quantity that can be traded given available funds.
        
        Args:
            symbol: FX pair
            side: "BUY" or "SELL"
            price: Current price
        
        Returns:
            Maximum quantity (rounded down to qty_step)
        """
        currency = self._get_required_currency(symbol, side)
        available = self.get_cash_balance(currency)
        
        if available <= 0 or price <= 0:
            return 0.0
        
        if side.upper() == "BUY":
            # BUY: need quote currency with buffer (price fluctuates)
            # required = qty * price * (1 + buffer)
            # max_qty = available / (price * (1 + buffer))
            buffer_multiplier = 1 + self.buffer
            max_qty = available / (price * buffer_multiplier)
        else:
            # SELL: need base currency, no buffer needed (we have exact amount)
            # required = qty (just the base currency we're selling)
            max_qty = available
        
        # Round down to qty_step
        max_qty = (int(max_qty) // self.qty_step) * self.qty_step
        
        return float(max_qty)
    
    def _create_result(
        self,
        can_trade: bool,
        original_qty: float,
        adjusted_qty: float,
        reason: str,
        currency_needed: str,
        available_cash: float,
        required_cash: float,
        price_used: float,
        price_source: str,
        was_adjusted: bool = False,
    ) -> FundsCheckResult:
        """Create FundsCheckResult with config fields populated."""
        return FundsCheckResult(
            can_trade=can_trade,
            original_qty=original_qty,
            adjusted_qty=adjusted_qty,
            reason=reason,
            currency_needed=currency_needed,
            available_cash=available_cash,
            required_cash=required_cash,
            price_used=price_used,
            price_source=price_source,
            was_adjusted=was_adjusted,
            # Config fields
            buffer=self.buffer,
            qty_step=self.qty_step,
            min_idealpro=self.min_idealpro,
            min_trade_qty=self.min_trade_qty,
            allow_odd_lots=self.allow_odd_lots,
            policy=self.policy.value,
        )
    
    def check_funds(
        self,
        symbol: str,
        side: str,
        desired_qty: float,
        price: Optional[float] = None,
    ) -> FundsCheckResult:
        """
        Check if sufficient funds are available for the trade.
        
        Args:
            symbol: FX pair (e.g., "EURUSD")
            side: "BUY" or "SELL"
            desired_qty: Desired quantity
            price: Price to use (optional, fetches current if not provided)
        
        Returns:
            FundsCheckResult with decision and details
        """
        currency = self._get_required_currency(symbol, side)
        available = self.get_cash_balance(currency)
        
        # Get price if not provided
        price_source = "provided"
        if price is None or price <= 0:
            price, price_source = self.get_fx_price(symbol, side)
        
        if price <= 0:
            return self._create_result(
                can_trade=False,
                original_qty=desired_qty,
                adjusted_qty=0,
                reason="no_price_data",
                currency_needed=currency,
                available_cash=available,
                required_cash=0,
                price_used=0,
                price_source=price_source,
            )
        
        # Calculate required funds
        # BUY: need quote currency with buffer
        # SELL: need base currency (exact, no buffer)
        if side.upper() == "BUY":
            buffer_multiplier = 1 + self.buffer
            required = desired_qty * price * buffer_multiplier
        else:
            # SELL: just need the base currency amount
            required = desired_qty
        
        # Check if we have enough
        if available >= required:
            # Sufficient funds - check minimum qty
            final_qty = desired_qty
            
            # Check minimum trade qty
            if final_qty < self.min_trade_qty:
                return self._create_result(
                    can_trade=False,
                    original_qty=desired_qty,
                    adjusted_qty=0,
                    reason=f"below_min_trade_qty:{final_qty}<{self.min_trade_qty}",
                    currency_needed=currency,
                    available_cash=available,
                    required_cash=required,
                    price_used=price,
                    price_source=price_source,
                )
            
            # Check IDEALPRO minimum
            if final_qty < self.min_idealpro and not self.allow_odd_lots:
                return self._create_result(
                    can_trade=False,
                    original_qty=desired_qty,
                    adjusted_qty=0,
                    reason=f"below_min_idealpro:{final_qty}<{self.min_idealpro}",
                    currency_needed=currency,
                    available_cash=available,
                    required_cash=required,
                    price_used=price,
                    price_source=price_source,
                )
            
            return self._create_result(
                can_trade=True,
                original_qty=desired_qty,
                adjusted_qty=final_qty,
                reason="sufficient_funds",
                currency_needed=currency,
                available_cash=available,
                required_cash=required,
                price_used=price,
                price_source=price_source,
            )
        
        # Insufficient funds - apply policy
        if self.policy == FundsPolicy.SKIP:
            return self._create_result(
                can_trade=False,
                original_qty=desired_qty,
                adjusted_qty=0,
                reason=f"insufficient_funds:need_{required:.2f}_{currency}_have_{available:.2f}",
                currency_needed=currency,
                available_cash=available,
                required_cash=required,
                price_used=price,
                price_source=price_source,
            )
        
        # AUTO_REDUCE policy
        max_qty = self.compute_max_qty(symbol, side, price)
        
        if max_qty <= 0:
            return self._create_result(
                can_trade=False,
                original_qty=desired_qty,
                adjusted_qty=0,
                reason="insufficient_funds:max_qty=0",
                currency_needed=currency,
                available_cash=available,
                required_cash=required,
                price_used=price,
                price_source=price_source,
            )
        
        # Check minimum trade qty
        if max_qty < self.min_trade_qty:
            return self._create_result(
                can_trade=False,
                original_qty=desired_qty,
                adjusted_qty=0,
                reason=f"auto_reduced_below_min_trade:{max_qty}<{self.min_trade_qty}",
                currency_needed=currency,
                available_cash=available,
                required_cash=required,
                price_used=price,
                price_source=price_source,
            )
        
        # Key rule: if original qty was >= min_idealpro but adjusted is below,
        # SKIP the trade even if allow_odd_lots=True (risk profile changed too much)
        if desired_qty >= self.min_idealpro and max_qty < self.min_idealpro:
            return self._create_result(
                can_trade=False,
                original_qty=desired_qty,
                adjusted_qty=0,
                reason=f"auto_reduced_below_min_idealpro:{max_qty}<{self.min_idealpro}_original={desired_qty}",
                currency_needed=currency,
                available_cash=available,
                required_cash=required,
                price_used=price,
                price_source=price_source,
            )
        
        # Check IDEALPRO minimum (for cases where original was also below)
        if max_qty < self.min_idealpro and not self.allow_odd_lots:
            return self._create_result(
                can_trade=False,
                original_qty=desired_qty,
                adjusted_qty=0,
                reason=f"adjusted_below_min_idealpro:{max_qty}<{self.min_idealpro}",
                currency_needed=currency,
                available_cash=available,
                required_cash=required,
                price_used=price,
                price_source=price_source,
            )
        
        # Can trade with reduced qty
        return self._create_result(
            can_trade=True,
            original_qty=desired_qty,
            adjusted_qty=max_qty,
            reason=f"auto_reduced:{desired_qty}->{max_qty}",
            currency_needed=currency,
            available_cash=available,
            required_cash=required,
            price_used=price,
            price_source=price_source,
            was_adjusted=True,
        )
    
    def precheck_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        log_event_callback: Optional[callable] = None,
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Pre-check order and return adjusted quantity.
        
        This is the main entry point for integration with OMS.
        
        Args:
            symbol: FX pair
            side: "BUY" or "SELL"
            quantity: Desired quantity
            price: Price (optional)
            log_event_callback: Optional callback to log events
        
        Returns:
            Tuple of (can_trade, adjusted_qty, details_dict)
        """
        result = self.check_funds(symbol, side, quantity, price)
        
        # Log the decision
        if log_event_callback:
            if result.can_trade:
                if result.was_adjusted:
                    log_event_callback(
                        event_type="EXECUTION_SIZE_ADJUSTED",
                        severity="warn",
                        message=f"Order size adjusted: {symbol} {side} {result.original_qty}->{result.adjusted_qty}",
                        data=result.to_dict(),
                    )
                else:
                    log_event_callback(
                        event_type="EXECUTION_PRECHECK_FUNDS",
                        severity="info",
                        message=f"Funds check passed: {symbol} {side} {result.adjusted_qty}",
                        data=result.to_dict(),
                    )
            else:
                log_event_callback(
                    event_type="EXECUTION_SKIPPED_INSUFFICIENT_FUNDS",
                    severity="warn",
                    message=f"Order skipped - {result.reason}: {symbol} {side} {result.original_qty}",
                    data=result.to_dict(),
                )
        
        logger.info(f"FX funds check: {symbol} {side} qty={quantity} -> can_trade={result.can_trade} adj_qty={result.adjusted_qty} reason={result.reason}")
        
        return result.can_trade, result.adjusted_qty, result.to_dict()


def create_funds_guard(ib: Any, account: Optional[str] = None) -> FXFundsGuard:
    """Factory function to create FXFundsGuard with env config."""
    return FXFundsGuard(ib=ib, account=account)
