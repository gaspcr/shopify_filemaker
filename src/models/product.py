"""Product and stock item data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class StockItem:
    """Represents a stock item in the inventory system."""

    sku: str
    quantity: int
    source: str  # "filemaker" or "shopify"
    metadata: Dict[str, Any] = field(default_factory=dict)
    last_updated: Optional[datetime] = None

    def __post_init__(self):
        """Validate and normalize data."""
        if not self.sku:
            raise ValueError("SKU cannot be empty")

        if self.quantity < 0:
            raise ValueError("Quantity cannot be negative")

        if self.source not in ["filemaker", "shopify"]:
            raise ValueError("Source must be 'filemaker' or 'shopify'")

        if self.last_updated is None:
            self.last_updated = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "sku": self.sku,
            "quantity": self.quantity,
            "source": self.source,
            "metadata": self.metadata,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StockItem":
        """Create instance from dictionary."""
        last_updated = data.get("last_updated")
        if isinstance(last_updated, str):
            last_updated = datetime.fromisoformat(last_updated)

        return cls(
            sku=data["sku"],
            quantity=data["quantity"],
            source=data["source"],
            metadata=data.get("metadata", {}),
            last_updated=last_updated
        )
