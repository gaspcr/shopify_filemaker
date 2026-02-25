"""Shopify to FileMaker synchronization service (webhook-based)."""

from typing import Dict, Any

from ..api.filemaker_client import FileMakerClient
from ..utils.logger import get_webhook_logger


class ShopifySyncService:
    """Service for processing Shopify webhooks and updating FileMaker."""

    def __init__(self):
        """Initialize Shopify sync service."""
        self.logger = get_webhook_logger()
        self.filemaker_client = FileMakerClient()

    def process_order_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a Shopify order webhook and create the corresponding stock
        movements in FileMaker.

        FileMaker computes the new Inventario value automatically after each
        movement record is created and the ActualizarStock_dapi script runs,
        so there is no need to read current stock or write the quantity
        directly — record_stock_movement() handles everything.

        The Shopify variant SKU must equal the FileMaker "Conceptos Cobro_pk"
        value for each product so the two systems can be matched.

        Args:
            webhook_data: Shopify order webhook payload (JSON-parsed body).

        Returns:
            Dict with success flag, counts, and any per-item errors.
        """
        result: Dict[str, Any] = {
            "success": True,
            "order_id": None,
            "order_name": None,
            "items_processed": 0,
            "errors": []
        }

        try:
            order_id = webhook_data.get("id")
            order_name = webhook_data.get("name")
            result["order_id"] = order_id
            result["order_name"] = order_name

            self.logger.info(f"Processing order webhook: {order_name} (ID: {order_id})")

            line_items = webhook_data.get("line_items", [])
            if not line_items:
                self.logger.warning(f"No line items in order {order_name}")
                return result

            self.logger.info(f"Found {len(line_items)} line items in order {order_name}")

            for item in line_items:
                sku = item.get("sku")
                quantity = item.get("quantity", 0)

                if not sku:
                    self.logger.warning(
                        f"Line item '{item.get('title', '?')}' has no SKU "
                        f"in order {order_name} — skipping"
                    )
                    continue

                if quantity <= 0:
                    self.logger.warning(
                        f"Skipping {sku}: invalid quantity {quantity} in order {order_name}"
                    )
                    continue

                try:
                    self.logger.info(
                        f"Recording exit — SKU (Conceptos Cobro_pk): {sku}, "
                        f"quantity: {quantity}, order: {order_name}"
                    )

                    # quantity_change is negative because units are leaving stock
                    self.filemaker_client.record_stock_movement(
                        sku=sku,
                        quantity_change=-quantity,
                        movement_type="shopify_order",
                        notes=f"Order {order_name} (ID: {order_id})"
                    )

                    result["items_processed"] += 1
                    self.logger.info(f"Movement recorded for SKU {sku}")

                except Exception as e:
                    error_msg = f"Failed to record movement for SKU {sku}: {str(e)}"
                    self.logger.error(error_msg)
                    result["errors"].append({"sku": sku, "error": str(e)})

            result["success"] = len(result["errors"]) == 0

            if result["success"]:
                self.logger.info(
                    f"Order {order_name} fully processed — "
                    f"{result['items_processed']} movement(s) recorded"
                )
            else:
                self.logger.warning(
                    f"Order {order_name} processed with {len(result['errors'])} error(s) — "
                    f"{result['items_processed']} movement(s) recorded successfully"
                )

        except Exception as e:
            self.logger.error(f"Unexpected error processing order webhook: {str(e)}", exc_info=True)
            result["success"] = False
            result["errors"].append({"error": str(e), "type": type(e).__name__})

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
