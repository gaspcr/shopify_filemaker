"""FileMaker Data API client with token caching and auto-refresh."""

import time
import threading
from typing import List, Dict, Any, Optional
import httpx

from .base_client import BaseClient
from ..utils.config import get_config
from ..utils.logger import get_api_logger
from ..utils.exceptions import FileMakerAPIError, AuthenticationError
from ..models.product import StockItem

STOCK_LAYOUT = "StockInventario_dapi"
MOVEMENTS_LAYOUT = "MovimientoStock_dapi"


# ---------------------------------------------------------------------------
# Token cache (shared across all FileMakerClient instances)
# ---------------------------------------------------------------------------

class _TokenCache:
    """Thread-safe in-memory token cache with TTL.

    FileMaker Data API sessions expire after 15 minutes of inactivity.
    We cache for 14 minutes (840 s) so we proactively refresh before
    expiry, avoiding mid-request failures.
    """

    def __init__(self, ttl_seconds: int = 840):
        self._token: Optional[str] = None
        self._expires_at: float = 0
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def get(self) -> Optional[str]:
        with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            return None

    def set(self, token: str):
        with self._lock:
            self._token = token
            self._expires_at = time.time() + self._ttl

    def invalidate(self):
        with self._lock:
            self._token = None
            self._expires_at = 0


# Module-level singleton — avoids creating duplicate FM sessions.
_token_cache = _TokenCache(ttl_seconds=840)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fm_code(data: dict) -> str:
    """Extract FileMaker message code from a response dict."""
    messages = data.get("messages", [])
    return messages[0].get("code", "") if messages else ""


def _fm_message(data: dict) -> str:
    """Extract FileMaker message text from a response dict."""
    messages = data.get("messages", [])
    return messages[0].get("message", "Unknown FileMaker error") if messages else "No messages"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class FileMakerClient(BaseClient):
    """Client for interacting with FileMaker Data API."""

    def __init__(self):
        """Initialize FileMaker client."""
        config = get_config()
        host = config.env.filemaker_host
        self.database = config.env.filemaker_database
        self.username = config.env.filemaker_username
        self.password = config.env.filemaker_password

        if not host.startswith("https://") and not host.startswith("http://"):
            host = f"https://{host}"

        super().__init__(base_url=host)
        self.logger = get_api_logger()
        self.token: Optional[str] = None
        self.session_timeout = config.filemaker.session_timeout
        self.auto_refresh_token = config.filemaker.auto_refresh_token

    # ------------------------------------------------------------------
    # Authentication (with cache)
    # ------------------------------------------------------------------

    def authenticate(self, force_refresh: bool = False) -> str:
        """
        Authenticate with FileMaker Data API and obtain a session token.

        When *force_refresh* is False the cached token is reused if it has
        not yet expired, avoiding unnecessary session creation.

        Returns:
            Session token string

        Raises:
            AuthenticationError: If authentication fails
        """
        # ── Try the cache first ───────────────────────────────────────
        if not force_refresh:
            cached = _token_cache.get()
            if cached:
                self.token = cached
                self.client.headers["Authorization"] = f"Bearer {cached}"
                self.logger.debug("Using cached FileMaker token")
                return cached

        # ── Request a new session ─────────────────────────────────────
        self.logger.info("Authenticating with FileMaker (new session)...")

        endpoint = f"/fmi/data/v1/databases/{self.database}/sessions"

        try:
            response = self.post(endpoint, auth=(self.username, self.password))
        except httpx.HTTPError as e:
            raise AuthenticationError(
                f"Network error during authentication: {str(e)}",
                details={"error": str(e)}
            )

        if response.status_code != 200:
            try:
                data = response.json()
                msg = _fm_message(data)
            except Exception:
                msg = response.text
            raise AuthenticationError(
                f"Authentication failed (HTTP {response.status_code}): {msg}",
                details={"status_code": response.status_code, "response": response.text}
            )

        data = response.json()
        self.token = data["response"]["token"]

        # Set auth header for all subsequent requests
        self.client.headers["Authorization"] = f"Bearer {self.token}"

        # Cache the token
        _token_cache.set(self.token)

        self.logger.info("FileMaker authentication successful (token cached)")
        return self.token

    def _ensure_authenticated(self):
        """Ensure the client is authenticated before making requests."""
        if not self.token:
            self.authenticate()

    # ------------------------------------------------------------------
    # Authenticated request wrapper (auto-refresh on 401)
    # ------------------------------------------------------------------

    def _fm_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        """
        Make an authenticated request to FileMaker with automatic token
        refresh when the session has expired (HTTP 401).

        This should be used instead of ``self.get()`` / ``self.post()``
        for **all** endpoints that require a valid session token —
        *not* for the ``/sessions`` endpoint itself.
        """
        self._ensure_authenticated()

        response = self._make_request_with_retry(method, endpoint, **kwargs)

        if response.status_code == 401:
            self.logger.warning(
                "FileMaker session expired (HTTP 401), re-authenticating..."
            )
            _token_cache.invalidate()
            self.token = None
            self.authenticate(force_refresh=True)
            response = self._make_request_with_retry(method, endpoint, **kwargs)

        return response

    # ------------------------------------------------------------------
    # Script execution
    # ------------------------------------------------------------------

    def run_script(
        self,
        layout: str,
        script_name: str,
        script_param: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run a FileMaker script via the Data API.

        GET /fmi/data/v1/databases/{db}/layouts/{layout}/script/{script_name}

        Args:
            layout: The layout context for the script.
            script_name: Name of the FileMaker script to execute.
            script_param: Optional parameter to pass to the script.

        Returns:
            Parsed response body from FileMaker.

        Raises:
            FileMakerAPIError: If the request fails.
        """
        import urllib.parse

        encoded_script = urllib.parse.quote(script_name, safe="")
        endpoint = (
            f"/fmi/data/v1/databases/{self.database}"
            f"/layouts/{layout}/script/{encoded_script}"
        )

        params: Dict[str, str] = {}
        if script_param is not None:
            params["script.param"] = script_param

        self.logger.info(
            f"Running FM script '{script_name}' on layout '{layout}'"
            + (f" with param '{script_param}'" if script_param else "")
        )

        try:
            response = self._fm_request("GET", endpoint, params=params)
        except httpx.HTTPError as e:
            raise FileMakerAPIError(
                f"Network error running script '{script_name}': {str(e)}",
                details={"error": str(e)},
            )

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"Unexpected HTTP {response.status_code} running script",
                details={"response": response.text},
            )

        data = response.json()
        code = _fm_code(data)

        if code != "0":
            raise FileMakerAPIError(
                f"FM script error: {_fm_message(data)}",
                details={"code": code},
            )

        self.logger.info(f"FM script '{script_name}' completed successfully")
        return data.get("response", {})

    # ------------------------------------------------------------------
    # New architecture methods
    # ------------------------------------------------------------------

    def get_all_products(self) -> List[Dict[str, str]]:
        """
        Fetch all product SKUs with Clasificación == "8" from FileMaker.

        Returns:
            List of {"sku": "...", "name": "..."} dicts.
        """
        self.logger.info("Fetching all product SKUs from FileMaker (paginated)...")

        endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{STOCK_LAYOUT}/_find"
        page_size = 100
        offset = 1
        products: List[Dict[str, str]] = []

        while True:
            payload = {
                "query": [{"Clasificación": "8"}],
                "limit": str(page_size),
                "offset": str(offset),
            }

            try:
                response = self._fm_request("POST", endpoint, json=payload)
            except httpx.HTTPError as e:
                raise FileMakerAPIError(
                    f"Network error fetching products (offset={offset}): {str(e)}",
                    details={"error": str(e)}
                )

            if response.status_code != 200:
                raise FileMakerAPIError(
                    f"Unexpected HTTP {response.status_code} fetching products",
                    details={"response": response.text}
                )

            data = response.json()
            code = _fm_code(data)

            if code == "401":  # No records match
                break
            if code != "0":
                raise FileMakerAPIError(
                    f"FileMaker error: {_fm_message(data)}", details={"code": code}
                )

            records = data["response"]["data"]
            if not records:
                break

            for record in records:
                fields = record["fieldData"]
                products.append({
                    "sku": str(fields["Conceptos Cobro_pk"]),
                    "name": fields.get("Nombre", ""),
                })

            self.logger.info(
                f"Fetched page {(offset - 1) // page_size + 1}: "
                f"{len(records)} records (total so far: {len(products)})"
            )

            if len(records) < page_size:
                break
            offset += page_size

        self.logger.info(f"Fetched {len(products)} product SKUs from FileMaker")
        return products

    def recalculate_stock(self, sku: str) -> None:
        """
        Execute the ActualizarStock_dapi script for a specific product.

        GET .../layouts/MovimientoStock_dapi/script/ActualizarStock_dapi?script.param={sku}

        Raises:
            FileMakerAPIError: If the script fails or returns a non-zero scriptError.
        """
        import urllib.parse

        script_name = "ActualizarStock_dapi"
        encoded = urllib.parse.quote(script_name, safe="")
        endpoint = (
            f"/fmi/data/v1/databases/{self.database}"
            f"/layouts/{MOVEMENTS_LAYOUT}/script/{encoded}"
        )

        try:
            response = self._fm_request(
                "GET", endpoint, params={"script.param": sku}
            )
        except httpx.HTTPError as e:
            raise FileMakerAPIError(
                f"Network error running recalc for SKU {sku}: {str(e)}",
                details={"sku": sku, "error": str(e)},
            )

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"HTTP {response.status_code} running recalc for SKU {sku}",
                details={"sku": sku, "response": response.text},
            )

        data = response.json()
        script_error = data.get("response", {}).get("scriptError", "")
        if script_error != "0":
            raise FileMakerAPIError(
                f"Recalc script error for SKU {sku}: scriptError={script_error}",
                details={"sku": sku, "script_error": script_error},
            )

    def get_stock(self, sku: str) -> int:
        """
        Fetch the current Inventario for a specific product by its SKU.

        POST .../layouts/StockInventario_dapi/_find
        Body: {"query": [{"Conceptos Cobro_pk": "{sku}"}]}

        Returns:
            The Inventario value (clamped to >= 0).

        Raises:
            FileMakerAPIError: If the product is not found or the request fails.
        """
        endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{STOCK_LAYOUT}/_find"
        payload = {"query": [{"Conceptos Cobro_pk": sku}]}

        try:
            response = self._fm_request("POST", endpoint, json=payload)
        except httpx.HTTPError as e:
            raise FileMakerAPIError(
                f"Network error fetching stock for SKU {sku}: {str(e)}",
                details={"sku": sku, "error": str(e)},
            )

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"HTTP {response.status_code} fetching stock for SKU {sku}",
                details={"sku": sku, "response": response.text},
            )

        data = response.json()
        code = _fm_code(data)

        if code == "401":
            raise FileMakerAPIError(
                f"Product not found in FM for SKU {sku}",
                details={"sku": sku},
            )
        if code != "0":
            raise FileMakerAPIError(
                f"FM error fetching stock for SKU {sku}: {_fm_message(data)}",
                details={"code": code},
            )

        fields = data["response"]["data"][0]["fieldData"]
        raw_inv = fields.get("Inventario")
        quantity = int(float(raw_inv)) if raw_inv not in (None, "", 0.0) else 0
        return max(0, quantity)

    def create_movement(self, sku: str, quantity_out: int) -> None:
        """
        Create a stock exit (salida) movement record in FileMaker.

        POST .../layouts/MovimientoStock_dapi/records
        Body: {"fieldData": {"Concepto Cobro_fk": sku,
                             "Inv_Cant_Salida": quantity_out,
                             "Inv_Cant_Entrada": 0}}

        Args:
            sku: Conceptos Cobro_pk.
            quantity_out: Number of units sold (positive integer).

        Raises:
            FileMakerAPIError: If record creation fails.
        """
        endpoint = (
            f"/fmi/data/v1/databases/{self.database}"
            f"/layouts/{MOVEMENTS_LAYOUT}/records"
        )
        payload = {
            "fieldData": {
                "Concepto Cobro_fk": int(sku),
                "Inv_Cant_Salida": quantity_out,
                "Inv_Cant_Entrada": 0,
            }
        }

        try:
            response = self._fm_request("POST", endpoint, json=payload)
        except httpx.HTTPError as e:
            raise FileMakerAPIError(
                f"Network error creating movement for SKU {sku}: {str(e)}",
                details={"sku": sku, "error": str(e)},
            )

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"HTTP {response.status_code} creating movement for SKU {sku}",
                details={"sku": sku, "response": response.text},
            )

        data = response.json()
        code = _fm_code(data)
        if code != "0":
            raise FileMakerAPIError(
                f"FM error creating movement for SKU {sku}: {_fm_message(data)}",
                details={"code": code},
            )

        self.logger.info(f"Movement record created for SKU {sku} (salida: {quantity_out})")

    # ------------------------------------------------------------------
    # Legacy stock retrieval (still used internally)
    # ------------------------------------------------------------------

    def get_all_stock(self) -> List[StockItem]:
        """
        Fetch all sellable products (Clasificación == "8") from FileMaker.

        FileMaker Data API returns a maximum of 100 records per request,
        so this method paginates automatically until all records are fetched.

        Returns:
            List of StockItem objects, one per product

        Raises:
            FileMakerAPIError: If the request fails
        """
        self.logger.info("Fetching all stock from FileMaker (paginated)...")

        endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{STOCK_LAYOUT}/_find"
        page_size = 100
        offset = 1  # FM uses 1-based offsets
        stock_items: List[StockItem] = []

        while True:
            payload = {
                "query": [{"Clasificación": "8"}],
                "limit": str(page_size),
                "offset": str(offset),
            }

            try:
                response = self._fm_request("POST", endpoint, json=payload)
            except httpx.HTTPError as e:
                raise FileMakerAPIError(
                    f"Network error fetching stock (offset={offset}): {str(e)}",
                    details={"error": str(e)}
                )

            if response.status_code != 200:
                raise FileMakerAPIError(
                    f"Unexpected HTTP {response.status_code} fetching stock",
                    details={"response": response.text}
                )

            data = response.json()
            code = _fm_code(data)

            if code == "401":
                # FM "No records match the request"
                if not stock_items:
                    self.logger.warning("No stock records found with Clasificación=8")
                break  # No more records

            if code != "0":
                raise FileMakerAPIError(
                    f"FileMaker error fetching stock: {_fm_message(data)}",
                    details={"code": code}
                )

            records = data["response"]["data"]
            if not records:
                break

            for record in records:
                fields = record["fieldData"]

                # Conceptos Cobro_pk is the product identifier used as SKU
                sku = str(fields["Conceptos Cobro_pk"])

                # Inventario may come back as int, float, str, or None
                raw_inv = fields.get("Inventario")
                quantity = int(float(raw_inv)) if raw_inv not in (None, "", 0.0) else 0
                # Ensure non-negative (FM can store negative stock in edge cases)
                quantity = max(0, quantity)

                stock_items.append(StockItem(
                    sku=sku,
                    quantity=quantity,
                    source="filemaker",
                    metadata={
                        "record_id": record["recordId"],
                        "nombre": fields.get("Nombre", ""),
                        "valor": fields.get("Valor"),
                        "clasificacion": fields.get("Clasificación", "")
                    }
                ))

            self.logger.info(
                f"Fetched page {(offset - 1) // page_size + 1}: "
                f"{len(records)} records (total so far: {len(stock_items)})"
            )

            # If we got fewer records than page_size, we're done
            if len(records) < page_size:
                break

            offset += page_size

        self.logger.info(f"Fetched {len(stock_items)} total stock items from FileMaker")
        return stock_items

    def get_stock_by_sku(self, sku: str) -> Optional[StockItem]:
        """
        Fetch stock for a single product by its Conceptos Cobro_pk value.

        Args:
            sku: The Conceptos Cobro_pk value (e.g. "56939129139")

        Returns:
            StockItem if found, None if the product does not exist

        Raises:
            FileMakerAPIError: If the request fails for any reason other than
                               "no records found"
        """
        self.logger.debug(f"Fetching stock for SKU (Conceptos Cobro_pk): {sku}")

        endpoint = f"/fmi/data/v1/databases/{self.database}/layouts/{STOCK_LAYOUT}/_find"
        # FileMaker exact-match operator: ==value
        payload = {"query": [{"Conceptos Cobro_pk": f"=={sku}"}]}

        try:
            response = self._fm_request("POST", endpoint, json=payload)
        except httpx.HTTPError as e:
            raise FileMakerAPIError(
                f"Network error fetching SKU {sku}: {str(e)}",
                details={"sku": sku, "error": str(e)}
            )

        if response.status_code != 200:
            raise FileMakerAPIError(
                f"Unexpected HTTP {response.status_code} fetching SKU {sku}",
                details={"sku": sku, "response": response.text}
            )

        data = response.json()
        code = _fm_code(data)

        # FM code "401" = "No records match the request" — not an HTTP 401
        if code == "401":
            self.logger.warning(f"SKU not found in FileMaker: {sku}")
            return None

        if code != "0":
            raise FileMakerAPIError(
                f"FileMaker error fetching SKU {sku}: {_fm_message(data)}",
                details={"sku": sku, "code": code}
            )

        records = data["response"]["data"]
        if not records:
            return None

        record = records[0]
        fields = record["fieldData"]

        raw_inv = fields.get("Inventario")
        quantity = int(float(raw_inv)) if raw_inv not in (None, "", 0.0) else 0
        quantity = max(0, quantity)

        return StockItem(
            sku=str(fields["Conceptos Cobro_pk"]),
            quantity=quantity,
            source="filemaker",
            metadata={
                "record_id": record["recordId"],
                "nombre": fields.get("Nombre", ""),
                "valor": fields.get("Valor"),
                "clasificacion": fields.get("Clasificación", "")
            }
        )

    # ------------------------------------------------------------------
    # Stock mutation — not used for this FM setup
    # ------------------------------------------------------------------

    def update_stock(self, sku: str, quantity: int) -> bool:
        """
        Not applicable for this FileMaker configuration.

        FileMaker calculates the Inventario field automatically from the
        movement records in MovimientoStock_dapi.  Direct writes to the
        stock quantity are not supported — use record_stock_movement() instead.
        """
        self.logger.warning(
            "update_stock() is not applicable for this FileMaker setup. "
            "Stock is calculated automatically from movement records. "
            "Use record_stock_movement() to register a stock change."
        )
        return False

    # ------------------------------------------------------------------
    # Stock movement (Shopify → FileMaker)
    # ------------------------------------------------------------------

    def record_stock_movement(
        self,
        sku: str,
        quantity_change: int,
        movement_type: str,
        notes: Optional[str] = None
    ) -> bool:
        """
        Record a stock movement in FileMaker and trigger the recalculation
        script so that the Inventario field is updated immediately.

        This is a TWO-STEP process required by the FileMaker data model:
          1. Create a movement record (Inv_Cant_Salida / Inv_Cant_Entrada).
          2. Run the ActualizarStock_dapi script to recalculate Inventario.

        Both steps use ``_fm_request`` so the token is refreshed
        automatically if it expires between calls.

        Args:
            sku: Conceptos Cobro_pk value that identifies the product in FM.
            quantity_change: Negative for exits (sales), positive for entries.
                             e.g. -2 means 2 units were sold.
            movement_type: Informational — not stored in FM (kept for interface
                           compatibility with the broader service layer).
            notes: Informational — same as above.

        Returns:
            True if both steps succeeded.

        Raises:
            FileMakerAPIError: If either step fails.
        """
        self.logger.info(
            f"Recording stock movement — SKU: {sku}, change: {quantity_change} ({movement_type})"
        )

        concepto_cobro_pk = int(sku)

        # Map signed quantity to FM's entrada/salida fields
        if quantity_change < 0:
            cant_salida = abs(quantity_change)
            cant_entrada = 0
        else:
            cant_salida = 0
            cant_entrada = quantity_change

        # ── Step 1: Create the movement record ────────────────────────
        create_endpoint = (
            f"/fmi/data/v1/databases/{self.database}"
            f"/layouts/{MOVEMENTS_LAYOUT}/records"
        )
        payload = {
            "fieldData": {
                "Concepto Cobro_fk": concepto_cobro_pk,
                "Inv_Cant_Salida": cant_salida,
                "Inv_Cant_Entrada": cant_entrada
            }
        }

        try:
            create_response = self._fm_request("POST", create_endpoint, json=payload)
        except httpx.HTTPError as e:
            raise FileMakerAPIError(
                f"Network error creating movement record for SKU {sku}: {str(e)}",
                details={"sku": sku, "error": str(e)}
            )

        if create_response.status_code != 200:
            raise FileMakerAPIError(
                f"Unexpected HTTP {create_response.status_code} creating movement for SKU {sku}",
                details={"sku": sku, "response": create_response.text}
            )

        create_data = create_response.json()
        code = _fm_code(create_data)
        if code != "0":
            raise FileMakerAPIError(
                f"FileMaker error creating movement for SKU {sku}: {_fm_message(create_data)}",
                details={"sku": sku, "code": code}
            )

        self.logger.debug(f"Movement record created for SKU {sku}")

        # ── Step 2: Trigger the stock-recalculation script ────────────
        script_endpoint = (
            f"/fmi/data/v1/databases/{self.database}"
            f"/layouts/{MOVEMENTS_LAYOUT}/script/ActualizarStock_dapi"
        )

        try:
            script_response = self._fm_request(
                "GET",
                script_endpoint,
                params={"script.param": concepto_cobro_pk}
            )
        except httpx.HTTPError as e:
            raise FileMakerAPIError(
                f"Network error running ActualizarStock_dapi for SKU {sku}: {str(e)}",
                details={"sku": sku, "error": str(e)}
            )

        if script_response.status_code != 200:
            raise FileMakerAPIError(
                f"Unexpected HTTP {script_response.status_code} running script for SKU {sku}",
                details={"sku": sku, "response": script_response.text}
            )

        script_data = script_response.json()
        script_error = script_data.get("response", {}).get("scriptError", "")
        if script_error != "0":
            raise FileMakerAPIError(
                f"ActualizarStock_dapi script error for SKU {sku}: scriptError={script_error}",
                details={"sku": sku, "script_error": script_error}
            )

        self.logger.info(
            f"Stock movement recorded and recalculated — "
            f"SKU: {sku}, salida: {cant_salida}, entrada: {cant_entrada}"
        )
        return True

    # ------------------------------------------------------------------
    # Session cleanup
    # ------------------------------------------------------------------

    def logout(self):
        """Delete the current FileMaker session and invalidate the cache.

        .. note::
           With token caching this should only be called for explicit
           cleanup.  Normal operation lets sessions expire naturally
           (15-minute timeout) to avoid invalidating a token still in
           use by other client instances.
        """
        if not self.token:
            return

        self.logger.info("Logging out from FileMaker...")

        try:
            endpoint = f"/fmi/data/v1/databases/{self.database}/sessions/{self.token}"
            self.delete(endpoint)
            self.logger.info("FileMaker logout successful")
        except Exception as e:
            self.logger.warning(f"FileMaker logout failed (session may have expired): {str(e)}")
        finally:
            self.token = None
            self.client.headers.pop("Authorization", None)
            _token_cache.invalidate()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up on context manager exit.

        We intentionally do NOT call ``logout()`` here because the
        cached token may still be used by other client instances.
        The FM session will expire naturally after 15 minutes.
        """
        super().__exit__(exc_type, exc_val, exc_tb)
