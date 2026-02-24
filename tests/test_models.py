"""Tests for data models."""

import pytest
from datetime import datetime

from src.models.product import StockItem
from src.models.sync_result import SyncResult, SyncError


class TestStockItem:
    """Tests for StockItem model."""

    def test_create_stock_item(self):
        """Test creating a valid StockItem."""
        item = StockItem(
            sku="TEST-001",
            quantity=100,
            source="filemaker"
        )

        assert item.sku == "TEST-001"
        assert item.quantity == 100
        assert item.source == "filemaker"
        assert item.last_updated is not None

    def test_stock_item_validation_empty_sku(self):
        """Test that empty SKU raises ValueError."""
        with pytest.raises(ValueError, match="SKU cannot be empty"):
            StockItem(sku="", quantity=100, source="filemaker")

    def test_stock_item_validation_negative_quantity(self):
        """Test that negative quantity raises ValueError."""
        with pytest.raises(ValueError, match="Quantity cannot be negative"):
            StockItem(sku="TEST-001", quantity=-1, source="filemaker")

    def test_stock_item_validation_invalid_source(self):
        """Test that invalid source raises ValueError."""
        with pytest.raises(ValueError, match="Source must be"):
            StockItem(sku="TEST-001", quantity=100, source="invalid")

    def test_stock_item_to_dict(self):
        """Test converting StockItem to dictionary."""
        item = StockItem(
            sku="TEST-001",
            quantity=100,
            source="filemaker",
            metadata={"test": "value"}
        )

        data = item.to_dict()

        assert data["sku"] == "TEST-001"
        assert data["quantity"] == 100
        assert data["source"] == "filemaker"
        assert data["metadata"]["test"] == "value"

    def test_stock_item_from_dict(self):
        """Test creating StockItem from dictionary."""
        data = {
            "sku": "TEST-001",
            "quantity": 100,
            "source": "filemaker",
            "metadata": {"test": "value"}
        }

        item = StockItem.from_dict(data)

        assert item.sku == "TEST-001"
        assert item.quantity == 100
        assert item.source == "filemaker"
        assert item.metadata["test"] == "value"


class TestSyncResult:
    """Tests for SyncResult model."""

    def test_create_sync_result(self):
        """Test creating a SyncResult."""
        result = SyncResult(success=True, total_items=10)

        assert result.success is True
        assert result.total_items == 10
        assert result.updated_count == 0
        assert result.failed_count == 0
        assert result.errors == []

    def test_add_error(self):
        """Test adding an error to SyncResult."""
        result = SyncResult(success=True)

        result.add_error("TEST-001", "TestError", "Test error message")

        assert result.failed_count == 1
        assert len(result.errors) == 1
        assert result.errors[0].sku == "TEST-001"
        assert result.errors[0].error_type == "TestError"

    def test_finalize(self):
        """Test finalizing a SyncResult."""
        result = SyncResult(success=True, total_items=10)
        result.updated_count = 8

        result.finalize()

        assert result.end_time is not None
        assert result.duration > 0

    def test_success_rate(self):
        """Test calculating success rate."""
        result = SyncResult(success=True, total_items=10)
        result.updated_count = 8

        assert result.success_rate == 80.0

    def test_success_rate_no_items(self):
        """Test success rate with no items."""
        result = SyncResult(success=True, total_items=0)

        assert result.success_rate == 0.0

    def test_get_summary(self):
        """Test getting summary string."""
        result = SyncResult(success=True, total_items=10)
        result.updated_count = 8
        result.failed_count = 1
        result.skipped_count = 1
        result.finalize()

        summary = result.get_summary()

        assert "Total items: 10" in summary
        assert "Updated: 8" in summary
        assert "Failed: 1" in summary
        assert "Skipped: 1" in summary
