"""Main synchronization service orchestrator."""

from typing import Optional

from .filemaker_sync import FileMakerSyncService
from .shopify_sync import ShopifySyncService
from ..models.sync_result import SyncResult
from ..utils.logger import get_sync_logger, get_error_logger
from ..utils.config import get_config


class SyncService:
    """
    Main orchestrator for synchronization operations.
    Coordinates between FileMaker and Shopify sync services.
    """

    def __init__(self):
        """Initialize sync service."""
        self.config = get_config()
        self.logger = get_sync_logger()
        self.error_logger = get_error_logger()

    def execute_filemaker_to_shopify_sync(self, dry_run: bool = False) -> SyncResult:
        """
        Execute full synchronization from FileMaker to Shopify.

        Args:
            dry_run: If True, preview changes without applying them

        Returns:
            SyncResult with operation details
        """
        self.logger.info("=" * 60)
        self.logger.info("Starting FileMaker → Shopify synchronization")
        self.logger.info("=" * 60)

        try:
            with FileMakerSyncService() as sync_service:
                result = sync_service.sync_all_stock(dry_run=dry_run)

                # Log errors to error log
                if result.errors:
                    for error in result.errors:
                        self.error_logger.error(
                            f"Sync error for {error.sku}: {error.message}",
                            extra={"details": error.details}
                        )

                return result

        except Exception as e:
            self.error_logger.error(f"Critical sync error: {str(e)}", exc_info=True)
            result = SyncResult(success=False)
            result.add_error("SYSTEM", "CriticalError", str(e))
            result.finalize()
            return result

    def execute_single_sku_sync(self, sku: str, dry_run: bool = False) -> SyncResult:
        """
        Execute synchronization for a single SKU.

        Args:
            sku: Product SKU to sync
            dry_run: If True, preview changes without applying them

        Returns:
            SyncResult with operation details
        """
        self.logger.info(f"Starting single SKU sync: {sku}")

        try:
            with FileMakerSyncService() as sync_service:
                result = sync_service.sync_single_sku(sku, dry_run=dry_run)

                if result.errors:
                    for error in result.errors:
                        self.error_logger.error(
                            f"Sync error for {error.sku}: {error.message}",
                            extra={"details": error.details}
                        )

                return result

        except Exception as e:
            self.error_logger.error(f"Critical error syncing {sku}: {str(e)}", exc_info=True)
            result = SyncResult(success=False, total_items=1)
            result.add_error(sku, "CriticalError", str(e))
            result.finalize()
            return result

    def test_connections(self) -> dict:
        """
        Test connectivity to FileMaker and Shopify APIs.

        Returns:
            Dictionary with connection test results
        """
        self.logger.info("Testing API connections...")

        results = {
            "filemaker": {"success": False, "error": None},
            "shopify": {"success": False, "error": None}
        }

        # Test FileMaker
        try:
            from ..api.filemaker_client import FileMakerClient
            with FileMakerClient() as client:
                client.authenticate()
                results["filemaker"]["success"] = True
                self.logger.info("✓ FileMaker connection successful")
        except NotImplementedError as e:
            results["filemaker"]["error"] = "Not implemented - awaiting user configuration"
            self.logger.warning(f"FileMaker: {str(e)}")
        except Exception as e:
            results["filemaker"]["error"] = str(e)
            self.logger.error(f"✗ FileMaker connection failed: {str(e)}")

        # Test Shopify
        try:
            from ..api.shopify_client import ShopifyClient
            with ShopifyClient() as client:
                # Try to fetch a test SKU (will fail gracefully if not found)
                test_result = client.get_inventory_by_sku("TEST-CONNECTION-SKU")
                results["shopify"]["success"] = True
                self.logger.info("✓ Shopify connection successful")
        except Exception as e:
            # If it's just a "not found" error, connection is still successful
            if "not found" in str(e).lower() or "404" in str(e):
                results["shopify"]["success"] = True
                self.logger.info("✓ Shopify connection successful")
            else:
                results["shopify"]["error"] = str(e)
                self.logger.error(f"✗ Shopify connection failed: {str(e)}")

        return results
