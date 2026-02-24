# Quick Start Guide

## 🚀 Get Running in 5 Steps

### Step 1: Install Dependencies (2 minutes)
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Configure Environment (3 minutes)
```bash
cp config/.env.example .env
# Edit .env with your credentials
```

**Required credentials:**
- FileMaker host, database, username, password
- Shopify shop URL, access token, location ID, webhook secret

### Step 3: Implement FileMaker API (15-30 minutes)
Edit: `src/api/filemaker_client.py`

Implement 5 methods:
1. `authenticate()` - Login to FileMaker
2. `get_all_stock()` - Fetch all inventory
3. `get_stock_by_sku()` - Fetch single item
4. `update_stock()` - Update quantity
5. `record_stock_movement()` - Audit trail

**See SETUP_GUIDE.md Step 3 for complete examples.**

### Step 4: Test Locally (5 minutes)
```bash
# Test connections
python -m src.cli test-connection

# Preview sync (no changes)
python -m src.cli sync --dry-run

# Run actual sync
python -m src.cli sync
```

### Step 5: Deploy to Railway (10 minutes)
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up

# Set environment variables in Railway dashboard
# Configure Shopify webhook to point to your Railway URL
```

---

## 📋 Common Commands

```bash
# Sync operations
python -m src.cli sync                    # Full sync
python -m src.cli sync --dry-run          # Preview only
python -m src.cli sync-sku SKU-001        # Single SKU

# Testing
python -m src.cli test-connection         # Test APIs
python -m src.cli config-info             # View config
pytest tests/ -v                          # Run tests

# Run servers (development)
uvicorn src.webhook_server:app --reload  # Webhook server
python -m src.scheduler                   # Background scheduler
```

---

## 🔧 FileMaker Implementation Template

Here's the minimal template for `filemaker_client.py`:

```python
def authenticate(self) -> str:
    endpoint = f"/fmi/data/v1/databases/{self.database}/sessions"
    response = self.post(endpoint, auth=(self.username, self.password))
    self.token = response.json()["response"]["token"]
    self.client.headers["Authorization"] = f"Bearer {self.token}"
    return self.token

def get_all_stock(self) -> List[StockItem]:
    layout = "YourLayoutName"  # ← Change this
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/records"
    response = self.get(endpoint, params={"_limit": 5000})

    stock_items = []
    for record in response.json()["response"]["data"]:
        fields = record["fieldData"]
        stock_items.append(StockItem(
            sku=fields["YourSKUField"],        # ← Change this
            quantity=int(fields["YourQtyField"]),  # ← Change this
            source="filemaker",
            metadata={"record_id": record["recordId"]}
        ))
    return stock_items

def get_stock_by_sku(self, sku: str) -> Optional[StockItem]:
    layout = "YourLayoutName"  # ← Change this
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/_find"
    response = self.post(endpoint, json={"query": [{"YourSKUField": sku}]})  # ← Change

    if response.status_code == 401:  # No records found
        return None

    record = response.json()["response"]["data"][0]
    fields = record["fieldData"]
    return StockItem(
        sku=sku,
        quantity=int(fields["YourQtyField"]),  # ← Change this
        source="filemaker",
        metadata={"record_id": record["recordId"]}
    )

def update_stock(self, sku: str, quantity: int) -> bool:
    stock_item = self.get_stock_by_sku(sku)
    record_id = stock_item.metadata["record_id"]

    layout = "YourLayoutName"  # ← Change this
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/records/{record_id}"
    response = self.patch(endpoint, json={
        "fieldData": {"YourQtyField": quantity}  # ← Change this
    })
    return response.status_code == 200

def record_stock_movement(self, sku: str, quantity_change: int,
                         movement_type: str, notes: Optional[str] = None) -> bool:
    layout = "YourMovementsLayout"  # ← Change this
    endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{layout}/records"

    from datetime import datetime
    response = self.post(endpoint, json={
        "fieldData": {
            "YourSKUField": sku,                    # ← Change
            "YourQtyChangeField": quantity_change,  # ← Change
            "YourTypeField": movement_type,         # ← Change
            "YourNotesField": notes or "",          # ← Change
            "YourTimestampField": datetime.utcnow().isoformat()  # ← Change
        }
    })
    return response.status_code in [200, 201]
```

**Replace all `YourXXXField` with your actual FileMaker field names!**

---

## 🎯 Shopify Setup

### Get Access Token
1. Shopify Admin → Settings → Apps → Develop apps
2. Create app with scopes: `read_inventory`, `write_inventory`, `read_products`
3. Install app and copy access token

### Get Location ID
Use GraphQL or check URL in Settings → Locations

### Create Webhook
1. Settings → Notifications → Webhooks
2. Event: `Order payment` or `Order fulfillment`
3. URL: `https://your-app.railway.app/webhooks/shopify/orders`
4. Copy webhook secret

---

## 📊 Sync Flow

```
FileMaker → Shopify (Scheduled/Manual)
1. Fetch all stock from FileMaker
2. For each SKU, check Shopify current quantity
3. If different, update Shopify
4. Log results

Shopify → FileMaker (Webhook)
1. Receive order webhook from Shopify
2. Validate HMAC signature
3. Extract line items (SKU + quantity)
4. Decrement FileMaker stock
5. Record movement in audit log
```

---

## ⚠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| FileMaker connection fails | Check host, credentials, Data API enabled |
| Shopify connection fails | Verify access token, check scopes |
| Webhook not receiving | Check URL accessible, verify secret |
| SKU not found | Ensure SKUs match exactly in both systems |
| Rate limited | Increase delays in config.yml |

Check logs: `logs/sync.log`, `logs/webhook.log`, `logs/error.log`

---

## 📚 Documentation

- **README.md** - Complete documentation
- **SETUP_GUIDE.md** - Detailed setup with examples
- **IMPLEMENTATION_SUMMARY.md** - Project overview
- **QUICK_START.md** - This file

---

## ✅ Checklist

- [ ] Dependencies installed
- [ ] .env configured
- [ ] FileMaker methods implemented
- [ ] Connections tested
- [ ] First sync completed
- [ ] Deployed to Railway
- [ ] Webhook configured
- [ ] Monitoring setup

---

**Need Help?** See SETUP_GUIDE.md for detailed examples and troubleshooting.
