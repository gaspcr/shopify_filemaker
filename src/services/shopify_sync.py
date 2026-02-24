"""Shopify to FileMaker synchronization service (webhook-based)."""

from typing import Dict, Any, List
from datetime import datetime

from ..api.filemaker_client import FileMakerClient
from ..utils.logger import get_webhook_logger
from ..utils.exceptions import FileMakerAPIError


class ShopifySyncService:
    """Service for processing Shopify webhooks and updating FileMaker."""

    def __init__(self):
        """Initialize Shopify sync service."""
        self.logger = get_webhook_logger()
        self.filemaker_client = FileMakerClient()

    def process_order_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Shopify order webhook and update FileMaker stock.

        Args:
            webhook_data: Shopify order webhook payload

        Returns:
            Processing result with success status and details
        """
        result = {
            "success": True,
            "order_id": None,
            "order_name": None,
            "items_processed": 0,
            "errors": []
        }

        try:
            # Extract order information
            order_id = webhook_data.get("id")
            order_name = webhook_data.get("name")
            result["order_id"] = order_id
            result["order_name"] = order_name

            self.logger.info(f"Processing order webhook: {order_name} (ID: {order_id})")

            # Extract line items
            line_items = webhook_data.get("line_items", [])
            if not line_items:
                self.logger.warning(f"No line items in order {order_name}")
                return result

            self.logger.info(f"Found {len(line_items)} line items")

            # Process each line item
            for item in line_items:
                sku = item.get("sku")
                quantity = item.get("quantity", 0)

                if not sku:
                    self.logger.warning(f"Line item without SKU in order {order_name}")
                    continue

                try:
                    self.logger.info(f"Processing SKU: {sku}, Quantity: {quantity}")

                    # Get current stock from FileMaker
                    current_stock = self.filemaker_client.get_stock_by_sku(sku)

                    if not current_stock:
                        error_msg = f"SKU not found in FileMaker: {sku}"
                        self.logger.error(error_msg)
                        result["errors"].append({
                            "sku": sku,
                            "error": error_msg
                        })
                        continue

                    # Calculate new quantity
                    new_quantity = max(0, current_stock.quantity - quantity)

                    self.logger.info(
                        f"Updating {sku}: {current_stock.quantity} -> {new_quantity} "
                        f"(decrement: {quantity})"
                    )

                    # Update FileMaker stock
                    self.filemaker_client.update_stock(sku, new_quantity)

                    # Record stock movement for audit trail
                    try:
                        self.filemaker_client.record_stock_movement(
                            sku=sku,
                            quantity_change=-quantity,
                            movement_type="shopify_order",
                            notes=f"Order {order_name} (ID: {order_id})"
                        )
                    except Exception as e:
                        self.logger.warning(f"Failed to record stock movement: {str(e)}")

                    result["items_processed"] += 1

                except Exception as e:
                    error_msg = f"Failed to process {sku}: {str(e)}"
                    self.logger.error(error_msg)
                    result["errors"].append({
                        "sku": sku,
                        "error": str(e)
                    })

            # Set overall success status
            result["success"] = len(result["errors"]) == 0

            if result["success"]:
                self.logger.info(
                    f"Successfully processed order {order_name}: "
                    f"{result['items_processed']} items updated"
                )
            else:
                self.logger.warning(
                    f"Order {order_name} processed with {len(result['errors'])} errors"
                )

        except Exception as e:
            self.logger.error(f"Failed to process order webhook: {str(e)}", exc_info=True)
            result["success"] = False
            result["errors"].append({
                "error": str(e),
                "type": type(e).__name__
            })

        return result

    def close(self):
        """Close FileMaker client."""
        self.filemaker_client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
