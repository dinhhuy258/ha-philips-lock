"""Exceptions of the cloud client."""


class CloudError(Exception):
    """Base class of every cloud client error."""


class AuthError(CloudError):
    """The cloud refused the credentials or the session cannot be renewed."""


class CloudConnectionError(CloudError):
    """A transient failure: network error, unreadable reply or a rate-limited re-login."""


class CommandError(CloudError):
    """The cloud accepted the request but refused the lock command."""
