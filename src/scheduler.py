"""Background scheduler for periodic synchronization (Railway worker service)."""

import sys
import signal
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .services.sync_service import SyncService
from .utils.config import get_config
from .utils.logger import get_sync_logger


class SyncScheduler:
    """Scheduler for periodic FileMaker to Shopify synchronization."""

    def __init__(self):
        """Initialize scheduler."""
        self.config = get_config()
        self.logger = get_sync_logger()
        self.sync_service = SyncService()

        # Create scheduler with configured settings
        self.scheduler = BlockingScheduler(
            timezone=self.config.scheduler.timezone
        )

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _shutdown_handler(self, signum, frame):
        """
        Handle shutdown signals gracefully.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        self.logger.info(f"Received shutdown signal ({signum}). Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        sys.exit(0)

    def sync_job(self):
        """
        Job function that executes the synchronization.

        This is called by the scheduler at the configured interval.
        """
        self.logger.info("=" * 70)
        self.logger.info(f"Scheduled sync job started at {datetime.now()}")
        self.logger.info("=" * 70)

        try:
            result = self.sync_service.execute_filemaker_to_shopify_sync(dry_run=False)

            # Log summary
            self.logger.info("Sync job completed:")
            self.logger.info(f"  Total items:  {result.total_items}")
            self.logger.info(f"  Updated:      {result.updated_count}")
            self.logger.info(f"  Failed:       {result.failed_count}")
            self.logger.info(f"  Skipped:      {result.skipped_count}")
            self.logger.info(f"  Duration:     {result.duration:.2f}s")
            self.logger.info(f"  Success rate: {result.success_rate:.2f}%")

            if not result.success:
                self.logger.warning(f"Sync completed with {result.failed_count} errors")

        except Exception as e:
            self.logger.error(f"Sync job failed with exception: {str(e)}", exc_info=True)

        self.logger.info("=" * 70)

    def start(self):
        """Start the scheduler."""
        sync_interval_minutes = self.config.env.sync_interval_minutes

        self.logger.info("=" * 70)
        self.logger.info("FileMaker-Shopify Sync Scheduler Starting")
        self.logger.info("=" * 70)
        self.logger.info(f"Environment:      {self.config.env.environment}")
        self.logger.info(f"Timezone:         {self.config.scheduler.timezone}")
        self.logger.info(f"Sync interval:    {sync_interval_minutes} minutes")
        self.logger.info(f"Max instances:    {self.config.scheduler.max_instances}")
        self.logger.info(f"Coalesce:         {self.config.scheduler.coalesce}")
        self.logger.info("=" * 70)

        # Add the sync job
        self.scheduler.add_job(
            func=self.sync_job,
            trigger=IntervalTrigger(minutes=sync_interval_minutes),
            id="filemaker_shopify_sync",
            name="FileMaker to Shopify Stock Sync",
            max_instances=self.config.scheduler.max_instances,
            coalesce=self.config.scheduler.coalesce,
            misfire_grace_time=self.config.scheduler.misfire_grace_time,
            replace_existing=True
        )

        self.logger.info(f"Scheduled job: sync every {sync_interval_minutes} minutes")
        self.logger.info("Scheduler started. Press Ctrl+C to stop.")
        self.logger.info("=" * 70)

        # Run initial sync immediately
        self.logger.info("Running initial sync job...")
        self.sync_job()

        # Start scheduler (blocking)
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            self.logger.info("Scheduler stopped.")


def main():
    """Main entry point for the scheduler."""
    try:
        scheduler = SyncScheduler()
        scheduler.start()
    except Exception as e:
        logger = get_sync_logger()
        logger.error(f"Scheduler failed to start: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
