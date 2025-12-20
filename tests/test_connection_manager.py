"""
Tests for IBKR Connection Manager
"""

import pytest
import time
from datetime import datetime

from app.broker.connection_manager import (
    IBKRConnectionManager,
    ConnectionConfig,
    ConnectionState,
    ConnectionCallback,
)


class MockIB:
    """Mock IB instance for testing."""
    
    def __init__(self, fail_connect: bool = False):
        self._connected = False
        self._fail_connect = fail_connect
        self.disconnectedEvent = MockEvent()
    
    def connect(self, host, port, clientId, readonly=False, timeout=10):
        if self._fail_connect:
            raise Exception("Connection refused")
        self._connected = True
    
    def disconnect(self):
        self._connected = False
    
    def isConnected(self):
        return self._connected


class MockEvent:
    """Mock event for ib_insync."""
    
    def __init__(self):
        self._handlers = []
    
    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self
    
    def __isub__(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
        return self
    
    def emit(self):
        for handler in self._handlers:
            handler()


class MockConnectionCallback(ConnectionCallback):
    """Mock callback that records events."""
    
    def __init__(self):
        self.connected_count = 0
        self.disconnected_count = 0
        self.reconnecting_events = []
        self.error_events = []
    
    def on_connected(self) -> None:
        self.connected_count += 1
    
    def on_disconnected(self, reason=None) -> None:
        self.disconnected_count += 1
    
    def on_reconnecting(self, attempt: int, max_attempts: int) -> None:
        self.reconnecting_events.append((attempt, max_attempts))
    
    def on_error(self, error: str) -> None:
        self.error_events.append(error)


class TestConnectionConfig:
    """Tests for connection configuration."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = ConnectionConfig()
        
        assert config.host == "127.0.0.1"
        assert config.port == 7497
        assert config.client_id == 1
        assert config.readonly is False
        assert config.auto_reconnect is True
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = ConnectionConfig(
            host="192.168.1.100",
            port=4001,
            client_id=10,
            readonly=True,
        )
        
        assert config.host == "192.168.1.100"
        assert config.port == 4001
        assert config.client_id == 10
        assert config.readonly is True
    
    def test_reconnection_settings(self):
        """Test reconnection configuration."""
        config = ConnectionConfig(
            max_reconnect_attempts=10,
            reconnect_delay_seconds=2.0,
            reconnect_backoff_multiplier=1.5,
        )
        
        assert config.max_reconnect_attempts == 10
        assert config.reconnect_delay_seconds == 2.0
        assert config.reconnect_backoff_multiplier == 1.5


class TestConnectionManager:
    """Tests for connection manager."""
    
    def test_initial_state(self):
        """Test initial state is disconnected."""
        mock_ib = MockIB()
        manager = IBKRConnectionManager(ib=mock_ib)
        
        assert manager.stats.state == ConnectionState.DISCONNECTED
        assert manager.is_connected is False
    
    def test_successful_connect(self):
        """Test successful connection."""
        mock_ib = MockIB()
        callback = MockConnectionCallback()
        manager = IBKRConnectionManager(ib=mock_ib, callback=callback)
        
        result = manager.connect()
        
        assert result is True
        assert manager.is_connected is True
        assert manager.stats.state == ConnectionState.CONNECTED
        assert callback.connected_count == 1
        
        # Cleanup
        manager.disconnect()
    
    def test_failed_connect(self):
        """Test connection failure."""
        mock_ib = MockIB(fail_connect=True)
        callback = MockConnectionCallback()
        config = ConnectionConfig(auto_reconnect=False)
        manager = IBKRConnectionManager(config=config, ib=mock_ib, callback=callback)
        
        result = manager.connect()
        
        assert result is False
        assert manager.is_connected is False
        assert manager.stats.state == ConnectionState.ERROR
        assert len(callback.error_events) == 1
    
    def test_disconnect(self):
        """Test disconnection."""
        mock_ib = MockIB()
        callback = MockConnectionCallback()
        manager = IBKRConnectionManager(ib=mock_ib, callback=callback)
        
        manager.connect()
        manager.disconnect()
        
        assert manager.is_connected is False
        assert manager.stats.state == ConnectionState.DISCONNECTED
        assert callback.disconnected_count == 1
    
    def test_get_stats(self):
        """Test getting connection stats."""
        mock_ib = MockIB()
        manager = IBKRConnectionManager(ib=mock_ib)
        
        manager.connect()
        stats = manager.get_stats()
        
        assert stats.state == ConnectionState.CONNECTED
        assert stats.connected_since is not None
        
        manager.disconnect()
    
    def test_stats_copy(self):
        """Test that get_stats returns a copy."""
        mock_ib = MockIB()
        manager = IBKRConnectionManager(ib=mock_ib)
        
        manager.connect()
        stats1 = manager.get_stats()
        stats2 = manager.get_stats()
        
        # Should be equal but not same object
        assert stats1.state == stats2.state
        assert stats1 is not stats2
        
        manager.disconnect()
    
    def test_reset_stats(self):
        """Test resetting stats."""
        mock_ib = MockIB()
        manager = IBKRConnectionManager(ib=mock_ib)
        
        manager.stats.total_reconnects = 5
        manager.stats.last_error = "test error"
        
        manager.reset_stats()
        
        assert manager.stats.total_reconnects == 0
        assert manager.stats.last_error is None


class TestConnectionState:
    """Tests for connection state enum."""
    
    def test_state_values(self):
        """Test state enum has expected values."""
        assert ConnectionState.DISCONNECTED.value == "DISCONNECTED"
        assert ConnectionState.CONNECTING.value == "CONNECTING"
        assert ConnectionState.CONNECTED.value == "CONNECTED"
        assert ConnectionState.RECONNECTING.value == "RECONNECTING"
        assert ConnectionState.ERROR.value == "ERROR"
