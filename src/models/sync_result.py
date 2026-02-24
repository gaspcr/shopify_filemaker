"""Synchronization result data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional


@dataclass
class SyncError:
    """Represents a synchronization error."""

    sku: str
    error_type: str
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "sku": self.sku,
            "error_type": self.error_type,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class SyncResult:
    """Represents the result of a synchronization operation."""

    success: bool
    updated_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    errors: List[SyncError] = field(default_factory=list)
    duration: float = 0.0  # seconds
    total_items: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize timestamps if not set."""
        if self.start_time is None:
            self.start_time = datetime.utcnow()

    def add_error(self, sku: str, error_type: str, message: str, details: Optional[Dict[str, Any]] = None):
        """Add an error to the result."""
        error = SyncError(
            sku=sku,
            error_type=error_type,
            message=message,
            details=details
        )
        self.errors.append(error)
        self.failed_count += 1

    def finalize(self):
        """Finalize the sync result with end time and duration."""
        self.end_time = datetime.utcnow()
        if self.start_time:
            self.duration = (self.end_time - self.start_time).total_seconds()

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_items == 0:
            return 0.0
        return (self.updated_count / self.total_items) * 100

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "success": self.success,
            "updated_count": self.updated_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "total_items": self.total_items,
            "success_rate": round(self.success_rate, 2),
            "duration": round(self.duration, 2),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "errors": [error.to_dict() for error in self.errors],
            "metadata": self.metadata
        }

    def get_summary(self) -> str:
        """Get a human-readable summary."""
        summary_lines = [
            f"Sync completed in {self.duration:.2f}s",
            f"Total items: {self.total_items}",
            f"Updated: {self.updated_count}",
            f"Failed: {self.failed_count}",
            f"Skipped: {self.skipped_count}",
            f"Success rate: {self.success_rate:.2f}%"
        ]

        if self.errors:
            summary_lines.append(f"\nErrors ({len(self.errors)}):")
            for error in self.errors[:5]:  # Show first 5 errors
                summary_lines.append(f"  - {error.sku}: {error.message}")
            if len(self.errors) > 5:
                summary_lines.append(f"  ... and {len(self.errors) - 5} more errors")

        return "\n".join(summary_lines)
