"""Custom exception classes for the application."""


class BaseAppException(Exception):
    """Base exception class for all application exceptions."""

    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class FileMakerAPIError(BaseAppException):
    """Raised when FileMaker API encounters an error."""
    pass


class ShopifyAPIError(BaseAppException):
    """Raised when Shopify API encounters an error."""
    pass


class SKUNotFoundError(BaseAppException):
    """Raised when a SKU is not found in the target system."""
    pass


class AuthenticationError(BaseAppException):
    """Raised when authentication fails."""
    pass


class ConfigurationError(BaseAppException):
    """Raised when there's a configuration error."""
    pass


class WebhookValidationError(BaseAppException):
    """Raised when webhook signature validation fails."""
    pass


class SyncError(BaseAppException):
    """Raised when synchronization fails."""
    pass


class RateLimitError(BaseAppException):
    """Raised when API rate limit is exceeded."""
    pass
