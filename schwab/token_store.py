import sqlite3
from datetime import datetime
from typing import Optional


class TokenStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def get_connection(self) -> Optional[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM schwab_connection LIMIT 1").fetchone()
            return dict(row) if row else None

    def save_oauth_state(self, state: str):
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM schwab_connection LIMIT 1").fetchone()
            if row:
                conn.execute(
                    "UPDATE schwab_connection SET oauth_state=?, is_connected=0 WHERE id=?",
                    (state, row[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO schwab_connection (oauth_state, is_connected) VALUES (?, 0)",
                    (state,),
                )
            conn.commit()

    def save_tokens(self, access_token, refresh_token, token_expiry, refresh_token_expiry, oauth_state=None):
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM schwab_connection LIMIT 1").fetchone()
            if row:
                conn.execute(
                    """UPDATE schwab_connection
                       SET access_token=?, refresh_token=?, token_expiry=?,
                           refresh_token_expiry=?, is_connected=1, oauth_state=?
                       WHERE id=?""",
                    (
                        access_token,
                        refresh_token,
                        token_expiry.isoformat(),
                        refresh_token_expiry.isoformat(),
                        oauth_state,
                        row[0],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO schwab_connection
                       (access_token, refresh_token, token_expiry, refresh_token_expiry, is_connected, oauth_state)
                       VALUES (?, ?, ?, ?, 1, ?)""",
                    (
                        access_token,
                        refresh_token,
                        token_expiry.isoformat(),
                        refresh_token_expiry.isoformat(),
                        oauth_state,
                    ),
                )
            conn.commit()

    def update_access_token(self, access_token, token_expiry, refresh_token=None, refresh_token_expiry=None):
        with self._connect() as conn:
            if refresh_token:
                conn.execute(
                    """UPDATE schwab_connection
                       SET access_token=?, token_expiry=?, refresh_token=?, refresh_token_expiry=?""",
                    (access_token, token_expiry.isoformat(), refresh_token, refresh_token_expiry.isoformat()),
                )
            else:
                conn.execute(
                    "UPDATE schwab_connection SET access_token=?, token_expiry=?",
                    (access_token, token_expiry.isoformat()),
                )
            conn.commit()

    def clear_tokens(self):
        with self._connect() as conn:
            conn.execute(
                """UPDATE schwab_connection
                   SET access_token=NULL, refresh_token=NULL, token_expiry=NULL,
                       refresh_token_expiry=NULL, oauth_state=NULL, is_connected=0"""
            )
            conn.commit()

    def mark_disconnected(self):
        with self._connect() as conn:
            conn.execute("UPDATE schwab_connection SET is_connected=0")
            conn.commit()

    def update_last_synced(self):
        with self._connect() as conn:
            conn.execute(
                "UPDATE schwab_connection SET last_synced_at=?",
                (datetime.utcnow().isoformat(),),
            )
            conn.commit()

    def get_stop_limit_settings(self, user_id: int = 1) -> dict:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM stop_limit_settings WHERE user_id=?", (user_id,)
            ).fetchone()
            if row is None:
                return {
                    "equity_stop_pct": 5.0,
                    "equity_limit_pct": 4.5,
                    "etf_stop_pct": 5.0,
                    "etf_limit_pct": 4.5,
                    "option_stop_pct": 10.0,
                    "option_limit_pct": 9.0,
                }
            return dict(row)

    def get_accounts(self) -> list:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, name, type, balance, cash_balance, institution, external_id
                   FROM accounts WHERE institution='Charles Schwab'"""
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_account(self, name, account_type, balance, cash_balance, institution, external_id, user_id=1) -> int:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM accounts WHERE external_id=?", (external_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE accounts SET balance=?, cash_balance=? WHERE external_id=?",
                    (balance, cash_balance, external_id),
                )
                conn.commit()
                return existing[0]
            cur = conn.execute(
                """INSERT INTO accounts (name, type, balance, cash_balance, institution, external_id, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, account_type, balance, cash_balance, institution, external_id, user_id),
            )
            conn.commit()
            return cur.lastrowid

    def upsert_investment(self, account_id, symbol, name, shares, cost_basis, current_price, asset_type, external_id):
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM investments WHERE external_id=?", (external_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE investments SET shares=?, current_price=?, cost_basis=? WHERE external_id=?",
                    (shares, current_price, cost_basis, external_id),
                )
            else:
                try:
                    conn.execute(
                        """INSERT INTO investments
                           (account_id, symbol, name, shares, cost_basis, current_price, asset_type, external_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (account_id, symbol, name, shares, cost_basis, current_price, asset_type, external_id),
                    )
                except sqlite3.IntegrityError:
                    pass
            conn.commit()

    def insert_transaction(self, account_id, date, description, amount, category, subcategory, external_id) -> bool:
        with self._connect() as conn:
            if conn.execute(
                "SELECT id FROM transactions WHERE external_id=?", (external_id,)
            ).fetchone():
                return False
            try:
                conn.execute(
                    """INSERT INTO transactions
                       (account_id, date, description, amount, category, subcategory, external_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (account_id, date, description, amount, category, subcategory, external_id),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False
