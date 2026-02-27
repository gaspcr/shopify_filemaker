"""Configuration management using pydantic-settings."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class APIConfig(BaseModel):
    """API configuration settings."""
    timeout: int = 30
    max_retries: int = 3
    retry_delay: int = 1
    exponential_backoff: bool = True


class SyncConfig(BaseModel):
    """Synchronization configuration settings."""
    batch_size: int = 100
    enable_diff_check: bool = True
    parallel_processing: bool = False


class ShopifyConfig(BaseModel):
    """Shopify-specific configuration."""
    rate_limit_delay: float = 0.5
    bulk_operation_timeout: int = 300
    api_version: str = "2024-01"


class FileMakerConfig(BaseModel):
    """FileMaker-specific configuration."""
    session_timeout: int = 900
    auto_refresh_token: bool = True


class LoggingFilesConfig(BaseModel):
    """Log file paths."""
    sync: str = "logs/sync.log"
    webhook: str = "logs/webhook.log"
    error: str = "logs/error.log"


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    max_bytes: int = 10485760  # 10MB
    backup_count: int = 5
    files: LoggingFilesConfig = LoggingFilesConfig()


class WebhookConfig(BaseModel):
    """Webhook configuration."""
    validate_signature: bool = True
    request_timeout: int = 30


class SchedulerConfig(BaseModel):
    """Scheduler configuration."""
    timezone: str = "America/Santiago"
    max_instances: int = 1
    coalesce: bool = True
    misfire_grace_time: int = 300

    # Nightly sync schedule — easy to modify for testing
    nightly_sync_hour: int = 22
    nightly_sync_minute: int = 0


class YAMLConfig(BaseModel):
    """Configuration loaded from YAML file."""
    api: APIConfig = APIConfig()
    sync: SyncConfig = SyncConfig()
    shopify: ShopifyConfig = ShopifyConfig()
    filemaker: FileMakerConfig = FileMakerConfig()
    logging: LoggingConfig = LoggingConfig()
    webhook: WebhookConfig = WebhookConfig()
    scheduler: SchedulerConfig = SchedulerConfig()


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # FileMaker settings
    filemaker_host: str = Field(..., description="FileMaker server URL")
    filemaker_database: str = Field(..., description="FileMaker database name")
    filemaker_username: str = Field(..., description="FileMaker username")
    filemaker_password: str = Field(..., description="FileMaker password")

    # Shopify settings
    shopify_shop_url: str = Field(..., description="Shopify shop URL")
    shopify_access_token: str = Field(..., description="Shopify access token")
    shopify_location_id: str = Field(..., description="Shopify inventory location ID")
    shopify_webhook_secret: str = Field(..., description="Shopify webhook secret")

    # Application settings
    environment: str = Field(default="development", description="Environment (development/production)")
    log_level: Optional[str] = Field(default=None, description="Override log level")
    sync_interval_minutes: int = Field(default=60, description="Sync interval in minutes")
    port: int = Field(default=8000, description="Server port")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


class AppConfig:
    """Combined application configuration."""

    def __init__(self):
        self.env = Settings()

        # Load YAML config
        config_path = Path(__file__).parent.parent.parent / "config" / "config.yml"
        if config_path.exists():
            with open(config_path, "r") as f:
                yaml_data = yaml.safe_load(f)
                self.yaml = YAMLConfig(**yaml_data)
        else:
            self.yaml = YAMLConfig()

        # Override log level if specified in env
        if self.env.log_level:
            self.yaml.logging.level = self.env.log_level

    @property
    def api(self) -> APIConfig:
        return self.yaml.api

    @property
    def sync(self) -> SyncConfig:
        return self.yaml.sync

    @property
    def shopify(self) -> ShopifyConfig:
        return self.yaml.shopify

    @property
    def filemaker(self) -> FileMakerConfig:
        return self.yaml.filemaker

    @property
    def logging(self) -> LoggingConfig:
        return self.yaml.logging

    @property
    def webhook(self) -> WebhookConfig:
        return self.yaml.webhook

    @property
    def scheduler(self) -> SchedulerConfig:
        return self.yaml.scheduler

    @property
    def is_production(self) -> bool:
        return self.env.environment.lower() == "production"


@lru_cache()
def get_config() -> AppConfig:
    """Get cached configuration instance."""
    return AppConfig()
