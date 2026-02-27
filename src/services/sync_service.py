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
        self.config = get_config()
        self.logger = get_sync_logger()
        self.error_logger = get_error_logger()

    def execute_nightly_sync(self) -> SyncResult:
        """
        Execute the full nightly FM → Shopify sync.

        This is the single nightly job that:
          1. Fetches all products from FM
          2. Recalculates each product's stock in FM
          3. Re-fetches updated stock values
          4. Updates Shopify inventory
        """
        self.logger.info("=" * 60)
        self.logger.info("Starting Nightly FM → Shopify Sync")
        self.logger.info("=" * 60)

        try:
            with FileMakerSyncService() as sync_service:
                result = sync_service.nightly_sync()

                if result.errors:
                    for error in result.errors:
                        self.error_logger.error(
                            f"Sync error for {error.sku}: {error.message}",
                            extra={"details": error.details}
                        )

                return result

        except Exception as e:
            self.error_logger.error(f"Critical nightly sync error: {str(e)}", exc_info=True)
            result = SyncResult(success=False)
            result.add_error("SYSTEM", "CriticalError", str(e))
            result.finalize()
            return result

    def test_connections(self) -> dict:
        """Test connectivity to FileMaker and Shopify APIs."""
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
        except Exception as e:
            results["filemaker"]["error"] = str(e)
            self.logger.error(f"✗ FileMaker connection failed: {str(e)}")

        # Test Shopify
        try:
            from ..api.shopify_client import ShopifyClient
            with ShopifyClient() as client:
                client.get_inventory_by_sku("TEST-CONNECTION-SKU")
                results["shopify"]["success"] = True
                self.logger.info("✓ Shopify connection successful")
        except Exception as e:
            if "not found" in str(e).lower() or "404" in str(e):
                results["shopify"]["success"] = True
                self.logger.info("✓ Shopify connection successful")
            else:
                results["shopify"]["error"] = str(e)
                self.logger.error(f"✗ Shopify connection failed: {str(e)}")

        return results
