"""FastAPI webhook server for receiving Shopify webhooks.

In production (Railway) the background sync scheduler is embedded in this
process so that a single service handles both webhooks and periodic syncs.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from .services.shopify_sync import ShopifySyncService
from .middleware.webhook_validator import WebhookValidator
from .scheduler import create_background_scheduler
from .utils.logger import get_webhook_logger
from .utils.config import get_config
from .utils.exceptions import WebhookValidationError

# Initialize shared state
config = get_config()
logger = get_webhook_logger()
webhook_validator = WebhookValidator()

# Webhook topics we actually want to process (stock decrements).
# Other order topics (e.g. orders/cancelled, orders/updated) are
# acknowledged but NOT processed to avoid incorrect stock changes.
ALLOWED_ORDER_TOPICS = {"orders/create", "orders/paid"}


# ------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event)
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup / shutdown of the application."""
    # ── Startup ───────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("FileMaker-Shopify Webhook Server Starting")
    logger.info("=" * 60)
    logger.info(f"Environment:          {config.env.environment}")
    logger.info(f"Port:                 {config.env.port}")
    logger.info(f"Webhook validation:   {config.webhook.validate_signature}")
    sc = config.scheduler
    logger.info(
        f"Nightly sync:         @ {sc.nightly_sync_hour:02d}:{sc.nightly_sync_minute:02d} ({sc.timezone})"
    )
    logger.info("=" * 60)

    # Start the background nightly scheduler
    scheduler = create_background_scheduler()
    scheduler.start()
    logger.info("Nightly scheduler started")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("Shutting down nightly scheduler...")
    scheduler.shutdown(wait=True)
    logger.info("Webhook server shut down.")


# ------------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------------

app = FastAPI(
    title="FileMaker-Shopify Sync Webhook Server",
    description="Webhook receiver for Shopify order events",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "FileMaker-Shopify Sync Webhook Server",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for Railway and monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "environment": config.env.environment
    }


# ------------------------------------------------------------------
# Shopify order webhook
# ------------------------------------------------------------------

async def process_order_in_background(webhook_data: Dict[str, Any]):
    """Process order webhook in background."""
    try:
        with ShopifySyncService() as sync_service:
            result = sync_service.process_order_webhook(webhook_data)

            if result["success"]:
                logger.info(
                    f"Background processing completed for order {result['order_name']}: "
                    f"{result['items_processed']} items updated"
                )
            else:
                logger.warning(
                    f"Background processing completed with errors for order {result['order_name']}"
                )

    except Exception as e:
        logger.error(f"Background processing failed: {str(e)}", exc_info=True)


@app.post("/webhooks/shopify/orders")
async def shopify_order_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive and process Shopify order webhooks.

    Only ``orders/create`` and ``orders/paid`` topics are processed.
    Other topics are acknowledged (HTTP 200) but ignored so that
    cancellations or updates do not incorrectly decrement stock.
    """
    # Get raw body for signature validation
    body = await request.body()

    # Get headers
    signature = request.headers.get("X-Shopify-Hmac-SHA256")
    shop_domain = request.headers.get("X-Shopify-Shop-Domain")
    topic = request.headers.get("X-Shopify-Topic")

    logger.info(f"Received webhook: {topic} from {shop_domain}")

    # ── Validate webhook signature ────────────────────────────────────
    try:
        webhook_validator.validate_signature(body, signature)
        webhook_validator.validate_shopify_domain(shop_domain)
    except WebhookValidationError as e:
        logger.error(f"Webhook validation failed: {e.message}")
        raise HTTPException(status_code=401, detail=e.message)

    # ── Filter by topic ───────────────────────────────────────────────
    if topic not in ALLOWED_ORDER_TOPICS:
        logger.info(f"Ignoring webhook topic: {topic}")
        return JSONResponse(
            status_code=200,
            content={
                "status": "ignored",
                "topic": topic,
                "message": "Topic not processed"
            }
        )

    # ── Parse webhook data ────────────────────────────────────────────
    try:
        webhook_data = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in webhook: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    order_id = webhook_data.get("id")
    order_name = webhook_data.get("name")
    logger.info(f"Processing order: {order_name} (ID: {order_id})")

    # Process webhook in background
    background_tasks.add_task(process_order_in_background, webhook_data)

    return JSONResponse(
        status_code=200,
        content={
            "status": "accepted",
            "order_id": order_id,
            "order_name": order_name,
            "message": "Webhook received and queued for processing"
        }
    )


# ------------------------------------------------------------------
# Test endpoint (development only)
# ------------------------------------------------------------------

@app.post("/webhooks/shopify/test")
async def test_webhook(request: Request):
    """Test endpoint — only available in development."""
    if config.is_production:
        raise HTTPException(status_code=404, detail="Not found")

    body = await request.body()
    webhook_data = json.loads(body)

    logger.info(f"Test webhook received: {json.dumps(webhook_data, indent=2)}")

    return {
        "status": "test_success",
        "message": "Test webhook received",
        "data": webhook_data
    }


# ------------------------------------------------------------------
# Exception handlers
# ------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler."""
    logger.warning(f"HTTP {exc.status_code}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """General exception handler for unexpected errors."""
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": str(exc) if not config.is_production else "An error occurred"
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "webhook_server:app",
        host="0.0.0.0",
        port=config.env.port,
        reload=not config.is_production
    )
