# FileMaker-Shopify Stock Synchronization

A robust Python application for bidirectional stock synchronization between FileMaker and Shopify systems.

## Overview

This application maintains inventory accuracy across FileMaker (source of truth) and Shopify by:

1. **FileMaker → Shopify**: Regular scheduled syncs and manual CLI operations to update Shopify inventory
2. **Shopify → FileMaker**: Webhook-based order notifications that trigger stock decrements in FileMaker

### Key Features

- ✅ Bidirectional stock synchronization
- ✅ Scheduled automatic syncs via APScheduler
- ✅ Manual sync via CLI
- ✅ Webhook receiver for Shopify orders
- ✅ Batch processing for efficiency
- ✅ Smart diff checking (only update changed quantities)
- ✅ Comprehensive logging and error handling
- ✅ Retry logic with exponential backoff
- ✅ Shopify rate limit handling
- ✅ Railway deployment ready

## Architecture

```
┌─────────────┐         Scheduled/Manual Sync          ┌──────────┐
│  FileMaker  │  ────────────────────────────────────>  │ Shopify  │
│  (Source)   │                                         │          │
└─────────────┘                                         └──────────┘
      ↑                                                       │
      │                 Webhook (Order Complete)             │
      └───────────────────────────────────────────────────────┘
```

### Components

- **API Clients**: FileMaker Data API and Shopify Admin API integration
- **Sync Services**: Business logic for synchronization operations
- **CLI**: Command-line interface for manual operations
- **Webhook Server**: FastAPI server for receiving Shopify webhooks
- **Scheduler**: Background worker for automated syncs

## Prerequisites

- Python 3.11+
- FileMaker Server with Data API enabled
- Shopify store with Admin API access
- Railway account (for deployment)

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd shopify_filemaker
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

```bash
cp config/.env.example .env
```

Edit `.env` with your credentials:

```env
# FileMaker Configuration
FILEMAKER_HOST=https://your-filemaker-server.com
FILEMAKER_DATABASE=your_database_name
FILEMAKER_USERNAME=your_username
FILEMAKER_PASSWORD=your_password

# Shopify Configuration
SHOPIFY_SHOP_URL=your-shop.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SHOPIFY_LOCATION_ID=gid://shopify/Location/123456789
SHOPIFY_WEBHOOK_SECRET=your_webhook_secret_key

# Application Configuration
ENVIRONMENT=development
LOG_LEVEL=INFO
SYNC_INTERVAL_MINUTES=60
```

### 5. FileMaker API Implementation

**IMPORTANT**: The FileMaker client contains placeholder methods that need implementation. Edit `src/api/filemaker_client.py` and implement:

- `authenticate()` - FileMaker authentication
- `get_all_stock()` - Fetch all stock records
- `get_stock_by_sku()` - Fetch stock for specific SKU
- `update_stock()` - Update stock quantity
- `record_stock_movement()` - Record stock movements for audit trail

Each method contains detailed comments and example implementations.

## Usage

### Test API Connections

Verify credentials and API connectivity:

```bash
python -m src.cli test-connection
```

### Manual Synchronization

Execute full sync (FileMaker → Shopify):

```bash
# Preview changes without applying
python -m src.cli sync --dry-run

# Execute sync
python -m src.cli sync
```

Sync a single SKU:

```bash
# Preview
python -m src.cli sync-sku TEST-SKU-001 --dry-run

# Execute
python -m src.cli sync-sku TEST-SKU-001
```

### View Configuration

```bash
python -m src.cli config-info
```

### Run Webhook Server (Development)

```bash
uvicorn src.webhook_server:app --reload --port 8000
```

Test webhook endpoint:

```bash
curl -X POST http://localhost:8000/webhooks/shopify/orders \
  -H "Content-Type: application/json" \
  -H "X-Shopify-Hmac-SHA256: <signature>" \
  -H "X-Shopify-Shop-Domain: your-shop.myshopify.com" \
  -H "X-Shopify-Topic: orders/create" \
  -d @test_order.json
```

### Run Scheduler (Development)

```bash
python -m src.scheduler
```

## Railway Deployment

### 1. Prepare for Deployment

Ensure these files are present:

- `Procfile` - Process definitions
- `runtime.txt` - Python version
- `railway.json` - Railway configuration
- `requirements.txt` - Dependencies

### 2. Create Railway Project

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize project
railway init
```

### 3. Set Environment Variables

In Railway dashboard, add all environment variables from `.env`:

```
FILEMAKER_HOST
FILEMAKER_DATABASE
FILEMAKER_USERNAME
FILEMAKER_PASSWORD
SHOPIFY_SHOP_URL
SHOPIFY_ACCESS_TOKEN
SHOPIFY_LOCATION_ID
SHOPIFY_WEBHOOK_SECRET
ENVIRONMENT=production
SYNC_INTERVAL_MINUTES=60
```

Railway automatically sets `PORT`.

### 4. Deploy Services

Railway will deploy two services from `Procfile`:

- **web**: FastAPI webhook server (auto-scaled, public domain)
- **worker**: Background scheduler for periodic syncs

```bash
railway up
```

### 5. Configure Shopify Webhook

1. Go to Shopify Admin → Settings → Notifications → Webhooks
2. Create new webhook:
   - Event: `Order payment` or `Order fulfillment`
   - Format: JSON
   - URL: `https://your-app.railway.app/webhooks/shopify/orders`
3. Copy webhook signing secret to Railway environment variables

### 6. Monitor Deployment

```bash
# View logs
railway logs

# Check status
railway status
```

## Configuration

### Application Settings (`config/config.yml`)

```yaml
api:
  timeout: 30
  max_retries: 3
  retry_delay: 1
  exponential_backoff: true

sync:
  batch_size: 100
  enable_diff_check: true

shopify:
  rate_limit_delay: 0.5
  api_version: "2024-01"

logging:
  level: INFO
  max_bytes: 10485760  # 10MB
  backup_count: 5
```

### Sync Interval

Adjust `SYNC_INTERVAL_MINUTES` in environment variables to control how often automated syncs run.

## Logging

Logs are written to:

- `logs/sync.log` - Synchronization operations
- `logs/webhook.log` - Webhook events
- `logs/error.log` - Errors only

Logs rotate at 10MB with 5 backups retained.

## Development

### Install Development Dependencies

```bash
pip install -r requirements-dev.txt
```

### Run Tests

```bash
pytest tests/ -v --cov=src
```

### Code Formatting

```bash
# Format code
black src/

# Sort imports
isort src/

# Lint
flake8 src/
```

## Project Structure

```
shopify_filemaker/
├── config/
│   ├── config.yml          # Application settings
│   └── .env.example        # Environment template
├── src/
│   ├── api/                # API clients
│   │   ├── base_client.py
│   │   ├── filemaker_client.py
│   │   └── shopify_client.py
│   ├── services/           # Business logic
│   │   ├── sync_service.py
│   │   ├── filemaker_sync.py
│   │   └── shopify_sync.py
│   ├── models/             # Data models
│   │   ├── product.py
│   │   └── sync_result.py
│   ├── utils/              # Utilities
│   │   ├── config.py
│   │   ├── logger.py
│   │   └── exceptions.py
│   ├── middleware/
│   │   └── webhook_validator.py
│   ├── cli.py              # CLI interface
│   ├── webhook_server.py   # FastAPI server
│   └── scheduler.py        # Background scheduler
├── tests/                  # Test suite
├── logs/                   # Log files
├── Procfile               # Railway process definitions
├── runtime.txt            # Python version
├── railway.json           # Railway configuration
├── requirements.txt
└── README.md
```

## Troubleshooting

### FileMaker Connection Issues

1. Verify FileMaker Data API is enabled
2. Check firewall rules allow API access
3. Confirm credentials are correct
4. Review FileMaker server logs

### Shopify Connection Issues

1. Verify access token has required permissions:
   - `read_inventory`
   - `write_inventory`
   - `read_products`
2. Check shop URL format (should be `shop-name.myshopify.com`)
3. Verify location ID is correct

### Webhook Not Receiving Events

1. Check webhook URL is accessible from internet
2. Verify webhook secret matches Shopify configuration
3. Check Railway logs for webhook validation errors
4. Test with Shopify's webhook testing tool

### Sync Errors

1. Check logs for specific error messages
2. Verify SKUs match exactly between systems
3. Test single SKU sync to isolate issues
4. Run with `--dry-run` to preview changes

### Rate Limiting

If hitting Shopify rate limits:

1. Increase `rate_limit_delay` in `config/config.yml`
2. Reduce `batch_size` for smaller batches
3. Increase `SYNC_INTERVAL_MINUTES` for less frequent syncs

## Security Considerations

- ✅ Webhook HMAC signature validation
- ✅ Environment variables for secrets (never commit `.env`)
- ✅ HTTPS for webhook endpoint
- ✅ Secure credential storage
- ✅ Input validation and sanitization

## Performance Optimization

- **Batch Updates**: Updates sent in batches of 100 (configurable)
- **Diff Checking**: Only updates items with quantity changes
- **Rate Limit Handling**: Respects Shopify API limits
- **Connection Pooling**: Reuses HTTP connections
- **Retry Logic**: Exponential backoff for network errors

## API Rate Limits

### Shopify

- REST API: 2 requests/second
- GraphQL API: 50 points/second
- Handled automatically by the client

### FileMaker

- Dependent on server configuration
- Configurable timeout and retry settings

## Support

For issues or questions:

1. Check logs for error details
2. Review troubleshooting section
3. Verify configuration settings
4. Test API connections

## License

[Your License Here]

## Version History

### 1.0.0 (Initial Release)

- FileMaker → Shopify synchronization
- Shopify → FileMaker webhook processing
- CLI interface
- Background scheduler
- Railway deployment support
