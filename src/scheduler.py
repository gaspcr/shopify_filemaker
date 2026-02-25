"""Background scheduler for periodic synchronization.

Supports two modes:
  - **Standalone** (``python -m src.scheduler``): runs a ``BlockingScheduler``
    as a separate worker process — useful during development.
  - **Embedded** (``create_background_scheduler()``): returns a
    ``BackgroundScheduler`` that the FastAPI web process starts in its
    ``lifespan`` handler — this is the Railway production setup.
"""

import sys
import signal
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .services.sync_service import SyncService
from .utils.config import get_config
from .utils.logger import get_sync_logger


# ------------------------------------------------------------------
# Shared sync-job factory
# ------------------------------------------------------------------

def _make_sync_job():
    """Create and return the sync-job callable."""
    config = get_config()
    logger = get_sync_logger()
    sync_service = SyncService()

    def sync_job():
        logger.info("=" * 70)
        logger.info(f"Scheduled sync job started at {datetime.now()}")
        logger.info("=" * 70)

        try:
            result = sync_service.execute_filemaker_to_shopify_sync(dry_run=False)

            logger.info("Sync job completed:")
            logger.info(f"  Total items:  {result.total_items}")
            logger.info(f"  Updated:      {result.updated_count}")
            logger.info(f"  Failed:       {result.failed_count}")
            logger.info(f"  Skipped:      {result.skipped_count}")
            logger.info(f"  Duration:     {result.duration:.2f}s")
            logger.info(f"  Success rate: {result.success_rate:.2f}%")

            if not result.success:
                logger.warning(f"Sync completed with {result.failed_count} errors")

        except Exception as e:
            logger.error(f"Sync job failed with exception: {str(e)}", exc_info=True)

        logger.info("=" * 70)

    return sync_job


# ------------------------------------------------------------------
# Embedded (non-blocking) scheduler — used by the web process
# ------------------------------------------------------------------

def create_background_scheduler() -> BackgroundScheduler:
    """Create a ``BackgroundScheduler`` for embedding inside FastAPI.

    The scheduler is returned **not started** — the caller must invoke
    ``scheduler.start()`` when ready.

    An initial sync is scheduled to run 15 seconds after creation so the
    web server can finish its startup first.
    """
    config = get_config()
    logger = get_sync_logger()
    sync_interval = config.env.sync_interval_minutes

    scheduler = BackgroundScheduler(timezone=config.scheduler.timezone)
    sync_job = _make_sync_job()

    # Recurring job
    scheduler.add_job(
        func=sync_job,
        trigger=IntervalTrigger(minutes=sync_interval),
        id="filemaker_shopify_sync",
        name="FileMaker to Shopify Stock Sync",
        max_instances=config.scheduler.max_instances,
        coalesce=config.scheduler.coalesce,
        misfire_grace_time=config.scheduler.misfire_grace_time,
        replace_existing=True
    )

    # Initial sync shortly after startup
    scheduler.add_job(
        func=sync_job,
        trigger="date",
        run_date=datetime.now() + timedelta(seconds=15),
        id="initial_sync",
        name="Initial sync on startup",
    )

    logger.info(
        f"Background scheduler configured: sync every {sync_interval} min "
        f"(initial run in ~15 s)"
    )
    return scheduler


# ------------------------------------------------------------------
# Standalone (blocking) scheduler — for local development / workers
# ------------------------------------------------------------------

class SyncScheduler:
    """Scheduler for periodic FileMaker to Shopify synchronization."""

    def __init__(self):
        """Initialize scheduler."""
        self.config = get_config()
        self.logger = get_sync_logger()
        self.sync_job = _make_sync_job()

        self.scheduler = BlockingScheduler(
            timezone=self.config.scheduler.timezone
        )

        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _shutdown_handler(self, signum, frame):
        self.logger.info(f"Received shutdown signal ({signum}). Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        sys.exit(0)

    def start(self):
        """Start the blocking scheduler (runs forever)."""
        sync_interval_minutes = self.config.env.sync_interval_minutes

        self.logger.info("=" * 70)
        self.logger.info("FileMaker-Shopify Sync Scheduler Starting (standalone)")
        self.logger.info("=" * 70)
        self.logger.info(f"Environment:      {self.config.env.environment}")
        self.logger.info(f"Timezone:         {self.config.scheduler.timezone}")
        self.logger.info(f"Sync interval:    {sync_interval_minutes} minutes")
        self.logger.info(f"Max instances:    {self.config.scheduler.max_instances}")
        self.logger.info(f"Coalesce:         {self.config.scheduler.coalesce}")
        self.logger.info("=" * 70)

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
        self.logger.info("Running initial sync job...")
        self.sync_job()

        self.logger.info("Scheduler started. Press Ctrl+C to stop.")
        self.logger.info("=" * 70)

        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            self.logger.info("Scheduler stopped.")


def main():
    """Main entry point for standalone scheduler."""
    try:
        scheduler = SyncScheduler()
        scheduler.start()
    except Exception as e:
        logger = get_sync_logger()
        logger.error(f"Scheduler failed to start: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
