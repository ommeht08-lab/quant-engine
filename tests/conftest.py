"""
Shared test fixtures/fakes.

`FakeTradingClient` stands in for `alpaca.trading.client.TradingClient`
across every trading-safety/rebalance/hedge test in this suite. It never
makes a network call and is never pointed at real credentials — no test
in this repository may construct a real `TradingClient`. All financial
math tests (DCF, conviction score, backtesting) use plain synthetic
`pandas` fixtures instead of live `yfinance` data.

Isolation note: this module does three things, in this exact order,
before anything else in the file (including the stdlib/third-party
imports directly below) — a real production incident happened because
none of this existed yet: three pytest runs of
`tests/trading/test_rebalance.py` inserted 12 synthetic rows into the
live Supabase `trade_logs` table (IDs 5-16, since deleted) because
`_safe_log_trade` swallows its own failures and nothing stopped
`psycopg2.connect()` from reaching the real `DATABASE_URL` in the repo
root `.env`.

1. Every credential env var this codebase reads is overwritten with an
   obviously-invalid sentinel value *before* any application module
   (`src.*`) is imported anywhere in the test session. Pytest always
   imports the top-level `tests/conftest.py` before collecting any test
   module beneath it, so this genuinely runs first.
2. `dotenv.load_dotenv` is replaced with a no-op *before* any
   application module does `from dotenv import load_dotenv` — that
   import binds the name at import time, so patching it late (e.g. from
   inside a per-test fixture) would be too late for a module already
   imported by then. (Belt-and-suspenders: `load_dotenv()`'s own default
   `override=False` behavior would already refuse to clobber the
   sentinel values from step 1, but a call site could pass
   `override=True`.)
3. `socket.socket.connect`/`.connect_ex` are replaced with a version that
   always raises, for the lifetime of the whole pytest process — the
   backstop that fails loudly the instant *any* code (ours or a
   dependency's, over Postgres, Alpaca, Redis, or yfinance/Yahoo
   Finance) tries to open a real network connection, independent of
   whether the specific call site happens to be mocked.
"""

import os

_TEST_ISOLATION_SENTINEL = "TEST-ISOLATION-INVALID-DO-NOT-USE"

os.environ["DATABASE_URL"] = f"postgresql://{_TEST_ISOLATION_SENTINEL}@invalid.test:5432/invalid"
os.environ["UPSTASH_REDIS_REST_URL"] = f"https://{_TEST_ISOLATION_SENTINEL}.invalid.test"
os.environ["UPSTASH_REDIS_REST_TOKEN"] = _TEST_ISOLATION_SENTINEL
os.environ["APCA_API_KEY_ID"] = _TEST_ISOLATION_SENTINEL
os.environ["APCA_API_SECRET_KEY"] = _TEST_ISOLATION_SENTINEL
os.environ["APCA_API_BASE_URL"] = "https://invalid.test"

import dotenv  # noqa: E402 - must be patched before any `src.*` module imports `load_dotenv`

dotenv.load_dotenv = lambda *args, **kwargs: False  # real load_dotenv() returns a bool

import socket  # noqa: E402

EXTERNAL_ACCESS_BLOCKED_MESSAGE = (
    "External access blocked during tests — this guards against accidental "
    "contact with Postgres, Alpaca, Redis/Upstash, yfinance/Yahoo Finance, or "
    "any other external service. Mock the specific client/call instead of "
    "relying on real network access."
)


def _blocked_socket_connect(self, *args, **kwargs):
    raise RuntimeError(f"Test attempted a raw network socket connection. {EXTERNAL_ACCESS_BLOCKED_MESSAGE}")


socket.socket.connect = _blocked_socket_connect
socket.socket.connect_ex = _blocked_socket_connect

import types  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Dict, List, Optional  # noqa: E402

import pytest  # noqa: E402

from alpaca.trading.enums import AssetClass, OrderStatus  # noqa: E402

# Order statuses `_await_order_resolution` (src.trading.alpaca_execution)
# treats as fully resolved / no further change expected.
TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
    OrderStatus.DONE_FOR_DAY,
    OrderStatus.STOPPED,
    OrderStatus.SUSPENDED,
    OrderStatus.CALCULATED,
}


def make_position(
    symbol: str,
    qty: float,
    market_value: float,
    current_price: float,
    asset_class: AssetClass = AssetClass.US_EQUITY,
) -> types.SimpleNamespace:
    """A duck-typed stand-in for `alpaca.trading.models.Position`."""
    return types.SimpleNamespace(
        symbol=symbol,
        qty=qty,
        market_value=market_value,
        current_price=current_price,
        asset_class=asset_class,
    )


def make_order(
    order_id: str,
    symbol: str,
    status: OrderStatus = OrderStatus.FILLED,
    filled_qty: Optional[float] = None,
    filled_avg_price: Optional[float] = None,
    asset_class: AssetClass = AssetClass.US_EQUITY,
) -> types.SimpleNamespace:
    """A duck-typed stand-in for `alpaca.trading.models.Order`."""
    return types.SimpleNamespace(
        id=order_id,
        symbol=symbol,
        status=status,
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
        asset_class=asset_class,
    )


def make_option_contract(
    symbol: str,
    strike_price: float,
    expiration_date: str,
    tradable: bool = True,
) -> types.SimpleNamespace:
    """A duck-typed stand-in for `alpaca.trading.models.OptionContract`."""
    return types.SimpleNamespace(
        symbol=symbol,
        strike_price=strike_price,
        expiration_date=expiration_date,
        tradable=tradable,
    )


@dataclass
class _ScriptedOrder:
    """
    A submitted order whose status/fill can change across successive
    `get_order_by_id` polls, simulating a real order resolving over time.
    Each poll advances to the next entry in the sequences (clamped at the
    last one once exhausted), so a test can script e.g. NEW -> NEW ->
    FILLED to exercise the polling loop, or a single-entry sequence for
    an order that resolves immediately.
    """

    order_id: str
    symbol: str
    asset_class: AssetClass
    statuses: List[OrderStatus]
    filled_qtys: List[Optional[float]]
    filled_avg_prices: List[Optional[float]]
    poll_count: int = field(default=0)

    def poll(self) -> types.SimpleNamespace:
        index = min(self.poll_count, len(self.statuses) - 1)
        self.poll_count += 1
        return make_order(
            self.order_id,
            self.symbol,
            status=self.statuses[index],
            filled_qty=self.filled_qtys[index],
            filled_avg_price=self.filled_avg_prices[index],
            asset_class=self.asset_class,
        )


class FakeTradingClient:
    """
    In-memory fake for `alpaca.trading.client.TradingClient`, covering
    every method `src.trading.alpaca_execution` calls: `get_account`,
    `get_all_positions`, `get_clock`, `get_orders`, `submit_order`,
    `close_position`, `get_order_by_id`, `get_option_contracts`.

    By default every submitted order resolves immediately as FILLED at
    the requested notional/qty (using `default_fill_price` to convert
    notional to a quantity). Call `script_fill(symbol, ...)` before
    submitting to control exactly how a specific symbol's next order
    resolves (partial fill, rejection, multi-poll pending, etc).
    """

    def __init__(
        self,
        positions: Optional[List[types.SimpleNamespace]] = None,
        equity: float = 100_000.0,
        market_open: bool = True,
        open_orders: Optional[List[types.SimpleNamespace]] = None,
        option_contracts: Optional[List[types.SimpleNamespace]] = None,
        default_fill_price: float = 100.0,
        cash: Optional[float] = None,
        buying_power: Optional[float] = None,
    ):
        self.positions: Dict[str, types.SimpleNamespace] = {
            p.symbol: p for p in (positions or [])
        }
        self.equity = equity
        # Defaults mirror `equity` so tests that don't care about cash/
        # buying-power constraints (the vast majority) get an effectively
        # unconstrained account, same as this fake's behavior before
        # these fields existed. Tests exercising the buying-power cap
        # pass an explicit, smaller `buying_power`.
        self.cash = cash if cash is not None else equity
        self.buying_power = buying_power if buying_power is not None else equity
        self.market_open = market_open
        self.open_orders = list(open_orders or [])
        self.option_contracts = list(option_contracts or [])
        self.default_fill_price = default_fill_price

        self._next_order_id = 1
        self._scripts: Dict[str, dict] = {}
        self._orders_by_id: Dict[str, _ScriptedOrder] = {}

        self.submitted_orders: List[object] = []
        self.closed_symbols: List[str] = []

    # -- test setup helpers ------------------------------------------------

    def script_fill(
        self,
        symbol: str,
        statuses: List[OrderStatus],
        filled_qtys: List[Optional[float]],
        filled_avg_prices: List[Optional[float]],
        asset_class: AssetClass = AssetClass.US_EQUITY,
    ) -> None:
        """Configure how the *next* order submitted for `symbol` resolves across polls."""
        self._scripts[symbol] = {
            "statuses": statuses,
            "filled_qtys": filled_qtys,
            "filled_avg_prices": filled_avg_prices,
            "asset_class": asset_class,
        }

    # -- TradingClient interface -------------------------------------------

    def get_account(self):
        return types.SimpleNamespace(
            equity=str(self.equity), cash=str(self.cash), buying_power=str(self.buying_power)
        )

    def get_all_positions(self):
        return list(self.positions.values())

    def get_clock(self):
        return types.SimpleNamespace(is_open=self.market_open)

    def get_orders(self, filter=None):  # noqa: A002 - matches alpaca-py's own param name
        return list(self.open_orders)

    def _next_id(self) -> str:
        order_id = f"fake-order-{self._next_order_id}"
        self._next_order_id += 1
        return order_id

    def _register_scripted_order(self, symbol: str, side) -> _ScriptedOrder:
        order_id = self._next_id()
        script = self._scripts.pop(symbol, None)
        if script is None:
            price = self.default_fill_price
            position = self.positions.get(symbol)
            if position is not None:
                price = position.current_price
            script = {
                "statuses": [OrderStatus.FILLED],
                "filled_qtys": [None],  # resolved lazily below once qty is known
                "filled_avg_prices": [price],
                "asset_class": AssetClass.US_EQUITY,
            }
        scripted = _ScriptedOrder(
            order_id=order_id,
            symbol=symbol,
            asset_class=script["asset_class"],
            statuses=list(script["statuses"]),
            filled_qtys=list(script["filled_qtys"]),
            filled_avg_prices=list(script["filled_avg_prices"]),
        )
        self._orders_by_id[order_id] = scripted
        return scripted

    def submit_order(self, order_data):
        self.submitted_orders.append(order_data)
        scripted = self._register_scripted_order(order_data.symbol, order_data.side)
        # A default (unscripted) fill needs a filled_qty; derive it from
        # notional/qty on the request so the immediate-fill happy path
        # doesn't require every test to script a fill explicitly.
        if scripted.filled_qtys == [None] and scripted.statuses == [OrderStatus.FILLED]:
            price = scripted.filled_avg_prices[0] or self.default_fill_price
            qty = getattr(order_data, "qty", None)
            if qty is None:
                notional = getattr(order_data, "notional", None) or 0.0
                qty = notional / price if price else 0.0
            scripted.filled_qtys = [qty]
        return scripted.poll()

    def close_position(self, symbol):
        self.closed_symbols.append(symbol)
        position = self.positions.get(symbol)
        price = position.current_price if position else self.default_fill_price
        qty = position.qty if position else 0.0
        scripted = self._scripts.pop(symbol, None)
        if scripted is None:
            scripted = {
                "statuses": [OrderStatus.FILLED],
                "filled_qtys": [qty],
                "filled_avg_prices": [price],
                "asset_class": AssetClass.US_EQUITY,
            }
        order_id = self._next_id()
        record = _ScriptedOrder(
            order_id=order_id,
            symbol=symbol,
            asset_class=scripted["asset_class"],
            statuses=list(scripted["statuses"]),
            filled_qtys=list(scripted["filled_qtys"]),
            filled_avg_prices=list(scripted["filled_avg_prices"]),
        )
        self._orders_by_id[order_id] = record
        return record.poll()

    def get_order_by_id(self, order_id, filter=None):  # noqa: A002
        scripted = self._orders_by_id[str(order_id)]
        return scripted.poll()

    def get_option_contracts(self, request):
        return types.SimpleNamespace(option_contracts=list(self.option_contracts), next_page_token=None)


class ClockTogglingClient(FakeTradingClient):
    """
    A `FakeTradingClient` whose market clock (`get_clock().is_open`)
    reports OPEN for the first `open_for_calls` calls, then CLOSED for
    every call after — used to prove that market status is rechecked
    immediately before EACH order submission rather than cached from a
    single check made once for the whole run (e.g. before a multi-minute
    scan). `open_for_calls=0` means every check sees a closed market;
    `open_for_calls=1` means only the very first check (e.g. an initial
    scan-start check, or the first of several order submissions) sees it
    open.
    """

    def __init__(self, *args, open_for_calls: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self._clock_calls = 0
        self._open_for_calls = open_for_calls

    def get_clock(self):
        self._clock_calls += 1
        return types.SimpleNamespace(is_open=self._clock_calls <= self._open_for_calls)


@pytest.fixture
def fake_trading_client():
    return FakeTradingClient()


@pytest.fixture(autouse=True)
def _no_real_redis(monkeypatch):
    """
    `src.utils.cache`'s `@cached` decorator reads `UPSTASH_REDIS_REST_URL`/
    `TOKEN` from the real repo-root `.env` via `load_dotenv()` — without
    this, any test that happens to exercise a `@cached`-wrapped function
    (statement fetchers, price history, the risk-free rate) would try to
    contact the REAL production Upstash Redis instance. Autouse so every
    test gets this for free: `_get_redis_client()` always returns None,
    which the decorator already treats as "caching disabled, call the
    wrapped function directly" — the same safe passthrough it uses when
    Redis is genuinely unconfigured.
    """
    monkeypatch.setattr("src.utils.cache._get_redis_client", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """
    `src.utils.db` reads the real repo-root `.env`'s `DATABASE_URL` (a
    live production Supabase instance) via `load_dotenv()`. Without this,
    any test that reaches `log_trade`/`log_backtest_curve`/`ensure_schema`
    without explicitly mocking it out first would silently write real
    rows to the production `trade_logs`/`backtest_curve` tables — exactly
    what happened before this fixture existed (see the incident noted in
    the remediation task's final report). Autouse so every test gets
    this for free: `psycopg2.connect` is replaced with a stub that always
    raises, so any accidental real-DB attempt fails loudly and immediately
    instead of silently succeeding against production.
    """

    def _blocked_connect(*args, **kwargs):
        raise RuntimeError(
            "Test attempted a real psycopg2.connect() — database calls must be mocked in tests."
        )

    monkeypatch.setattr("src.utils.db.psycopg2.connect", _blocked_connect)


@pytest.fixture(autouse=True)
def _no_real_alpaca_client(monkeypatch):
    """
    Every test in this suite must exercise trading logic against
    `FakeTradingClient` above, never a real `alpaca.trading.client.
    TradingClient`. `FakeTradingClient` is a separate, duck-typed class
    (it never calls the real client's `__init__`), so blocking real
    construction here cannot affect it — but it means any test that
    accidentally constructs (or forgets to fake) a real `TradingClient`
    fails immediately with a clear error instead of silently attempting
    to reach Alpaca's API with whatever credentials are configured.
    """
    from alpaca.trading.client import TradingClient

    def _blocked_init(self, *args, **kwargs):
        raise RuntimeError(
            "Test attempted to construct a real alpaca.trading.client.TradingClient — "
            "use FakeTradingClient from tests/conftest.py instead."
        )

    monkeypatch.setattr(TradingClient, "__init__", _blocked_init)


@pytest.fixture(autouse=True)
def _no_real_yfinance(monkeypatch):
    """
    Blocks yfinance at its own API surface rather than relying only on the
    module-level `socket.socket` patch above — recent yfinance versions
    fetch over `curl_cffi`, a compiled libcurl backend that never goes
    through Python's `socket` module, so the transport-level patch alone
    does not stop it (this is exactly how one real Yahoo Finance request
    for AAPL slipped through and got live data mid-remediation, before
    this fixture existed).

    `src.data_ingestion.fetch_financials.get_ticker_object` is this
    repository's only `yf.Ticker(...)` construction site, so blocking the
    class's own `__init__` blocks every downstream `.info`/`.history()`/
    `.income_stmt`/etc. access transitively — none of them are reachable
    without a constructed `Ticker` first. `yfinance.download` is blocked
    the same way even though nothing in `src/` currently calls it, per
    the explicit remediation requirement. Tests needing market-data-shaped
    values pass a synthetic ticker/history object directly (see the module
    docstring: "financial math tests ... use plain synthetic pandas
    fixtures instead of live yfinance data") rather than overriding this
    fixture.

    `curl_cffi.requests.Session.request/.get/.post` are additionally
    blocked as defense in depth, in case yfinance (or anything else) ever
    reaches for a session directly without going through `yf.Ticker`.
    """
    import yfinance as yf
    from curl_cffi import requests as curl_cffi_requests

    def _blocked_ticker_init(self, *args, **kwargs):
        raise RuntimeError(
            f"Test attempted to construct a real yfinance.Ticker. {EXTERNAL_ACCESS_BLOCKED_MESSAGE}"
        )

    def _blocked_download(*args, **kwargs):
        raise RuntimeError(f"Test attempted yfinance.download(). {EXTERNAL_ACCESS_BLOCKED_MESSAGE}")

    def _blocked_curl_cffi_request(self, *args, **kwargs):
        raise RuntimeError(
            f"Test attempted a curl_cffi.requests.Session request. {EXTERNAL_ACCESS_BLOCKED_MESSAGE}"
        )

    monkeypatch.setattr(yf.Ticker, "__init__", _blocked_ticker_init)
    monkeypatch.setattr(yf, "download", _blocked_download)
    monkeypatch.setattr(curl_cffi_requests.Session, "request", _blocked_curl_cffi_request)
    monkeypatch.setattr(curl_cffi_requests.Session, "get", _blocked_curl_cffi_request)
    monkeypatch.setattr(curl_cffi_requests.Session, "post", _blocked_curl_cffi_request)
