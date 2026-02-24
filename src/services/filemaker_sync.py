"""FileMaker to Shopify synchronization service."""

import time
from typing import List, Dict, Any

from ..api.filemaker_client import FileMakerClient
from ..api.shopify_client import ShopifyClient
from ..models.sync_result import SyncResult
from ..models.product import StockItem
from ..utils.config import get_config
from ..utils.logger import get_sync_logger
from ..utils.exceptions import SKUNotFoundError, ShopifyAPIError


class FileMakerSyncService:
    """Service for syncing stock from FileMaker to Shopify."""

    def __init__(self):
        """Initialize sync service."""
        self.config = get_config()
        self.logger = get_sync_logger()
        self.filemaker_client = FileMakerClient()
        self.shopify_client = ShopifyClient()

    def sync_all_stock(self, dry_run: bool = False) -> SyncResult:
        """
        Sync all stock from FileMaker to Shopify.

        Args:
            dry_run: If True, only preview changes without updating

        Returns:
            SyncResult with operation details
        """
        result = SyncResult(success=True)
        self.logger.info(f"Starting FileMaker -> Shopify sync (dry_run={dry_run})")

        try:
            # Fetch all stock from FileMaker
            self.logger.info("Fetching stock from FileMaker...")
            filemaker_stock = self.filemaker_client.get_all_stock()
            result.total_items = len(filemaker_stock)

            self.logger.info(f"Found {result.total_items} items in FileMaker")

            # Process items in batches
            batch_size = self.config.sync.batch_size
            updates_to_make = []

            for i, fm_item in enumerate(filemaker_stock, 1):
                sku = fm_item.sku
                self.logger.debug(f"Processing {i}/{result.total_items}: {sku}")

                try:
                    # Get current Shopify inventory
                    shopify_item = self.shopify_client.get_inventory_by_sku(sku)

                    if not shopify_item:
                        self.logger.warning(f"SKU not found in Shopify: {sku}")
                        result.add_error(sku, "SKUNotFoundError", f"SKU not found in Shopify: {sku}")
                        continue

                    # Check if update is needed
                    if self.config.sync.enable_diff_check:
                        if shopify_item.quantity == fm_item.quantity:
                            self.logger.debug(f"Skipping {sku}: quantity unchanged ({fm_item.quantity})")
                            result.skipped_count += 1
                            continue

                    # Queue update
                    updates_to_make.append({
                        "sku": sku,
                        "quantity": fm_item.quantity,
                        "old_quantity": shopify_item.quantity
                    })

                    self.logger.info(
                        f"Queued update for {sku}: {shopify_item.quantity} -> {fm_item.quantity}"
                    )

                except Exception as e:
                    self.logger.error(f"Error processing {sku}: {str(e)}")
                    result.add_error(sku, type(e).__name__, str(e))

            # Execute updates in batches
            if updates_to_make:
                self.logger.info(f"Updating {len(updates_to_make)} items in Shopify...")

                if dry_run:
                    self.logger.info("DRY RUN - No updates will be made")
                    result.updated_count = len(updates_to_make)
                else:
                    result.updated_count = self._execute_updates_in_batches(
                        updates_to_make,
                        batch_size,
                        result
                    )

            # Finalize result
            result.finalize()
            result.success = result.failed_count == 0

            self.logger.info(f"Sync completed: {result.get_summary()}")

        except Exception as e:
            self.logger.error(f"Sync failed: {str(e)}", exc_info=True)
            result.success = False
            result.add_error("SYSTEM", "SyncError", str(e))
            result.finalize()

        return result

    def sync_single_sku(self, sku: str, dry_run: bool = False) -> SyncResult:
        """
        Sync a single SKU from FileMaker to Shopify.

        Args:
            sku: Product SKU to sync
            dry_run: If True, only preview changes without updating

        Returns:
            SyncResult with operation details
        """
        result = SyncResult(success=True, total_items=1)
        self.logger.info(f"Syncing single SKU: {sku} (dry_run={dry_run})")

        try:
            # Get FileMaker stock
            fm_item = self.filemaker_client.get_stock_by_sku(sku)
            if not fm_item:
                raise SKUNotFoundError(f"SKU not found in FileMaker: {sku}")

            # Get Shopify stock
            shopify_item = self.shopify_client.get_inventory_by_sku(sku)
            if not shopify_item:
                raise SKUNotFoundError(f"SKU not found in Shopify: {sku}")

            # Check if update needed
            if self.config.sync.enable_diff_check and shopify_item.quantity == fm_item.quantity:
                self.logger.info(f"Skipping {sku}: quantity unchanged ({fm_item.quantity})")
                result.skipped_count = 1
            else:
                self.logger.info(
                    f"Updating {sku}: {shopify_item.quantity} -> {fm_item.quantity}"
                )

                if not dry_run:
                    self.shopify_client.update_inventory(sku, fm_item.quantity)
                    result.updated_count = 1
                else:
                    self.logger.info("DRY RUN - No update made")
                    result.updated_count = 1

        except Exception as e:
            self.logger.error(f"Failed to sync {sku}: {str(e)}")
            result.success = False
            result.add_error(sku, type(e).__name__, str(e))

        result.finalize()
        return result

    def _execute_updates_in_batches(
        self,
        updates: List[Dict[str, Any]],
        batch_size: int,
        result: SyncResult
    ) -> int:
        """
        Execute inventory updates in batches.

        Args:
            updates: List of update dictionaries
            batch_size: Number of updates per batch
            result: SyncResult to track errors

        Returns:
            Number of successful updates
        """
        success_count = 0

        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(updates) + batch_size - 1) // batch_size

            self.logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} items)")

            for update in batch:
                sku = update["sku"]
                quantity = update["quantity"]

                try:
                    self.shopify_client.update_inventory(sku, quantity)
                    success_count += 1
                    self.logger.info(f"Updated {sku}: {quantity}")
                except Exception as e:
                    self.logger.error(f"Failed to update {sku}: {str(e)}")
                    result.add_error(sku, type(e).__name__, str(e))

            # Small delay between batches
            if i + batch_size < len(updates):
                time.sleep(0.5)

        return success_count

    def close(self):
        """Close API clients."""
        self.filemaker_client.close()
        self.shopify_client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
