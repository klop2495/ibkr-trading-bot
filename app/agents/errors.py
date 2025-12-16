class AgentError(Exception):
    """Base class for agent-related errors."""


class AgentTimeout(AgentError):
    """Raised when an agent call exceeds the configured timeout."""


class AgentHTTPError(AgentError):
    def __init__(self, message: str, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AgentSchemaError(AgentError):
    """Raised when a response does not match the expected schema."""


class AgentRefusal(AgentError):
    """Raised when the model refuses or does not produce structured output."""


# Alias retained for compatibility with other components if needed.
class AgentCallError(AgentError):
    """Raised when an agent call fails for any non-timeout reason."""
