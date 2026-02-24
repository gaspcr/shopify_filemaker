"""Shopify Admin API client."""

import time
from typing import List, Dict, Any, Optional
import httpx

from .base_client import BaseClient
from ..utils.config import get_config
from ..utils.logger import get_api_logger
from ..utils.exceptions import ShopifyAPIError, SKUNotFoundError, RateLimitError
from ..models.product import StockItem


class ShopifyClient(BaseClient):
    """Client for interacting with Shopify Admin API."""

    def __init__(self):
        """Initialize Shopify client."""
        config = get_config()
        shop_url = config.env.shopify_shop_url
        access_token = config.env.shopify_access_token

        # Ensure shop URL format
        if not shop_url.startswith("https://"):
            shop_url = f"https://{shop_url}"

        headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json"
        }

        super().__init__(base_url=shop_url, headers=headers)
        self.logger = get_api_logger()
        self.location_id = config.env.shopify_location_id
        self.api_version = config.shopify.api_version
        self.rate_limit_delay = config.shopify.rate_limit_delay

    def _handle_rate_limit(self, response: httpx.Response):
        """
        Handle Shopify rate limiting.

        Args:
            response: HTTP response to check for rate limit headers
        """
        # Check rate limit header
        rate_limit_header = response.headers.get("X-Shopify-Shop-Api-Call-Limit")
        if rate_limit_header:
            current, limit = map(int, rate_limit_header.split("/"))
            if current >= limit * 0.9:  # If at 90% capacity
                self.logger.warning(f"Approaching rate limit: {current}/{limit}. Waiting...")
                time.sleep(self.rate_limit_delay * 2)

        # Handle 429 Too Many Requests
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 2))
            self.logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
            time.sleep(retry_after)
            raise RateLimitError(f"Rate limited. Retry after {retry_after} seconds.")

    def _make_graphql_request(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make GraphQL request to Shopify.

        Args:
            query: GraphQL query string
            variables: Optional query variables

        Returns:
            GraphQL response data

        Raises:
            ShopifyAPIError: If the request fails
        """
        endpoint = f"/admin/api/{self.api_version}/graphql.json"

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            response = self.post(endpoint, json=payload)
            self._handle_rate_limit(response)

            if response.status_code != 200:
                raise ShopifyAPIError(
                    f"GraphQL request failed: {response.status_code}",
                    details={"response": response.text}
                )

            data = response.json()

            # Check for GraphQL errors
            if "errors" in data:
                raise ShopifyAPIError(
                    "GraphQL errors",
                    details={"errors": data["errors"]}
                )

            # Small delay to respect rate limits
            time.sleep(self.rate_limit_delay)

            return data.get("data", {})

        except httpx.HTTPError as e:
            raise ShopifyAPIError(f"HTTP error: {str(e)}", details={"error": str(e)})

    def get_inventory_by_sku(self, sku: str) -> Optional[StockItem]:
        """
        Get inventory information for a product by SKU.

        Args:
            sku: Product SKU

        Returns:
            StockItem if found, None otherwise

        Raises:
            ShopifyAPIError: If the request fails
        """
        query = """
        query getInventoryBySKU($sku: String!) {
          productVariants(first: 1, query: $sku) {
            edges {
              node {
                id
                sku
                inventoryItem {
                  id
                  inventoryLevels(first: 10) {
                    edges {
                      node {
                        location {
                          id
                        }
                        available
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """

        variables = {"sku": f"sku:{sku}"}

        try:
            data = self._make_graphql_request(query, variables)

            edges = data.get("productVariants", {}).get("edges", [])
            if not edges:
                self.logger.warning(f"SKU not found in Shopify: {sku}")
                return None

            variant = edges[0]["node"]
            inventory_item = variant.get("inventoryItem", {})
            inventory_levels = inventory_item.get("inventoryLevels", {}).get("edges", [])

            # Find inventory for our location
            quantity = 0
            for level in inventory_levels:
                location_id = level["node"]["location"]["id"]
                if location_id == self.location_id:
                    quantity = level["node"]["available"]
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

        except Exception as e:
            raise ShopifyAPIError(f"Failed to get inventory for SKU {sku}: {str(e)}")

    def update_inventory(self, sku: str, quantity: int) -> bool:
        """
        Update inventory quantity for a product.

        Args:
            sku: Product SKU
            quantity: New quantity

        Returns:
            True if successful

        Raises:
            ShopifyAPIError: If the update fails
            SKUNotFoundError: If SKU is not found
        """
        # First get the inventory item ID
        stock_item = self.get_inventory_by_sku(sku)
        if not stock_item:
            raise SKUNotFoundError(f"SKU not found: {sku}")

        inventory_item_id = stock_item.metadata.get("inventory_item_id")
        if not inventory_item_id:
            raise ShopifyAPIError(f"No inventory item ID for SKU: {sku}")

        mutation = """
        mutation inventorySetQuantity($input: InventorySetQuantitiesInput!) {
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

        variables = {
            "input": {
                "reason": "correction",
                "name": "available",
                "quantities": [
                    {
                        "inventoryItemId": inventory_item_id,
                        "locationId": self.location_id,
                        "quantity": quantity
                    }
                ]
            }
        }

        try:
            data = self._make_graphql_request(mutation, variables)

            result = data.get("inventorySetQuantities", {})
            errors = result.get("userErrors", [])

            if errors:
                error_messages = [f"{e['field']}: {e['message']}" for e in errors]
                raise ShopifyAPIError(
                    f"Failed to update inventory for {sku}",
                    details={"errors": error_messages}
                )

            self.logger.info(f"Updated inventory for {sku}: {quantity}")
            return True

        except Exception as e:
            raise ShopifyAPIError(f"Failed to update inventory for {sku}: {str(e)}")

    def bulk_update_inventory(self, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Update inventory for multiple products.

        Args:
            updates: List of dicts with 'sku' and 'quantity' keys

        Returns:
            Dict with success count and errors

        Raises:
            ShopifyAPIError: If the bulk operation fails
        """
        results = {
            "success_count": 0,
            "error_count": 0,
            "errors": []
        }

        for update in updates:
            sku = update["sku"]
            quantity = update["quantity"]

            try:
                self.update_inventory(sku, quantity)
                results["success_count"] += 1
            except Exception as e:
                results["error_count"] += 1
                results["errors"].append({
                    "sku": sku,
                    "error": str(e)
                })
                self.logger.error(f"Failed to update {sku}: {str(e)}")

        return results

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get order details by ID.

        Args:
            order_id: Shopify order ID

        Returns:
            Order data if found

        Raises:
            ShopifyAPIError: If the request fails
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

        variables = {"id": order_id}

        try:
            data = self._make_graphql_request(query, variables)
            return data.get("order")
        except Exception as e:
            raise ShopifyAPIError(f"Failed to get order {order_id}: {str(e)}")
