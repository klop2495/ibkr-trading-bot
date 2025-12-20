"""
IBKR Connection Manager

Handles:
- Connection lifecycle
- Automatic reconnection
- Connection health monitoring
- Multiple client IDs (for paper/live separation)
"""

import logging
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field


logger = logging.getLogger(__name__)


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


class ConnectionConfig(BaseModel):
    """IBKR connection configuration."""
    model_config = ConfigDict(extra="forbid")
    
    host: str = "127.0.0.1"
    port: int = 7497  # 7497 = TWS Paper, 7496 = TWS Live, 4001 = Gateway Paper, 4002 = Gateway Live
    client_id: int = 1
    readonly: bool = False
    
    # Reconnection settings
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 5
    reconnect_delay_seconds: float = 5.0
    reconnect_backoff_multiplier: float = 2.0
    max_reconnect_delay_seconds: float = 60.0
    
    # Health check settings
    health_check_interval_seconds: float = 30.0
    connection_timeout_seconds: float = 10.0


class ConnectionStats(BaseModel):
    """Connection statistics."""
    model_config = ConfigDict(extra="forbid")
    
    state: ConnectionState = ConnectionState.DISCONNECTED
    connected_since: Optional[datetime] = None
    last_disconnect: Optional[datetime] = None
    reconnect_attempts: int = 0
    total_reconnects: int = 0
    last_error: Optional[str] = None
    last_health_check: Optional[datetime] = None
    

class ConnectionCallback:
    """Callbacks for connection events."""
    
    def on_connected(self) -> None:
        """Called when connection is established."""
        pass
    
    def on_disconnected(self, reason: Optional[str] = None) -> None:
        """Called when connection is lost."""
        pass
    
    def on_reconnecting(self, attempt: int, max_attempts: int) -> None:
        """Called when attempting to reconnect."""
        pass
    
    def on_error(self, error: str) -> None:
        """Called on connection error."""
        pass


class IBKRConnectionManager:
    """
    Manages IBKR connection lifecycle.
    
    Provides:
    - Automatic reconnection with exponential backoff
    - Health monitoring
    - Thread-safe state management
    """
    
    def __init__(
        self,
        config: Optional[ConnectionConfig] = None,
        callback: Optional[ConnectionCallback] = None,
        ib: Any = None,
    ) -> None:
        self.config = config or ConnectionConfig()
        self.callback = callback or ConnectionCallback()
        self.stats = ConnectionStats()
        
        self._ib = ib
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._health_thread: Optional[threading.Thread] = None
        
    def _ensure_ib(self) -> Any:
        """Lazy initialization of IB instance."""
        if self._ib is None:
            from ib_insync import IB
            self._ib = IB()
            self._register_handlers()
        return self._ib
    
    def _register_handlers(self) -> None:
        """Register disconnect handler."""
        if hasattr(self._ib, "disconnectedEvent"):
            self._ib.disconnectedEvent += self._on_disconnect
    
    def _unregister_handlers(self) -> None:
        """Unregister handlers."""
        if self._ib and hasattr(self._ib, "disconnectedEvent"):
            try:
                self._ib.disconnectedEvent -= self._on_disconnect
            except Exception:
                pass
    
    def _on_disconnect(self) -> None:
        """Handle disconnect event from ib_insync."""
        with self._lock:
            if self.stats.state == ConnectionState.DISCONNECTED:
                return
            
            self.stats.state = ConnectionState.DISCONNECTED
            self.stats.last_disconnect = datetime.now(timezone.utc)
            
        logger.warning("IBKR connection lost")
        self.callback.on_disconnected("connection_lost")
        
        if self.config.auto_reconnect:
            self._schedule_reconnect()
    
    def _schedule_reconnect(self) -> None:
        """Schedule reconnection attempt."""
        thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        thread.start()
    
    def _reconnect_loop(self) -> None:
        """Reconnection loop with exponential backoff."""
        delay = self.config.reconnect_delay_seconds
        
        for attempt in range(1, self.config.max_reconnect_attempts + 1):
            if self._stop_event.is_set():
                return
            
            with self._lock:
                if self.stats.state == ConnectionState.CONNECTED:
                    return
                self.stats.state = ConnectionState.RECONNECTING
                self.stats.reconnect_attempts = attempt
            
            logger.info(f"Reconnection attempt {attempt}/{self.config.max_reconnect_attempts}")
            self.callback.on_reconnecting(attempt, self.config.max_reconnect_attempts)
            
            try:
                self._do_connect()
                with self._lock:
                    self.stats.total_reconnects += 1
                    self.stats.reconnect_attempts = 0
                return
            except Exception as e:
                logger.warning(f"Reconnection failed: {e}")
                with self._lock:
                    self.stats.last_error = str(e)
            
            # Wait before next attempt
            time.sleep(delay)
            delay = min(
                delay * self.config.reconnect_backoff_multiplier,
                self.config.max_reconnect_delay_seconds
            )
        
        # Max attempts reached
        with self._lock:
            self.stats.state = ConnectionState.ERROR
            self.stats.last_error = "max_reconnect_attempts_reached"
        
        logger.error("Max reconnection attempts reached")
        self.callback.on_error("max_reconnect_attempts_reached")
    
    def _do_connect(self) -> None:
        """Perform actual connection."""
        ib = self._ensure_ib()
        
        with self._lock:
            self.stats.state = ConnectionState.CONNECTING
        
        ib.connect(
            host=self.config.host,
            port=self.config.port,
            clientId=self.config.client_id,
            readonly=self.config.readonly,
            timeout=self.config.connection_timeout_seconds,
        )
        
        with self._lock:
            self.stats.state = ConnectionState.CONNECTED
            self.stats.connected_since = datetime.now(timezone.utc)
            self.stats.last_error = None
        
        logger.info(f"Connected to IBKR at {self.config.host}:{self.config.port}")
        self.callback.on_connected()
    
    def connect(self) -> bool:
        """
        Establish connection to IBKR.
        
        Returns:
            True if connected successfully
        """
        try:
            self._do_connect()
            self._start_health_monitor()
            return True
        except Exception as e:
            with self._lock:
                self.stats.state = ConnectionState.ERROR
                self.stats.last_error = str(e)
            
            logger.error(f"Failed to connect: {e}")
            self.callback.on_error(str(e))
            return False
    
    def disconnect(self) -> None:
        """Disconnect from IBKR."""
        self._stop_event.set()
        self._stop_health_monitor()
        
        with self._lock:
            self.stats.state = ConnectionState.DISCONNECTED
            self.stats.last_disconnect = datetime.now(timezone.utc)
        
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        
        self.callback.on_disconnected("user_disconnect")
        logger.info("Disconnected from IBKR")
    
    def _start_health_monitor(self) -> None:
        """Start background health monitoring."""
        if self._health_thread and self._health_thread.is_alive():
            return
        
        self._stop_event.clear()
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()
    
    def _stop_health_monitor(self) -> None:
        """Stop health monitoring."""
        self._stop_event.set()
        if self._health_thread:
            self._health_thread.join(timeout=2.0)
    
    def _health_loop(self) -> None:
        """Background health check loop."""
        while not self._stop_event.is_set():
            try:
                self._check_health()
            except Exception as e:
                logger.debug(f"Health check error: {e}")
            
            # Wait for next check or stop event
            self._stop_event.wait(self.config.health_check_interval_seconds)
    
    def _check_health(self) -> None:
        """Perform health check."""
        with self._lock:
            if self.stats.state != ConnectionState.CONNECTED:
                return
            self.stats.last_health_check = datetime.now(timezone.utc)
        
        if self._ib and not self._ib.isConnected():
            self._on_disconnect()
    
    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        with self._lock:
            return self.stats.state == ConnectionState.CONNECTED
    
    @property
    def ib(self) -> Any:
        """Get the IB instance (may not be connected)."""
        return self._ensure_ib()
    
    def get_stats(self) -> ConnectionStats:
        """Get current connection statistics."""
        with self._lock:
            return self.stats.model_copy()
    
    def reset_stats(self) -> None:
        """Reset reconnection counters."""
        with self._lock:
            self.stats.reconnect_attempts = 0
            self.stats.total_reconnects = 0
            self.stats.last_error = None
