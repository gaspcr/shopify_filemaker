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

        # Normalise location_id to a plain numeric string
        raw_loc = config.env.shopify_location_id
        if raw_loc.startswith(GID_LOCATION_PREFIX):
            self.location_id = raw_loc[len(GID_LOCATION_PREFIX):]
        else:
            self.location_id = raw_loc

        # Cached SKU → variant mapping (built lazily)
        self._sku_cache: Optional[Dict[str, Dict[str, Any]]] = None

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
    # Low-level REST helpers
    # ------------------------------------------------------------------

    def _rest_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET request against the Shopify Admin REST API."""
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
        """POST request against the Shopify Admin REST API."""
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
    # SKU cache — fetch ALL products once and build a lookup table
    # ------------------------------------------------------------------

    def _build_sku_cache(self) -> Dict[str, Dict[str, Any]]:
        """
        Fetch all products (paginated) and build a SKU → variant info map.

        The /variants.json endpoint does NOT support SKU filtering, so we
        must fetch all products and search locally.  We use the products
        endpoint with ``fields`` to minimise payload size.
        """
        self.logger.info("Building SKU cache — fetching all products from Shopify...")
        v = self.api_version
        sku_map: Dict[str, Dict[str, Any]] = {}
        page = 0
        page_info: Optional[str] = None

        while True:
            page += 1
            if page_info:
                # Cursor-based pagination
                params = {"limit": 250, "page_info": page_info, "fields": "id,title,variants"}
                url = f"/admin/api/{v}/products.json"
            else:
                params = {"limit": 250, "fields": "id,title,variants"}
                url = f"/admin/api/{v}/products.json"

            data = self._rest_get(url, params=params)
            products = data.get("products", [])

            for product in products:
                product_title = product.get("title", "")
                for variant in product.get("variants", []):
                    sku = variant.get("sku", "")
                    if sku:
                        sku_map[sku] = {
                            "variant_id": variant["id"],
                            "inventory_item_id": variant.get("inventory_item_id"),
                            "inventory_quantity": variant.get("inventory_quantity", 0),
                            "product_id": product.get("id"),
                            "product_title": product_title,
                        }

            self.logger.info(
                f"  Page {page}: {len(products)} products "
                f"(cache size: {len(sku_map)} SKUs)"
            )

            if len(products) < 250:
                break

            # Extract cursor from Link header for next page
            page_info = self._extract_page_info(data)
            if not page_info:
                break

        self.logger.info(f"SKU cache built: {len(sku_map)} variants indexed")
        return sku_map

    def _extract_page_info(self, response_data: Any) -> Optional[str]:
        """
        Extract page_info cursor from Link header for Shopify pagination.

        NOTE: Our current _rest_get returns parsed JSON and doesn't expose
        headers. We rely instead on the product count heuristic (< 250
        means last page).  This method is a placeholder for future
        enhancement with proper header-based cursor pagination.
        """
        # For now we rely on "len(products) < 250" in the caller.
        return None

    def invalidate_cache(self):
        """Clear the SKU cache so it gets rebuilt on next access."""
        self._sku_cache = None

    def _get_sku_map(self) -> Dict[str, Dict[str, Any]]:
        """Get or build the SKU cache."""
        if self._sku_cache is None:
            self._sku_cache = self._build_sku_cache()
        return self._sku_cache

    # ------------------------------------------------------------------
    # Inventory queries
    # ------------------------------------------------------------------

    def get_inventory_by_sku(self, sku: str) -> Optional[StockItem]:
        """
        Look up the current *available* inventory for a single SKU at
        the configured location.

        Uses the pre-built SKU cache (fetches all products once) and then
        queries inventory_levels for the specific inventory_item_id.

        Returns:
            A StockItem with current available quantity, or None if the
            SKU does not exist in Shopify.
        """
        sku_map = self._get_sku_map()
        variant_info = sku_map.get(sku)

        if not variant_info:
            return None

        inventory_item_id = variant_info["inventory_item_id"]
        v = self.api_version

        # Get inventory level at our location
        try:
            inv_data = self._rest_get(
                f"/admin/api/{v}/inventory_levels.json",
                params={
                    "inventory_item_ids": str(inventory_item_id),
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
                "variant_id": str(variant_info["variant_id"]),
                "inventory_item_id": str(inventory_item_id),
                "product_id": str(variant_info.get("product_id", "")),
                "product_title": variant_info.get("product_title", ""),
            }
        )

    # ------------------------------------------------------------------
    # Inventory mutations
    # ------------------------------------------------------------------

    def update_inventory(self, sku: str, quantity: int) -> bool:
        """
        Set the *available* inventory for ``sku`` at the configured location.

        Args:
            sku: Shopify variant SKU (= FileMaker Conceptos Cobro_pk).
            quantity: Absolute quantity to set.

        Returns:
            True on success.
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
            self._rest_post(
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
        """Call update_inventory for each item in *updates*."""
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
