"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import Mock, MagicMock

from src.models.product import StockItem
from src.models.sync_result import SyncResult


@pytest.fixture
def sample_stock_item():
    """Create a sample StockItem for testing."""
    return StockItem(
        sku="TEST-SKU-001",
        quantity=100,
        source="filemaker",
        metadata={"record_id": "12345"}
    )


@pytest.fixture
def sample_stock_items():
    """Create multiple sample StockItems for testing."""
    return [
        StockItem(sku="TEST-001", quantity=10, source="filemaker"),
        StockItem(sku="TEST-002", quantity=20, source="filemaker"),
        StockItem(sku="TEST-003", quantity=30, source="filemaker"),
    ]


@pytest.fixture
def sample_sync_result():
    """Create a sample SyncResult for testing."""
    result = SyncResult(success=True, total_items=10)
    result.updated_count = 8
    result.failed_count = 1
    result.skipped_count = 1
    result.finalize()
    return result


@pytest.fixture
def mock_filemaker_client():
    """Create a mock FileMaker client."""
    client = MagicMock()
    client.authenticate.return_value = "mock-token"
    client.get_all_stock.return_value = []
    client.get_stock_by_sku.return_value = None
    client.update_stock.return_value = True
    client.record_stock_movement.return_value = True
    return client


@pytest.fixture
def mock_shopify_client():
    """Create a mock Shopify client."""
    client = MagicMock()
    client.get_inventory_by_sku.return_value = None
    client.update_inventory.return_value = True
    client.bulk_update_inventory.return_value = {
        "success_count": 0,
        "error_count": 0,
        "errors": []
    }
    return client


@pytest.fixture
def sample_shopify_order_webhook():
    """Create a sample Shopify order webhook payload."""
    return {
        "id": 123456789,
        "name": "#1001",
        "email": "customer@example.com",
        "created_at": "2024-01-15T10:00:00-05:00",
        "line_items": [
            {
                "id": 1,
                "sku": "TEST-SKU-001",
                "quantity": 2,
                "price": "29.99",
                "name": "Test Product 1"
            },
            {
                "id": 2,
                "sku": "TEST-SKU-002",
                "quantity": 1,
                "price": "49.99",
                "name": "Test Product 2"
            }
        ],
        "total_price": "109.97",
        "financial_status": "paid",
        "fulfillment_status": None
    }
