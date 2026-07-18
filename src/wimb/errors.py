"""Explicit failures surfaced to the presentation layer."""


class WimbError(Exception):
    """Base WIMB exception."""


class ConfigurationError(WimbError):
    """Required local configuration is missing or invalid."""


class ApiError(WimbError):
    """The 511 API could not fulfil a request."""


class ApiUnavailableError(ApiError):
    """511 returned a server error or was unreachable."""


class ApiAuthenticationError(ApiError):
    """The configured API key was rejected."""


class StaleFeedError(WimbError):
    """A realtime feed timestamp is too old to represent live facts."""


class NoLiveVehiclesError(WimbError):
    """No vehicle positions are available for the configured route."""


class NoUsableRealtimeDataError(WimbError):
    """Vehicles exist but cannot be joined to a current-stop delay fact."""
