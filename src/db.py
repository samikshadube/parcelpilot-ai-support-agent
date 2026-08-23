"""Database helpers, schema initialization, and snapshot metadata handling."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.config import DB_PATH


def get_db_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get a SQLite connection with dict-like row access."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize database tables for accounts, orders, tickets, meta, and actions."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            account_name TEXT NOT NULL,
            plan TEXT NOT NULL,
            status TEXT NOT NULL,
            csm TEXT,
            contract_file TEXT,
            premium_support BOOLEAN NOT NULL DEFAULT 0,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(account_id),
            carrier TEXT NOT NULL,
            status TEXT NOT NULL,
            booked_at TEXT NOT NULL,
            pickup_window_start TEXT,
            pickup_window_end TEXT,
            pickup_actual_at TEXT,
            shipment_fee_inr REAL NOT NULL,
            carrier_fault BOOLEAN NOT NULL DEFAULT 0,
            customer_fault BOOLEAN NOT NULL DEFAULT 0,
            cancellation_requested_at TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(account_id),
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            channel TEXT NOT NULL,
            assigned_to TEXT,
            last_customer_message_at TEXT,
            historical_resolution TEXT
        );

        CREATE TABLE IF NOT EXISTS staged_actions (
            action_id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            account_id TEXT,
            target_entity_type TEXT NOT NULL,
            target_entity_id TEXT NOT NULL,
            description TEXT NOT NULL,
            parameters_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending_confirmation',
            staged_by_role TEXT NOT NULL,
            staged_by_user TEXT,
            staged_at TEXT NOT NULL,
            executed_at TEXT,
            execution_result TEXT,
            requires_manager_approval BOOLEAN NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id);
        CREATE INDEX IF NOT EXISTS idx_tickets_account ON tickets(account_id);
        CREATE INDEX IF NOT EXISTS idx_actions_account ON staged_actions(account_id);
    """)

    conn.commit()
    conn.close()


def get_meta_value(key: str, default: Optional[str] = None, db_path: Path = DB_PATH) -> Optional[str]:
    """Retrieve metadata key from SQLite."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["value"]
    return default


def get_snapshot_time(db_path: Path = DB_PATH) -> str:
    """Retrieve reference dataset snapshot time string."""
    return get_meta_value("dataset_snapshot", "2026-08-16 11:00 Asia/Kolkata", db_path)


def get_snapshot_datetime(db_path: Path = DB_PATH) -> datetime:
    """Get parsed datetime object of the snapshot time for relative SLA / time calculations.

    Never uses datetime.now() for business logic.
    """
    raw = get_snapshot_time(db_path)
    # Parse e.g. "2026-08-16 11:00 Asia/Kolkata" or "2026-08-16 11:00"
    date_part = raw.split(" Asia/")[0].strip()
    try:
        return datetime.strptime(date_part, "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
