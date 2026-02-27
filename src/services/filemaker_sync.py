"""Nightly FileMaker → Shopify synchronization service.

Flow:
  1. Fetch all product SKUs from FileMaker (Clasificación == "8").
  2. For each product, run the ActualizarStock_dapi recalculation script.
  3. After all products are recalculated, re-fetch stock for every product.
  4. Update Shopify inventory for each product.
"""

import sys
import time
from typing import List, Dict, Any

from ..api.filemaker_client import FileMakerClient
from ..api.shopify_client import ShopifyClient
from ..models.sync_result import SyncResult
from ..utils.config import get_config
from ..utils.logger import get_sync_logger, get_error_logger
from ..utils.exceptions import SKUNotFoundError


class FileMakerSyncService:
    """Service for the nightly FM → Shopify sync."""

    def __init__(self):
        self.config = get_config()
        self.logger = get_sync_logger()
        self.error_logger = get_error_logger()
        self.filemaker_client = FileMakerClient()
        self.shopify_client = ShopifyClient()

    # ------------------------------------------------------------------
    # Main nightly sync
    # ------------------------------------------------------------------

    def nightly_sync(self) -> SyncResult:
        """
        Execute the full nightly sync:
          Step 1: Fetch all products from FM.
          Step 2: Recalculate each product in FM (with delay).
          Step 3: Re-fetch stock for all products.
          Step 4: Update Shopify inventory.
        """
        result = SyncResult(success=True)
        self.logger.info("=" * 60)
        self.logger.info("NIGHTLY SYNC — Starting")
        self.logger.info("=" * 60)

        try:
            # ── Step 1: Fetch all product SKUs ────────────────────────
            self.logger.info("Step 1/4: Fetching all product SKUs from FileMaker...")
            self.filemaker_client.authenticate()
            products = self.filemaker_client.get_all_products()
            result.total_items = len(products)

            self.logger.info(f"Found {len(products)} products in FileMaker")
            print(f"[SYNC] Step 1: Fetched {len(products)} products from FM", flush=True)

            if not products:
                self.logger.warning("No products found — nothing to sync")
                result.finalize()
                return result

            # ── Step 2: Recalculate each product ──────────────────────
            self.logger.info("Step 2/4: Recalculating stock for each product...")
            print("[SYNC] Step 2: Recalculating stock for each product...", flush=True)

            recalc_errors: List[Dict[str, str]] = []
            for i, product in enumerate(products, 1):
                sku = product["sku"]
                name = product["name"]
                try:
                    self.filemaker_client.recalculate_stock(sku)
                    if i % 20 == 0 or i == len(products):
                        self.logger.info(f"  Recalculated {i}/{len(products)}")
                except Exception as e:
                    self.logger.error(f"  ✗ Recalc failed for {name} (SKU: {sku}): {str(e)}")
                    self.error_logger.error(f"Recalc error for {sku}: {str(e)}")
                    recalc_errors.append({"sku": sku, "name": name, "error": str(e)})

                time.sleep(0.5)  # Avoid overwhelming FileMaker

            self.logger.info(
                f"Step 2 complete: {len(products) - len(recalc_errors)} OK, "
                f"{len(recalc_errors)} failed"
            )
            print(
                f"[SYNC] Step 2 done: {len(products) - len(recalc_errors)} recalculated, "
                f"{len(recalc_errors)} failed",
                flush=True,
            )

            # ── Step 3: Re-fetch stock for all products ───────────────
            self.logger.info("Step 3/4: Fetching updated stock from FileMaker...")
            print("[SYNC] Step 3: Fetching updated stock from FM...", flush=True)

            stock_map: Dict[str, int] = {}
            stock_errors: List[Dict[str, str]] = []

            for i, product in enumerate(products, 1):
                sku = product["sku"]
                name = product["name"]
                try:
                    quantity = self.filemaker_client.get_stock(sku)
                    stock_map[sku] = quantity
                except Exception as e:
                    self.logger.error(f"  ✗ Stock fetch failed for {name} (SKU: {sku}): {str(e)}")
                    self.error_logger.error(f"Stock fetch error for {sku}: {str(e)}")
                    stock_errors.append({"sku": sku, "name": name, "error": str(e)})

            self.logger.info(
                f"Step 3 complete: {len(stock_map)} stock values fetched, "
                f"{len(stock_errors)} failed"
            )

            # ── Step 4: Update Shopify inventory ──────────────────────
            self.logger.info(f"Step 4/4: Updating {len(stock_map)} products in Shopify...")
            print(f"[SYNC] Step 4: Updating {len(stock_map)} products in Shopify...", flush=True)

            # Invalidate Shopify SKU cache so we get fresh product data
            self.shopify_client.invalidate_cache()

            updated = 0
            skipped = 0
            update_errors: List[Dict[str, str]] = []

            for i, (sku, fm_quantity) in enumerate(stock_map.items(), 1):
                name = next(
                    (p["name"] for p in products if p["sku"] == sku), sku
                )
                try:
                    # Get current Shopify inventory to check if update needed
                    shopify_item = self.shopify_client.get_inventory_by_sku(sku)

                    if not shopify_item:
                        self.logger.warning(f"  ✗ NOT IN SHOPIFY: {name} (SKU: {sku})")
                        result.add_error(sku, "SKUNotFoundError", f"Not in Shopify: {sku}")
                        continue

                    shopify_qty = shopify_item.quantity

                    if shopify_qty == fm_quantity:
                        skipped += 1
                        continue

                    # Needs update
                    self.shopify_client.update_inventory(sku, fm_quantity)
                    updated += 1
                    self.logger.info(
                        f"  ✓ {name} (SKU: {sku}): Shopify {shopify_qty} → {fm_quantity}"
                    )

                except Exception as e:
                    self.logger.error(
                        f"  ✗ Shopify update failed for {name} (SKU: {sku}): {str(e)}"
                    )
                    self.error_logger.error(f"Shopify update error for {sku}: {str(e)}")
                    update_errors.append({"sku": sku, "name": name, "error": str(e)})
                    result.add_error(sku, type(e).__name__, str(e))

            result.updated_count = updated
            result.skipped_count = skipped
            result.finalize()
            result.success = len(update_errors) == 0 and len(recalc_errors) == 0

            # ── Summary ──────────────────────────────────────────────
            self.logger.info("=" * 60)
            self.logger.info("NIGHTLY SYNC SUMMARY")
            self.logger.info("=" * 60)
            self.logger.info(f"  Total products:     {result.total_items}")
            self.logger.info(f"  Recalc errors:      {len(recalc_errors)}")
            self.logger.info(f"  Stock fetch errors: {len(stock_errors)}")
            self.logger.info(f"  Shopify updated:    {updated}")
            self.logger.info(f"  Shopify skipped:    {skipped}")
            self.logger.info(f"  Shopify errors:     {len(update_errors)}")
            self.logger.info(f"  Duration:           {result.duration:.2f}s")
            self.logger.info("=" * 60)
            sys.stdout.flush()

            print(
                f"[SYNC] Done — recalc:{len(products) - len(recalc_errors)}ok "
                f"updated:{updated} skipped:{skipped} errors:{len(update_errors)}",
                flush=True,
            )

        except Exception as e:
            self.logger.error(f"NIGHTLY SYNC CRITICAL FAILURE: {str(e)}", exc_info=True)
            print(f"[SYNC] CRITICAL FAILURE: {str(e)}", flush=True)
            result.success = False
            result.add_error("SYSTEM", "CriticalError", str(e))
            result.finalize()

        return result

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        self.filemaker_client.close()
        self.shopify_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
