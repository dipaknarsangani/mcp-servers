import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

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

server = Server("schwab-mcp")


@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="schwab_status",
            description="Check Schwab connection status, token expiry, and last sync time.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="schwab_get_connect_url",
            description=(
                "Generate the Schwab OAuth authorization URL. "
                "Open it in a browser, approve access, then copy the code and state "
                "from the redirect URL and pass them to schwab_exchange_code."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="schwab_exchange_code",
            description="Complete Schwab OAuth by exchanging the authorization code from the redirect URL.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Authorization code from the redirect URL query string"},
                    "state": {"type": "string", "description": "State parameter from the redirect URL query string"},
                },
                "required": ["code"],
            },
        ),
        types.Tool(
            name="schwab_disconnect",
            description="Disconnect from Schwab and clear all stored tokens.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="schwab_sync",
            description="Sync all Schwab accounts, positions, and transactions (last 90 days) into the local database.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="schwab_get_accounts",
            description="List all synced Schwab accounts with balances and their external_id for use in other tools.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="schwab_get_positions",
            description="Get live real-time positions for a specific Schwab account.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account_external_id": {
                        "type": "string",
                        "description": "Account external_id, e.g. 'schwab_acct_ABC123'. Get from schwab_get_accounts.",
                    }
                },
                "required": ["account_external_id"],
            },
        ),
        types.Tool(
            name="schwab_place_stop_limit",
            description=(
                "Place a stop-limit sell order. Uses default stop/limit % from settings "
                "unless stop_price and limit_price are explicitly provided."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "account_external_id": {"type": "string"},
                    "symbol": {"type": "string", "description": "Ticker symbol, e.g. AAPL"},
                    "asset_type": {"type": "string", "enum": ["stock", "etf", "option"]},
                    "shares": {"type": "number", "description": "Number of shares to sell"},
                    "current_price": {"type": "number", "description": "Current market price per share"},
                    "instruction": {"type": "string", "default": "SELL", "description": "SELL or SELL_TO_CLOSE"},
                    "stop_price": {"type": "number", "description": "Override: exact stop trigger price"},
                    "limit_price": {"type": "number", "description": "Override: exact limit execution price"},
                },
                "required": ["account_external_id", "symbol", "asset_type", "shares", "current_price"],
            },
        ),
        types.Tool(
            name="schwab_place_equity_order",
            description="Place a market, limit, or stop-limit order for a stock or ETF.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account_external_id": {"type": "string"},
                    "symbol": {"type": "string"},
                    "instruction": {"type": "string", "enum": ["BUY", "SELL"]},
                    "quantity": {"type": "number"},
                    "order_type": {
                        "type": "string",
                        "enum": ["MARKET", "LIMIT", "STOP_LIMIT"],
                        "default": "MARKET",
                    },
                    "price": {"type": "number", "description": "Limit price (required for LIMIT/STOP_LIMIT)"},
                    "stop_price": {"type": "number", "description": "Stop trigger price (required for STOP_LIMIT)"},
                    "duration": {"type": "string", "default": "GOOD_TILL_CANCEL", "description": "GOOD_TILL_CANCEL or DAY"},
                    "asset_type": {"type": "string", "default": "EQUITY"},
                },
                "required": ["account_external_id", "symbol", "instruction", "quantity"],
            },
        ),
        types.Tool(
            name="schwab_place_option_order",
            description="Place an option order. The OCC symbol is auto-generated from the provided parameters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "account_external_id": {"type": "string"},
                    "underlying": {"type": "string", "description": "Underlying ticker, e.g. AAPL"},
                    "option_type": {"type": "string", "enum": ["CALL", "PUT"]},
                    "expiration": {"type": "string", "description": "Expiration date in YYYY-MM-DD format"},
                    "strike": {"type": "number", "description": "Strike price"},
                    "instruction": {
                        "type": "string",
                        "enum": ["SELL_TO_OPEN", "BUY_TO_OPEN", "SELL_TO_CLOSE", "BUY_TO_CLOSE"],
                    },
                    "contracts": {"type": "integer", "default": 1},
                    "order_type": {"type": "string", "enum": ["LIMIT", "MARKET"], "default": "LIMIT"},
                    "price": {"type": "number", "description": "Premium per contract (required for LIMIT)"},
                    "duration": {"type": "string", "default": "GOOD_TILL_CANCEL"},
                },
                "required": [
                    "account_external_id", "underlying", "option_type",
                    "expiration", "strike", "instruction",
                ],
            },
        ),
        types.Tool(
            name="schwab_get_stop_limit_settings",
            description="Get default stop % and limit % thresholds per asset class (equity, ETF, option).",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "schwab_status":
            result = await sc.schwab_status(store)

        elif name == "schwab_get_connect_url":
            result = sc.schwab_get_connect_url(store)

        elif name == "schwab_exchange_code":
            result = await sc.schwab_exchange_code(
                store, arguments["code"], arguments.get("state")
            )

        elif name == "schwab_disconnect":
            result = sc.schwab_disconnect(store)

        elif name == "schwab_sync":
            result = await sc.schwab_sync(store)

        elif name == "schwab_get_accounts":
            result = {"accounts": store.get_accounts()}

        elif name == "schwab_get_positions":
            result = await sc.schwab_get_positions(store, arguments["account_external_id"])

        elif name == "schwab_place_stop_limit":
            result = await sc.schwab_place_stop_limit(
                store,
                account_external_id=arguments["account_external_id"],
                symbol=arguments["symbol"],
                asset_type=arguments["asset_type"],
                shares=arguments["shares"],
                current_price=arguments["current_price"],
                instruction=arguments.get("instruction", "SELL"),
                stop_price=arguments.get("stop_price"),
                limit_price=arguments.get("limit_price"),
            )

        elif name == "schwab_place_equity_order":
            result = await sc.schwab_place_equity_order(
                store,
                account_external_id=arguments["account_external_id"],
                symbol=arguments["symbol"],
                instruction=arguments["instruction"],
                quantity=arguments["quantity"],
                order_type=arguments.get("order_type", "MARKET"),
                price=arguments.get("price"),
                stop_price=arguments.get("stop_price"),
                duration=arguments.get("duration", "GOOD_TILL_CANCEL"),
                asset_type=arguments.get("asset_type", "EQUITY"),
            )

        elif name == "schwab_place_option_order":
            result = await sc.schwab_place_option_order(
                store,
                account_external_id=arguments["account_external_id"],
                underlying=arguments["underlying"],
                option_type=arguments["option_type"],
                expiration=arguments["expiration"],
                strike=arguments["strike"],
                instruction=arguments["instruction"],
                contracts=arguments.get("contracts", 1),
                order_type=arguments.get("order_type", "LIMIT"),
                price=arguments.get("price"),
                duration=arguments.get("duration", "GOOD_TILL_CANCEL"),
            )

        elif name == "schwab_get_stop_limit_settings":
            result = store.get_stop_limit_settings()

        else:
            raise ValueError(f"Unknown tool: {name}")

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]


if __name__ == "__main__":
    asyncio.run(stdio_server(server))
