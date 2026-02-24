"""Command-line interface for manual synchronization operations."""

import sys
import click
from typing import Optional

from .services.sync_service import SyncService
from .utils.config import get_config
from .utils.exceptions import ConfigurationError


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """
    FileMaker-Shopify Stock Synchronization CLI.

    Manage stock synchronization between FileMaker and Shopify.
    """
    pass


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without applying them"
)
def sync(dry_run: bool):
    """
    Execute full stock synchronization from FileMaker to Shopify.

    This command fetches all stock records from FileMaker and updates
    Shopify inventory to match.
    """
    click.echo("╔════════════════════════════════════════════════════════╗")
    click.echo("║  FileMaker → Shopify Stock Synchronization            ║")
    click.echo("╚════════════════════════════════════════════════════════╝")
    click.echo()

    if dry_run:
        click.echo(click.style("🔍 DRY RUN MODE - No changes will be made", fg="yellow", bold=True))
        click.echo()

    try:
        service = SyncService()

        with click.progressbar(
            length=1,
            label="Synchronizing stock",
            show_eta=False
        ) as bar:
            result = service.execute_filemaker_to_shopify_sync(dry_run=dry_run)
            bar.update(1)

        click.echo()
        click.echo("─" * 60)

        # Display results
        if result.success:
            click.echo(click.style("✓ Sync completed successfully!", fg="green", bold=True))
        else:
            click.echo(click.style("✗ Sync completed with errors", fg="red", bold=True))

        click.echo()
        click.echo(f"Total items:    {result.total_items}")
        click.echo(click.style(f"Updated:        {result.updated_count}", fg="green"))
        click.echo(click.style(f"Failed:         {result.failed_count}", fg="red" if result.failed_count > 0 else None))
        click.echo(f"Skipped:        {result.skipped_count}")
        click.echo(f"Duration:       {result.duration:.2f}s")
        click.echo(f"Success rate:   {result.success_rate:.2f}%")

        # Display errors if any
        if result.errors:
            click.echo()
            click.echo(click.style(f"Errors ({len(result.errors)}):", fg="red", bold=True))
            for i, error in enumerate(result.errors[:10], 1):
                click.echo(f"  {i}. {error.sku}: {error.message}")

            if len(result.errors) > 10:
                click.echo(f"  ... and {len(result.errors) - 10} more errors")
                click.echo("  Check logs/sync.log for full details")

        click.echo("─" * 60)

        sys.exit(0 if result.success else 1)

    except ConfigurationError as e:
        click.echo(click.style(f"✗ Configuration error: {e.message}", fg="red"), err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(click.style(f"✗ Unexpected error: {str(e)}", fg="red"), err=True)
        sys.exit(1)


@cli.command("sync-sku")
@click.argument("sku")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without applying them"
)
def sync_sku(sku: str, dry_run: bool):
    """
    Synchronize a single product by SKU.

    SKU: Product SKU to synchronize
    """
    click.echo(f"Synchronizing SKU: {sku}")

    if dry_run:
        click.echo(click.style("🔍 DRY RUN MODE", fg="yellow"))

    click.echo()

    try:
        service = SyncService()
        result = service.execute_single_sku_sync(sku, dry_run=dry_run)

        if result.success:
            click.echo(click.style("✓ Sync successful!", fg="green", bold=True))

            if result.updated_count > 0:
                click.echo(f"Updated {sku}")
            elif result.skipped_count > 0:
                click.echo(f"Skipped {sku} (no changes needed)")

        else:
            click.echo(click.style("✗ Sync failed", fg="red", bold=True))
            for error in result.errors:
                click.echo(click.style(f"Error: {error.message}", fg="red"))

        sys.exit(0 if result.success else 1)

    except Exception as e:
        click.echo(click.style(f"✗ Error: {str(e)}", fg="red"), err=True)
        sys.exit(1)


@cli.command("test-connection")
def test_connection():
    """
    Test connectivity to FileMaker and Shopify APIs.

    Validates that credentials are correct and APIs are accessible.
    """
    click.echo("Testing API connections...")
    click.echo()

    try:
        service = SyncService()
        results = service.test_connections()

        # FileMaker results
        click.echo("FileMaker Data API:")
        if results["filemaker"]["success"]:
            click.echo(click.style("  ✓ Connected successfully", fg="green"))
        else:
            error = results["filemaker"]["error"]
            if "Not implemented" in error:
                click.echo(click.style(f"  ⚠ {error}", fg="yellow"))
            else:
                click.echo(click.style(f"  ✗ Connection failed: {error}", fg="red"))

        click.echo()

        # Shopify results
        click.echo("Shopify Admin API:")
        if results["shopify"]["success"]:
            click.echo(click.style("  ✓ Connected successfully", fg="green"))
        else:
            click.echo(click.style(f"  ✗ Connection failed: {results['shopify']['error']}", fg="red"))

        click.echo()

        # Overall status
        all_success = all(r["success"] for r in results.values())
        if all_success:
            click.echo(click.style("✓ All connections successful!", fg="green", bold=True))
            sys.exit(0)
        else:
            click.echo(click.style("⚠ Some connections failed", fg="yellow", bold=True))
            sys.exit(1)

    except Exception as e:
        click.echo(click.style(f"✗ Error: {str(e)}", fg="red"), err=True)
        sys.exit(1)


@cli.command()
def config_info():
    """Display current configuration settings."""
    try:
        config = get_config()

        click.echo("Configuration Settings:")
        click.echo("=" * 60)
        click.echo()

        click.echo("Environment:")
        click.echo(f"  Environment:     {config.env.environment}")
        click.echo(f"  Log level:       {config.logging.level}")
        click.echo()

        click.echo("FileMaker:")
        click.echo(f"  Host:            {config.env.filemaker_host}")
        click.echo(f"  Database:        {config.env.filemaker_database}")
        click.echo(f"  Username:        {config.env.filemaker_username}")
        click.echo(f"  Password:        {'*' * len(config.env.filemaker_password)}")
        click.echo()

        click.echo("Shopify:")
        click.echo(f"  Shop URL:        {config.env.shopify_shop_url}")
        click.echo(f"  Location ID:     {config.env.shopify_location_id}")
        click.echo(f"  Access Token:    {config.env.shopify_access_token[:10]}...")
        click.echo()

        click.echo("Sync Settings:")
        click.echo(f"  Batch size:      {config.sync.batch_size}")
        click.echo(f"  Diff check:      {config.sync.enable_diff_check}")
        click.echo(f"  Interval:        {config.env.sync_interval_minutes} minutes")
        click.echo()

    except Exception as e:
        click.echo(click.style(f"✗ Error loading config: {str(e)}", fg="red"), err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
