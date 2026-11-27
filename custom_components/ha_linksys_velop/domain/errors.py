"""Domain errors raised across layer boundaries."""

from __future__ import annotations


class VelopError(Exception):
    """Base class for every error raised by this integration."""


class CannotConnectError(VelopError):
    """The hub could not be reached or answered with unusable data."""


class ConnectionTimeoutError(CannotConnectError):
    """The hub did not answer within the allowed time."""


class UnsupportedDeviceError(VelopError):
    """The endpoint is reachable but does not speak JNAP."""


class InvalidAuthError(VelopError):
    """The hub rejected the admin credentials."""


class AccountLockedError(VelopError):
    """The hub locked or delayed admin authentication attempts."""


class JnapResultError(VelopError):
    """The hub returned a non-OK JNAP result for an action."""

    def __init__(self, result: str) -> None:
        """Store the JNAP result code."""
        super().__init__(result)
        self.result = result


class UnknownActionError(JnapResultError):
    """The firmware does not implement the requested JNAP action."""


class NotPrimaryNodeError(VelopError):
    """The endpoint is a child node, not the primary node."""


class InvalidHostError(VelopError):
    """The supplied host is not an acceptable hub address."""


class SecretUnavailableError(VelopError):
    """The stored admin password cannot be decrypted (missing or wrong key)."""


class OperationInProgressError(JnapResultError):
    """The hub is already running the requested operation."""
