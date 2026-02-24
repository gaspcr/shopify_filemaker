"""Shopify webhook signature validation middleware."""

import hmac
import hashlib
import base64
from typing import Optional

from ..utils.config import get_config
from ..utils.logger import get_webhook_logger
from ..utils.exceptions import WebhookValidationError


class WebhookValidator:
    """Validates Shopify webhook signatures."""

    def __init__(self):
        """Initialize webhook validator."""
        config = get_config()
        self.secret = config.env.shopify_webhook_secret
        self.logger = get_webhook_logger()
        self.validate_enabled = config.webhook.validate_signature

    def validate_signature(self, body: bytes, signature_header: Optional[str]) -> bool:
        """
        Validate Shopify webhook HMAC signature.

        Args:
            body: Raw request body bytes
            signature_header: Value of X-Shopify-Hmac-SHA256 header

        Returns:
            True if signature is valid

        Raises:
            WebhookValidationError: If validation fails
        """
        if not self.validate_enabled:
            self.logger.warning("Webhook signature validation is disabled!")
            return True

        if not signature_header:
            raise WebhookValidationError(
                "Missing webhook signature header",
                details={"header": "X-Shopify-Hmac-SHA256"}
            )

        try:
            # Calculate expected signature
            expected_signature = base64.b64encode(
                hmac.new(
                    self.secret.encode('utf-8'),
                    body,
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')

            # Compare signatures (constant-time comparison)
            is_valid = hmac.compare_digest(expected_signature, signature_header)

            if not is_valid:
                raise WebhookValidationError(
                    "Invalid webhook signature",
                    details={
                        "expected": expected_signature[:10] + "...",
                        "received": signature_header[:10] + "..."
                    }
                )

            self.logger.debug("Webhook signature validated successfully")
            return True

        except Exception as e:
            if isinstance(e, WebhookValidationError):
                raise
            raise WebhookValidationError(
                f"Signature validation error: {str(e)}",
                details={"error": str(e)}
            )

    def validate_shopify_domain(self, shop_domain: Optional[str]) -> bool:
        """
        Validate that the shop domain matches expected pattern.

        Args:
            shop_domain: Shop domain from webhook

        Returns:
            True if domain is valid

        Raises:
            WebhookValidationError: If domain is invalid
        """
        if not shop_domain:
            raise WebhookValidationError("Missing shop domain in webhook")

        # Shopify domains should end with .myshopify.com
        if not shop_domain.endswith(".myshopify.com"):
            raise WebhookValidationError(
                f"Invalid shop domain: {shop_domain}",
                details={"domain": shop_domain}
            )

        self.logger.debug(f"Shop domain validated: {shop_domain}")
        return True
