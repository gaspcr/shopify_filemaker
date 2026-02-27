"""Shopify order webhook → FileMaker stock decrement (real-time).

Flow per line item:
  1. Create movement record in FM (Inv_Cant_Salida).
  2. Run ActualizarStock_dapi script for that SKU.
  3. Fetch updated Inventario from FM.
  4. Update Shopify inventory for that SKU.
"""

from typing import Dict, Any

from ..api.filemaker_client import FileMakerClient
from ..api.shopify_client import ShopifyClient
from ..utils.logger import get_webhook_logger, get_error_logger


class ShopifySyncService:
    """Process Shopify order webhooks and update FM + Shopify inventory."""

    def __init__(self):
        self.logger = get_webhook_logger()
        self.error_logger = get_error_logger()
        self.fm = FileMakerClient()
        self.shopify = ShopifyClient()

    def process_order_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a Shopify order webhook.

        For each line item with a SKU:
          1. create_movement(sku, quantity_sold)
          2. recalculate_stock(sku)
          3. get_stock(sku)  → new_quantity
          4. shopify.update_inventory(sku, new_quantity)

        Args:
            webhook_data: Parsed JSON body from the Shopify webhook.

        Returns:
            Dict with success flag, counts, and any per-item errors.
        """
        result: Dict[str, Any] = {
            "success": True,
            "order_id": None,
            "order_name": None,
            "items_processed": 0,
            "errors": [],
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

            # Authenticate FM once for the whole order
            self.fm.authenticate()

            for item in line_items:
                sku = item.get("sku")
                quantity_sold = item.get("quantity", 0)
                title = item.get("title", "?")

                if not sku:
                    self.logger.warning(
                        f"Line item '{title}' has no SKU in order {order_name} — skipping"
                    )
                    continue

                if quantity_sold <= 0:
                    self.logger.warning(
                        f"Skipping {sku}: invalid quantity {quantity_sold} in order {order_name}"
                    )
                    continue

                try:
                    self._process_line_item(sku, quantity_sold, order_name, title)
                    result["items_processed"] += 1
                except Exception as e:
                    error_msg = (
                        f"Failed processing SKU {sku} ({title}) "
                        f"in order {order_name}: {str(e)}"
                    )
                    self.logger.error(error_msg)
                    self.error_logger.error(error_msg)
                    result["errors"].append({"sku": sku, "title": title, "error": str(e)})

            result["success"] = len(result["errors"]) == 0

            if result["success"]:
                self.logger.info(
                    f"Order {order_name} fully processed — "
                    f"{result['items_processed']} item(s) updated"
                )
            else:
                self.logger.warning(
                    f"Order {order_name} processed with {len(result['errors'])} error(s)"
                )

        except Exception as e:
            self.logger.error(
                f"Unexpected error processing order webhook: {str(e)}",
                exc_info=True,
            )
            result["success"] = False
            result["errors"].append({"error": str(e), "type": type(e).__name__})

        return result

    def _process_line_item(
        self, sku: str, quantity_sold: int, order_name: str, title: str
    ) -> None:
        """
        Process a single line item through the 4-step webhook flow.

        Args:
            sku: Product SKU (Conceptos Cobro_pk).
            quantity_sold: How many units were sold.
            order_name: Shopify order name (for logging).
            title: Product title (for logging).
        """
        self.logger.info(
            f"  [{sku}] {title} — qty sold: {quantity_sold} (order {order_name})"
        )

        # Step 1: Create movement record in FM
        self.logger.info(f"  [{sku}] Step 1/4: Creating movement record (salida: {quantity_sold})")
        self.fm.create_movement(sku, quantity_out=quantity_sold)

        # Step 2: Run recalculation script
        self.logger.info(f"  [{sku}] Step 2/4: Running ActualizarStock_dapi")
        self.fm.recalculate_stock(sku)

        # Step 3: Fetch updated stock from FM
        self.logger.info(f"  [{sku}] Step 3/4: Fetching updated stock from FM")
        new_quantity = self.fm.get_stock(sku)
        self.logger.info(f"  [{sku}] FM Inventario = {new_quantity}")

        # Step 4: Update Shopify inventory
        self.logger.info(f"  [{sku}] Step 4/4: Updating Shopify inventory → {new_quantity}")
        self.shopify.update_inventory(sku, new_quantity)

        self.logger.info(
            f"  [{sku}] ✓ Complete — {title}: Shopify stock set to {new_quantity}"
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        self.fm.close()
        self.shopify.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
