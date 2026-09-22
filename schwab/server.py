import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Always resolve modules relative to this file, regardless of cwd
_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv(dotenv_path=_HERE / ".env")

from token_store import TokenStore
import schwab_client as sc

_default_db = (
    Path(__file__).parent.parent.parent
    / "PersonalFinanceDashboard"
    / "FinanceView"
    / "backend"
    / "financeview.db"
)
DB_PATH = os.getenv("DB_PATH", str(_default_db))
store = TokenStore(DB_PATH)

server = MCPServer("schwab-mcp")


@server.tool()
async def schwab_status() -> dict:
    """Check Schwab connection status, token expiry, and last sync time."""
    return await sc.schwab_status(store)


@server.tool()
def schwab_get_connect_url() -> dict:
    """Generate the Schwab OAuth authorization URL.

    Open the returned auth_url in a browser. After approving, copy the 'code'
    and 'state' query parameters from the redirect URL and pass them to
    schwab_exchange_code.
    """
    return sc.schwab_get_connect_url(store)


@server.tool()
async def schwab_exchange_code(code: str, state: Optional[str] = None) -> dict:
    """Complete Schwab OAuth by exchanging the authorization code.

    Args:
        code: The 'code' query parameter from the Schwab redirect URL.
        state: The 'state' query parameter from the redirect URL (for CSRF check).
    """
    return await sc.schwab_exchange_code(store, code, state)


@server.tool()
def schwab_disconnect() -> dict:
    """Disconnect from Schwab and clear all stored tokens."""
    return sc.schwab_disconnect(store)


@server.tool()
async def schwab_sync() -> dict:
    """Sync all Schwab accounts, positions, and transactions (last 90 days) into the local database."""
    return await sc.schwab_sync(store)


@server.tool()
def schwab_get_accounts() -> dict:
    """List all synced Schwab accounts with balances and external_id values for use in other tools."""
    return {"accounts": store.get_accounts()}


@server.tool()
async def schwab_get_positions(account_external_id: str) -> dict:
    """Get live real-time positions for a specific Schwab account.

    Args:
        account_external_id: Account external_id, e.g. 'schwab_acct_ABC123'.
            Get this value from schwab_get_accounts.
    """
    return await sc.schwab_get_positions(store, account_external_id)


@server.tool()
async def schwab_place_stop_limit(
    account_external_id: str,
    symbol: str,
    asset_type: str,
    shares: float,
    current_price: float,
    instruction: str = "SELL",
    stop_price: Optional[float] = None,
    limit_price: Optional[float] = None,
) -> dict:
    """Place a stop-limit sell order for a position.

    Uses default stop/limit percentages from settings unless stop_price and
    limit_price are explicitly provided.

    Args:
        account_external_id: Account external_id from schwab_get_accounts.
        symbol: Ticker symbol, e.g. 'AAPL'.
        asset_type: 'stock', 'etf', or 'option'.
        shares: Number of shares to sell.
        current_price: Current market price per share.
        instruction: 'SELL' for stocks/ETFs or 'SELL_TO_CLOSE' for options.
        stop_price: Override: exact stop trigger price (skips settings calculation).
        limit_price: Override: exact limit execution price (skips settings calculation).
    """
    return await sc.schwab_place_stop_limit(
        store,
        account_external_id=account_external_id,
        symbol=symbol,
        asset_type=asset_type,
        shares=shares,
        current_price=current_price,
        instruction=instruction,
        stop_price=stop_price,
        limit_price=limit_price,
    )


@server.tool()
async def schwab_place_equity_order(
    account_external_id: str,
    symbol: str,
    instruction: str,
    quantity: float,
    order_type: str = "MARKET",
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    duration: str = "GOOD_TILL_CANCEL",
    asset_type: str = "EQUITY",
) -> dict:
    """Place a market, limit, or stop-limit order for a stock or ETF.

    Args:
        account_external_id: Account external_id from schwab_get_accounts.
        symbol: Ticker symbol, e.g. 'AAPL'.
        instruction: 'BUY' or 'SELL'.
        quantity: Number of shares.
        order_type: 'MARKET', 'LIMIT', or 'STOP_LIMIT'.
        price: Limit price — required for LIMIT and STOP_LIMIT orders.
        stop_price: Stop trigger price — required for STOP_LIMIT orders.
        duration: 'GOOD_TILL_CANCEL' (default) or 'DAY'.
        asset_type: 'EQUITY' (default) or other Schwab asset type string.
    """
    return await sc.schwab_place_equity_order(
        store,
        account_external_id=account_external_id,
        symbol=symbol,
        instruction=instruction,
        quantity=quantity,
        order_type=order_type,
        price=price,
        stop_price=stop_price,
        duration=duration,
        asset_type=asset_type,
    )


@server.tool()
async def schwab_place_option_order(
    account_external_id: str,
    underlying: str,
    option_type: str,
    expiration: str,
    strike: float,
    instruction: str,
    contracts: int = 1,
    order_type: str = "LIMIT",
    price: Optional[float] = None,
    duration: str = "GOOD_TILL_CANCEL",
) -> dict:
    """Place an option order. The OCC symbol is automatically generated.

    Args:
        account_external_id: Account external_id from schwab_get_accounts.
        underlying: Underlying ticker, e.g. 'AAPL'.
        option_type: 'CALL' or 'PUT'.
        expiration: Expiration date in YYYY-MM-DD format.
        strike: Strike price.
        instruction: 'SELL_TO_OPEN', 'BUY_TO_OPEN', 'SELL_TO_CLOSE', or 'BUY_TO_CLOSE'.
        contracts: Number of contracts (default 1; each contract = 100 shares).
        order_type: 'LIMIT' (default) or 'MARKET'.
        price: Premium per contract — required for LIMIT orders.
        duration: 'GOOD_TILL_CANCEL' (default) or 'DAY'.
    """
    return await sc.schwab_place_option_order(
        store,
        account_external_id=account_external_id,
        underlying=underlying,
        option_type=option_type,
        expiration=expiration,
        strike=strike,
        instruction=instruction,
        contracts=contracts,
        order_type=order_type,
        price=price,
        duration=duration,
    )


@server.tool()
def schwab_get_stop_limit_settings() -> dict:
    """Get default stop % and limit % thresholds per asset class (equity, ETF, option)."""
    return store.get_stop_limit_settings()


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
