"""Base HTTP client with retry logic and error handling."""

import httpx
from typing import Optional, Dict, Any
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from ..utils.config import get_config
from ..utils.logger import get_api_logger
from ..utils.exceptions import RateLimitError


class BaseClient:
    """Base HTTP client with retry logic and logging."""

    def __init__(self, base_url: str, headers: Optional[Dict[str, str]] = None):
        """
        Initialize base client.

        Args:
            base_url: Base URL for API requests
            headers: Optional default headers
        """
        self.base_url = base_url.rstrip("/")
        self.config = get_config()
        self.logger = get_api_logger()

        default_headers = {
            "Content-Type": "application/json",
            "User-Agent": "FileMaker-Shopify-Sync/1.0"
        }

        if headers:
            default_headers.update(headers)

        self.client = httpx.Client(
            base_url=self.base_url,
            headers=default_headers,
            timeout=self.config.api.timeout,
            follow_redirects=True
        )

    def _make_request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            HTTP response

        Raises:
            Exception: If all retry attempts fail
        """
        @retry(
            stop=stop_after_attempt(self.config.api.max_retries),
            wait=wait_exponential(multiplier=self.config.api.retry_delay) if self.config.api.exponential_backoff else None,
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
            reraise=True
        )
        def _request():
            self.logger.debug(f"{method} {url}")
            response = self.client.request(method, url, **kwargs)
            self.logger.debug(f"Response: {response.status_code}")
            return response

        return _request()

    def get(self, endpoint: str, **kwargs) -> httpx.Response:
        """Make GET request."""
        return self._make_request_with_retry("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs) -> httpx.Response:
        """Make POST request."""
        return self._make_request_with_retry("POST", endpoint, **kwargs)

    def put(self, endpoint: str, **kwargs) -> httpx.Response:
        """Make PUT request."""
        return self._make_request_with_retry("PUT", endpoint, **kwargs)

    def patch(self, endpoint: str, **kwargs) -> httpx.Response:
        """Make PATCH request."""
        return self._make_request_with_retry("PATCH", endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs) -> httpx.Response:
        """Make DELETE request."""
        return self._make_request_with_retry("DELETE", endpoint, **kwargs)

    def close(self):
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
