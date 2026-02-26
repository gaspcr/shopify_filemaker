"""Shopify Admin REST API client."""

import time
from typing import List, Dict, Any, Optional
import httpx

from .base_client import BaseClient
from ..utils.config import get_config
from ..utils.logger import get_api_logger
from ..utils.exceptions import ShopifyAPIError, SKUNotFoundError, RateLimitError
from ..models.product import StockItem

GID_LOCATION_PREFIX = "gid://shopify/Location/"


class ShopifyClient(BaseClient):
    """Client for the Shopify Admin REST API."""

    def __init__(self):
        """Initialize Shopify client from environment configuration."""
        config = get_config()
        shop_url = config.env.shopify_shop_url
        access_token = config.env.shopify_access_token

        if not shop_url.startswith("https://"):
            shop_url = f"https://{shop_url}"

        headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json"
        }

        super().__init__(base_url=shop_url, headers=headers)
        self.logger = get_api_logger()
        self.api_version = config.shopify.api_version
        self.rate_limit_delay = config.shopify.rate_limit_delay

        # Normalise location_id to a plain numeric string (REST API
        # expects a number, not the gid:// format).
        raw_loc = config.env.shopify_location_id
        if raw_loc.startswith(GID_LOCATION_PREFIX):
            self.location_id = raw_loc[len(GID_LOCATION_PREFIX):]
        else:
            self.location_id = raw_loc

    # ------------------------------------------------------------------
    # Rate-limit handling
    # ------------------------------------------------------------------

    def _handle_rate_limit(self, response: httpx.Response):
        """Inspect Shopify headers and back off when approaching limits."""
        rate_limit_header = response.headers.get("X-Shopify-Shop-Api-Call-Limit")
        if rate_limit_header:
            current, limit = map(int, rate_limit_header.split("/"))
            if current >= limit * 0.9:
                self.logger.warning(f"Approaching rate limit: {current}/{limit}. Waiting...")
                time.sleep(self.rate_limit_delay * 2)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 2))
            self.logger.warning(f"Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            raise RateLimitError(f"Rate limited. Retry after {retry_after}s.")

    # ------------------------------------------------------------------
    # Low-level REST helper
    # ------------------------------------------------------------------

    def _rest_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute a GET request against the Shopify Admin REST API.

        Args:
            path: e.g. "/admin/api/2026-01/variants.json"
            params: Optional query parameters

        Returns:
            Parsed JSON response body.

        Raises:
            ShopifyAPIError: On HTTP failure or unexpected response.
        """
        try:
            response = self.get(path, params=params)
            self._handle_rate_limit(response)

            if response.status_code != 200:
                raise ShopifyAPIError(
                    f"REST GET {path} failed (HTTP {response.status_code})",
                    details={"response": response.text}
                )

            time.sleep(self.rate_limit_delay)
            return response.json()

        except (ShopifyAPIError, RateLimitError):
            raise
        except httpx.HTTPError as e:
            raise ShopifyAPIError(f"HTTP error on GET {path}: {str(e)}")

    def _rest_post(self, path: str, json_body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a POST request against the Shopify Admin REST API.

        Returns:
            Parsed JSON response body.
        """
        try:
            response = self.post(path, json=json_body)
            self._handle_rate_limit(response)

            if response.status_code not in (200, 201):
                raise ShopifyAPIError(
                    f"REST POST {path} failed (HTTP {response.status_code})",
                    details={"response": response.text}
                )

            time.sleep(self.rate_limit_delay)
            return response.json()

        except (ShopifyAPIError, RateLimitError):
            raise
        except httpx.HTTPError as e:
            raise ShopifyAPIError(f"HTTP error on POST {path}: {str(e)}")

    # ------------------------------------------------------------------
    # Inventory queries
    # ------------------------------------------------------------------

    def get_inventory_by_sku(self, sku: str) -> Optional[StockItem]:
        """
        Look up the current *available* inventory for a single SKU at
        the configured location using the REST API.

        Step 1: GET /admin/api/{version}/variants.json?sku={sku}
        Step 2: GET /admin/api/{version}/inventory_levels.json
                    ?inventory_item_ids={id}&location_ids={loc}

        Args:
            sku: Shopify variant SKU — must match the FileMaker
                 ``Conceptos Cobro_pk`` value exactly.

        Returns:
            A StockItem with current available quantity, or None if the
            SKU does not exist in Shopify.
        """
        v = self.api_version

        # ── Step 1: Find the variant by SKU ───────────────────────────
        try:
            data = self._rest_get(
                f"/admin/api/{v}/variants.json",
                params={"sku": sku}
            )
        except ShopifyAPIError as e:
            self.logger.error(f"Error looking up variant for SKU {sku}: {e.message}")
            raise

        variants = data.get("variants", [])
        if not variants:
            self.logger.debug(f"SKU not found in Shopify: {sku}")
            return None

        variant = variants[0]
        variant_id = variant["id"]
        inventory_item_id = variant.get("inventory_item_id")

        if not inventory_item_id:
            self.logger.warning(f"Variant {variant_id} has no inventory_item_id")
            return None

        # ── Step 2: Get inventory level at our location ───────────────
        try:
            inv_data = self._rest_get(
                f"/admin/api/{v}/inventory_levels.json",
                params={
                    "inventory_item_ids": inventory_item_id,
                    "location_ids": self.location_id
                }
            )
        except ShopifyAPIError as e:
            self.logger.error(f"Error fetching inventory level for SKU {sku}: {e.message}")
            raise

        levels = inv_data.get("inventory_levels", [])
        quantity = 0
        if levels:
            quantity = levels[0].get("available", 0) or 0

        return StockItem(
            sku=sku,
            quantity=quantity,
            source="shopify",
            metadata={
                "variant_id": str(variant_id),
                "inventory_item_id": str(inventory_item_id),
                "product_id": str(variant.get("product_id", "")),
                "title": variant.get("title", ""),
            }
        )

    # ------------------------------------------------------------------
    # Inventory mutations
    # ------------------------------------------------------------------

    def update_inventory(self, sku: str, quantity: int) -> bool:
        """
        Set the *available* inventory for ``sku`` at the configured location.

        Step 1: Fetch inventory_item_id via get_inventory_by_sku(sku)
        Step 2: POST /admin/api/{version}/inventory_levels/set.json

        Args:
            sku: Shopify variant SKU (= FileMaker Conceptos Cobro_pk).
            quantity: Absolute quantity to set.

        Returns:
            True on success.

        Raises:
            SKUNotFoundError: If the SKU does not exist in Shopify.
            ShopifyAPIError: If the API rejects the update.
        """
        stock_item = self.get_inventory_by_sku(sku)
        if not stock_item:
            raise SKUNotFoundError(f"SKU not found in Shopify: {sku}")

        inventory_item_id = stock_item.metadata.get("inventory_item_id")
        if not inventory_item_id:
            raise ShopifyAPIError(f"No inventory item ID for SKU: {sku}")

        v = self.api_version

        body = {
            "location_id": int(self.location_id),
            "inventory_item_id": int(inventory_item_id),
            "available": quantity
        }

        try:
            result = self._rest_post(
                f"/admin/api/{v}/inventory_levels/set.json",
                json_body=body
            )
        except ShopifyAPIError as e:
            self.logger.error(f"Failed to update inventory for {sku}: {e.message}")
            raise

        self.logger.info(f"Updated Shopify inventory for {sku}: {quantity}")
        return True

    # ------------------------------------------------------------------
    # Bulk helper
    # ------------------------------------------------------------------

    def bulk_update_inventory(self, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Call update_inventory for each item in *updates*.

        Args:
            updates: List of {"sku": "…", "quantity": N} dicts.

        Returns:
            {"success_count": int, "error_count": int, "errors": [...]}.
        """
        results: Dict[str, Any] = {
            "success_count": 0,
            "error_count": 0,
            "errors": []
        }

        for update in updates:
            sku = update["sku"]
            qty = update["quantity"]

            try:
                self.update_inventory(sku, qty)
                results["success_count"] += 1
            except Exception as e:
                results["error_count"] += 1
                results["errors"].append({"sku": sku, "error": str(e)})
                self.logger.error(f"Failed to update {sku}: {str(e)}")

        return results

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        """Close the underlying HTTP client."""
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
