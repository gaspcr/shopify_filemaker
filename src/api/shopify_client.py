"""Shopify Admin API client (GraphQL)."""

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
    """Client for the Shopify Admin GraphQL API."""

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

        # Normalise location_id to the full GID format regardless of whether
        # the env var was set as a plain number or a gid:// string.
        raw_loc = config.env.shopify_location_id
        if raw_loc.startswith(GID_LOCATION_PREFIX):
            self.location_gid = raw_loc
        else:
            self.location_gid = f"{GID_LOCATION_PREFIX}{raw_loc}"

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
    # Low-level GraphQL helper
    # ------------------------------------------------------------------

    def _graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute a GraphQL operation and return the ``data`` dict.

        Raises:
            ShopifyAPIError: On HTTP failure or top-level GraphQL errors.
        """
        endpoint = f"/admin/api/{self.api_version}/graphql.json"
        payload: Dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            response = self.post(endpoint, json=payload)
            self._handle_rate_limit(response)

            if response.status_code != 200:
                raise ShopifyAPIError(
                    f"GraphQL request failed (HTTP {response.status_code})",
                    details={"response": response.text}
                )

            body = response.json()

            if "errors" in body:
                raise ShopifyAPIError(
                    "GraphQL errors",
                    details={"errors": body["errors"]}
                )

            time.sleep(self.rate_limit_delay)
            return body.get("data", {})

        except (ShopifyAPIError, RateLimitError):
            raise
        except httpx.HTTPError as e:
            raise ShopifyAPIError(f"HTTP error: {str(e)}", details={"error": str(e)})

    # ------------------------------------------------------------------
    # Inventory queries
    # ------------------------------------------------------------------

    _QUERY_INVENTORY_BY_SKU = """
    query getInventoryBySKU($sku: String!) {
      productVariants(first: 1, query: $sku) {
        edges {
          node {
            id
            sku
            inventoryItem {
              id
              inventoryLevels(first: 5) {
                edges {
                  node {
                    location {
                      id
                    }
                    quantities(names: ["available"]) {
                      name
                      quantity
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    def get_inventory_by_sku(self, sku: str) -> Optional[StockItem]:
        """
        Look up the current *available* inventory for a single SKU at the
        configured location.

        Args:
            sku: Shopify variant SKU — must match the FileMaker
                 ``Conceptos Cobro_pk`` value exactly (e.g. "852738006010").

        Returns:
            A ``StockItem`` with the current available quantity, or **None**
            when the SKU does not exist in Shopify.
        """
        variables = {"sku": f"sku:{sku}"}

        try:
            data = self._graphql(self._QUERY_INVENTORY_BY_SKU, variables)
        except ShopifyAPIError:
            raise
        except Exception as e:
            raise ShopifyAPIError(f"Failed to query inventory for SKU {sku}: {str(e)}")

        edges = data.get("productVariants", {}).get("edges", [])
        if not edges:
            self.logger.warning(f"SKU not found in Shopify: {sku}")
            return None

        variant = edges[0]["node"]
        inventory_item = variant.get("inventoryItem", {})
        levels = inventory_item.get("inventoryLevels", {}).get("edges", [])

        # Walk inventory levels and find our location
        quantity = 0
        for level in levels:
            node = level["node"]
            if node["location"]["id"] == self.location_gid:
                for q in node.get("quantities", []):
                    if q["name"] == "available":
                        quantity = q["quantity"]
                        break
                break

        return StockItem(
            sku=sku,
            quantity=quantity,
            source="shopify",
            metadata={
                "variant_id": variant["id"],
                "inventory_item_id": inventory_item["id"]
            }
        )

    # ------------------------------------------------------------------
    # Inventory mutations
    # ------------------------------------------------------------------

    _MUTATION_SET_QUANTITIES = """
    mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) {
      inventorySetQuantities(input: $input) {
        userErrors {
          field
          message
        }
        inventoryAdjustmentGroup {
          id
        }
      }
    }
    """

    def update_inventory(self, sku: str, quantity: int) -> bool:
        """
        Set the *available* inventory for ``sku`` at the configured location.

        Fetches the ``inventoryItemId`` via ``get_inventory_by_sku`` first,
        then issues the ``inventorySetQuantities`` mutation.

        Args:
            sku: Shopify variant SKU (= FileMaker ``Conceptos Cobro_pk``).
            quantity: Absolute quantity to set.

        Returns:
            True on success.

        Raises:
            SKUNotFoundError: If the SKU does not exist in Shopify.
            ShopifyAPIError: If the mutation returns ``userErrors``.
        """
        stock_item = self.get_inventory_by_sku(sku)
        if not stock_item:
            raise SKUNotFoundError(f"SKU not found in Shopify: {sku}")

        inventory_item_id = stock_item.metadata.get("inventory_item_id")
        if not inventory_item_id:
            raise ShopifyAPIError(f"No inventory item ID for SKU: {sku}")

        variables = {
            "input": {
                "reason": "correction",
                "name": "available",
                "quantities": [
                    {
                        "inventoryItemId": inventory_item_id,
                        "locationId": self.location_gid,
                        "quantity": quantity
                    }
                ]
            }
        }

        try:
            data = self._graphql(self._MUTATION_SET_QUANTITIES, variables)
        except ShopifyAPIError:
            raise
        except Exception as e:
            raise ShopifyAPIError(f"Failed to update inventory for {sku}: {str(e)}")

        result = data.get("inventorySetQuantities", {})
        user_errors = result.get("userErrors", [])

        if user_errors:
            error_messages = [f"{e.get('field')}: {e.get('message')}" for e in user_errors]
            raise ShopifyAPIError(
                f"Shopify rejected inventory update for {sku}",
                details={"errors": error_messages}
            )

        self.logger.info(f"Updated Shopify inventory for {sku}: {quantity}")
        return True

    # ------------------------------------------------------------------
    # Bulk helper
    # ------------------------------------------------------------------

    def bulk_update_inventory(self, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Call ``update_inventory`` for each item in *updates*.

        Args:
            updates: List of ``{"sku": "…", "quantity": N}`` dicts.

        Returns:
            ``{"success_count": int, "error_count": int, "errors": [...]}``.
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
    # Order query (utility)
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch order details by Shopify GraphQL ID.

        Args:
            order_id: e.g. ``gid://shopify/Order/123456789``
        """
        query = """
        query getOrder($id: ID!) {
          order(id: $id) {
            id
            name
            lineItems(first: 50) {
              edges {
                node {
                  sku
                  quantity
                  variant {
                    id
                  }
                }
              }
            }
          }
        }
        """

        try:
            data = self._graphql(query, {"id": order_id})
            return data.get("order")
        except ShopifyAPIError:
            raise
        except Exception as e:
            raise ShopifyAPIError(f"Failed to get order {order_id}: {str(e)}")
