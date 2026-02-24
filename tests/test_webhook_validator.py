"""Tests for webhook validation."""

import pytest
import hmac
import hashlib
import base64

from src.middleware.webhook_validator import WebhookValidator
from src.utils.exceptions import WebhookValidationError


class TestWebhookValidator:
    """Tests for WebhookValidator."""

    @pytest.fixture
    def validator(self, monkeypatch):
        """Create a WebhookValidator with test secret."""
        # Mock the config
        class MockConfig:
            class EnvConfig:
                shopify_webhook_secret = "test_secret"

            class WebhookConfig:
                validate_signature = True

            env = EnvConfig()
            webhook = WebhookConfig()

        def mock_get_config():
            return MockConfig()

        monkeypatch.setattr("src.middleware.webhook_validator.get_config", mock_get_config)
        return WebhookValidator()

    def create_signature(self, body: bytes, secret: str) -> str:
        """Create a valid HMAC signature."""
        return base64.b64encode(
            hmac.new(
                secret.encode('utf-8'),
                body,
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

    def test_validate_signature_success(self, validator):
        """Test successful signature validation."""
        body = b'{"test": "data"}'
        signature = self.create_signature(body, "test_secret")

        result = validator.validate_signature(body, signature)

        assert result is True

    def test_validate_signature_invalid(self, validator):
        """Test invalid signature raises error."""
        body = b'{"test": "data"}'
        invalid_signature = "invalid_signature"

        with pytest.raises(WebhookValidationError, match="Invalid webhook signature"):
            validator.validate_signature(body, invalid_signature)

    def test_validate_signature_missing(self, validator):
        """Test missing signature raises error."""
        body = b'{"test": "data"}'

        with pytest.raises(WebhookValidationError, match="Missing webhook signature"):
            validator.validate_signature(body, None)

    def test_validate_shopify_domain_success(self, validator):
        """Test valid Shopify domain."""
        result = validator.validate_shopify_domain("test-shop.myshopify.com")

        assert result is True

    def test_validate_shopify_domain_invalid(self, validator):
        """Test invalid domain raises error."""
        with pytest.raises(WebhookValidationError, match="Invalid shop domain"):
            validator.validate_shopify_domain("malicious-site.com")

    def test_validate_shopify_domain_missing(self, validator):
        """Test missing domain raises error."""
        with pytest.raises(WebhookValidationError, match="Missing shop domain"):
            validator.validate_shopify_domain(None)
