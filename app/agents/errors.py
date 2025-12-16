class AgentTimeout(Exception):
    """Raised when an agent call exceeds the configured timeout."""


class AgentCallError(Exception):
    """Raised when an agent call fails for any non-timeout reason."""
