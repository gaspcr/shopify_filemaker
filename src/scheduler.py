"""Background scheduler for nightly synchronization.

Single nightly job at a configurable hour (default 22:00 America/Santiago):
  1. Fetch all products from FM.
  2. Recalculate stock for each product.
  3. Re-fetch updated stock.
  4. Update Shopify inventory.
"""

import sys
import signal
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .services.sync_service import SyncService
from .utils.config import get_config
from .utils.logger import get_sync_logger, get_scheduler_logger

# Ensure APScheduler's internal exceptions are visible in Railway logs.
get_scheduler_logger()


# ------------------------------------------------------------------
# Nightly sync job
# ------------------------------------------------------------------

def _make_nightly_job():
    """Create the nightly FM → Shopify sync job callable."""
    logger = get_sync_logger()
    sync_service = SyncService()

    def nightly_job():
        import sys
        print("[NIGHTLY] Nightly FM → Shopify sync started", flush=True)
        logger.info("=" * 70)
        logger.info(f"Nightly sync job started at {datetime.now()}")
        logger.info("=" * 70)
        sys.stdout.flush()

        try:
            result = sync_service.execute_nightly_sync()

            logger.info("Nightly job completed:")
            logger.info(f"  Total items:  {result.total_items}")
            logger.info(f"  Updated:      {result.updated_count}")
            logger.info(f"  Failed:       {result.failed_count}")
            logger.info(f"  Skipped:      {result.skipped_count}")
            logger.info(f"  Duration:     {result.duration:.2f}s")

            if not result.success:
                logger.warning(f"Nightly sync completed with errors")

            print(
                f"[NIGHTLY] Done — {result.updated_count} updated, "
                f"{result.failed_count} failed, {result.skipped_count} unchanged",
                flush=True,
            )

        except Exception as e:
            logger.error(f"Nightly job failed: {str(e)}", exc_info=True)
            print(f"[NIGHTLY] Job FAILED: {str(e)}", flush=True)

        logger.info("=" * 70)
        sys.stdout.flush()

    return nightly_job


# ------------------------------------------------------------------
# Embedded (non-blocking) scheduler — used by the web process
# ------------------------------------------------------------------

def create_background_scheduler() -> BackgroundScheduler:
    """Create a ``BackgroundScheduler`` with the nightly sync job.

    Returns the scheduler (not started).
    """
    config = get_config()
    logger = get_sync_logger()
    sc = config.scheduler

    scheduler = BackgroundScheduler(timezone=sc.timezone)

    scheduler.add_job(
        func=_make_nightly_job(),
        trigger=CronTrigger(
            hour=sc.nightly_sync_hour,
            minute=sc.nightly_sync_minute,
            timezone=sc.timezone,
        ),
        id="nightly_fm_shopify_sync",
        name="Nightly FM → Shopify Sync",
        max_instances=sc.max_instances,
        coalesce=sc.coalesce,
        misfire_grace_time=sc.misfire_grace_time,
        replace_existing=True,
    )

    logger.info(
        f"Nightly scheduler configured ({sc.timezone}): "
        f"sync @ {sc.nightly_sync_hour:02d}:{sc.nightly_sync_minute:02d}"
    )
    return scheduler


# ------------------------------------------------------------------
# Standalone (blocking) scheduler — for local development
# ------------------------------------------------------------------

class SyncScheduler:
    """Standalone scheduler for nightly sync."""

    def __init__(self):
        self.config = get_config()
        self.logger = get_sync_logger()
        sc = self.config.scheduler

        self.scheduler = BlockingScheduler(timezone=sc.timezone)
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _shutdown_handler(self, signum, frame):
        self.logger.info(f"Received shutdown signal ({signum}). Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        sys.exit(0)

    def start(self):
        sc = self.config.scheduler

        self.logger.info("=" * 70)
        self.logger.info("Nightly Scheduler Starting (standalone)")
        self.logger.info("=" * 70)
        self.logger.info(f"Environment:    {self.config.env.environment}")
        self.logger.info(f"Timezone:       {sc.timezone}")
        self.logger.info(
            f"Nightly sync:   {sc.nightly_sync_hour:02d}:{sc.nightly_sync_minute:02d}"
        )
        self.logger.info("=" * 70)

        self.scheduler.add_job(
            func=_make_nightly_job(),
            trigger=CronTrigger(
                hour=sc.nightly_sync_hour,
                minute=sc.nightly_sync_minute,
                timezone=sc.timezone,
            ),
            id="nightly_fm_shopify_sync",
            name="Nightly FM → Shopify Sync",
            max_instances=sc.max_instances,
            coalesce=sc.coalesce,
            misfire_grace_time=sc.misfire_grace_time,
            replace_existing=True,
        )

        self.logger.info("Scheduler started. Press Ctrl+C to stop.")
        self.logger.info("=" * 70)

        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            self.logger.info("Scheduler stopped.")


def main():
    try:
        scheduler = SyncScheduler()
        scheduler.start()
    except Exception as e:
        logger = get_sync_logger()
        logger.error(f"Scheduler failed to start: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
