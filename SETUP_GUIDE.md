# Setup Guide

This guide will help you get the FileMaker-Shopify synchronization application up and running.

## Quick Start Checklist

- [ ] Install Python 3.11+
- [ ] Clone repository and create virtual environment
- [ ] Install dependencies
- [ ] Configure environment variables
- [ ] **Implement FileMaker API methods** (REQUIRED)
- [ ] Get Shopify credentials
- [ ] Test API connections
- [ ] Run first sync
- [ ] Deploy to Railway
- [ ] Configure Shopify webhooks

## Step 1: Environment Setup

### Install Python 3.11+

Check your Python version:

```bash
python3 --version
```

If you need to install Python 3.11+, visit https://www.python.org/downloads/

### Create Virtual Environment

```bash
cd shopify_filemaker
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 2: Configure Environment Variables

### Copy Environment Template

```bash
cp config/.env.example .env
```

### Get Shopify Credentials

#### 1. Create Private App (Custom App)

1. Go to Shopify Admin
2. Navigate to **Settings** → **Apps and sales channels** → **Develop apps**
3. Click **Create an app**
4. Name it "Stock Sync" or similar
5. Click **Configure Admin API scopes**
6. Enable these scopes:
   - `read_inventory`
   - `write_inventory`
   - `read_products`
   - `read_orders` (for webhooks)
7. Click **Save**
8. Click **Install app**
9. Copy the **Admin API access token** (starts with `shpat_`)

#### 2. Get Location ID

The location ID is needed to know which inventory location to update.

**Option A: Using Shopify Admin**
1. Go to **Settings** → **Locations**
2. Click on your location
3. The URL will contain the location ID

**Option B: Using GraphQL**
```graphql
{
  locations(first: 5) {
    edges {
      node {
        id
        name
      }
    }
  }
}
```

The ID will be in format: `gid://shopify/Location/123456789`

### Edit .env File

```env
# FileMaker Configuration
FILEMAKER_HOST=https://your-filemaker-server.com
FILEMAKER_DATABASE=YourDatabaseName
FILEMAKER_USERNAME=api_user
FILEMAKER_PASSWORD=secure_password

# Shopify Configuration
SHOPIFY_SHOP_URL=your-shop.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SHOPIFY_LOCATION_ID=gid://shopify/Location/123456789
SHOPIFY_WEBHOOK_SECRET=your_webhook_secret_from_shopify

# Application Settings
ENVIRONMENT=development
LOG_LEVEL=DEBUG
SYNC_INTERVAL_MINUTES=60
```

## Step 3: Implement FileMaker API Methods (REQUIRED)

**This is the most important step!** The FileMaker client has placeholder methods that you must implement based on your FileMaker Data API setup.

### Open the FileMaker Client

```bash
# Edit this file
src/api/filemaker_client.py
```

### Methods to Implement

#### 1. `authenticate()`

Authenticate with FileMaker and get a session token.

**Example Implementation:**

```python
def authenticate(self) -> str:
    """Authenticate with FileMaker Data API."""
    self.logger.info("Authenticating with FileMaker...")

    endpoint = f"/fmi/data/v1/databases/{self.database}/sessions"

    try:
        response = self.post(
            endpoint,
            auth=(self.username, self.password)
        )

        if response.status_code != 200:
            raise AuthenticationError(
                f"Authentication failed: {response.status_code}",
                details={"response": response.text}
            )

        data = response.json()
        self.token = data["response"]["token"]

        # Add token to headers for subsequent requests
        self.client.headers["Authorization"] = f"Bearer {self.token}"

        self.logger.info("Authentication successful")
        return self.token

    except httpx.HTTPError as e:
        raise AuthenticationError(f"HTTP error during authentication: {str(e)}")
```

#### 2. `get_all_stock()`

Fetch all stock records from FileMaker.

**Example Implementation:**

```python
def get_all_stock(self) -> List[StockItem]:
    """Get all stock records from FileMaker."""
    self._ensure_authenticated()
    self.logger.info("Fetching all stock from FileMaker...")

    layout = "Stock"  # Your layout name
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/records"

    try:
        # Fetch records (adjust _limit as needed)
        response = self.get(endpoint, params={"_limit": 5000})

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"Failed to fetch stock: {response.status_code}",
                details={"response": response.text}
            )

        data = response.json()
        records = data["response"]["data"]

        stock_items = []
        for record in records:
            fields = record["fieldData"]

            # Map your FileMaker fields to StockItem
            stock_items.append(StockItem(
                sku=fields["SKU"],           # Your field name
                quantity=int(fields["Quantity"]),  # Your field name
                source="filemaker",
                metadata={"record_id": record["recordId"]}
            ))

        self.logger.info(f"Fetched {len(stock_items)} stock items")
        return stock_items

    except Exception as e:
        raise FileMakerAPIError(f"Failed to fetch all stock: {str(e)}")
```

#### 3. `get_stock_by_sku()`

Fetch stock for a specific SKU.

**Example Implementation:**

```python
def get_stock_by_sku(self, sku: str) -> Optional[StockItem]:
    """Get stock information for a specific SKU."""
    self._ensure_authenticated()
    self.logger.debug(f"Fetching stock for SKU: {sku}")

    layout = "Stock"  # Your layout name
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/_find"

    # FileMaker find request
    find_request = {
        "query": [
            {"SKU": sku}  # Your field name
        ]
    }

    try:
        response = self.post(endpoint, json=find_request)

        # FileMaker returns 401 when no records found
        if response.status_code == 401:
            return None

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"Failed to find SKU: {sku}",
                details={"response": response.text}
            )

        data = response.json()
        records = data["response"]["data"]

        if not records:
            return None

        # Return first matching record
        record = records[0]
        fields = record["fieldData"]

        return StockItem(
            sku=sku,
            quantity=int(fields["Quantity"]),  # Your field name
            source="filemaker",
            metadata={"record_id": record["recordId"]}
        )

    except Exception as e:
        raise FileMakerAPIError(f"Failed to get stock for {sku}: {str(e)}")
```

#### 4. `update_stock()`

Update stock quantity for a SKU.

**Example Implementation:**

```python
def update_stock(self, sku: str, quantity: int) -> bool:
    """Update stock quantity for a SKU."""
    self._ensure_authenticated()
    self.logger.info(f"Updating stock for {sku}: {quantity}")

    # First find the record
    stock_item = self.get_stock_by_sku(sku)
    if not stock_item:
        raise FileMakerAPIError(f"SKU not found: {sku}")

    record_id = stock_item.metadata["record_id"]

    # Update the record
    layout = "Stock"  # Your layout name
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/records/{record_id}"

    update_data = {
        "fieldData": {
            "Quantity": quantity  # Your field name
        }
    }

    try:
        response = self.patch(endpoint, json=update_data)

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"Failed to update stock for {sku}",
                details={"response": response.text}
            )

        self.logger.info(f"Successfully updated {sku} to {quantity}")
        return True

    except Exception as e:
        raise FileMakerAPIError(f"Failed to update {sku}: {str(e)}")
```

#### 5. `record_stock_movement()`

Record stock movements for audit trail (optional but recommended).

**Example Implementation:**

```python
def record_stock_movement(
    self,
    sku: str,
    quantity_change: int,
    movement_type: str,
    notes: Optional[str] = None
) -> bool:
    """Record a stock movement for audit trail."""
    self._ensure_authenticated()
    self.logger.info(f"Recording stock movement for {sku}: {quantity_change}")

    layout = "StockMovements"  # Your layout name for movements
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/records"

    from datetime import datetime

    movement_data = {
        "fieldData": {
            "SKU": sku,                                    # Your field name
            "QuantityChange": quantity_change,             # Your field name
            "MovementType": movement_type,                 # Your field name
            "Notes": notes or "",                          # Your field name
            "Timestamp": datetime.utcnow().isoformat()     # Your field name
        }
    }

    try:
        response = self.post(endpoint, json=movement_data)

        if response.status_code not in [200, 201]:
            raise FileMakerAPIError(
                "Failed to record stock movement",
                details={"response": response.text}
            )

        self.logger.debug(f"Recorded movement for {sku}")
        return True

    except Exception as e:
        # Log warning but don't fail the whole operation
        self.logger.warning(f"Failed to record movement: {str(e)}")
        return False
```

### Field Mapping Reference

You'll need to know your FileMaker field names. Common mappings:

| Application Field | Your FileMaker Field | Type |
|------------------|---------------------|------|
| SKU | `SKU` or `ProductCode` | Text |
| Quantity | `Quantity` or `Stock` | Number |
| RecordID | `recordId` (automatic) | - |

### Testing Your Implementation

After implementing the methods, test them:

```bash
python -m src.cli test-connection
```

## Step 4: Test the Application Locally

### Test API Connections

```bash
python -m src.cli test-connection
```

Expected output:
```
FileMaker Data API:
  ✓ Connected successfully

Shopify Admin API:
  ✓ Connected successfully

✓ All connections successful!
```

### Run a Dry-Run Sync

Preview what would be synced without making changes:

```bash
python -m src.cli sync --dry-run
```

### Run Actual Sync

```bash
python -m src.cli sync
```

### Test Single SKU

```bash
python -m src.cli sync-sku YOUR-TEST-SKU
```

## Step 5: Test Webhook Server Locally

### Start the Webhook Server

```bash
uvicorn src.webhook_server:app --reload --port 8000
```

### Test Health Endpoint

```bash
curl http://localhost:8000/health
```

### Test Webhook (Development Only)

```bash
curl -X POST http://localhost:8000/webhooks/shopify/test \
  -H "Content-Type: application/json" \
  -d @test_order.json
```

## Step 6: Deploy to Railway

### Install Railway CLI

```bash
npm install -g @railway/cli
```

### Login to Railway

```bash
railway login
```

### Create New Project

```bash
railway init
```

### Set Environment Variables

In Railway dashboard, add all variables from your `.env` file:

1. Go to your project
2. Click on **Variables**
3. Add each variable:
   - `FILEMAKER_HOST`
   - `FILEMAKER_DATABASE`
   - `FILEMAKER_USERNAME`
   - `FILEMAKER_PASSWORD`
   - `SHOPIFY_SHOP_URL`
   - `SHOPIFY_ACCESS_TOKEN`
   - `SHOPIFY_LOCATION_ID`
   - `SHOPIFY_WEBHOOK_SECRET`
   - `ENVIRONMENT=production`
   - `SYNC_INTERVAL_MINUTES=60`

### Deploy

```bash
railway up
```

Railway will automatically:
- Detect Python application
- Install dependencies
- Start both `web` and `worker` services from Procfile

### Get Your App URL

```bash
railway domain
```

Your webhook URL will be: `https://your-app.railway.app/webhooks/shopify/orders`

## Step 7: Configure Shopify Webhooks

### Create Order Webhook

1. Go to Shopify Admin
2. Navigate to **Settings** → **Notifications**
3. Scroll to **Webhooks** section
4. Click **Create webhook**
5. Configure:
   - **Event**: `Order payment` or `Order fulfillment`
   - **Format**: JSON
   - **URL**: `https://your-app.railway.app/webhooks/shopify/orders`
   - **API version**: Latest
6. Click **Save**

### Copy Webhook Secret

After creating the webhook, Shopify will show a signing secret. Copy this and:

1. Go to Railway dashboard
2. Update `SHOPIFY_WEBHOOK_SECRET` variable
3. Restart your web service

### Test Webhook

1. Place a test order in Shopify
2. Check Railway logs: `railway logs`
3. Verify FileMaker stock was decremented

## Troubleshooting

### FileMaker Connection Fails

- Verify FileMaker Data API is enabled
- Check host URL is correct
- Confirm credentials
- Test with FileMaker Data API browser tool

### Shopify Connection Fails

- Verify access token
- Check scopes are correct
- Confirm shop URL format
- Test in GraphiQL explorer

### Webhook Not Working

- Verify webhook URL is publicly accessible
- Check webhook secret matches
- Review Railway logs for errors
- Test with Shopify's webhook testing tool

### Sync Errors

- Check logs in `logs/` directory
- Verify SKUs match between systems
- Test single SKU first
- Use `--dry-run` to preview

## Next Steps

1. Monitor logs for the first few days
2. Adjust sync interval as needed
3. Set up monitoring/alerting
4. Create backups of configurations
5. Document your specific FileMaker field mappings

## Support

For questions or issues:
- Check logs first
- Review troubleshooting section in README
- Test connections with `test-connection` command
