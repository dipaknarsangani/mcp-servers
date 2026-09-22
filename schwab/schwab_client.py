import os
import sys
import base64
import secrets
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))
load_dotenv(dotenv_path=_HERE / ".env")

from token_store import TokenStore

SCHWAB_APP_KEY = os.getenv("SCHWAB_APP_KEY", "")
SCHWAB_APP_SECRET = os.getenv("SCHWAB_APP_SECRET", "")
SCHWAB_REDIRECT_URI = os.getenv("SCHWAB_REDIRECT_URI", "https://127.0.0.1:5173/schwab/callback")
SCHWAB_AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_API_BASE = "https://api.schwabapi.com/trader/v1"


def _basic_auth_header() -> str:
    creds = base64.b64encode(f"{SCHWAB_APP_KEY}:{SCHWAB_APP_SECRET}".encode()).decode()
    return f"Basic {creds}"


def _map_asset_type(schwab_type: str) -> str:
    return {
        "EQUITY": "stock",
        "ETF": "etf",
        "FIXED_INCOME": "bond",
        "BOND": "bond",
        "CASH_EQUIVALENT": "cash",
        "MONEY_MARKET_FUND": "cash",
        "MUTUAL_FUND": "etf",
        "CRYPTO": "crypto",
        "OPTION": "option",
        "FUTURE": "other",
        "INDEX": "other",
    }.get(schwab_type, "stock")


def _schwab_asset_type(local_type: str) -> str:
    return {
        "stock": "EQUITY",
        "etf": "EQUITY",
        "bond": "FIXED_INCOME",
        "cash": "CASH_EQUIVALENT",
        "crypto": "CRYPTO",
        "option": "OPTION",
    }.get(local_type, "EQUITY")


def _map_account_type(schwab_type: str) -> str:
    return {
        "MARGIN": "investment",
        "CASH": "investment",
        "CHECKING": "checking",
        "SAVINGS": "savings",
    }.get(schwab_type, "investment")


def _map_transaction_category(schwab_type: str):
    return {
        "TRADE": ("Investments", "Trade"),
        "RECEIVE_AND_DELIVER": ("Investments", "Transfer"),
        "DIVIDEND_OR_INTEREST": ("Income", "Dividend"),
        "ELECTRONIC_FUND": ("Transfer", "ACH"),
        "WIRE_IN": ("Transfer", "Wire"),
        "WIRE_OUT": ("Transfer", "Wire"),
        "JOURNAL": ("Transfer", "Journal"),
    }.get(schwab_type, ("Other", "Schwab"))


def _build_option_symbol(underlying: str, expiration: str, option_type: str, strike: float) -> str:
    dt = datetime.strptime(expiration, "%Y-%m-%d")
    cp = "C" if option_type.upper() == "CALL" else "P"
    strike_int = int(round(strike * 1000))
    padded = f"{underlying.upper():<6}"[:6]
    return f"{padded}{dt.strftime('%y%m%d')}{cp}{strike_int:08d}"


def _extract_order_id(response: httpx.Response) -> Optional[str]:
    location = response.headers.get("Location", "")
    return location.rstrip("/").split("/")[-1] if location else None


async def get_valid_access_token(store: TokenStore) -> str:
    conn = store.get_connection()
    if not conn or not conn.get("is_connected") or not conn.get("refresh_token"):
        raise ValueError("Not connected to Schwab. Call schwab_get_connect_url first.")

    now = datetime.utcnow()

    token_expiry_str = conn.get("token_expiry")
    if token_expiry_str:
        token_expiry = datetime.fromisoformat(token_expiry_str)
        if token_expiry > now + timedelta(seconds=60):
            return conn["access_token"]

    refresh_expiry_str = conn.get("refresh_token_expiry")
    if refresh_expiry_str:
        refresh_expiry = datetime.fromisoformat(refresh_expiry_str)
        if refresh_expiry <= now:
            store.mark_disconnected()
            raise ValueError("Schwab refresh token expired. Reconnect via schwab_get_connect_url.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            SCHWAB_TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": conn["refresh_token"]},
        )

    if resp.status_code != 200:
        store.mark_disconnected()
        raise ValueError(f"Token refresh failed: {resp.status_code} {resp.text}")

    data = resp.json()
    new_expiry = now + timedelta(seconds=data.get("expires_in", 1800))
    new_refresh = data.get("refresh_token")
    new_refresh_expiry = now + timedelta(days=7) if new_refresh else None
    store.update_access_token(data["access_token"], new_expiry, new_refresh, new_refresh_expiry)
    return data["access_token"]


async def schwab_status(store: TokenStore) -> dict:
    conn = store.get_connection()
    if not conn:
        return {
            "is_connected": False,
            "last_synced_at": None,
            "token_expiry": None,
            "refresh_token_expiry": None,
        }
    return {
        "is_connected": bool(conn.get("is_connected")),
        "last_synced_at": conn.get("last_synced_at"),
        "token_expiry": conn.get("token_expiry"),
        "refresh_token_expiry": conn.get("refresh_token_expiry"),
    }


def schwab_get_connect_url(store: TokenStore) -> dict:
    if not SCHWAB_APP_KEY:
        raise ValueError("SCHWAB_APP_KEY is not configured in .env")
    state = secrets.token_urlsafe(32)
    store.save_oauth_state(state)
    params = {
        "client_id": SCHWAB_APP_KEY,
        "redirect_uri": SCHWAB_REDIRECT_URI,
        "response_type": "code",
        "scope": "readonly PlaceTrades",
        "state": state,
    }
    return {"auth_url": f"{SCHWAB_AUTH_URL}?{urlencode(params)}"}


async def schwab_exchange_code(store: TokenStore, code: str, state: Optional[str] = None) -> dict:
    conn = store.get_connection()
    if state and conn and conn.get("oauth_state") and conn["oauth_state"] != state:
        raise ValueError("OAuth state mismatch — possible CSRF. Please reconnect.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            SCHWAB_TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": SCHWAB_REDIRECT_URI,
            },
        )

    if resp.status_code != 200:
        raise ValueError(f"Token exchange failed: {resp.status_code} {resp.text}")

    data = resp.json()
    now = datetime.utcnow()
    store.save_tokens(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_expiry=now + timedelta(seconds=data.get("expires_in", 1800)),
        refresh_token_expiry=now + timedelta(days=7),
        oauth_state=None,
    )
    return {"message": "Connected to Charles Schwab successfully"}


def schwab_disconnect(store: TokenStore) -> dict:
    store.clear_tokens()
    return {"message": "Disconnected from Charles Schwab"}


async def schwab_sync(store: TokenStore) -> dict:
    access_token = await get_valid_access_token(store)
    headers = {"Authorization": f"Bearer {access_token}"}

    accounts_synced = investments_synced = transactions_synced = transactions_skipped = 0
    errors = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{SCHWAB_API_BASE}/accounts/accountNumbers", headers=headers)
        if resp.status_code != 200:
            raise ValueError(f"Failed to fetch account numbers: {resp.status_code} {resp.text}")

        for acct_ref in resp.json():
            acct_hash = acct_ref.get("hashValue")
            try:
                r2 = await client.get(
                    f"{SCHWAB_API_BASE}/accounts/{acct_hash}?fields=positions", headers=headers
                )
                if r2.status_code != 200:
                    errors.append(f"Account {acct_hash}: {r2.status_code}")
                    continue

                sec = r2.json().get("securitiesAccount", {})
                balances = sec.get("currentBalances", {})
                acct_number = acct_ref.get("accountNumber", acct_hash[-4:])
                acct_type = _map_account_type(sec.get("type", "CASH"))
                total_balance = balances.get("liquidationValue", balances.get("totalValue", 0.0))
                cash_balance = balances.get("cashBalance", balances.get("moneyMarketFund", 0.0))
                acct_name = f"Schwab {acct_type.title()} ...{acct_number[-4:]}"

                acct_id = store.upsert_account(
                    name=acct_name,
                    account_type=acct_type,
                    balance=total_balance,
                    cash_balance=cash_balance,
                    institution="Charles Schwab",
                    external_id=f"schwab_acct_{acct_hash}",
                )
                accounts_synced += 1

                for pos in sec.get("positions", []):
                    inst = pos.get("instrument", {})
                    symbol = inst.get("symbol", "UNKNOWN")
                    local_type = _map_asset_type(inst.get("assetType", "EQUITY"))
                    for qty, leg in [
                        (pos.get("longQuantity", 0.0), "long"),
                        (pos.get("shortQuantity", 0.0), "short"),
                    ]:
                        if qty <= 0:
                            continue
                        mkt_val = pos.get("marketValue", 0.0)
                        avg_price = pos.get("averagePrice", 0.0)
                        current_price = (mkt_val / qty) if qty else avg_price
                        store.upsert_investment(
                            account_id=acct_id,
                            symbol=symbol,
                            name=inst.get("description", symbol),
                            shares=qty,
                            cost_basis=avg_price * qty,
                            current_price=current_price,
                            asset_type=local_type,
                            external_id=f"schwab_pos_{acct_hash}_{symbol}_{leg}",
                        )
                        investments_synced += 1

                today = date.today()
                windows = [
                    (today - timedelta(days=90), today - timedelta(days=45)),
                    (today - timedelta(days=45), today),
                ]
                txn_types = "TRADE,RECEIVE_AND_DELIVER,DIVIDEND_OR_INTEREST,ELECTRONIC_FUND,OTHER"
                for start, end in windows:
                    tr = await client.get(
                        f"{SCHWAB_API_BASE}/accounts/{acct_hash}/transactions",
                        headers=headers,
                        params={
                            "startDate": start.isoformat(),
                            "endDate": end.isoformat(),
                            "types": txn_types,
                        },
                    )
                    if tr.status_code != 200:
                        errors.append(f"Transactions {acct_hash}: {tr.status_code}")
                        continue
                    for txn in tr.json():
                        cat, subcat = _map_transaction_category(txn.get("type", "OTHER"))
                        inserted = store.insert_transaction(
                            account_id=acct_id,
                            date=txn.get("tradeDate", today.isoformat())[:10],
                            description=txn.get("description", "Schwab Transaction"),
                            amount=txn.get("netAmount", 0.0),
                            category=cat,
                            subcategory=subcat,
                            external_id=f"schwab_txn_{txn.get('activityId', '')}",
                        )
                        if inserted:
                            transactions_synced += 1
                        else:
                            transactions_skipped += 1

            except Exception as e:
                errors.append(f"Error syncing {acct_hash}: {e}")

    store.update_last_synced()
    return {
        "accounts_synced": accounts_synced,
        "investments_synced": investments_synced,
        "transactions_synced": transactions_synced,
        "transactions_skipped": transactions_skipped,
        "errors": errors,
    }


async def schwab_get_positions(store: TokenStore, account_external_id: str) -> dict:
    if not account_external_id.startswith("schwab_acct_"):
        raise ValueError("account_external_id must start with 'schwab_acct_'")
    acct_hash = account_external_id.replace("schwab_acct_", "")
    access_token = await get_valid_access_token(store)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{SCHWAB_API_BASE}/accounts/{acct_hash}?fields=positions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if resp.status_code != 200:
        raise ValueError(f"Failed to fetch positions: {resp.status_code} {resp.text}")

    sec = resp.json().get("securitiesAccount", {})
    balances = sec.get("currentBalances", {})
    positions = []
    for pos in sec.get("positions", []):
        inst = pos.get("instrument", {})
        qty = pos.get("longQuantity", 0.0) - pos.get("shortQuantity", 0.0)
        mkt_val = pos.get("marketValue", 0.0)
        avg_price = pos.get("averagePrice", 0.0)
        current_price = (mkt_val / abs(qty)) if qty else avg_price
        cost_basis = avg_price * abs(qty)
        gain_loss = mkt_val - cost_basis
        positions.append({
            "symbol": inst.get("symbol", ""),
            "description": inst.get("description", inst.get("symbol", "")),
            "asset_type": _map_asset_type(inst.get("assetType", "EQUITY")),
            "shares": qty,
            "cost_basis": cost_basis,
            "current_price": current_price,
            "market_value": mkt_val,
            "gain_loss": gain_loss,
            "gain_loss_pct": (gain_loss / cost_basis * 100) if cost_basis else 0.0,
        })

    return {
        "account_external_id": account_external_id,
        "positions": positions,
        "cash_balance": balances.get("cashBalance", 0.0),
        "total_value": balances.get("liquidationValue", 0.0),
        "as_of": datetime.utcnow().isoformat(),
    }


async def schwab_place_stop_limit(
    store: TokenStore,
    account_external_id: str,
    symbol: str,
    asset_type: str,
    shares: float,
    current_price: float,
    instruction: str = "SELL",
    stop_price: Optional[float] = None,
    limit_price: Optional[float] = None,
    user_id: int = 1,
) -> dict:
    acct_hash = account_external_id.replace("schwab_acct_", "")
    access_token = await get_valid_access_token(store)
    settings = store.get_stop_limit_settings(user_id)

    at = asset_type.lower()
    if stop_price is None:
        pct = settings["etf_stop_pct"] if at == "etf" else (settings["option_stop_pct"] if at == "option" else settings["equity_stop_pct"])
        stop_price = round(current_price * (1 - pct / 100), 2)
    else:
        stop_price = round(stop_price, 2)

    if limit_price is None:
        pct = settings["etf_limit_pct"] if at == "etf" else (settings["option_limit_pct"] if at == "option" else settings["equity_limit_pct"])
        limit_price = round(current_price * (1 - pct / 100), 2)
    else:
        limit_price = round(limit_price, 2)

    payload = {
        "orderType": "STOP_LIMIT",
        "session": "NORMAL",
        "duration": "GOOD_TILL_CANCEL",
        "orderStrategyType": "SINGLE",
        "stopPrice": str(stop_price),
        "price": str(limit_price),
        "orderLegCollection": [{
            "instruction": instruction.upper(),
            "quantity": shares,
            "instrument": {"symbol": symbol.upper(), "assetType": _schwab_asset_type(at)},
        }],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{SCHWAB_API_BASE}/accounts/{acct_hash}/orders",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )

    if resp.status_code not in (200, 201):
        raise ValueError(f"Order failed: {resp.status_code} {resp.text}")

    return {
        "message": "Stop-limit order placed",
        "stop_price": stop_price,
        "limit_price": limit_price,
        "order_id": _extract_order_id(resp),
    }


async def schwab_place_equity_order(
    store: TokenStore,
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
    acct_hash = account_external_id.replace("schwab_acct_", "")
    access_token = await get_valid_access_token(store)

    payload = {
        "orderType": order_type.upper(),
        "session": "NORMAL",
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": instruction.upper(),
            "quantity": quantity,
            "instrument": {"symbol": symbol.upper(), "assetType": asset_type.upper()},
        }],
    }
    ot = order_type.upper()
    if ot in ("LIMIT", "STOP_LIMIT") and price is not None:
        payload["price"] = str(round(price, 2))
    if ot == "STOP_LIMIT" and stop_price is not None:
        payload["stopPrice"] = str(round(stop_price, 2))

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{SCHWAB_API_BASE}/accounts/{acct_hash}/orders",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )

    if resp.status_code not in (200, 201):
        raise ValueError(f"Order failed: {resp.status_code} {resp.text}")

    return {
        "message": f"{order_type} {instruction} order placed for {quantity} {symbol}",
        "order_id": _extract_order_id(resp),
    }


async def schwab_place_option_order(
    store: TokenStore,
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
    acct_hash = account_external_id.replace("schwab_acct_", "")
    access_token = await get_valid_access_token(store)
    option_symbol = _build_option_symbol(underlying, expiration, option_type, strike)

    payload = {
        "orderType": order_type.upper(),
        "session": "NORMAL",
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": instruction.upper(),
            "quantity": contracts,
            "instrument": {"symbol": option_symbol, "assetType": "OPTION"},
        }],
    }
    if order_type.upper() == "LIMIT" and price is not None:
        payload["price"] = str(round(price, 2))

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{SCHWAB_API_BASE}/accounts/{acct_hash}/orders",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )

    if resp.status_code not in (200, 201):
        raise ValueError(f"Order failed: {resp.status_code} {resp.text}")

    return {
        "message": f"{instruction} {contracts} contract(s) of {option_symbol}",
        "option_symbol": option_symbol,
        "order_id": _extract_order_id(resp),
    }
