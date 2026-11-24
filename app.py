#!/usr/bin/env python3
"""
FamilyFinance — Personal & Family Budget Tracker (Enhanced Edition)

Run locally:
    pip install -r requirements.txt
    python app.py

Default behavior:
- If FF_ADMIN_PASS is not set, a one-time admin password is generated
  and printed to the console on first startup.
- For production, set FF_SECRET_KEY and FF_ADMIN_PASS.

This implementation is API-first. If templates/index.html exists, it is served
at "/". Otherwise, a minimal API landing page is rendered.
"""

from flask import (
    Flask,
    jsonify,
    request,
    make_response,
    session,
    g,
    current_app,
    render_template,
    render_template_string,
    send_file,
)

import sqlite3
import json
import csv
import io
import re
import os
import hashlib
import html
import secrets
import time
import logging
import tempfile
import calendar
import threading
import hmac

from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from functools import wraps
from urllib.parse import quote
from werkzeug.security import generate_password_hash, check_password_hash


BASE_DIR = Path(__file__).parent

BILL_STATUSES = {"Pending", "Partially Paid", "Paid", "Void"}
ASSET_ACCOUNT_TYPES = {"Checking", "Savings", "Cash", "Investment", "Other"}
LIABILITY_ACCOUNT_TYPES = {"Credit Card", "Loan"}
ACCOUNT_TYPES = ASSET_ACCOUNT_TYPES | LIABILITY_ACCOUNT_TYPES

EXPENSE_CATEGORIES = [
    "Groceries & Food",
    "Housing & Rent",
    "Utilities",
    "Transportation",
    "Healthcare & Medical",
    "Education",
    "Entertainment & Fun",
    "Dining Out",
    "Clothing & Shopping",
    "Personal Care",
    "Insurance",
    "Subscriptions",
    "Debt Payments",
    "Gifts & Donations",
    "Pet Care",
    "Travel & Vacation",
    "Kids & Family",
    "Home Maintenance",
    "Savings & Investments",
    "Other",
]

INCOME_CATEGORIES = [
    "Salary / Wages",
    "Freelance / Gig Work",
    "Business Income",
    "Investments & Dividends",
    "Rental Income",
    "Government Benefits",
    "Gift / Inheritance",
    "Tax Refund",
    "Other",
]

BILL_CATEGORIES = [
    "Mortgage / Rent",
    "Electricity",
    "Water & Sewage",
    "Gas / Heating",
    "Internet & Cable",
    "Phone / Mobile",
    "Insurance Premium",
    "Subscription Service",
    "Credit Card Payment",
    "Loan Repayment",
    "School / Tuition Fees",
    "Other",
]

DEFAULT_SETTINGS = {
    "family_name": "Our Family",
    "family_address": "123 Home Street\nCity, State 00000",
    "primary_email": "family@example.com",
    "primary_phone": "",
    "currency_symbol": "$",
    "currency_code": "USD",
    "monthly_income_goal": 5000.0,
    "savings_target_pct": 20.0,
    "bill_prefix": "BILL",
    "family_notes": "Track your family finances with ease!",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Admin' CHECK (role IN ('Admin', 'Editor', 'Viewer')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('expense', 'income', 'bill')),
    is_default INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, type)
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    role TEXT DEFAULT 'Member',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'Checking' CHECK (
        account_type IN ('Checking', 'Savings', 'Cash', 'Investment', 'Credit Card', 'Loan', 'Other')
    ),
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    interest_rate REAL DEFAULT 0.0,
    minimum_payment_cents INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS expense_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    member TEXT DEFAULT '',
    member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS income_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    income_id INTEGER NOT NULL REFERENCES income(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    member TEXT DEFAULT '',
    member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS allowances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'monthly' CHECK (frequency IN ('weekly', 'monthly')),
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    address TEXT DEFAULT '',
    category TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_number TEXT UNIQUE NOT NULL,
    payee_id INTEGER REFERENCES payees(id) ON DELETE SET NULL,
    payee_name TEXT NOT NULL,
    payee_email TEXT DEFAULT '',
    payee_address TEXT DEFAULT '',
    bill_category TEXT NOT NULL DEFAULT 'Other',
    bill_date DATE NOT NULL,
    due_date DATE NOT NULL,
    subtotal_cents INTEGER NOT NULL DEFAULT 0,
    discount_pct REAL NOT NULL DEFAULT 0,
    discount_cents INTEGER NOT NULL DEFAULT 0,
    tax_rate REAL NOT NULL DEFAULT 0,
    tax_cents INTEGER NOT NULL DEFAULT 0,
    total_cents INTEGER NOT NULL DEFAULT 0,
    paid_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (
        status IN ('Pending', 'Partially Paid', 'Paid', 'Void')
    ),
    notes TEXT DEFAULT '',
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMP,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bill_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    item_name TEXT NOT NULL,
    description TEXT DEFAULT '',
    quantity REAL NOT NULL DEFAULT 1,
    unit_price_cents INTEGER NOT NULL DEFAULT 0,
    total_cents INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bill_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL,
    payment_date DATE NOT NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    amount_cents INTEGER NOT NULL,
    expense_date DATE NOT NULL,
    store TEXT DEFAULT '',
    receipt_ref TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    member TEXT DEFAULT '',
    member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    tags TEXT DEFAULT '',
    original_currency TEXT DEFAULT '',
    exchange_rate REAL DEFAULT 0.0,
    original_amount_cents INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS income (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    amount_cents INTEGER NOT NULL,
    income_date DATE NOT NULL,
    source TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    member TEXT DEFAULT '',
    member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    tags TEXT DEFAULT '',
    original_currency TEXT DEFAULT '',
    exchange_rate REAL DEFAULT 0.0,
    original_amount_cents INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    member TEXT DEFAULT '',
    period TEXT NOT NULL DEFAULT 'monthly' CHECK (period IN ('monthly', 'yearly')),
    amount_cents INTEGER NOT NULL,
    notes TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target_cents INTEGER NOT NULL,
    current_cents INTEGER NOT NULL DEFAULT 0,
    target_date DATE,
    allocation_pct REAL NOT NULL DEFAULT 0,
    notes TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recurring_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('expense', 'income', 'bill')),
    payload TEXT NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'monthly' CHECK (frequency IN ('daily', 'weekly', 'monthly', 'yearly')),
    interval_value INTEGER NOT NULL DEFAULT 1 CHECK (interval_value > 0),
    next_run_date DATE NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    last_run_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT DEFAULT '',
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    link TEXT DEFAULT '',
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT DEFAULT '',
    action TEXT NOT NULL,
    entity_type TEXT DEFAULT '',
    entity_id TEXT DEFAULT '',
    details TEXT DEFAULT '',
    ip_address TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bill_sequences (
    prefix TEXT PRIMARY KEY,
    next_number INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'Viewer' CHECK (role IN ('Admin', 'Editor', 'Viewer')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    to_account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    amount_cents INTEGER NOT NULL,
    transfer_date DATE NOT NULL,
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_deleted INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bills_status_due ON bills(status, due_date);
CREATE INDEX IF NOT EXISTS idx_bills_payee ON bills(payee_id);
CREATE INDEX IF NOT EXISTS idx_bills_created ON bills(created_at);
CREATE INDEX IF NOT EXISTS idx_bills_paid_at ON bills(paid_at);
CREATE INDEX IF NOT EXISTS idx_bills_deleted ON bills(is_deleted);
CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items(bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_payments_bill ON bill_payments(bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_payments_date ON bill_payments(payment_date);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);
CREATE INDEX IF NOT EXISTS idx_expenses_deleted ON expenses(is_deleted);
CREATE INDEX IF NOT EXISTS idx_income_date ON income(income_date);
CREATE INDEX IF NOT EXISTS idx_income_category ON income(category);
CREATE INDEX IF NOT EXISTS idx_income_deleted ON income(is_deleted);
CREATE INDEX IF NOT EXISTS idx_categories_type ON categories(type);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read);
CREATE INDEX IF NOT EXISTS idx_recurring_active ON recurring_rules(is_active, next_run_date);
CREATE INDEX IF NOT EXISTS idx_expense_splits_expense ON expense_splits(expense_id);
CREATE INDEX IF NOT EXISTS idx_income_splits_income ON income_splits(income_id);
CREATE INDEX IF NOT EXISTS idx_expenses_member ON expenses(member);
CREATE INDEX IF NOT EXISTS idx_income_member ON income(member);
CREATE INDEX IF NOT EXISTS idx_expenses_account ON expenses(account_id);
CREATE INDEX IF NOT EXISTS idx_income_account ON income(account_id);
CREATE INDEX IF NOT EXISTS idx_bill_payments_account ON bill_payments(account_id);
CREATE INDEX IF NOT EXISTS idx_budgets_active ON budgets(is_active, period, category);
CREATE INDEX IF NOT EXISTS idx_allowances_member ON allowances(member_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_username ON notifications(username, is_read);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_transfers_from ON transfers(from_account_id);
CREATE INDEX IF NOT EXISTS idx_transfers_to ON transfers(to_account_id);
CREATE INDEX IF NOT EXISTS idx_transfers_date ON transfers(transfer_date);
CREATE INDEX IF NOT EXISTS idx_transfers_deleted ON transfers(is_deleted);
"""

MINIMAL_INDEX = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FamilyFinance API</title>
  <style>
    body {
      font-family: system-ui, sans-serif;
      margin: 40px auto;
      max-width: 900px;
      line-height: 1.6;
      padding: 0 16px;
    }
    code {
      background: #f1f5f9;
      padding: 2px 6px;
      border-radius: 6px;
    }
    .card {
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 20px;
      margin: 16px 0;
    }
  </style>
</head>
<body>
  <h1>FamilyFinance Enhanced API</h1>
  <p>The backend is running. If you have a frontend, place it at <code>templates/index.html</code>.</p>

  <div class="card">
    <h2>Authentication</h2>
    <p>POST <code>/api/auth/login</code> with JSON:</p>
    <pre>{ "username": "admin", "password": "your-password" }</pre>
    <p>The response includes a CSRF token. Send it as <code>X-CSRF-Token</code> on all mutating API calls.</p>
  </div>

  <div class="card">
    <h2>Core endpoints</h2>
    <ul>
      <li><code>GET /api/dashboard</code></li>
      <li><code>GET/POST /api/settings</code></li>
      <li><code>GET/POST /api/payees</code></li>
      <li><code>GET/POST /api/bills</code></li>
      <li><code>GET/POST /api/expenses</code></li>
      <li><code>GET/POST /api/income</code></li>
      <li><code>GET/POST /api/budgets</code></li>
      <li><code>GET/POST /api/goals</code></li>
      <li><code>GET/POST /api/accounts</code></li>
      <li><code>GET/POST /api/transfers</code></li>
      <li><code>GET/POST /api/members</code></li>
      <li><code>GET /api/reports</code></li>
      <li><code>GET /api/reports/budget</code></li>
      <li><code>GET /api/reports/cash-flow</code></li>
      <li><code>GET /api/reminders</code></li>
      <li><code>GET /api/admin/backup</code></li>
    </ul>
  </div>
</body>
</html>
"""

VALID_BILL_PAYMENT_FROM = (
    "FROM bill_payments bp "
    "JOIN bills b ON b.id = bp.bill_id AND b.is_deleted = 0 AND b.status != 'Void'"
)


class ValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class RateLimiter:
    """Fixed-window in-memory rate limiter.

    NOTE: state lives in this process only. When running under multiple
    worker processes (e.g. `gunicorn -w 4`), each worker enforces its own
    table, which multiplies the effective limits. Run a single worker, put a
    rate-limiting reverse proxy in front, or swap this class for a shared
    store (Redis, etc.) when scaling out — see README "Rate Limiting".
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._store = {}

    def allow(self, key: str, limit: int, window: int) -> bool:
        if current_app and current_app.config.get("TESTING"):
            return True

        now = time.time()

        with self._lock:
            # Opportunistic cleanup so abandoned keys don't grow without bound.
            if len(self._store) > 4096:
                self._store = {
                    k: v
                    for k, v in self._store.items()
                    if v and max(v) > now - window
                }

            entries = [
                timestamp
                for timestamp in self._store.get(key, [])
                if timestamp > now - window
            ]

            if len(entries) >= limit:
                self._store[key] = entries
                return False

            entries.append(now)
            self._store[key] = entries
            return True


RATE_LIMITER = RateLimiter()


def rate_limit(key: str, limit: int, window: int) -> bool:
    return RATE_LIMITER.allow(key, limit, window)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def rows_to_list(rows):
    return [dict(r) for r in rows]


def row_to_dict(row):
    return dict(row) if row else None


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def parse_iso_date(value, field="date", allow_none=False):
    if value in (None, ""):
        if allow_none:
            return None
        raise ValidationError(f"{field} is required")

    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValidationError(f"{field} must be in YYYY-MM-DD format")


# Common export date formats accepted by the CSV importer, most-specific
# first. ISO wins so unambiguous input is never misread.
FLEXIBLE_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%m-%d-%Y",
)


def parse_flexible_date(value, field="date"):
    """Accept the small set of date formats real bank exports actually use.

    ISO `YYYY-MM-DD` is tried first; US `MM/DD/YYYY` before international
    `DD/MM/YYYY`, matching the dominant convention when both could parse.
    """
    if value in (None, ""):
        raise ValidationError(f"{field} is required")

    text = str(value).strip()

    for fmt in FLEXIBLE_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue

    raise ValidationError(
        f"{field} must be a valid date (YYYY-MM-DD or MM/DD/YYYY)"
    )


def parse_number(value, field="value", minimum=None, maximum=None):
    try:
        dec = Decimal(str(value if value is not None else 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{field} is invalid")

    if minimum is not None and dec < Decimal(str(minimum)):
        raise ValidationError(f"{field} must be at least {minimum}")

    if maximum is not None and dec > Decimal(str(maximum)):
        raise ValidationError(f"{field} must be at most {maximum}")

    return float(dec)


def parse_cents(value, field="amount", allow_zero=False, allow_negative=False):
    if value is None:
        value = 0

    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{field} is invalid")

    if dec.is_nan() or dec.is_infinite():
        raise ValidationError(f"{field} is invalid")

    dec = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cents = int(dec * 100)

    if not allow_negative and cents < 0:
        raise ValidationError(f"{field} cannot be negative")

    if not allow_zero and cents <= 0:
        raise ValidationError(f"{field} must be greater than 0")

    return cents


def parse_amount_from_data(data, field="amount", default_cents=None, allow_zero=False):
    cents_field = f"{field}_cents"

    if cents_field in data:
        try:
            cents = int(data[cents_field])
        except (TypeError, ValueError):
            raise ValidationError(f"{cents_field} is invalid")

        if cents < 0:
            raise ValidationError(f"{field} cannot be negative")

        if not allow_zero and cents <= 0:
            raise ValidationError(f"{field} must be greater than 0")

        return cents

    if field in data:
        return parse_cents(data[field], field=field, allow_zero=allow_zero)

    if default_cents is not None:
        return default_cents

    raise ValidationError(f"{field} is required")


def parse_percent(value, field="percent", minimum=0.0, maximum=100.0):
    try:
        dec = Decimal(str(value if value is not None else 0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{field} is invalid")

    if dec < Decimal(str(minimum)) or dec > Decimal(str(maximum)):
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")

    return float(dec)


def parse_quantity(value, field="quantity"):
    try:
        dec = Decimal(str(value if value not in (None, "") else 1)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{field} is invalid")

    if dec <= 0:
        raise ValidationError(f"{field} must be greater than 0")

    return dec


def parse_int(value, field="value", default=None, minimum=None):
    if value is None or value == "":
        if default is not None:
            return default
        raise ValidationError(f"{field} is required")

    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} is invalid")

    if minimum is not None and result < minimum:
        raise ValidationError(f"{field} must be at least {minimum}")

    return result


def cents_to_float(cents):
    return round(int(cents) / 100, 2)


def fmt_cents(cents):
    return f"{Decimal(int(cents)) / Decimal(100):,.2f}"


def cents_to_plain(cents):
    return f"{Decimal(int(cents)) / Decimal(100):.2f}"


def escape_like(value):
    """Escape SQL LIKE wildcards so user input matches literally."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def sanitize_csv_cell(value):
    if value is None:
        return ""

    text = str(value)

    if text and text[0] in {"=", "+", "-", "@", "\t"}:
        return "'" + text

    return text


def csv_response(filename, rows, headers):
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)

    writer.writerow([
        sanitize_csv_cell(f"FamilyFinance Export — {filename}"),
        "",
        "",
        "",
        sanitize_csv_cell(f"Generated: {date.today().isoformat()}"),
    ])
    writer.writerow([])
    writer.writerow([sanitize_csv_cell(h) for h in headers])

    for row in rows:
        writer.writerow([sanitize_csv_cell(v) for v in row])

    content = "\ufeff" + out.getvalue()

    resp = make_response(content)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = (
        f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
    )

    return resp


def get_pagination():
    try:
        limit = min(max(int(request.args.get("limit", 25)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        raise ValidationError("Invalid pagination parameters")

    return limit, offset


def json_payload():
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------------------
# Flask / DB plumbing
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=5000")

    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def create_app(test_config=None):
    app = Flask(__name__)

    config = {
        "SECRET_KEY": os.getenv("FF_SECRET_KEY", ""),
        "DATABASE": os.getenv("FF_DB_PATH", str(BASE_DIR / "familyfinance.db")),
        # Applies to every request body, including CSV imports and database
        # restores. A multi-year family database can comfortably exceed the
        # old hardcoded 5 MB, so this is configurable and defaults higher.
        "MAX_CONTENT_LENGTH": int(os.getenv("FF_MAX_CONTENT_LENGTH_MB", "50")) * 1024 * 1024,
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=int(os.getenv("FF_SESSION_HOURS", "8"))),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": os.getenv("FF_SECURE_COOKIES", "0") == "1",
        "ADMIN_USER": os.getenv("FF_ADMIN_USER", "admin"),
        "ADMIN_PASSWORD": os.getenv("FF_ADMIN_PASS", ""),
    }

    if test_config:
        config.update(test_config)

    app.config.update(config)

    db_path = Path(app.config["DATABASE"])
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)

    if not app.config["SECRET_KEY"]:
        if app.debug or app.testing or os.getenv("FF_ALLOW_DEV_SECRET", "1") == "1":
            app.config["SECRET_KEY"] = secrets.token_hex(32)
            app.logger.warning(
                "FF_SECRET_KEY was not set. A development secret was generated. "
                "Set FF_SECRET_KEY in production."
            )
        else:
            raise RuntimeError("FF_SECRET_KEY must be set in production")

    logging.basicConfig(level=logging.INFO)

    @app.teardown_appcontext
    def teardown_db_exception(exception=None):
        close_db(exception)

    @app.errorhandler(ValidationError)
    def handle_validation_error(e):
        return jsonify({"error": e.message}), 400

    @app.errorhandler(400)
    def handle_400(e):
        return jsonify({"error": "Bad request"}), 400

    @app.errorhandler(401)
    def handle_401(e):
        return jsonify({"error": "Authentication required"}), 401

    @app.errorhandler(403)
    def handle_403(e):
        return jsonify({"error": "Forbidden"}), 403

    @app.errorhandler(404)
    def handle_404(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def handle_405(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(429)
    def handle_429(e):
        return jsonify({"error": "Too many requests"}), 429

    @app.errorhandler(413)
    def handle_413(e):
        limit_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
        return jsonify({
            "error": f"Request body too large (limit: {limit_mb} MB). "
            "Adjust FF_MAX_CONTENT_LENGTH_MB if you need to upload larger backups."
        }), 413

    @app.errorhandler(500)
    def handle_500(e):
        app.logger.exception("Internal server error")
        return jsonify({"error": "Internal server error"}), 500

    @app.after_request
    def set_security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # The SPA uses inline handlers/styles and loads Google Fonts, so
        # 'unsafe-inline' for scripts/styles plus explicit font/style origins
        # is the pragmatic first-step policy.
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "img-src 'self' data:",
        )

        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")

        if not app.debug and os.getenv("FF_HSTS", "0") == "1":
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains"
            )

        return resp

    @app.before_request
    def global_api_guard():
        if not request.path.startswith("/api/"):
            return None

        ip = request.remote_addr or "unknown"

        if not RATE_LIMITER.allow(f"api:{ip}", limit=900, window=60):
            return jsonify({"error": "Too many requests"}), 429

        public_paths = {
            "/api/auth/login",
            "/api/auth/logout",
            "/healthz",
            "/readyz",
        }

        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path not in public_paths:
            user = get_current_user()

            if not user:
                return jsonify({"error": "Authentication required"}), 401

            # Requests authenticated with a personal access token carry no
            # session cookie, so CSRF (a cookie-authentication defense) does
            # not apply to them.
            if not g.get("auth_via_bearer", False):
                token = request.headers.get("X-CSRF-Token", "")

                if not token:
                    payload = json_payload()
                    token = payload.get("_csrf", "")

                expected = session.get("_csrf", "")

                if (
                    not token
                    or not expected
                    or not hmac.compare_digest(str(token), str(expected))
                ):
                    return jsonify({"error": "CSRF validation failed"}), 403

        return None

    with app.app_context():
        init_db()

    register_routes(app)

    return app


# ---------------------------------------------------------------------------
# Init / default data
# ---------------------------------------------------------------------------

def _ensure_column(conn, table, column_name, column_decl):
    """Lightweight migration: add a column to an existing table if missing."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_decl}")


def init_db():
    if sqlite3.sqlite_version_info < (3, 35, 0):
        raise RuntimeError(
            f"FamilyFinance requires SQLite 3.35.0 or newer "
            f"(found {sqlite3.sqlite_version}). The bill numbering sequence "
            "relies on INSERT ... ON CONFLICT ... RETURNING. Upgrade the "
            "system's libsqlite3 (or Python build) and try again."
        )

    conn = get_db()

    with conn:
        conn.executescript(SCHEMA)

    # Migrations for databases created before these columns existed.
    with conn:
        for table in ("expenses", "income"):
            _ensure_column(conn, table, "member_id", "member_id INTEGER REFERENCES members(id) ON DELETE SET NULL")
            _ensure_column(conn, table, "original_currency", "original_currency TEXT DEFAULT ''")
            _ensure_column(conn, table, "exchange_rate", "exchange_rate REAL DEFAULT 0.0")
            _ensure_column(conn, table, "original_amount_cents", "original_amount_cents INTEGER DEFAULT 0")

        for table in ("expense_splits", "income_splits"):
            _ensure_column(conn, table, "member_id", "member_id INTEGER REFERENCES members(id) ON DELETE SET NULL")

        _ensure_column(conn, "goals", "allocation_pct", "allocation_pct REAL NOT NULL DEFAULT 0")

    # Duplicate guards. Applied best-effort so legacy databases that already
    # contain duplicates still boot; the application-level checks in the
    # create/update endpoints enforce the same rule regardless.
    with conn:
        for statement in (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_allowances_member_frequency "
            "ON allowances(member_id, frequency)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_budgets_unique_active "
            "ON budgets(category, member, period) WHERE is_active=1",
        ):
            try:
                conn.execute(statement)
            except sqlite3.DatabaseError:
                current_app.logger.warning(
                    "Could not create uniqueness index — resolve duplicate "
                    "allowance/budget rows to enable database-level enforcement."
                )

    ensure_default_settings()
    ensure_default_categories()
    ensure_default_admin()


def ensure_default_settings():
    with get_db() as conn:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


def ensure_default_categories():
    with get_db() as conn:
        for name in EXPENSE_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, type, is_default, is_active) VALUES (?, 'expense', 1, 1)",
                (name,),
            )

        for name in INCOME_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, type, is_default, is_active) VALUES (?, 'income', 1, 1)",
                (name,),
            )

        for name in BILL_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, type, is_default, is_active) VALUES (?, 'bill', 1, 1)",
                (name,),
            )


def ensure_default_admin():
    conn = get_db()

    admin_user = current_app.config.get("ADMIN_USER", "admin")
    admin_pass = current_app.config.get("ADMIN_PASSWORD") or ""

    exists = conn.execute(
        "SELECT 1 FROM users WHERE username=?",
        (admin_user,),
    ).fetchone()

    if exists:
        return

    if not admin_pass:
        admin_pass = secrets.token_urlsafe(16)

        current_app.logger.warning("*" * 70)
        current_app.logger.warning("Created admin user '%s' with generated password:", admin_user)
        current_app.logger.warning(admin_pass)
        current_app.logger.warning("Set FF_ADMIN_PASS to define your own admin password.")
        current_app.logger.warning("*" * 70)

    with conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'Admin')",
            (admin_user, generate_password_hash(admin_pass)),
        )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def load_settings():
    settings = DEFAULT_SETTINGS.copy()
    conn = get_db()

    rows = conn.execute("SELECT key, value FROM settings").fetchall()

    for row in rows:
        key = row["key"]

        if key not in DEFAULT_SETTINGS:
            continue

        try:
            settings[key] = json.loads(row["value"])
        except Exception:
            settings[key] = row["value"]

    return settings


def save_settings(data):
    with get_db() as conn:
        for key, value in data.items():
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (key, json.dumps(value)),
            )


# ---------------------------------------------------------------------------
# Auth / audit helpers
# ---------------------------------------------------------------------------

# Roles are ordered so API tokens can be scoped to a role no stronger than
# their owner's.
ROLE_RANK = {"Viewer": 1, "Editor": 2, "Admin": 3}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_from_session():
    username = session.get("username")

    if not username:
        return None

    row = get_db().execute(
        "SELECT id, username, role FROM users WHERE username=?",
        (username,),
    ).fetchone()

    if not row:
        return None

    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def _user_from_bearer_token():
    """Resolve `Authorization: Bearer <token>` personal access tokens."""
    header = request.headers.get("Authorization", "")

    if not header.startswith("Bearer "):
        return None

    token = header[len("Bearer "):].strip()

    # A presented Bearer credential is authoritative: an invalid one must
    # not silently fall back to the session cookie.
    g.auth_via_bearer = True

    if not token:
        return None

    row = get_db().execute(
        """
        SELECT t.id AS token_id, t.role AS token_role, t.last_used_at,
               u.id AS user_id, u.username, u.role AS user_role
        FROM api_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash=? AND t.revoked=0
        """,
        (hash_token(token),),
    ).fetchone()

    if not row:
        return None

    # Throttle last-used writes to at most one per minute per token.
    try:
        last_used = datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None
    except (TypeError, ValueError):
        last_used = None

    if last_used is None or (datetime.now(timezone.utc).replace(tzinfo=None) - last_used).total_seconds() > 60:
        try:
            conn = get_db()
            already_in_transaction = conn.in_transaction
            conn.execute(
                "UPDATE api_tokens SET last_used_at=CURRENT_TIMESTAMP WHERE id=?",
                (row["token_id"],),
            )
            if not already_in_transaction:
                conn.commit()
        except Exception:
            current_app.logger.exception("Updating token last_used_at failed")

    # A token can never grant a stronger role than its owning user has now.
    effective_role = min(
        (row["user_role"], row["token_role"]),
        key=lambda r: ROLE_RANK.get(r, 0),
    )

    return {
        "id": row["user_id"],
        "username": row["username"],
        "role": effective_role,
        "token_id": row["token_id"],
    }


def get_current_user():
    """Return the authenticated user for this request.

    The result is cached on `g` so the global guard and the per-route
    decorators share a single database lookup per request instead of each
    re-querying the users table.
    """
    if "user" in g:
        return g.user

    user = None
    g.auth_via_bearer = False

    if request.headers.get("Authorization", "").startswith("Bearer "):
        user = _user_from_bearer_token()

        if user is not None:
            g.auth_via_bearer = True
    else:
        user = _user_from_session()

    if user is not None:
        g.user = user

    return user


def generate_csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)

    return session["_csrf"]


def api_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()

        if not user:
            return jsonify({"error": "Authentication required"}), 401

        g.user = user

        return fn(*args, **kwargs)

    return wrapper


def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()

            if not user:
                return jsonify({"error": "Authentication required"}), 401

            if roles and user["role"] not in roles:
                return jsonify({"error": "Forbidden"}), 403

            g.user = user

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def audit(action, entity_type="", entity_id="", details=""):
    try:
        conn = get_db()
        already_in_transaction = conn.in_transaction

        actor = session.get("username")
        if not actor and g.get("user"):
            actor = g.user["username"]

        conn.execute(
            """
            INSERT INTO audit_logs (
                username,
                action,
                entity_type,
                entity_id,
                details,
                ip_address,
                user_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor or "anonymous",
                action,
                entity_type,
                str(entity_id),
                details,
                request.remote_addr or "",
                request.headers.get("User-Agent", ""),
            ),
        )

        if not already_in_transaction:
            conn.commit()
    except Exception:
        current_app.logger.exception("Audit logging failed")


def notify(title, body="", link="", username=""):
    try:
        conn = get_db()
        already_in_transaction = conn.in_transaction

        conn.execute(
            "INSERT INTO notifications (username, title, body, link) VALUES (?, ?, ?, ?)",
            (username, title, body, link),
        )

        if not already_in_transaction:
            conn.commit()
    except Exception:
        current_app.logger.exception("Notification logging failed")


# ---------------------------------------------------------------------------
# Money / transaction helpers
# ---------------------------------------------------------------------------

def ensure_reference_exists(conn, table, row_id, field_name, require_active=False):
    if row_id in (None, ""):
        return None

    try:
        clean_id = int(row_id)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} is invalid")

    if clean_id <= 0:
        raise ValidationError(f"{field_name} is invalid")

    sql = f"SELECT 1 FROM {table} WHERE id=?"

    row = conn.execute(sql, (clean_id,)).fetchone()
    if row is None:
        raise ValidationError(f"{field_name} does not exist")

    # New records must reference *active* rows; existing records that point
    # at a since-deactivated row keep rendering (that's why deletes are soft).
    if require_active and not conn.execute(
        f"{sql} AND is_active=1", (clean_id,)
    ).fetchone():
        raise ValidationError(f"{field_name} is inactive and cannot be referenced by new records")

    return clean_id


def resolve_member_reference(data, field_name="member", require_active=True):
    """Resolve the legacy free-text `member` field and/or a `member_id`.

    Returns (member_text, member_id_or_None). An explicit valid `member_id`
    wins and its canonical name is used; otherwise the text is matched
    case-insensitively against active members so typos that hit a real
    member still link. Unmatched text stays as a display-only fallback.
    """
    conn = get_db()
    member_text = str(data.get(field_name) or "").strip()

    raw_member_id = data.get("member_id")
    if raw_member_id not in (None, ""):
        member_id = ensure_reference_exists(
            conn, "members", raw_member_id, "member_id", require_active=require_active
        )
        row = conn.execute(
            "SELECT name FROM members WHERE id=?", (member_id,)
        ).fetchone()
        return (row["name"] if row else member_text), member_id

    if member_text:
        sql = "SELECT id, name FROM members WHERE name=? COLLATE NOCASE"
        if require_active:
            sql += " AND is_active=1"
        row = conn.execute(sql, (member_text,)).fetchone()
        if row:
            return row["name"], row["id"]

    return member_text, None


def _parse_fx_fields(data):
    """Parse optional multi-currency fields (settling to the primary currency).

    `amount_cents` always stays the settled amount in the household's primary
    currency; the original currency/rate/original value are stored for
    reference and reporting.
    """
    original_currency = str(data.get("original_currency") or "").strip().upper()[:3]

    if not original_currency:
        return "", 0.0, 0

    if not re.match(r"^[A-Z]{3}$", original_currency):
        raise ValidationError("original_currency must be a 3-letter currency code")

    exchange_rate = parse_number(data.get("exchange_rate", 0), "exchange_rate", minimum=0)
    if exchange_rate <= 0:
        raise ValidationError(
            "exchange_rate must be greater than 0 when original_currency is set"
        )

    if "original_amount_cents" in data or "original_amount" in data:
        original_amount_cents = parse_amount_from_data(
            data, field="original_amount", allow_zero=True
        )
    else:
        original_amount_cents = 0

    return original_currency, exchange_rate, original_amount_cents


def parse_transaction_payload(
    data,
    date_field,
    require_active_refs=False,
    require_active_account=None,
    require_active_member=None,
):
    title = str(data.get("title") or "").strip()

    if not title:
        raise ValidationError("title is required")

    transaction_date = parse_iso_date(data.get(date_field), date_field)
    amount_cents = parse_amount_from_data(data, "amount")

    category = str(data.get("category") or "Other").strip() or "Other"
    notes = str(data.get("notes") or "").strip()
    active_account = require_active_refs if require_active_account is None else require_active_account
    active_member = require_active_refs if require_active_member is None else require_active_member
    account_id = ensure_reference_exists(
        get_db(),
        "accounts",
        data.get("account_id"),
        "account_id",
        require_active=active_account,
    )
    member, member_id = resolve_member_reference(data, require_active=active_member)
    tags = data.get("tags") or ""
    original_currency, exchange_rate, original_amount_cents = _parse_fx_fields(data)

    if isinstance(tags, list):
        tags = ",".join(str(t).strip() for t in tags if str(t).strip())
    else:
        tags = str(tags).strip()

    return {
        "title": title,
        "category": category,
        "amount_cents": amount_cents,
        "date": transaction_date,
        "member": member,
        "member_id": member_id,
        "notes": notes,
        "account_id": account_id,
        "tags": tags,
        "original_currency": original_currency,
        "exchange_rate": exchange_rate,
        "original_amount_cents": original_amount_cents,
    }


def apply_goal_auto_allocations(conn, amount_cents):
    """Sinking-fund style auto-allocation.

    Splits a freshly recorded income deposit across every active goal with an
    `allocation_pct > 0`, crediting round(amount * pct / 100) cents to each.
    Returns a list of applied contributions for reporting/notifications.
    """
    applied = []

    rows = conn.execute(
        "SELECT id, name, allocation_pct FROM goals WHERE is_active=1 AND allocation_pct > 0"
    ).fetchall()

    for row in rows:
        contribution_cents = int(
            (
                Decimal(int(amount_cents))
                * Decimal(str(row["allocation_pct"]))
                / Decimal("100")
            ).to_integral_value(rounding=ROUND_HALF_UP)
        )

        if contribution_cents <= 0:
            continue

        conn.execute(
            "UPDATE goals SET current_cents = current_cents + ? WHERE id=?",
            (contribution_cents, row["id"]),
        )
        applied.append({
            "goal_id": row["id"],
            "goal": row["name"],
            "amount_cents": contribution_cents,
        })
        notify(
            title=f"Auto-allocated {fmt_cents(contribution_cents)} to '{row['name']}'",
            body=f"{row['allocation_pct']:g}% of an income deposit was routed to this sinking fund.",
            link="/api/goals",
        )

    return applied


def _resolve_split_member(data, default_member, default_member_id):
    """Resolve a single split's member (id wins, then name, then parent)."""
    if data.get("member_id") not in (None, ""):
        member_id = ensure_reference_exists(
            get_db(), "members", data.get("member_id"), "member_id"
        )
        row = get_db().execute(
            "SELECT name FROM members WHERE id=?", (member_id,)
        ).fetchone()
        return (row["name"] if row else str(data.get("member") or "").strip()), member_id

    member_text = str(data.get("member") if data.get("member") is not None else default_member or "").strip()
    if not member_text and default_member:
        member_text = default_member

    if member_text:
        row = get_db().execute(
            "SELECT id, name FROM members WHERE name=? COLLATE NOCASE AND is_active=1",
            (member_text,),
        ).fetchone()
        if row:
            return row["name"], row["id"]
        return member_text, default_member_id if (default_member and member_text == default_member) else None

    return "", None


def insert_expense(conn, data, require_active_refs=False):
    payload = parse_transaction_payload(data, "expense_date", require_active_refs=require_active_refs)
    splits = data.get("splits") or []

    if splits and isinstance(splits, list):
        split_total = 0
        for s in splits:
            s_amount = parse_amount_from_data(s, "amount")
            split_total += s_amount
        if split_total != payload["amount_cents"]:
            raise ValidationError("Sum of splits must equal total expense amount")

    cur = conn.execute(
        """
        INSERT INTO expenses (
            title,
            category,
            amount_cents,
            expense_date,
            store,
            receipt_ref,
            notes,
            member,
            member_id,
            account_id,
            tags,
            original_currency,
            exchange_rate,
            original_amount_cents
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["title"],
            payload["category"],
            payload["amount_cents"],
            payload["date"],
            str(data.get("store") or "").strip(),
            str(data.get("receipt_ref") or "").strip(),
            payload["notes"],
            payload["member"],
            payload["member_id"],
            payload["account_id"],
            payload["tags"],
            payload["original_currency"],
            payload["exchange_rate"],
            payload["original_amount_cents"],
        ),
    )

    expense_id = cur.lastrowid

    if splits and isinstance(splits, list):
        for s in splits:
            s_member, s_member_id = _resolve_split_member(
                s, payload["member"], payload["member_id"]
            )
            conn.execute(
                """
                INSERT INTO expense_splits (expense_id, category, amount_cents, member, member_id, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    expense_id,
                    str(s.get("category") or payload["category"]).strip() or "Other",
                    parse_amount_from_data(s, "amount"),
                    s_member,
                    s_member_id,
                    str(s.get("notes") or "").strip(),
                ),
            )

    return expense_id


def insert_income(conn, data, require_active_refs=False):
    payload = parse_transaction_payload(data, "income_date", require_active_refs=require_active_refs)
    splits = data.get("splits") or []

    if splits and isinstance(splits, list):
        split_total = 0
        for s in splits:
            s_amount = parse_amount_from_data(s, "amount")
            split_total += s_amount
        if split_total != payload["amount_cents"]:
            raise ValidationError("Sum of splits must equal total income amount")

    cur = conn.execute(
        """
        INSERT INTO income (
            title,
            category,
            amount_cents,
            income_date,
            source,
            notes,
            member,
            member_id,
            account_id,
            tags,
            original_currency,
            exchange_rate,
            original_amount_cents
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["title"],
            payload["category"],
            payload["amount_cents"],
            payload["date"],
            str(data.get("source") or "").strip(),
            payload["notes"],
            payload["member"],
            payload["member_id"],
            payload["account_id"],
            payload["tags"],
            payload["original_currency"],
            payload["exchange_rate"],
            payload["original_amount_cents"],
        ),
    )

    income_id = cur.lastrowid

    if splits and isinstance(splits, list):
        for s in splits:
            s_member, s_member_id = _resolve_split_member(
                s, payload["member"], payload["member_id"]
            )
            conn.execute(
                """
                INSERT INTO income_splits (income_id, category, amount_cents, member, member_id, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    income_id,
                    str(s.get("category") or payload["category"]).strip() or "Other",
                    parse_amount_from_data(s, "amount"),
                    s_member,
                    s_member_id,
                    str(s.get("notes") or "").strip(),
                ),
            )

    # Sinking funds: route a configured percentage of this deposit toward
    # goals that opted into auto-allocation.
    apply_goal_auto_allocations(conn, payload["amount_cents"])

    return income_id


def parse_bill_payload(data, require_active_refs=False, require_active_account=None):
    payee_name = str(data.get("payee_name") or "").strip()
    payee_email = str(data.get("payee_email") or "").strip()
    payee_address = str(data.get("payee_address") or "").strip()
    raw_payee_id = data.get("payee_id") or None

    # Always validate a supplied payee_id up front — even when a free-text
    # payee_name is also present — so an invalid id surfaces as a clean 400
    # instead of a foreign-key 500 at insert time.
    if raw_payee_id not in (None, ""):
        payee_id = ensure_reference_exists(
            get_db(), "payees", raw_payee_id, "payee_id"
        )
        if not payee_name:
            # Resolve the payee's stored details from the id so recurring
            # rules and manual creation behave identically.
            payee = get_db().execute(
                "SELECT name, email, address FROM payees WHERE id=?",
                (payee_id,),
            ).fetchone()

            if not payee:
                raise ValidationError("payee_id does not exist")

            payee_name = payee["name"]
            payee_email = payee_email or payee["email"]
            payee_address = payee_address or payee["address"]
    else:
        payee_id = None

    if not payee_name and not payee_id:
        raise ValidationError("payee_name or payee_id is required")

    bill_date = parse_iso_date(data.get("bill_date"), "bill_date")
    due_date = parse_iso_date(data.get("due_date"), "due_date")

    status = str(data.get("status") or "Pending").strip()

    if status not in BILL_STATUSES:
        raise ValidationError("Invalid bill status")

    bill_category = str(data.get("bill_category") or "Other").strip() or "Other"
    notes = str(data.get("notes") or "").strip()
    active_account = require_active_refs if require_active_account is None else require_active_account
    account_id = ensure_reference_exists(
        get_db(),
        "accounts",
        data.get("account_id"),
        "account_id",
        require_active=active_account,
    )

    items = data.get("items") or []

    if not isinstance(items, list):
        raise ValidationError("items must be a list")

    clean_items = []
    subtotal_cents = 0

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"items[{idx}] must be an object")

        item_name = str(item.get("item_name") or "").strip()

        if not item_name:
            raise ValidationError(f"items[{idx}].item_name is required")

        quantity = parse_quantity(item.get("quantity", 1), f"items[{idx}].quantity")

        unit_price_cents = parse_amount_from_data(
            item,
            field="unit_price",
            default_cents=0,
            allow_zero=True,
        )

        line_total_cents = int(
            (quantity * Decimal(unit_price_cents)).to_integral_value(rounding=ROUND_HALF_UP)
        )

        subtotal_cents += line_total_cents

        clean_items.append({
            "item_name": item_name,
            "description": str(item.get("description") or "").strip(),
            "quantity": float(quantity),
            "unit_price_cents": unit_price_cents,
            "total_cents": line_total_cents,
        })

    if not clean_items:
        raise ValidationError("items must contain at least one item")

    discount_pct = parse_percent(data.get("discount_pct", 0), "discount_pct")
    tax_rate = parse_percent(data.get("tax_rate", 0), "tax_rate")

    discount_cents = int(
        (Decimal(subtotal_cents) * Decimal(str(discount_pct)) / Decimal("100"))
        .to_integral_value(rounding=ROUND_HALF_UP)
    )

    taxable_cents = subtotal_cents - discount_cents

    tax_cents = int(
        (Decimal(taxable_cents) * Decimal(str(tax_rate)) / Decimal("100"))
        .to_integral_value(rounding=ROUND_HALF_UP)
    )

    total_cents = taxable_cents + tax_cents

    return {
        "payee_id": payee_id,
        "payee_name": payee_name,
        "payee_email": payee_email,
        "payee_address": payee_address,
        "bill_category": bill_category,
        "bill_date": bill_date,
        "due_date": due_date,
        "subtotal_cents": subtotal_cents,
        "discount_pct": discount_pct,
        "discount_cents": discount_cents,
        "tax_rate": tax_rate,
        "tax_cents": tax_cents,
        "total_cents": total_cents,
        "status": status,
        "notes": notes,
        "account_id": account_id,
        "items": clean_items,
    }


def next_bill_number(conn, prefix="BILL"):
    prefix = re.sub(r"[^A-Za-z0-9\-]", "", str(prefix or "BILL")).upper() or "BILL"

    # Single atomic UPSERT: concurrent callers each get a unique number even
    # when they race, because the increment happens inside one statement.
    row = conn.execute(
        """
        INSERT INTO bill_sequences (prefix, next_number) VALUES (?, 2)
        ON CONFLICT(prefix) DO UPDATE SET next_number = next_number + 1
        RETURNING next_number
        """,
        (prefix,),
    ).fetchone()

    number = int(row["next_number"]) - 1

    return f"{prefix}-{number:04d}"


def insert_bill(conn, payload, bill_number, paid_cents=0, status="Pending", paid_at=None, payment_date=None):
    cur = conn.execute(
        """
        INSERT INTO bills (
            bill_number,
            payee_id,
            payee_name,
            payee_email,
            payee_address,
            bill_category,
            bill_date,
            due_date,
            subtotal_cents,
            discount_pct,
            discount_cents,
            tax_rate,
            tax_cents,
            total_cents,
            paid_cents,
            status,
            notes,
            account_id,
            paid_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bill_number,
            payload.get("payee_id"),
            payload["payee_name"],
            payload.get("payee_email", ""),
            payload.get("payee_address", ""),
            payload.get("bill_category", "Other"),
            payload["bill_date"],
            payload["due_date"],
            payload["subtotal_cents"],
            payload["discount_pct"],
            payload["discount_cents"],
            payload["tax_rate"],
            payload["tax_cents"],
            payload["total_cents"],
            paid_cents,
            status,
            payload.get("notes", ""),
            payload.get("account_id"),
            paid_at,
        ),
    )

    bill_id = cur.lastrowid

    for item in payload["items"]:
        conn.execute(
            """
            INSERT INTO bill_items (
                bill_id,
                item_name,
                description,
                quantity,
                unit_price_cents,
                total_cents
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                bill_id,
                item["item_name"],
                item["description"],
                item["quantity"],
                item["unit_price_cents"],
                item["total_cents"],
            ),
        )

    if paid_cents > 0:
        conn.execute(
            """
            INSERT INTO bill_payments (
                bill_id,
                amount_cents,
                payment_date,
                account_id,
                notes
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                bill_id,
                paid_cents,
                payment_date or date.today().isoformat(),
                payload.get("account_id"),
                "Initial payment",
            ),
        )

    return bill_id


def advance_recurring_date(current_date_str, frequency, interval_value):
    d = date.fromisoformat(current_date_str)

    if frequency == "daily":
        d = d + timedelta(days=interval_value)
    elif frequency == "weekly":
        d = d + timedelta(weeks=interval_value)
    elif frequency == "monthly":
        d = add_months(d, interval_value)
    elif frequency == "yearly":
        d = add_months(d, 12 * interval_value)
    else:
        raise ValidationError("Invalid recurring frequency")

    return d.isoformat()


def compute_account_balance(conn, account):
    income_cents = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0)
        FROM income
        WHERE is_deleted = 0 AND account_id = ?
        """,
        (account["id"],),
    ).fetchone()[0]

    expense_cents = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0)
        FROM expenses
        WHERE is_deleted = 0 AND account_id = ?
        """,
        (account["id"],),
    ).fetchone()[0]

    payment_cents = conn.execute(
        f"""
        SELECT COALESCE(SUM(bp.amount_cents), 0)
        {VALID_BILL_PAYMENT_FROM}
        WHERE bp.account_id = ?
        """,
        (account["id"],),
    ).fetchone()[0]

    outgoing_transfer_cents = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0)
        FROM transfers
        WHERE is_deleted = 0 AND from_account_id = ?
        """,
        (account["id"],),
    ).fetchone()[0]

    incoming_transfer_cents = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0)
        FROM transfers
        WHERE is_deleted = 0 AND to_account_id = ?
        """,
        (account["id"],),
    ).fetchone()[0]

    opening_cents = account["opening_balance_cents"]

    if account["account_type"] in LIABILITY_ACCOUNT_TYPES:
        # Liability accounts represent money owed.
        # Expenses & outgoing transfers increase debt; payments, income & incoming transfers reduce debt.
        balance_cents = (
            opening_cents
            + expense_cents
            - payment_cents
            - income_cents
            + outgoing_transfer_cents
            - incoming_transfer_cents
        )
        net_worth_cents = -balance_cents
    else:
        # Asset accounts represent money owned.
        balance_cents = (
            opening_cents
            + income_cents
            - expense_cents
            - payment_cents
            + incoming_transfer_cents
            - outgoing_transfer_cents
        )
        net_worth_cents = balance_cents

    return balance_cents, net_worth_cents


def compute_all_account_balances(conn, accounts):
    """
    Compute balances/net-worth contributions for many accounts efficiently.

    Returns {account_id: {"balance_cents": int, "net_worth_cents": int}}.
    """
    income_by_account = {
        row["account_id"]: row["total"]
        for row in conn.execute(
            """
            SELECT account_id, SUM(amount_cents) AS total
            FROM income
            WHERE is_deleted=0 AND account_id IS NOT NULL
            GROUP BY account_id
            """
        ).fetchall()
    }

    expense_by_account = {
        row["account_id"]: row["total"]
        for row in conn.execute(
            """
            SELECT account_id, SUM(amount_cents) AS total
            FROM expenses
            WHERE is_deleted=0 AND account_id IS NOT NULL
            GROUP BY account_id
            """
        ).fetchall()
    }

    payments_by_account = {
        row["account_id"]: row["total"]
        for row in conn.execute(
            f"""
            SELECT bp.account_id, SUM(bp.amount_cents) AS total
            {VALID_BILL_PAYMENT_FROM}
            WHERE bp.account_id IS NOT NULL
            GROUP BY bp.account_id
            """
        ).fetchall()
    }

    outgoing_transfers_by_account = {
        row["from_account_id"]: row["total"]
        for row in conn.execute(
            """
            SELECT from_account_id, SUM(amount_cents) AS total
            FROM transfers
            WHERE is_deleted=0
            GROUP BY from_account_id
            """
        ).fetchall()
    }

    incoming_transfers_by_account = {
        row["to_account_id"]: row["total"]
        for row in conn.execute(
            """
            SELECT to_account_id, SUM(amount_cents) AS total
            FROM transfers
            WHERE is_deleted=0
            GROUP BY to_account_id
            """
        ).fetchall()
    }

    result = {}

    for account in accounts:
        opening_cents = account["opening_balance_cents"]
        income_cents = income_by_account.get(account["id"], 0)
        expense_cents = expense_by_account.get(account["id"], 0)
        payment_cents = payments_by_account.get(account["id"], 0)
        outgoing_cents = outgoing_transfers_by_account.get(account["id"], 0)
        incoming_cents = incoming_transfers_by_account.get(account["id"], 0)

        if account["account_type"] in LIABILITY_ACCOUNT_TYPES:
            balance_cents = (
                opening_cents
                + expense_cents
                - payment_cents
                - income_cents
                + outgoing_cents
                - incoming_cents
            )
            net_worth_cents = -balance_cents
        else:
            balance_cents = (
                opening_cents
                + income_cents
                - expense_cents
                - payment_cents
                + incoming_cents
                - outgoing_cents
            )
            net_worth_cents = balance_cents

        result[account["id"]] = {
            "balance_cents": balance_cents,
            "net_worth_cents": net_worth_cents,
        }

    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register_routes(app):
    # -----------------------------------------------------------------------
    # Home / health
    # -----------------------------------------------------------------------

    @app.route("/")
    def index():
        try:
            return render_template("index.html")
        except Exception:
            return render_template_string(MINIMAL_INDEX)

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.route("/readyz")
    def readyz():
        try:
            conn = get_db()
            conn.execute("SELECT 1").fetchone()
            return jsonify({"status": "ready"})
        except Exception:
            return jsonify({"status": "unavailable"}), 503

    # -----------------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------------

    @app.post("/api/auth/login")
    def auth_login():
        ip = request.remote_addr or "unknown"

        if not RATE_LIMITER.allow(f"login:{ip}", limit=10, window=60):
            return jsonify({"error": "Too many login attempts"}), 429

        data = json_payload()

        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        # Second throttle keyed on the account itself: credential stuffing
        # against one known username from many rotating IPs is still capped.
        # Deliberately looser than the per-IP limit so a household sharing
        # one account across devices isn't locked out by normal use.
        if not RATE_LIMITER.allow(f"login:user:{username.lower()}", limit=30, window=300):
            return jsonify({"error": "Too many login attempts"}), 429

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username,),
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            audit("login_failed", "user", username, "Invalid credentials")
            return jsonify({"error": "Invalid credentials"}), 401

        session.clear()
        session["username"] = user["username"]
        session["role"] = user["role"]
        session.permanent = True

        csrf_token = generate_csrf_token()

        audit("login_success", "user", user["username"])

        return jsonify({
            "success": True,
            "user": {
                "username": user["username"],
                "role": user["role"],
            },
            "csrf_token": csrf_token,
        })

    @app.post("/api/auth/logout")
    def auth_logout():
        username = session.get("username")

        if username:
            audit("logout", "user", username)

        session.clear()

        return jsonify({"success": True})

    @app.get("/api/me")
    @api_login_required
    def me():
        return jsonify({
            "user": {
                "username": g.user["username"],
                "role": g.user["role"],
            },
            "csrf_token": generate_csrf_token(),
        })

    @app.get("/api/csrf")
    @api_login_required
    def csrf():
        return jsonify({"csrf_token": generate_csrf_token()})

    # -----------------------------------------------------------------------
    # Personal access tokens (Bearer auth for cron jobs / automations)
    # -----------------------------------------------------------------------

    @app.get("/api/auth/tokens")
    @api_login_required
    def tokens_list():
        conn = get_db()

        if g.user["role"] == "Admin":
            rows = conn.execute(
                """
                SELECT t.id, t.name, t.role, t.created_at, t.last_used_at, t.revoked,
                       u.username AS username
                FROM api_tokens t JOIN users u ON u.id = t.user_id
                ORDER BY t.id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT t.id, t.name, t.role, t.created_at, t.last_used_at, t.revoked,
                       u.username AS username
                FROM api_tokens t JOIN users u ON u.id = t.user_id
                WHERE t.user_id=?
                ORDER BY t.id DESC
                """,
                (g.user["id"],),
            ).fetchall()

        return jsonify(rows_to_list(rows))

    @app.post("/api/auth/tokens")
    @api_login_required
    def token_create():
        data = json_payload()

        name = str(data.get("name") or "").strip()[:80]
        requested_role = str(data.get("role") or g.user["role"]).strip()

        if requested_role not in ROLE_RANK:
            raise ValidationError("role must be Admin, Editor, or Viewer")

        # A token can never outrank its creator.
        if ROLE_RANK[requested_role] > ROLE_RANK[g.user["role"]]:
            raise ValidationError("You cannot create a token with a stronger role than your own")

        plain_token = f"ffpat_{secrets.token_urlsafe(32)}"

        conn = get_db()

        with conn:
            cur = conn.execute(
                """
                INSERT INTO api_tokens (user_id, token_hash, name, role)
                VALUES (?, ?, ?, ?)
                """,
                (g.user["id"], hash_token(plain_token), name, requested_role),
            )

        audit("token_created", "api_token", cur.lastrowid, name or requested_role)

        # The plaintext token is shown exactly once — only its SHA-256 hash
        # is stored.
        return jsonify({
            "success": True,
            "id": cur.lastrowid,
            "name": name,
            "role": requested_role,
            "token": plain_token,
        })

    @app.delete("/api/auth/tokens/<int:tid>")
    @api_login_required
    def token_revoke(tid):
        conn = get_db()

        row = conn.execute(
            """
            SELECT t.*, u.username FROM api_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.id=?
            """,
            (tid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        if row["user_id"] != g.user["id"] and g.user["role"] != "Admin":
            return jsonify({"error": "Forbidden"}), 403

        with conn:
            conn.execute("UPDATE api_tokens SET revoked=1 WHERE id=?", (tid,))

        audit("token_revoked", "api_token", tid, row["username"])
        return jsonify({"success": True})

    @app.post("/api/auth/password")
    @api_login_required
    def change_password():
        data = json_payload()

        current_password = str(data.get("current_password") or "")
        new_password = str(data.get("new_password") or "")

        if len(new_password) < 8:
            raise ValidationError("new_password must be at least 8 characters")

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (g.user["username"],),
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], current_password):
            return jsonify({"error": "Current password is incorrect"}), 400

        with conn:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(new_password), user["id"]),
            )

        audit("password_changed", "user", user["username"])

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Users (household logins & roles)
    # -----------------------------------------------------------------------

    VALID_USER_ROLES = ("Admin", "Editor", "Viewer")

    @app.get("/api/users")
    @require_roles("Admin")
    def users_list():
        conn = get_db()
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY created_at ASC"
        ).fetchall()
        return jsonify(rows_to_list(rows))

    @app.post("/api/users")
    @require_roles("Admin")
    def user_create():
        data = json_payload()

        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        role = str(data.get("role") or "Viewer").strip()

        if not username:
            raise ValidationError("Username is required")
        if role not in VALID_USER_ROLES:
            raise ValidationError(f"Role must be one of: {', '.join(VALID_USER_ROLES)}")
        if len(password) < 8:
            raise ValidationError("Password must be at least 8 characters")

        conn = get_db()
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username=?", (username,)
        ).fetchone()
        if exists:
            raise ValidationError("That username is already taken")

        with conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), role),
            )

        audit("user_created", "user", cur.lastrowid, username)

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.put("/api/users/<int:uid>")
    @require_roles("Admin")
    def user_update(uid):
        data = json_payload()

        conn = get_db()
        existing = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if existing is None:
            return jsonify({"error": "Not found"}), 404

        role = str(data.get("role") or existing["role"]).strip()
        if role not in VALID_USER_ROLES:
            raise ValidationError(f"Role must be one of: {', '.join(VALID_USER_ROLES)}")

        if role != "Admin" and existing["role"] == "Admin":
            remaining_admins = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='Admin' AND id != ?", (uid,)
            ).fetchone()[0]
            if remaining_admins == 0:
                raise ValidationError("At least one Admin account must remain")

        new_password = str(data.get("password") or "")
        if new_password and len(new_password) < 8:
            raise ValidationError("Password must be at least 8 characters")

        with conn:
            if new_password:
                conn.execute(
                    "UPDATE users SET role=?, password_hash=? WHERE id=?",
                    (role, generate_password_hash(new_password), uid),
                )
            else:
                conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))

        if session.get("username") == existing["username"]:
            session["role"] = role

        audit("user_updated", "user", uid, existing["username"])

        return jsonify({"success": True})

    @app.delete("/api/users/<int:uid>")
    @require_roles("Admin")
    def user_delete(uid):
        conn = get_db()
        existing = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if existing is None:
            return jsonify({"error": "Not found"}), 404

        if existing["username"] == g.user["username"]:
            raise ValidationError("You can't delete your own account while signed in")

        if existing["role"] == "Admin":
            remaining_admins = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='Admin' AND id != ?", (uid,)
            ).fetchone()[0]
            if remaining_admins == 0:
                raise ValidationError("At least one Admin account must remain")

        with conn:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))

        audit("user_deleted", "user", uid, existing["username"])

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Settings
    # -----------------------------------------------------------------------

    @app.get("/api/settings")
    @api_login_required
    def settings_get():
        settings = load_settings()
        conn = get_db()

        settings["expense_categories"] = [
            r["name"] for r in conn.execute(
                "SELECT name FROM categories WHERE type='expense' AND is_active=1 ORDER BY name"
            ).fetchall()
        ]

        settings["income_categories"] = [
            r["name"] for r in conn.execute(
                "SELECT name FROM categories WHERE type='income' AND is_active=1 ORDER BY name"
            ).fetchall()
        ]

        settings["bill_categories"] = [
            r["name"] for r in conn.execute(
                "SELECT name FROM categories WHERE type='bill' AND is_active=1 ORDER BY name"
            ).fetchall()
        ]

        return jsonify(settings)

    @app.post("/api/settings")
    @require_roles("Admin")
    def settings_post():
        data = json_payload()
        settings = load_settings()

        for key, value in data.items():
            if key in DEFAULT_SETTINGS:
                settings[key] = value

        if "monthly_income_goal" in settings:
            settings["monthly_income_goal"] = parse_number(
                settings["monthly_income_goal"],
                "monthly_income_goal",
                minimum=0,
            )

        if "savings_target_pct" in settings:
            settings["savings_target_pct"] = parse_percent(
                settings["savings_target_pct"],
                "savings_target_pct",
                minimum=0,
                maximum=100,
            )

        if "currency_symbol" in settings:
            settings["currency_symbol"] = re.sub(
                r"[<>&\"']",
                "",
                str(settings["currency_symbol"]).strip(),
            )[:8]

        if "currency_code" in settings:
            settings["currency_code"] = str(settings["currency_code"]).strip()[:3].upper()

        if "bill_prefix" in settings:
            settings["bill_prefix"] = re.sub(
                r"[^A-Za-z0-9\-]",
                "",
                str(settings["bill_prefix"]).strip(),
            ).upper()[:12] or "BILL"

        save_settings(settings)

        audit("settings_updated", "settings", "", "Settings updated")

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Categories
    # -----------------------------------------------------------------------

    @app.get("/api/categories")
    @api_login_required
    def categories_list():
        category_type = request.args.get("type", "")
        active_only = request.args.get("active_only", "1") == "1"

        sql = "SELECT * FROM categories WHERE 1=1"
        params = []

        if category_type:
            if category_type not in {"expense", "income", "bill"}:
                raise ValidationError("Invalid category type")

            sql += " AND type=?"
            params.append(category_type)

        if active_only:
            sql += " AND is_active=1"

        sql += " ORDER BY type, name"

        conn = get_db()

        return jsonify(rows_to_list(conn.execute(sql, params).fetchall()))

    @app.post("/api/categories")
    @require_roles("Admin", "Editor")
    def category_create():
        data = json_payload()

        name = str(data.get("name") or "").strip()
        category_type = str(data.get("type") or "").strip()

        if not name:
            raise ValidationError("Category name is required")

        if category_type not in {"expense", "income", "bill"}:
            raise ValidationError("Invalid category type")

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO categories (name, type, is_default, is_active)
                VALUES (?, ?, 0, 1)
                ON CONFLICT(name, type) DO UPDATE SET is_active=1
                """,
                (name, category_type),
            )
            cat_id = conn.execute(
                "SELECT id FROM categories WHERE name=? AND type=?",
                (name, category_type),
            ).fetchone()["id"]

        audit("category_created", "category", name)

        return jsonify({"success": True, "id": cat_id})

    @app.put("/api/categories/<int:cid>")
    @require_roles("Admin", "Editor")
    def category_update(cid):
        data = json_payload()

        conn = get_db()
        existing = conn.execute(
            "SELECT * FROM categories WHERE id=?", (cid,)
        ).fetchone()
        if existing is None:
            return jsonify({"error": "Not found"}), 404

        name = str(data.get("name") if "name" in data else existing["name"]).strip()
        is_active = 1 if data.get("is_active", existing["is_active"]) else 0

        if not name:
            raise ValidationError("Category name is required")

        clash = conn.execute(
            "SELECT 1 FROM categories WHERE name=? AND type=? AND id != ?",
            (name, existing["type"], cid),
        ).fetchone()
        if clash:
            raise ValidationError(f"Category '{name}' already exists for {existing['type']}")

        old_name = existing["name"]
        with get_db() as conn:
            if name != old_name:
                if existing["type"] == "expense":
                    for b in conn.execute(
                        "SELECT member, period FROM budgets WHERE category=? AND is_active=1",
                        (old_name,),
                    ).fetchall():
                        b_clash = conn.execute(
                            "SELECT 1 FROM budgets WHERE category=? AND member=? AND period=? AND is_active=1",
                            (name, b["member"], b["period"]),
                        ).fetchone()
                        if b_clash:
                            raise ValidationError(
                                f"Cannot rename category to '{name}' because an active {b['period']} budget already exists for '{name}'"
                            )
                    conn.execute("UPDATE expenses SET category=? WHERE category=?", (name, old_name))
                    conn.execute("UPDATE expense_splits SET category=? WHERE category=?", (name, old_name))
                    conn.execute("UPDATE budgets SET category=? WHERE category=?", (name, old_name))
                elif existing["type"] == "income":
                    conn.execute("UPDATE income SET category=? WHERE category=?", (name, old_name))
                    conn.execute("UPDATE income_splits SET category=? WHERE category=?", (name, old_name))
                elif existing["type"] == "bill":
                    conn.execute("UPDATE bills SET bill_category=? WHERE bill_category=?", (name, old_name))
                    conn.execute("UPDATE payees SET category=? WHERE category=?", (name, old_name))

            conn.execute(
                "UPDATE categories SET name=?, is_active=? WHERE id=?",
                (name, is_active, cid),
            )

        audit("category_updated", "category", cid)

        return jsonify({"success": True})

    @app.delete("/api/categories/<int:cid>")
    @require_roles("Admin")
    def category_delete(cid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM categories WHERE id=?", (cid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("UPDATE categories SET is_active=0 WHERE id=?", (cid,))

        audit("category_deactivated", "category", cid)

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Members
    # -----------------------------------------------------------------------

    @app.get("/api/members")
    @api_login_required
    def members_list():
        conn = get_db()

        rows = conn.execute(
            "SELECT * FROM members WHERE is_active=1 ORDER BY name"
        ).fetchall()

        return jsonify(rows_to_list(rows))

    @app.post("/api/members")
    @require_roles("Admin", "Editor")
    def member_create():
        data = json_payload()

        name = str(data.get("name") or "").strip()

        if not name:
            raise ValidationError("Member name is required")

        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO members (name, email, role) VALUES (?, ?, ?)",
                (name, str(data.get("email") or ""), str(data.get("role") or "Member")),
            )

        audit("member_created", "member", name)

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.put("/api/members/<int:mid>")
    @require_roles("Admin", "Editor")
    def member_update(mid):
        data = json_payload()
        conn = get_db()
        existing = conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if existing is None:
            return jsonify({"error": "Not found"}), 404

        name = str(data.get("name") if "name" in data else existing["name"]).strip()

        if not name:
            raise ValidationError("Member name is required")

        email = str(data["email"] if "email" in data else (existing["email"] or ""))
        role = str((data["role"] if "role" in data else existing["role"]) or "Member")
        is_active = 1 if data.get("is_active", bool(existing["is_active"])) else 0
        old_name = existing["name"]

        with conn:
            if name != old_name:
                for b in conn.execute(
                    "SELECT category, period FROM budgets WHERE member=? AND is_active=1",
                    (old_name,),
                ).fetchall():
                    b_clash = conn.execute(
                        "SELECT 1 FROM budgets WHERE category=? AND member=? AND period=? AND is_active=1",
                        (b["category"], name, b["period"]),
                    ).fetchone()
                    if b_clash:
                        raise ValidationError(
                            f"Cannot rename member to '{name}' because an active {b['period']} budget already exists for {b['category']}"
                        )
                conn.execute("UPDATE expenses SET member=? WHERE member_id=?", (name, mid))
                conn.execute("UPDATE expense_splits SET member=? WHERE member_id=?", (name, mid))
                conn.execute("UPDATE income SET member=? WHERE member_id=?", (name, mid))
                conn.execute("UPDATE income_splits SET member=? WHERE member_id=?", (name, mid))
                conn.execute("UPDATE budgets SET member=? WHERE member=?", (name, old_name))

            conn.execute(
                "UPDATE members SET name=?, email=?, role=?, is_active=? WHERE id=?",
                (name, email, role, is_active, mid),
            )

        audit("member_updated", "member", mid)

        return jsonify({"success": True})

    @app.delete("/api/members/<int:mid>")
    @require_roles("Admin")
    def member_delete(mid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM members WHERE id=?", (mid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("UPDATE members SET is_active=0 WHERE id=?", (mid,))

        audit("member_deactivated", "member", mid)

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Accounts
    # -----------------------------------------------------------------------

    @app.get("/api/accounts")
    @api_login_required
    def accounts_list():
        conn = get_db()

        accounts = rows_to_list(
            conn.execute(
                "SELECT * FROM accounts WHERE is_active=1 ORDER BY name"
            ).fetchall()
        )

        balances = compute_all_account_balances(conn, accounts)

        for account in accounts:
            entry = balances[account["id"]]
            account["balance_cents"] = entry["balance_cents"]
            account["balance"] = cents_to_float(entry["balance_cents"])
            account["net_worth_cents"] = entry["net_worth_cents"]
            account["net_worth"] = cents_to_float(entry["net_worth_cents"])

        return jsonify(accounts)

    @app.post("/api/accounts")
    @require_roles("Admin", "Editor")
    def account_create():
        data = json_payload()

        name = str(data.get("name") or "").strip()
        account_type = str(data.get("account_type") or "Checking").strip()

        if not name:
            raise ValidationError("Account name is required")

        if account_type not in ACCOUNT_TYPES:
            raise ValidationError("Invalid account_type")

        opening_balance_cents = parse_amount_from_data(
            data,
            field="opening_balance",
            default_cents=0,
            allow_zero=True,
        )

        interest_rate = parse_number(data.get("interest_rate", 0), "interest_rate", minimum=0, maximum=100)
        minimum_payment_cents = parse_amount_from_data(
            data, field="minimum_payment", default_cents=0, allow_zero=True
        )

        with get_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO accounts (name, account_type, opening_balance_cents, interest_rate, minimum_payment_cents, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, account_type, opening_balance_cents, interest_rate, minimum_payment_cents, str(data.get("notes") or "")),
            )

        audit("account_created", "account", name)

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.put("/api/accounts/<int:aid>")
    @require_roles("Admin", "Editor")
    def account_update(aid):
        data = json_payload()

        with get_db() as conn:
            existing = conn.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
            if not existing:
                return jsonify({"error": "Not found"}), 404

            name = str(data.get("name") if "name" in data else existing["name"]).strip()
            account_type = str(
                data.get("account_type") if "account_type" in data else existing["account_type"]
            ).strip()

            if not name:
                raise ValidationError("Account name is required")

            if account_type not in ACCOUNT_TYPES:
                raise ValidationError("Invalid account_type")

            if "opening_balance_cents" in data or "opening_balance" in data:
                opening_balance_cents = parse_amount_from_data(
                    data,
                    field="opening_balance",
                    default_cents=existing["opening_balance_cents"],
                    allow_zero=True,
                )
            else:
                opening_balance_cents = existing["opening_balance_cents"]

            if "interest_rate" in data:
                interest_rate = parse_number(
                    data.get("interest_rate", 0), "interest_rate", minimum=0, maximum=100
                )
            else:
                interest_rate = existing["interest_rate"]

            if "minimum_payment_cents" in data or "minimum_payment" in data:
                minimum_payment_cents = parse_amount_from_data(
                    data,
                    field="minimum_payment",
                    default_cents=existing["minimum_payment_cents"],
                    allow_zero=True,
                )
            else:
                minimum_payment_cents = existing["minimum_payment_cents"]

            notes = str(data.get("notes") if "notes" in data else existing["notes"])
            is_active = 1 if (data.get("is_active") if "is_active" in data else existing["is_active"]) else 0

            conn.execute(
                """
                UPDATE accounts
                SET name=?, account_type=?, opening_balance_cents=?, interest_rate=?, minimum_payment_cents=?, notes=?, is_active=?
                WHERE id=?
                """,
                (
                    name,
                    account_type,
                    opening_balance_cents,
                    interest_rate,
                    minimum_payment_cents,
                    notes,
                    is_active,
                    aid,
                ),
            )

        audit("account_updated", "account", aid)

        return jsonify({"success": True})

    @app.delete("/api/accounts/<int:aid>")
    @require_roles("Admin")
    def account_delete(aid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (aid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("UPDATE accounts SET is_active=0 WHERE id=?", (aid,))

        audit("account_deactivated", "account", aid)

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Transfers (account-to-account movements, neutral to income/expenses)
    # -----------------------------------------------------------------------

    @app.get("/api/transfers")
    @api_login_required
    def transfers_list():
        account_id = request.args.get("account_id")
        date_from = parse_iso_date(request.args.get("from"), "from", allow_none=True)
        date_to = parse_iso_date(request.args.get("to"), "to", allow_none=True)
        limit, offset = get_pagination()

        sql = """
        SELECT t.*,
               fa.name AS from_account_name,
               fa.account_type AS from_account_type,
               ta.name AS to_account_name,
               ta.account_type AS to_account_type
        FROM transfers t
        JOIN accounts fa ON fa.id = t.from_account_id
        JOIN accounts ta ON ta.id = t.to_account_id
        WHERE t.is_deleted=0
        """
        params = []

        if account_id:
            clean_aid = parse_int(account_id, "account_id", minimum=1)
            sql += " AND (t.from_account_id=? OR t.to_account_id=?)"
            params.extend([clean_aid, clean_aid])

        if date_from:
            sql += " AND t.transfer_date >= ?"
            params.append(date_from)

        if date_to:
            sql += " AND t.transfer_date <= ?"
            params.append(date_to)

        conn = get_db()
        agg = conn.execute(
            f"SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_cents), 0) AS sum_cents FROM ({sql})",
            params,
        ).fetchone()

        sql += " ORDER BY t.transfer_date DESC, t.id DESC LIMIT ? OFFSET ?"
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        items = rows_to_list(rows)
        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

        return jsonify({
            "items": items,
            "total": agg["cnt"],
            "filtered_total_cents": agg["sum_cents"],
            "filtered_total": cents_to_float(agg["sum_cents"]),
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/transfers")
    @require_roles("Admin", "Editor")
    def transfer_create():
        data = json_payload()
        conn = get_db()

        from_account_id = ensure_reference_exists(
            conn, "accounts", data.get("from_account_id"), "from_account_id", require_active=True
        )
        to_account_id = ensure_reference_exists(
            conn, "accounts", data.get("to_account_id"), "to_account_id", require_active=True
        )

        if not from_account_id:
            raise ValidationError("from_account_id is required")
        if not to_account_id:
            raise ValidationError("to_account_id is required")
        if from_account_id == to_account_id:
            raise ValidationError("Source and destination accounts must be different")

        amount_cents = parse_amount_from_data(data, "amount", allow_zero=False)
        transfer_date = parse_iso_date(
            data.get("transfer_date") or date.today().isoformat(),
            "transfer_date",
        )
        notes = str(data.get("notes") or "").strip()

        with conn:
            cur = conn.execute(
                """
                INSERT INTO transfers (from_account_id, to_account_id, amount_cents, transfer_date, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (from_account_id, to_account_id, amount_cents, transfer_date, notes),
            )

        audit("transfer_created", "transfer", cur.lastrowid, f"{from_account_id}->{to_account_id}:{amount_cents}")

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.delete("/api/transfers/<int:tid>")
    @require_roles("Admin", "Editor")
    def transfer_delete(tid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM transfers WHERE id=? AND is_deleted=0", (tid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("UPDATE transfers SET is_deleted=1 WHERE id=?", (tid,))

        audit("transfer_deleted", "transfer", tid)

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Payees
    # -----------------------------------------------------------------------

    @app.get("/api/payees")
    @api_login_required
    def payees_list():
        q = str(request.args.get("q") or "").strip()
        category = str(request.args.get("category") or "").strip()

        limit, offset = get_pagination()

        where = "WHERE 1=1"
        params = []

        if q:
            like = f"%{escape_like(q)}%"
            where += """ AND (p.name LIKE ? ESCAPE '\\' OR p.email LIKE ? ESCAPE '\\' OR p.phone LIKE ? ESCAPE '\\' OR p.notes LIKE ? ESCAPE '\\')"""
            params.extend([like, like, like, like])

        if category:
            where += " AND p.category=?"
            params.append(category)

        conn = get_db()

        total = conn.execute(
            f"SELECT COUNT(*) FROM payees p {where}",
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT p.*,
                   COUNT(b.id) AS bill_count,
                   COALESCE(SUM(CASE WHEN b.status='Paid' THEN b.total_cents ELSE 0 END), 0) AS total_paid_cents
            FROM payees p
            LEFT JOIN bills b ON b.payee_id = p.id AND b.is_deleted=0
            {where}
            GROUP BY p.id
            ORDER BY p.name
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        items = rows_to_list(rows)

        for item in items:
            item["total_paid"] = cents_to_float(item["total_paid_cents"])

        return jsonify({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/payees")
    @require_roles("Admin", "Editor")
    def payee_create():
        data = json_payload()

        name = str(data.get("name") or "").strip()

        if not name:
            raise ValidationError("Payee name is required")

        with get_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO payees (name, email, phone, address, category, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    str(data.get("email") or ""),
                    str(data.get("phone") or ""),
                    str(data.get("address") or ""),
                    str(data.get("category") or ""),
                    str(data.get("notes") or ""),
                ),
            )

        audit("payee_created", "payee", name)

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.get("/api/payees/<int:pid>")
    @api_login_required
    def payee_detail(pid):
        conn = get_db()

        row = conn.execute("SELECT * FROM payees WHERE id=?", (pid,)).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        payee = row_to_dict(row)

        payee["bills"] = rows_to_list(conn.execute(
            """
            SELECT id, bill_number, total_cents, paid_cents, status, bill_date, due_date
            FROM bills
            WHERE payee_id=? AND is_deleted=0
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (pid,),
        ).fetchall())

        return jsonify(payee)

    @app.put("/api/payees/<int:pid>")
    @require_roles("Admin", "Editor")
    def payee_update(pid):
        data = json_payload()

        name = str(data.get("name") or "").strip()

        if not name:
            raise ValidationError("Payee name is required")

        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM payees WHERE id=?", (pid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute(
                """
                UPDATE payees
                SET name=?, email=?, phone=?, address=?, category=?, notes=?
                WHERE id=?
                """,
                (
                    name,
                    str(data.get("email") or ""),
                    str(data.get("phone") or ""),
                    str(data.get("address") or ""),
                    str(data.get("category") or ""),
                    str(data.get("notes") or ""),
                    pid,
                ),
            )

        audit("payee_updated", "payee", pid)

        return jsonify({"success": True})

    @app.delete("/api/payees/<int:pid>")
    @require_roles("Admin", "Editor")
    def payee_delete(pid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM payees WHERE id=?", (pid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("DELETE FROM payees WHERE id=?", (pid,))

        audit("payee_deleted", "payee", pid)

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Bills
    # -----------------------------------------------------------------------

    @app.get("/api/bills")
    @api_login_required
    def bills_list():
        q = str(request.args.get("q") or "").strip()
        status = str(request.args.get("status") or "").strip()
        payee_id = request.args.get("payee_id")
        account_id = request.args.get("account_id")
        category = str(request.args.get("category") or "").strip()

        date_from = parse_iso_date(request.args.get("from"), "from", allow_none=True)
        date_to = parse_iso_date(request.args.get("to"), "to", allow_none=True)

        limit, offset = get_pagination()

        sql = """
        SELECT b.*,
               (b.total_cents - b.paid_cents) AS balance_cents
        FROM bills b
        WHERE b.is_deleted=0
        """

        params = []

        if q:
            like = f"%{escape_like(q)}%"
            sql += """ AND (b.bill_number LIKE ? ESCAPE '\\' OR b.payee_name LIKE ? ESCAPE '\\' OR b.notes LIKE ? ESCAPE '\\')"""
            params.extend([like, like, like])

        if status:
            if status == "Overdue":
                sql += " AND b.status IN ('Pending', 'Partially Paid') AND b.due_date < ?"
                params.append(date.today().isoformat())
            elif status in BILL_STATUSES:
                sql += " AND b.status=?"
                params.append(status)
            else:
                raise ValidationError("Invalid status filter")

        if payee_id:
            sql += " AND b.payee_id=?"
            params.append(payee_id)

        if account_id:
            clean_aid = parse_int(account_id, "account_id", minimum=1)
            sql += " AND b.account_id=?"
            params.append(clean_aid)

        if category:
            sql += " AND b.bill_category=?"
            params.append(category)

        if date_from:
            sql += " AND b.bill_date >= ?"
            params.append(date_from)

        if date_to:
            sql += " AND b.bill_date <= ?"
            params.append(date_to)

        conn = get_db()

        agg = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt,
                   COALESCE(SUM(CASE WHEN status != 'Void' THEN total_cents ELSE 0 END), 0) AS total_sum,
                   COALESCE(SUM(CASE WHEN status != 'Void' THEN balance_cents ELSE 0 END), 0) AS bal_sum
            FROM ({sql})
            """,
            params,
        ).fetchone()

        sql += " ORDER BY b.created_at DESC LIMIT ? OFFSET ?"

        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        items = rows_to_list(rows)

        today = date.today().isoformat()

        for item in items:
            item["balance"] = cents_to_float(item["balance_cents"])
            item["subtotal"] = cents_to_float(item["subtotal_cents"])
            item["discount_amount"] = cents_to_float(item["discount_cents"])
            item["tax_amount"] = cents_to_float(item["tax_cents"])
            item["total_amount"] = cents_to_float(item["total_cents"])
            item["paid_amount"] = cents_to_float(item["paid_cents"])

            if item["status"] in {"Pending", "Partially Paid"} and item["due_date"] < today:
                item["display_status"] = "Overdue"
            else:
                item["display_status"] = item["status"]

        return jsonify({
            "items": items,
            "total": agg["cnt"],
            "filtered_total_cents": agg["total_sum"],
            "filtered_total": cents_to_float(agg["total_sum"]),
            "filtered_balance_cents": agg["bal_sum"],
            "filtered_balance": cents_to_float(agg["bal_sum"]),
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/bills")
    @require_roles("Admin", "Editor")
    def bill_create():
        data = json_payload()

        # parse_bill_payload validates payee_id and resolves payee_name (plus
        # email/address fallbacks) from the id, so manual creation and
        # recurring rules share one code path.
        payload = parse_bill_payload(data, require_active_refs=True)
        payment_date = parse_iso_date(
            data.get("payment_date"), "payment_date", allow_none=True
        ) or date.today().isoformat()

        with get_db() as conn:
            if not payload["payee_name"]:
                raise ValidationError("payee_name is required")

            settings = load_settings()
            bill_number = next_bill_number(conn, settings.get("bill_prefix", "BILL"))

            paid_cents = 0
            status = payload["status"]
            paid_at = None

            if status == "Void":
                paid_cents = 0
            elif status == "Paid":
                paid_cents = payload["total_cents"]
                paid_at = datetime.now(timezone.utc).isoformat()
            else:
                if "paid_amount" in data or "paid_amount_cents" in data:
                    paid_cents = parse_amount_from_data(
                        data,
                        field="paid_amount",
                        default_cents=0,
                        allow_zero=True,
                    )

                    if paid_cents > payload["total_cents"]:
                        raise ValidationError("paid_amount cannot exceed total_amount")

                    if paid_cents == payload["total_cents"]:
                        status = "Paid"
                        paid_at = datetime.now(timezone.utc).isoformat()
                    elif paid_cents > 0:
                        status = "Partially Paid"
                    else:
                        status = "Pending"
                else:
                    status = "Pending"

            bill_id = insert_bill(
                conn,
                payload,
                bill_number,
                paid_cents=paid_cents,
                status=status,
                paid_at=paid_at,
                payment_date=payment_date,
            )

            audit("bill_created", "bill", bill_id, bill_number)

        return jsonify({
            "success": True,
            "id": bill_id,
            "bill_number": bill_number,
        })

    @app.get("/api/bills/<int:bid>")
    @api_login_required
    def bill_detail(bid):
        conn = get_db()

        row = conn.execute(
            "SELECT * FROM bills WHERE id=? AND is_deleted=0",
            (bid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        bill = row_to_dict(row)

        bill["items"] = rows_to_list(conn.execute(
            "SELECT * FROM bill_items WHERE bill_id=? ORDER BY id",
            (bid,),
        ).fetchall())

        for it in bill["items"]:
            it["unit_price"] = cents_to_float(it["unit_price_cents"])
            it["total"] = cents_to_float(it["total_cents"])

        bill["payments"] = rows_to_list(conn.execute(
            """
            SELECT bp.*, a.name AS account_name
            FROM bill_payments bp
            LEFT JOIN accounts a ON a.id = bp.account_id
            WHERE bp.bill_id=?
            ORDER BY bp.payment_date DESC, bp.id DESC
            """,
            (bid,),
        ).fetchall())

        for p in bill["payments"]:
            p["amount"] = cents_to_float(p["amount_cents"])

        bill["balance_cents"] = bill["total_cents"] - bill["paid_cents"]
        bill["balance"] = cents_to_float(bill["balance_cents"])
        bill["subtotal"] = cents_to_float(bill["subtotal_cents"])
        bill["discount_amount"] = cents_to_float(bill["discount_cents"])
        bill["tax_amount"] = cents_to_float(bill["tax_cents"])
        bill["total_amount"] = cents_to_float(bill["total_cents"])
        bill["paid_amount"] = cents_to_float(bill["paid_cents"])

        today = date.today().isoformat()

        if bill["status"] in {"Pending", "Partially Paid"} and bill["due_date"] < today:
            bill["display_status"] = "Overdue"
        else:
            bill["display_status"] = bill["status"]

        return jsonify(bill)

    @app.put("/api/bills/<int:bid>")
    @require_roles("Admin", "Editor")
    def bill_update(bid):
        data = json_payload()
        conn = get_db()

        row = conn.execute(
            "SELECT * FROM bills WHERE id=? AND is_deleted=0",
            (bid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        if data.get("action") == "status":
            new_status = str(data.get("status") or "").strip()

            if new_status not in BILL_STATUSES:
                raise ValidationError("Invalid status")

            # Re-read and mutate inside an IMMEDIATE transaction so a
            # concurrent payment cannot slip between the balance check and
            # the mark-as-paid write.
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")

            try:
                row = conn.execute(
                    "SELECT * FROM bills WHERE id=? AND is_deleted=0",
                    (bid,),
                ).fetchone()

                if not row:
                    conn.rollback()
                    return jsonify({"error": "Not found"}), 404

                if new_status == "Paid":
                    balance = row["total_cents"] - row["paid_cents"]

                    if balance > 0:
                        conn.execute(
                            """
                            INSERT INTO bill_payments (
                                bill_id,
                                amount_cents,
                                payment_date,
                                account_id,
                                notes
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                bid,
                                balance,
                                date.today().isoformat(),
                                row["account_id"],
                                "Manual mark-as-paid payment",
                            ),
                        )

                    conn.execute(
                        """
                        UPDATE bills
                        SET status='Paid', paid_cents=?, paid_at=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (row["total_cents"], datetime.now(timezone.utc).isoformat(), bid),
                    )

                elif new_status == "Void":
                    conn.execute(
                        """
                        UPDATE bills
                        SET status='Void', updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (bid,),
                    )

                elif new_status == "Pending":
                    derived_status = "Pending" if row["paid_cents"] == 0 else "Partially Paid"

                    conn.execute(
                        """
                        UPDATE bills
                        SET status=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (derived_status, bid),
                    )

                else:
                    conn.execute(
                        """
                        UPDATE bills
                        SET status=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (new_status, bid),
                    )

                conn.commit()
            except Exception:
                conn.rollback()
                raise

            audit("bill_status_changed", "bill", bid, new_status)

            return jsonify({"success": True})

        account_changed = "account_id" in data and str(data.get("account_id") or "") != str(row["account_id"] or "")
        payload = parse_bill_payload(data, require_active_account=account_changed)

        if not payload["payee_name"]:
            raise ValidationError("payee_name is required")

        paid_cents = row["paid_cents"]
        paid_at = row["paid_at"]

        if payload["status"] != "Void" and paid_cents > payload["total_cents"]:
            raise ValidationError(
                "total_amount cannot be less than the amount already paid on this bill"
            )

        if payload["status"] == "Void":
            status = "Void"
        else:
            if paid_cents <= 0:
                status = "Pending"
            elif paid_cents >= payload["total_cents"]:
                status = "Paid"

                if not paid_at:
                    paid_at = datetime.now(timezone.utc).isoformat()
            else:
                status = "Partially Paid"

        with conn:
            conn.execute(
                """
                UPDATE bills
                SET payee_id=?, payee_name=?, payee_email=?, payee_address=?,
                    bill_category=?, bill_date=?, due_date=?, subtotal_cents=?,
                    discount_pct=?, discount_cents=?, tax_rate=?, tax_cents=?,
                    total_cents=?, status=?, notes=?, account_id=?, paid_at=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    payload.get("payee_id"),
                    payload["payee_name"],
                    payload.get("payee_email", ""),
                    payload.get("payee_address", ""),
                    payload.get("bill_category", "Other"),
                    payload["bill_date"],
                    payload["due_date"],
                    payload["subtotal_cents"],
                    payload["discount_pct"],
                    payload["discount_cents"],
                    payload["tax_rate"],
                    payload["tax_cents"],
                    payload["total_cents"],
                    status,
                    payload.get("notes", ""),
                    payload.get("account_id"),
                    paid_at,
                    bid,
                ),
            )

            conn.execute("DELETE FROM bill_items WHERE bill_id=?", (bid,))

            for item in payload["items"]:
                conn.execute(
                    """
                    INSERT INTO bill_items (
                        bill_id,
                        item_name,
                        description,
                        quantity,
                        unit_price_cents,
                        total_cents
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bid,
                        item["item_name"],
                        item["description"],
                        item["quantity"],
                        item["unit_price_cents"],
                        item["total_cents"],
                    ),
                )

        audit("bill_updated", "bill", bid)

        return jsonify({"success": True})

    @app.delete("/api/bills/<int:bid>")
    @require_roles("Admin", "Editor")
    def bill_delete(bid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM bills WHERE id=? AND is_deleted=0", (bid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute(
                "UPDATE bills SET is_deleted=1 WHERE id=?",
                (bid,),
            )

        audit("bill_deleted", "bill", bid)

        return jsonify({"success": True})

    @app.post("/api/bills/<int:bid>/pay")
    @require_roles("Admin", "Editor")
    def bill_pay(bid):
        data = json_payload()
        conn = get_db()

        # Read, validate, and record the payment inside one IMMEDIATE
        # transaction: two concurrent payments can no longer both pass the
        # balance check and overpay the bill.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")

        try:
            bill = conn.execute(
                "SELECT * FROM bills WHERE id=? AND is_deleted=0",
                (bid,),
            ).fetchone()

            if not bill:
                conn.rollback()
                return jsonify({"error": "Not found"}), 404

            if bill["status"] == "Void":
                raise ValidationError("Cannot pay a void bill")

            balance_cents = bill["total_cents"] - bill["paid_cents"]

            if balance_cents <= 0:
                raise ValidationError("Bill is already fully paid")

            account_changed = "account_id" in data and str(data.get("account_id") or "") != str(bill["account_id"] or "")
            account_id = ensure_reference_exists(
                conn,
                "accounts",
                data.get("account_id", bill["account_id"]),
                "account_id",
                require_active=account_changed,
            )

            amount_cents = parse_amount_from_data(
                data,
                field="amount",
                default_cents=balance_cents,
                allow_zero=False,
            )

            if amount_cents > balance_cents:
                raise ValidationError("Payment amount cannot exceed remaining balance")

            payment_date = parse_iso_date(
                data.get("payment_date", date.today().isoformat()),
                "payment_date",
            )

            new_paid_cents = bill["paid_cents"] + amount_cents
            new_status = "Paid" if new_paid_cents >= bill["total_cents"] else "Partially Paid"
            paid_at = datetime.now(timezone.utc).isoformat() if new_status == "Paid" else bill["paid_at"]

            conn.execute(
                """
                INSERT INTO bill_payments (
                    bill_id,
                    amount_cents,
                    payment_date,
                    account_id,
                    notes
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    bid,
                    amount_cents,
                    payment_date,
                    account_id,
                    str(data.get("notes") or ""),
                ),
            )

            conn.execute(
                """
                UPDATE bills
                SET paid_cents=?, status=?, paid_at=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (new_paid_cents, new_status, paid_at, bid),
            )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        audit("bill_payment_recorded", "bill", bid, str(amount_cents))

        return jsonify({"success": True})

    @app.get("/api/bills/<int:bid>/payments")
    @api_login_required
    def bill_payments_list(bid):
        conn = get_db()

        if not conn.execute(
            "SELECT 1 FROM bills WHERE id=? AND is_deleted=0", (bid,)
        ).fetchone():
            return jsonify({"error": "Not found"}), 404

        rows = conn.execute(
            """
            SELECT bp.*, a.name AS account_name
            FROM bill_payments bp
            LEFT JOIN accounts a ON a.id = bp.account_id
            WHERE bp.bill_id=?
            ORDER BY bp.payment_date DESC, bp.id DESC
            """,
            (bid,),
        ).fetchall()

        items = rows_to_list(rows)

        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

        return jsonify({
            "items": items,
            "total_cents": sum(i["amount_cents"] for i in items),
        })

    @app.delete("/api/bills/<int:bid>/payments/<int:payment_id>")
    @require_roles("Admin", "Editor")
    def bill_payment_delete(bid, payment_id):
        conn = get_db()

        # Re-reading and recomputing under IMMEDIATE so a concurrent payment
        # cannot slip in between the read and the recompute.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")

        try:
            bill = conn.execute(
                "SELECT * FROM bills WHERE id=? AND is_deleted=0",
                (bid,),
            ).fetchone()

            if not bill:
                conn.rollback()
                return jsonify({"error": "Not found"}), 404

            if bill["status"] == "Void":
                raise ValidationError("Cannot modify payments on a void bill")

            payment = conn.execute(
                "SELECT * FROM bill_payments WHERE id=? AND bill_id=?",
                (payment_id, bid),
            ).fetchone()

            if not payment:
                conn.rollback()
                return jsonify({"error": "Not found"}), 404

            conn.execute("DELETE FROM bill_payments WHERE id=?", (payment_id,))

            # Recompute paid_cents and status from the remaining payments.
            remaining_paid = conn.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) FROM bill_payments WHERE bill_id=?",
                (bid,),
            ).fetchone()[0]

            if remaining_paid >= bill["total_cents"]:
                new_status = "Paid"
                new_paid_at = bill["paid_at"] or datetime.now(timezone.utc).isoformat()
            elif remaining_paid > 0:
                new_status = "Partially Paid"
                new_paid_at = None
            else:
                new_status = "Pending"
                new_paid_at = None

            conn.execute(
                """
                UPDATE bills
                SET paid_cents=?, status=?, paid_at=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (remaining_paid, new_status, new_paid_at, bid),
            )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        audit("bill_payment_deleted", "bill", bid, str(payment_id))

        return jsonify({
            "success": True,
            "paid_cents": remaining_paid,
            "status": new_status,
        })

    @app.post("/api/bills/<int:bid>/void")
    @require_roles("Admin", "Editor")
    def bill_void(bid):
        conn = get_db()

        exists = conn.execute(
            "SELECT 1 FROM bills WHERE id=? AND is_deleted=0",
            (bid,),
        ).fetchone()

        if not exists:
            return jsonify({"error": "Not found"}), 404

        with conn:
            conn.execute(
                """
                UPDATE bills
                SET status='Void', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND is_deleted=0
                """,
                (bid,),
            )

        audit("bill_voided", "bill", bid)

        return jsonify({"success": True})

    @app.get("/api/bills/<int:bid>/print")
    @api_login_required
    def bill_print(bid):
        conn = get_db()

        row = conn.execute(
            "SELECT * FROM bills WHERE id=? AND is_deleted=0",
            (bid,),
        ).fetchone()

        if not row:
            return "Bill not found", 404

        bill = row_to_dict(row)

        items = rows_to_list(conn.execute(
            "SELECT * FROM bill_items WHERE bill_id=? ORDER BY id",
            (bid,),
        ).fetchall())

        settings = load_settings()

        return generate_print_bill(bill, items, settings)

    @app.get("/api/bills/export")
    @api_login_required
    def bills_export():
        conn = get_db()

        rows = conn.execute(
            "SELECT * FROM bills WHERE is_deleted=0 ORDER BY created_at DESC"
        ).fetchall()

        total_billed_cents = sum(
            r["total_cents"] for r in rows if r["status"] != "Void"
        )
        total_paid_cents = sum(
            r["paid_cents"] for r in rows if r["status"] != "Void"
        )
        total_outstanding_cents = sum(
            (r["total_cents"] - r["paid_cents"])
            for r in rows
            if r["status"] != "Void"
        )

        data_rows = []

        for r in rows:
            data_rows.append([
                r["bill_number"],
                r["payee_name"],
                r["payee_email"],
                r["bill_category"],
                r["bill_date"],
                r["due_date"],
                cents_to_plain(r["subtotal_cents"]),
                cents_to_plain(r["discount_cents"]),
                cents_to_plain(r["tax_cents"]),
                cents_to_plain(r["total_cents"]),
                cents_to_plain(r["paid_cents"]),
                cents_to_plain(r["total_cents"] - r["paid_cents"]),
                r["status"],
            ])

        data_rows.append([])
        data_rows.append([
            "SUMMARY", "", "", "", "", "", "", "", "",
            cents_to_plain(total_billed_cents),
            cents_to_plain(total_paid_cents),
            cents_to_plain(total_outstanding_cents),
            "",
        ])

        return csv_response(
            "bills.csv",
            data_rows,
            [
                "Bill #",
                "Payee",
                "Email",
                "Category",
                "Bill Date",
                "Due Date",
                "Subtotal",
                "Discount",
                "Tax",
                "Total",
                "Paid",
                "Balance",
                "Status",
            ],
        )

    # -----------------------------------------------------------------------
    # Expenses
    # -----------------------------------------------------------------------

    @app.get("/api/expenses")
    @api_login_required
    def expenses_list():
        q = str(request.args.get("q") or "").strip()
        category = str(request.args.get("category") or "").strip()
        member = str(request.args.get("member") or "").strip()
        member_id = request.args.get("member_id")
        account_id = request.args.get("account_id")

        date_from = parse_iso_date(request.args.get("from"), "from", allow_none=True)
        date_to = parse_iso_date(request.args.get("to"), "to", allow_none=True)

        limit, offset = get_pagination()

        sql = "SELECT * FROM expenses WHERE is_deleted=0"
        params = []

        if q:
            like = f"%{escape_like(q)}%"
            sql += """ AND (title LIKE ? ESCAPE '\\' OR store LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\' OR member LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')"""
            params.extend([like, like, like, like, like])

        if category:
            sql += " AND category=?"
            params.append(category)

        if member:
            sql += " AND member=?"
            params.append(member)

        if member_id:
            clean_mid = parse_int(member_id, "member_id", minimum=1)
            sql += " AND member_id=?"
            params.append(clean_mid)

        if account_id:
            clean_aid = parse_int(account_id, "account_id", minimum=1)
            sql += " AND account_id=?"
            params.append(clean_aid)

        if date_from:
            sql += " AND expense_date >= ?"
            params.append(date_from)

        if date_to:
            sql += " AND expense_date <= ?"
            params.append(date_to)

        conn = get_db()

        agg = conn.execute(
            f"SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_cents), 0) AS sum_cents FROM ({sql})",
            params,
        ).fetchone()

        sql += " ORDER BY expense_date DESC, created_at DESC LIMIT ? OFFSET ?"

        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        items = rows_to_list(rows)

        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

        return jsonify({
            "items": items,
            "total": agg["cnt"],
            "filtered_total_cents": agg["sum_cents"],
            "filtered_total": cents_to_float(agg["sum_cents"]),
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/expenses")
    @require_roles("Admin", "Editor")
    def expense_create():
        data = json_payload()

        with get_db() as conn:
            expense_id = insert_expense(conn, data, require_active_refs=True)

        audit("expense_created", "expense", expense_id)

        return jsonify({"success": True, "id": expense_id})

    @app.get("/api/expenses/<int:eid>")
    @api_login_required
    def expense_detail(eid):
        conn = get_db()

        row = conn.execute(
            "SELECT * FROM expenses WHERE id=? AND is_deleted=0",
            (eid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        expense = row_to_dict(row)
        expense["amount"] = cents_to_float(expense["amount_cents"])

        splits = rows_to_list(conn.execute(
            "SELECT * FROM expense_splits WHERE expense_id=?", (eid,)
        ).fetchall())
        for s in splits:
            s["amount"] = cents_to_float(s["amount_cents"])
        expense["splits"] = splits

        return jsonify(expense)

    @app.put("/api/expenses/<int:eid>")
    @require_roles("Admin", "Editor")
    def expense_update(eid):
        data = json_payload()
        conn = get_db()

        row = conn.execute(
            "SELECT * FROM expenses WHERE id=? AND is_deleted=0",
            (eid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        clearing_member = (
            "member_id" in data
            and data["member_id"] in (None, "")
            and "member" not in data
        )

        # Merge with existing values so partial updates (e.g. only notes)
        # don't wipe fields the client omitted.
        merged = {
            "title": data.get("title", row["title"]),
            "amount": data.get("amount", cents_to_float(row["amount_cents"])),
            "category": data.get("category", row["category"]),
            "member": "" if clearing_member else data.get("member", row["member"]),
            "member_id": (
                data["member_id"] if "member_id" in data
                # An explicit member *name* re-resolves the link by name; keep
                # the stored id only when the name was left untouched too.
                else (row["member_id"] if "member" not in data else None)
            ),
            "notes": data.get("notes", row["notes"]),
            "store": data.get("store", row["store"]),
            "receipt_ref": data.get("receipt_ref", row["receipt_ref"]),
            "tags": data.get("tags", row["tags"]),
            "account_id": data.get("account_id", row["account_id"]),
            "original_currency": data.get("original_currency", row["original_currency"]),
            "exchange_rate": data.get("exchange_rate", row["exchange_rate"]),
        }
        if "original_amount_cents" in data:
            merged["original_amount_cents"] = data["original_amount_cents"]
        elif "original_amount" in data:
            merged["original_amount"] = data["original_amount"]
        else:
            merged["original_amount_cents"] = row["original_amount_cents"]

        merged.setdefault("expense_date", data.get("expense_date", row["expense_date"]))
        account_changed = "account_id" in data and str(data.get("account_id") or "") != str(row["account_id"] or "")
        member_changed = (
            ("member_id" in data and str(data.get("member_id") or "") != str(row["member_id"] or ""))
            or ("member" in data and data.get("member") != row["member"])
        )
        payload = parse_transaction_payload(
            merged,
            "expense_date",
            require_active_account=account_changed,
            require_active_member=member_changed,
        )

        # Only touch splits when the client actually sent them. Treating an
        # absent key as `[]` used to delete every split on partial updates.
        has_splits = "splits" in data
        splits = data.get("splits") if has_splits else None

        if has_splits:
            if not isinstance(splits, list):
                raise ValidationError("splits must be a list")

        if has_splits and splits:
            split_total = 0
            for s in splits:
                split_total += parse_amount_from_data(s, "amount")
            if split_total != payload["amount_cents"]:
                raise ValidationError("Sum of splits must equal total expense amount")

        with conn:
            conn.execute(
                """
                UPDATE expenses
                SET title=?, category=?, amount_cents=?, expense_date=?, store=?,
                    receipt_ref=?, notes=?, member=?, member_id=?, account_id=?, tags=?,
                    original_currency=?, exchange_rate=?, original_amount_cents=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    payload["title"],
                    payload["category"],
                    payload["amount_cents"],
                    payload["date"],
                    str(merged.get("store") or "").strip(),
                    str(merged.get("receipt_ref") or "").strip(),
                    payload["notes"],
                    payload["member"],
                    payload["member_id"],
                    payload["account_id"],
                    payload["tags"],
                    payload["original_currency"],
                    payload["exchange_rate"],
                    payload["original_amount_cents"],
                    eid,
                ),
            )

            # Splits are only replaced when the client actually sent them;
            # a partial update leaves them untouched.
            if has_splits:
                conn.execute("DELETE FROM expense_splits WHERE expense_id=?", (eid,))
                for s in splits:
                    s_member, s_member_id = _resolve_split_member(
                        s, payload["member"], payload["member_id"]
                    )
                    conn.execute(
                        """
                        INSERT INTO expense_splits (expense_id, category, amount_cents, member, member_id, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            eid,
                            str(s.get("category") or payload["category"]).strip() or "Other",
                            parse_amount_from_data(s, "amount"),
                            s_member,
                            s_member_id,
                            str(s.get("notes") or "").strip(),
                        ),
                    )

        audit("expense_updated", "expense", eid)

        return jsonify({"success": True})

    @app.delete("/api/expenses/<int:eid>")
    @require_roles("Admin", "Editor")
    def expense_delete(eid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM expenses WHERE id=? AND is_deleted=0", (eid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute(
                "UPDATE expenses SET is_deleted=1 WHERE id=?",
                (eid,),
            )

        audit("expense_deleted", "expense", eid)

        return jsonify({"success": True})

    @app.get("/api/expenses/export")
    @api_login_required
    def expenses_export():
        conn = get_db()

        rows = conn.execute(
            "SELECT * FROM expenses WHERE is_deleted=0 ORDER BY expense_date DESC"
        ).fetchall()

        total_cents = sum(r["amount_cents"] for r in rows)

        data_rows = []

        for r in rows:
            data_rows.append([
                r["id"],
                r["title"],
                r["category"],
                cents_to_plain(r["amount_cents"]),
                r["expense_date"],
                r["store"],
                r["receipt_ref"],
                r["member"],
                r["tags"],
                r["notes"],
            ])

        data_rows.append([])
        data_rows.append(["", "TOTAL", "", cents_to_plain(total_cents), "", "", "", "", "", ""])

        return csv_response(
            "expenses.csv",
            data_rows,
            [
                "ID",
                "Title",
                "Category",
                "Amount",
                "Date",
                "Store / Vendor",
                "Receipt Ref",
                "Family Member",
                "Tags",
                "Notes",
            ],
        )

    # -----------------------------------------------------------------------
    # Income
    # -----------------------------------------------------------------------

    @app.get("/api/income")
    @api_login_required
    def income_list():
        q = str(request.args.get("q") or "").strip()
        category = str(request.args.get("category") or "").strip()
        member = str(request.args.get("member") or "").strip()
        member_id = request.args.get("member_id")
        account_id = request.args.get("account_id")

        date_from = parse_iso_date(request.args.get("from"), "from", allow_none=True)
        date_to = parse_iso_date(request.args.get("to"), "to", allow_none=True)

        limit, offset = get_pagination()

        sql = "SELECT * FROM income WHERE is_deleted=0"
        params = []

        if q:
            like = f"%{escape_like(q)}%"
            sql += """ AND (title LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\' OR member LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')"""
            params.extend([like, like, like, like, like])

        if category:
            sql += " AND category=?"
            params.append(category)

        if member:
            sql += " AND member=?"
            params.append(member)

        if member_id:
            clean_mid = parse_int(member_id, "member_id", minimum=1)
            sql += " AND member_id=?"
            params.append(clean_mid)

        if account_id:
            clean_aid = parse_int(account_id, "account_id", minimum=1)
            sql += " AND account_id=?"
            params.append(clean_aid)

        if date_from:
            sql += " AND income_date >= ?"
            params.append(date_from)

        if date_to:
            sql += " AND income_date <= ?"
            params.append(date_to)

        conn = get_db()

        agg = conn.execute(
            f"SELECT COUNT(*) AS cnt, COALESCE(SUM(amount_cents), 0) AS sum_cents FROM ({sql})",
            params,
        ).fetchone()

        sql += " ORDER BY income_date DESC, created_at DESC LIMIT ? OFFSET ?"

        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        items = rows_to_list(rows)

        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

        return jsonify({
            "items": items,
            "total": agg["cnt"],
            "filtered_total_cents": agg["sum_cents"],
            "filtered_total": cents_to_float(agg["sum_cents"]),
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/income")
    @require_roles("Admin", "Editor")
    def income_create():
        data = json_payload()

        with get_db() as conn:
            income_id = insert_income(conn, data, require_active_refs=True)

        audit("income_created", "income", income_id)

        return jsonify({"success": True, "id": income_id})

    @app.get("/api/income/<int:iid>")
    @api_login_required
    def income_detail(iid):
        conn = get_db()

        row = conn.execute(
            "SELECT * FROM income WHERE id=? AND is_deleted=0",
            (iid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        income_item = row_to_dict(row)
        income_item["amount"] = cents_to_float(income_item["amount_cents"])

        splits = rows_to_list(conn.execute(
            "SELECT * FROM income_splits WHERE income_id=?", (iid,)
        ).fetchall())
        for s in splits:
            s["amount"] = cents_to_float(s["amount_cents"])
        income_item["splits"] = splits

        return jsonify(income_item)

    @app.put("/api/income/<int:iid>")
    @require_roles("Admin", "Editor")
    def income_update(iid):
        data = json_payload()
        conn = get_db()

        row = conn.execute(
            "SELECT * FROM income WHERE id=? AND is_deleted=0",
            (iid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        clearing_member = (
            "member_id" in data
            and data["member_id"] in (None, "")
            and "member" not in data
        )

        # Merge with existing values so partial updates (e.g. only notes)
        # don't wipe fields the client omitted.
        merged = {
            "title": data.get("title", row["title"]),
            "amount": data.get("amount", cents_to_float(row["amount_cents"])),
            "category": data.get("category", row["category"]),
            "member": "" if clearing_member else data.get("member", row["member"]),
            "member_id": (
                data["member_id"] if "member_id" in data
                else (row["member_id"] if "member" not in data else None)
            ),
            "notes": data.get("notes", row["notes"]),
            "source": data.get("source", row["source"]),
            "tags": data.get("tags", row["tags"]),
            "account_id": data.get("account_id", row["account_id"]),
            "original_currency": data.get("original_currency", row["original_currency"]),
            "exchange_rate": data.get("exchange_rate", row["exchange_rate"]),
        }
        if "original_amount_cents" in data:
            merged["original_amount_cents"] = data["original_amount_cents"]
        elif "original_amount" in data:
            merged["original_amount"] = data["original_amount"]
        else:
            merged["original_amount_cents"] = row["original_amount_cents"]

        merged.setdefault("income_date", data.get("income_date", row["income_date"]))
        account_changed = "account_id" in data and str(data.get("account_id") or "") != str(row["account_id"] or "")
        member_changed = (
            ("member_id" in data and str(data.get("member_id") or "") != str(row["member_id"] or ""))
            or ("member" in data and data.get("member") != row["member"])
        )
        payload = parse_transaction_payload(
            merged,
            "income_date",
            require_active_account=account_changed,
            require_active_member=member_changed,
        )

        # Only touch splits when the client actually sent them.
        has_splits = "splits" in data
        splits = data.get("splits") if has_splits else None

        if has_splits:
            if not isinstance(splits, list):
                raise ValidationError("splits must be a list")

        if has_splits and splits:
            split_total = 0
            for s in splits:
                split_total += parse_amount_from_data(s, "amount")
            if split_total != payload["amount_cents"]:
                raise ValidationError("Sum of splits must equal total income amount")

        with conn:
            conn.execute(
                """
                UPDATE income
                SET title=?, category=?, amount_cents=?, income_date=?, source=?,
                    notes=?, member=?, member_id=?, account_id=?, tags=?,
                    original_currency=?, exchange_rate=?, original_amount_cents=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    payload["title"],
                    payload["category"],
                    payload["amount_cents"],
                    payload["date"],
                    str(merged.get("source") or "").strip(),
                    payload["notes"],
                    payload["member"],
                    payload["member_id"],
                    payload["account_id"],
                    payload["tags"],
                    payload["original_currency"],
                    payload["exchange_rate"],
                    payload["original_amount_cents"],
                    iid,
                ),
            )

            # Splits are only replaced when the client actually sent them;
            # a partial update leaves them untouched.
            if has_splits:
                conn.execute("DELETE FROM income_splits WHERE income_id=?", (iid,))
                for s in splits:
                    s_member, s_member_id = _resolve_split_member(
                        s, payload["member"], payload["member_id"]
                    )
                    conn.execute(
                        """
                        INSERT INTO income_splits (income_id, category, amount_cents, member, member_id, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            iid,
                            str(s.get("category") or payload["category"]).strip() or "Other",
                            parse_amount_from_data(s, "amount"),
                            s_member,
                            s_member_id,
                            str(s.get("notes") or "").strip(),
                        ),
                    )

        audit("income_updated", "income", iid)

        return jsonify({"success": True})

    @app.delete("/api/income/<int:iid>")
    @require_roles("Admin", "Editor")
    def income_delete(iid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM income WHERE id=? AND is_deleted=0", (iid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute(
                "UPDATE income SET is_deleted=1 WHERE id=?",
                (iid,),
            )

        audit("income_deleted", "income", iid)

        return jsonify({"success": True})

    @app.get("/api/income/export")
    @api_login_required
    def income_export():
        conn = get_db()

        rows = conn.execute(
            "SELECT * FROM income WHERE is_deleted=0 ORDER BY income_date DESC"
        ).fetchall()

        total_cents = sum(r["amount_cents"] for r in rows)

        data_rows = []

        for r in rows:
            data_rows.append([
                r["id"],
                r["title"],
                r["category"],
                cents_to_plain(r["amount_cents"]),
                r["income_date"],
                r["source"],
                r["member"],
                r["tags"],
                r["notes"],
            ])

        data_rows.append([])
        data_rows.append(["", "TOTAL", "", cents_to_plain(total_cents), "", "", "", "", ""])

        return csv_response(
            "income.csv",
            data_rows,
            [
                "ID",
                "Title",
                "Category",
                "Amount",
                "Date",
                "Source",
                "Family Member",
                "Tags",
                "Notes",
            ],
        )

    # -----------------------------------------------------------------------
    # Budgets
    # -----------------------------------------------------------------------

    @app.get("/api/budgets")
    @api_login_required
    def budgets_list():
        period = request.args.get("period", "")
        category = request.args.get("category", "")

        sql = "SELECT * FROM budgets WHERE is_active=1"
        params = []

        if period:
            if period not in {"monthly", "yearly"}:
                raise ValidationError("Invalid period")

            sql += " AND period=?"
            params.append(period)

        if category:
            sql += " AND category=?"
            params.append(category)

        sql += " ORDER BY category, member"

        conn = get_db()

        items = rows_to_list(conn.execute(sql, params).fetchall())

        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

        return jsonify(items)

    @app.post("/api/budgets")
    @require_roles("Admin", "Editor")
    def budget_create():
        data = json_payload()

        category = str(data.get("category") or "").strip()
        period = str(data.get("period") or "monthly").strip()

        if not category:
            raise ValidationError("category is required")

        if period not in {"monthly", "yearly"}:
            raise ValidationError("period must be monthly or yearly")

        amount_cents = parse_amount_from_data(data, "amount", allow_zero=False)
        member = str(data.get("member") or "").strip()

        conn = get_db()

        # Guard against two active budgets for the same (category, member,
        # period). The IMMEDIATE transaction closes the check-then-insert
        # race; a matching partial UNIQUE index backs this up at the DB level
        # where the data allows.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")

        try:
            exists = conn.execute(
                "SELECT 1 FROM budgets WHERE category=? AND member=? AND period=? AND is_active=1",
                (category, member, period),
            ).fetchone()

            if exists:
                conn.rollback()
                label = f"'{member}' for {category} ({period})" if member else f"{category} ({period})"
                raise ValidationError(f"An active {period} budget already exists for {label}")

            cur = conn.execute(
                """
                INSERT INTO budgets (category, member, period, amount_cents, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    category,
                    member,
                    period,
                    amount_cents,
                    str(data.get("notes") or ""),
                ),
            )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        audit("budget_created", "budget", cur.lastrowid, category)

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.put("/api/budgets/<int:bud_id>")
    @require_roles("Admin", "Editor")
    def budget_update(bud_id):
        data = json_payload()

        category = str(data.get("category") or "").strip()
        period = str(data.get("period") or "monthly").strip()

        if not category:
            raise ValidationError("category is required")

        if period not in {"monthly", "yearly"}:
            raise ValidationError("period must be monthly or yearly")

        amount_cents = parse_amount_from_data(data, "amount", allow_zero=False)
        member = str(data.get("member") or "").strip()
        is_active = 1 if data.get("is_active", True) else 0

        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM budgets WHERE id=?", (bud_id,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            if is_active:
                clash = conn.execute(
                    """
                    SELECT 1 FROM budgets
                    WHERE category=? AND member=? AND period=? AND is_active=1 AND id != ?
                    """,
                    (category, member, period, bud_id),
                ).fetchone()

                if clash:
                    label = f"'{member}' for {category} ({period})" if member else f"{category} ({period})"
                    raise ValidationError(
                        f"An active {period} budget already exists for {label}"
                    )

            conn.execute(
                """
                UPDATE budgets
                SET category=?, member=?, period=?, amount_cents=?, notes=?, is_active=?
                WHERE id=?
                """,
                (
                    category,
                    member,
                    period,
                    amount_cents,
                    str(data.get("notes") or ""),
                    is_active,
                    bud_id,
                ),
            )

        audit("budget_updated", "budget", bud_id)

        return jsonify({"success": True})

    @app.delete("/api/budgets/<int:bud_id>")
    @require_roles("Admin", "Editor")
    def budget_delete(bud_id):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM budgets WHERE id=?", (bud_id,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("DELETE FROM budgets WHERE id=?", (bud_id,))

        audit("budget_deleted", "budget", bud_id)

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Goals
    # -----------------------------------------------------------------------

    @app.get("/api/goals")
    @api_login_required
    def goals_list():
        conn = get_db()

        items = rows_to_list(conn.execute(
            "SELECT * FROM goals WHERE is_active=1 ORDER BY name"
        ).fetchall())

        for item in items:
            item["target"] = cents_to_float(item["target_cents"])
            item["current"] = cents_to_float(item["current_cents"])
            item["progress_pct"] = round(
                (item["current_cents"] / item["target_cents"] * 100), 1
            ) if item["target_cents"] > 0 else 0

        return jsonify(items)

    @app.post("/api/goals")
    @require_roles("Admin", "Editor")
    def goal_create():
        data = json_payload()

        name = str(data.get("name") or "").strip()

        if not name:
            raise ValidationError("Goal name is required")

        target_cents = parse_amount_from_data(data, "target", allow_zero=False)

        current_cents = parse_amount_from_data(
            data,
            field="current",
            default_cents=0,
            allow_zero=True,
        )

        target_date = parse_iso_date(data.get("target_date"), "target_date", allow_none=True)
        allocation_pct = parse_percent(data.get("allocation_pct", 0), "allocation_pct")

        with get_db() as conn:
            existing_alloc = conn.execute(
                "SELECT COALESCE(SUM(allocation_pct), 0) FROM goals WHERE is_active=1"
            ).fetchone()[0]
            if float(existing_alloc) + allocation_pct > 100.0 + 1e-6:
                raise ValidationError(
                    f"Total auto-allocation across active goals cannot exceed 100% (currently {existing_alloc:g}%)"
                )

            cur = conn.execute(
                """
                INSERT INTO goals (name, target_cents, current_cents, target_date, allocation_pct, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    target_cents,
                    current_cents,
                    target_date,
                    allocation_pct,
                    str(data.get("notes") or ""),
                ),
            )

        audit("goal_created", "goal", name)

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.put("/api/goals/<int:gid>")
    @require_roles("Admin", "Editor")
    def goal_update(gid):
        data = json_payload()

        name = str(data.get("name") or "").strip()

        if not name:
            raise ValidationError("Goal name is required")

        target_cents = parse_amount_from_data(data, "target", allow_zero=False)

        current_cents = parse_amount_from_data(
            data,
            field="current",
            default_cents=0,
            allow_zero=True,
        )

        target_date = parse_iso_date(data.get("target_date"), "target_date", allow_none=True)
        allocation_pct = parse_percent(data.get("allocation_pct", 0), "allocation_pct")
        is_active = 1 if data.get("is_active", True) else 0

        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM goals WHERE id=?", (gid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            if is_active:
                other_alloc = conn.execute(
                    "SELECT COALESCE(SUM(allocation_pct), 0) FROM goals WHERE is_active=1 AND id != ?",
                    (gid,),
                ).fetchone()[0]
                if float(other_alloc) + allocation_pct > 100.0 + 1e-6:
                    raise ValidationError(
                        f"Total auto-allocation across active goals cannot exceed 100% (other goals use {other_alloc:g}%)"
                    )

            conn.execute(
                """
                UPDATE goals
                SET name=?, target_cents=?, current_cents=?, target_date=?, allocation_pct=?, notes=?, is_active=?
                WHERE id=?
                """,
                (
                    name,
                    target_cents,
                    current_cents,
                    target_date,
                    allocation_pct,
                    str(data.get("notes") or ""),
                    is_active,
                    gid,
                ),
            )

        audit("goal_updated", "goal", gid)

        return jsonify({"success": True})

    @app.post("/api/goals/<int:gid>/contribute")
    @require_roles("Admin", "Editor")
    def goal_contribute(gid):
        data = json_payload()

        amount_cents = parse_amount_from_data(data, "amount", allow_zero=False)

        conn = get_db()

        goal = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()

        if not goal:
            return jsonify({"error": "Not found"}), 404

        new_current = goal["current_cents"] + amount_cents

        with conn:
            conn.execute(
                "UPDATE goals SET current_cents=? WHERE id=?",
                (new_current, gid),
            )

        audit("goal_contribution", "goal", gid, str(amount_cents))

        return jsonify({"success": True})

    @app.delete("/api/goals/<int:gid>")
    @require_roles("Admin", "Editor")
    def goal_delete(gid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM goals WHERE id=?", (gid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("UPDATE goals SET is_active=0 WHERE id=?", (gid,))

        audit("goal_deactivated", "goal", gid)

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Recurring rules
    # -----------------------------------------------------------------------

    @app.get("/api/recurring")
    @api_login_required
    def recurring_list():
        conn = get_db()

        items = rows_to_list(conn.execute(
            "SELECT * FROM recurring_rules ORDER BY next_run_date"
        ).fetchall())

        for item in items:
            try:
                item["payload"] = json.loads(item["payload"])
            except Exception:
                item["payload"] = {}

        return jsonify(items)

    @app.post("/api/recurring")
    @require_roles("Admin", "Editor")
    def recurring_create():
        data = json_payload()

        name = str(data.get("name") or "").strip()
        entity_type = str(data.get("entity_type") or "").strip()
        frequency = str(data.get("frequency") or "monthly").strip()
        interval_value = parse_int(
            data.get("interval_value", 1),
            "interval_value",
            default=1,
            minimum=1,
        )
        next_run_date = parse_iso_date(data.get("next_run_date"), "next_run_date")
        payload = data.get("payload") or {}

        if not name:
            raise ValidationError("name is required")

        if entity_type not in {"expense", "income", "bill"}:
            raise ValidationError("entity_type must be expense, income, or bill")

        if frequency not in {"daily", "weekly", "monthly", "yearly"}:
            raise ValidationError("Invalid frequency")

        if not isinstance(payload, dict):
            raise ValidationError("payload must be an object")

        is_active = 1 if data.get("is_active", True) else 0

        with get_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO recurring_rules (
                    name,
                    entity_type,
                    payload,
                    frequency,
                    interval_value,
                    next_run_date,
                    is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    entity_type,
                    json.dumps(payload),
                    frequency,
                    interval_value,
                    next_run_date,
                    is_active,
                ),
            )

        audit("recurring_created", "recurring", name)

        return jsonify({"success": True, "id": cur.lastrowid})

    @app.put("/api/recurring/<int:rid>")
    @require_roles("Admin", "Editor")
    def recurring_update(rid):
        data = json_payload()

        name = str(data.get("name") or "").strip()
        entity_type = str(data.get("entity_type") or "").strip()
        frequency = str(data.get("frequency") or "monthly").strip()
        interval_value = parse_int(
            data.get("interval_value", 1),
            "interval_value",
            default=1,
            minimum=1,
        )
        next_run_date = parse_iso_date(data.get("next_run_date"), "next_run_date")
        payload = data.get("payload") or {}
        is_active = 1 if data.get("is_active", True) else 0

        if not name:
            raise ValidationError("name is required")

        if entity_type not in {"expense", "income", "bill"}:
            raise ValidationError("entity_type must be expense, income, or bill")

        if frequency not in {"daily", "weekly", "monthly", "yearly"}:
            raise ValidationError("Invalid frequency")

        if not isinstance(payload, dict):
            raise ValidationError("payload must be an object")

        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM recurring_rules WHERE id=?", (rid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute(
                """
                UPDATE recurring_rules
                SET name=?, entity_type=?, payload=?, frequency=?, interval_value=?,
                    next_run_date=?, is_active=?
                WHERE id=?
                """,
                (
                    name,
                    entity_type,
                    json.dumps(payload),
                    frequency,
                    interval_value,
                    next_run_date,
                    is_active,
                    rid,
                ),
            )

        audit("recurring_updated", "recurring", rid)

        return jsonify({"success": True})

    @app.delete("/api/recurring/<int:rid>")
    @require_roles("Admin", "Editor")
    def recurring_delete(rid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM recurring_rules WHERE id=?", (rid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("DELETE FROM recurring_rules WHERE id=?", (rid,))

        audit("recurring_deleted", "recurring", rid)

        return jsonify({"success": True})

    @app.post("/api/recurring/run")
    @require_roles("Admin", "Editor")
    def recurring_run():
        today = date.today().isoformat()
        created = []
        failed = []

        with get_db() as conn:
            rules = conn.execute(
                """
                SELECT * FROM recurring_rules
                WHERE is_active=1 AND next_run_date <= ?
                ORDER BY next_run_date
                """,
                (today,),
            ).fetchall()

            for rule_row in rules:
                rule = dict(rule_row)

                # Each rule runs inside a SAVEPOINT so one broken rule cannot
                # abort or roll back the work of the other rules.
                conn.execute("SAVEPOINT recurring_rule")

                try:
                    # Catch-up guard: do not create more than 24 items per rule in one run.
                    for _ in range(24):
                        if rule["next_run_date"] > today:
                            break

                        payload = {}

                        try:
                            payload = json.loads(rule["payload"])
                        except Exception:
                            payload = {}

                        if not isinstance(payload, dict):
                            payload = {}

                        # Date keys are removed up front and re-stamped for
                        # every iteration, otherwise setdefault() would pin
                        # every catch-up item to the first occurrence date.
                        payload.pop("expense_date", None)
                        payload.pop("income_date", None)
                        payload.pop("bill_date", None)
                        payload.pop("due_date", None)

                        if rule["entity_type"] == "expense":
                            payload["expense_date"] = rule["next_run_date"]
                            entity_id = insert_expense(conn, payload, require_active_refs=True)
                            created.append({"rule": rule["name"], "entity_type": "expense", "id": entity_id})

                        elif rule["entity_type"] == "income":
                            payload["income_date"] = rule["next_run_date"]
                            entity_id = insert_income(conn, payload, require_active_refs=True)
                            created.append({"rule": rule["name"], "entity_type": "income", "id": entity_id})

                        elif rule["entity_type"] == "bill":
                            due_offset = parse_int(
                                payload.get("due_days_offset", 0),
                                "due_days_offset",
                                default=0,
                                minimum=0,
                            )
                            payload["bill_date"] = rule["next_run_date"]
                            payload["due_date"] = (
                                date.fromisoformat(rule["next_run_date"])
                                + timedelta(days=due_offset)
                            ).isoformat()

                            # parse_bill_payload resolves payee_name from
                            # payee_id, matching manual bill creation.
                            parsed_bill = parse_bill_payload(payload, require_active_refs=True)
                            settings = load_settings()
                            bill_number = next_bill_number(conn, settings.get("bill_prefix", "BILL"))

                            entity_id = insert_bill(
                                conn,
                                parsed_bill,
                                bill_number,
                                paid_cents=0,
                                status="Pending",
                                paid_at=None,
                            )

                            created.append({"rule": rule["name"], "entity_type": "bill", "id": entity_id})

                        new_next_run_date = advance_recurring_date(
                            rule["next_run_date"],
                            rule["frequency"],
                            rule["interval_value"],
                        )

                        conn.execute(
                            """
                            UPDATE recurring_rules
                            SET next_run_date=?, last_run_date=?
                            WHERE id=?
                            """,
                            (new_next_run_date, rule["next_run_date"], rule["id"]),
                        )

                        rule["next_run_date"] = new_next_run_date

                    conn.execute("RELEASE SAVEPOINT recurring_rule")

                    notify(
                        title=f"Recurring rule processed: {rule['name']}",
                        body=f"Created recurring {rule['entity_type']} item(s).",
                        link="/api/recurring",
                    )

                except ValidationError as exc:
                    conn.execute("ROLLBACK TO SAVEPOINT recurring_rule")
                    conn.execute("RELEASE SAVEPOINT recurring_rule")
                    failed.append({"rule": rule["name"], "error": exc.message})
                    notify(
                        title=f"Recurring rule failed: {rule['name']}",
                        body=exc.message,
                        link="/api/recurring",
                    )

                except Exception as exc:
                    # Any unexpected error (constraint violation, bad payload
                    # shape, ...) is contained to this rule so the rest of the
                    # batch still lands.
                    conn.execute("ROLLBACK TO SAVEPOINT recurring_rule")
                    conn.execute("RELEASE SAVEPOINT recurring_rule")
                    current_app.logger.exception("Recurring rule failed: %s", rule["name"])
                    failed.append({"rule": rule["name"], "error": str(exc)})
                    notify(
                        title=f"Recurring rule failed: {rule['name']}",
                        body=str(exc),
                        link="/api/recurring",
                    )

        audit("recurring_run", "recurring", "", f"Created {len(created)} item(s), failed {len(failed)} rule(s)")

        return jsonify({
            "success": True,
            "created": created,
            "failed": failed,
        })

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------

    @app.get("/api/notifications")
    @api_login_required
    def notifications_list():
        limit, offset = get_pagination()
        conn = get_db()
        username = g.user["username"]

        visible = "(username = '' OR username = ?)"

        total = conn.execute(
            f"SELECT COUNT(*) FROM notifications WHERE {visible}",
            (username,),
        ).fetchone()[0]

        unread_count = conn.execute(
            f"SELECT COUNT(*) FROM notifications WHERE is_read=0 AND {visible}",
            (username,),
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT * FROM notifications
            WHERE {visible}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (username, limit, offset),
        ).fetchall()

        return jsonify({
            "items": rows_to_list(rows),
            "total": total,
            "unread_count": unread_count,
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/notifications/<int:nid>/read")
    @api_login_required
    def notification_mark_read(nid):
        with get_db() as conn:
            conn.execute(
                "UPDATE notifications SET is_read=1 WHERE id=? AND (username = '' OR username = ?)",
                (nid, g.user["username"]),
            )

        return jsonify({"success": True})

    @app.post("/api/notifications/read-all")
    @api_login_required
    def notifications_mark_all_read():
        with get_db() as conn:
            conn.execute(
                "UPDATE notifications SET is_read=1 WHERE is_read=0 AND (username = '' OR username = ?)",
                (g.user["username"],),
            )

        return jsonify({"success": True})

    # -----------------------------------------------------------------------
    # Dashboard
    # -----------------------------------------------------------------------

    @app.get("/api/dashboard")
    @api_login_required
    def dashboard():
        conn = get_db()

        today = date.today()
        today_str = today.isoformat()
        this_month = today.strftime("%Y-%m")

        month_start = today.replace(day=1)
        next_month = add_months(month_start, 1)

        year_start = date(today.year, 1, 1)
        next_year = date(today.year + 1, 1, 1)

        month_start_iso = month_start.isoformat()
        next_month_iso = next_month.isoformat()
        year_start_iso = year_start.isoformat()
        next_year_iso = next_year.isoformat()

        monthly_income_cents = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM income
            WHERE is_deleted=0 AND income_date >= ? AND income_date < ?
            """,
            (month_start_iso, next_month_iso),
        ).fetchone()[0]

        monthly_expense_cents = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM expenses
            WHERE is_deleted=0 AND expense_date >= ? AND expense_date < ?
            """,
            (month_start_iso, next_month_iso),
        ).fetchone()[0]

        monthly_bill_payment_cents = conn.execute(
            f"""
            SELECT COALESCE(SUM(bp.amount_cents), 0)
            {VALID_BILL_PAYMENT_FROM}
            WHERE bp.payment_date >= ? AND bp.payment_date < ?
            """,
            (month_start_iso, next_month_iso),
        ).fetchone()[0]

        monthly_outflow_cents = monthly_expense_cents + monthly_bill_payment_cents
        monthly_savings_cents = monthly_income_cents - monthly_outflow_cents

        savings_rate = round(
            (monthly_savings_cents / monthly_income_cents * 100), 1
        ) if monthly_income_cents > 0 else 0

        pending_bills_cents = conn.execute(
            """
            SELECT COALESCE(SUM(total_cents - paid_cents), 0)
            FROM bills
            WHERE is_deleted=0 AND status IN ('Pending', 'Partially Paid')
            """,
        ).fetchone()[0]

        overdue_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM bills
            WHERE is_deleted=0
              AND status IN ('Pending', 'Partially Paid')
              AND due_date < ?
            """,
            (today_str,),
        ).fetchone()[0]

        overdue_amount_cents = conn.execute(
            """
            SELECT COALESCE(SUM(total_cents - paid_cents), 0)
            FROM bills
            WHERE is_deleted=0
              AND status IN ('Pending', 'Partially Paid')
              AND due_date < ?
            """,
            (today_str,),
        ).fetchone()[0]

        payee_count = conn.execute("SELECT COUNT(*) FROM payees").fetchone()[0]
        bill_count = conn.execute("SELECT COUNT(*) FROM bills WHERE is_deleted=0").fetchone()[0]
        income_count = conn.execute("SELECT COUNT(*) FROM income WHERE is_deleted=0").fetchone()[0]
        expense_count = conn.execute("SELECT COUNT(*) FROM expenses WHERE is_deleted=0").fetchone()[0]

        annual_income_cents = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM income
            WHERE is_deleted=0 AND income_date >= ? AND income_date < ?
            """,
            (year_start_iso, next_year_iso),
        ).fetchone()[0]

        annual_expense_cents = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM expenses
            WHERE is_deleted=0 AND expense_date >= ? AND expense_date < ?
            """,
            (year_start_iso, next_year_iso),
        ).fetchone()[0]

        annual_bill_payment_cents = conn.execute(
            f"""
            SELECT COALESCE(SUM(bp.amount_cents), 0)
            {VALID_BILL_PAYMENT_FROM}
            WHERE bp.payment_date >= ? AND bp.payment_date < ?
            """,
            (year_start_iso, next_year_iso),
        ).fetchone()[0]

        monthly = []
        current_month_start = today.replace(day=1)
        trend_start_iso = add_months(current_month_start, -5).isoformat()

        income_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                """
                SELECT strftime('%Y-%m', income_date) AS ym, SUM(amount_cents) AS total
                FROM income
                WHERE is_deleted=0 AND income_date >= ?
                GROUP BY ym
                """,
                (trend_start_iso,),
            ).fetchall()
        }

        expense_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                """
                SELECT strftime('%Y-%m', expense_date) AS ym, SUM(amount_cents) AS total
                FROM expenses
                WHERE is_deleted=0 AND expense_date >= ?
                GROUP BY ym
                """,
                (trend_start_iso,),
            ).fetchall()
        }

        bill_payment_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                f"""
                SELECT strftime('%Y-%m', bp.payment_date) AS ym, SUM(bp.amount_cents) AS total
                {VALID_BILL_PAYMENT_FROM}
                WHERE bp.payment_date >= ?
                GROUP BY ym
                """,
                (trend_start_iso,),
            ).fetchall()
        }

        for i in range(5, -1, -1):
            d = add_months(current_month_start, -i)
            m = d.strftime("%Y-%m")

            inc = income_by_month.get(m, 0)
            exp = expense_by_month.get(m, 0)
            bp = bill_payment_by_month.get(m, 0)

            total_out = exp + bp
            savings = inc - total_out

            monthly.append({
                "month": d.strftime("%b"),
                "income": cents_to_float(inc),
                "expenses": cents_to_float(total_out),
                "savings": cents_to_float(savings),
            })

        # Top categories include split allocations (an expense with splits
        # counts each split's category instead of its own — no double
        # counting) and bill payments, so recurring rent/utilities actually
        # show up in the breakdown.
        top_cats = rows_to_list(conn.execute(
            f"""
            SELECT category, SUM(cents) AS total_cents
            FROM (
                SELECT e.category AS category, SUM(e.amount_cents) AS cents
                FROM expenses e
                WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=?
                  AND NOT EXISTS (SELECT 1 FROM expense_splits es WHERE es.expense_id=e.id)
                GROUP BY e.category

                UNION ALL

                SELECT es.category AS category, SUM(es.amount_cents) AS cents
                FROM expense_splits es
                JOIN expenses e ON e.id = es.expense_id
                WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=?
                GROUP BY es.category

                UNION ALL

                SELECT b.bill_category AS category, SUM(bp.amount_cents) AS cents
                {VALID_BILL_PAYMENT_FROM}
                WHERE strftime('%Y-%m', bp.payment_date)=?
                GROUP BY b.bill_category
            )
            GROUP BY category
            ORDER BY total_cents DESC
            LIMIT 6
            """,
            (this_month, this_month, this_month),
        ).fetchall())

        for row in top_cats:
            row["total"] = cents_to_float(row.pop("total_cents"))

        recent_expenses = rows_to_list(conn.execute(
            """
            SELECT id, title, category, amount_cents, expense_date, store
            FROM expenses
            WHERE is_deleted=0
            ORDER BY created_at DESC
            LIMIT 6
            """,
        ).fetchall())

        for row in recent_expenses:
            row["amount"] = cents_to_float(row.pop("amount_cents"))

        recent_income = rows_to_list(conn.execute(
            """
            SELECT id, title, category, amount_cents, income_date, source
            FROM income
            WHERE is_deleted=0
            ORDER BY created_at DESC
            LIMIT 4
            """,
        ).fetchall())

        for row in recent_income:
            row["amount"] = cents_to_float(row.pop("amount_cents"))

        monthly_budgets = conn.execute(
            """
            SELECT category, member, amount_cents
            FROM budgets
            WHERE is_active=1 AND period='monthly'
            """
        ).fetchall()

        monthly_budget_cents = 0
        monthly_budget_actual_cents = 0

        if monthly_budgets:
            actual_by_category = {
                row["category"]: int(row["actual_cents"])
                for row in conn.execute(
                    f"""
                    SELECT category, SUM(cents) AS actual_cents
                    FROM (
                        SELECT e.category AS category, SUM(e.amount_cents) AS cents
                        FROM expenses e
                        WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=?
                          AND NOT EXISTS (SELECT 1 FROM expense_splits es WHERE es.expense_id=e.id)
                        GROUP BY e.category

                        UNION ALL

                        SELECT es.category AS category, SUM(es.amount_cents) AS cents
                        FROM expense_splits es
                        JOIN expenses e ON e.id = es.expense_id
                        WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=?
                        GROUP BY es.category

                        UNION ALL

                        SELECT b.bill_category AS category, SUM(bp.amount_cents) AS cents
                        {VALID_BILL_PAYMENT_FROM}
                        WHERE strftime('%Y-%m', bp.payment_date)=?
                        GROUP BY b.bill_category
                    )
                    GROUP BY category
                    """,
                    (this_month, this_month, this_month),
                ).fetchall()
            }

            member_actual_by_key = {
                (row["member"], row["category"]): int(row["cents"])
                for row in conn.execute(
                    """
                    SELECT member, category, SUM(cents) AS cents
                    FROM (
                        SELECT e.member AS member, e.category AS category, SUM(e.amount_cents) AS cents
                        FROM expenses e
                        WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=? AND e.member != ''
                          AND NOT EXISTS (SELECT 1 FROM expense_splits es WHERE es.expense_id=e.id)
                        GROUP BY e.member, e.category

                        UNION ALL

                        SELECT es.member AS member, es.category AS category, SUM(es.amount_cents) AS cents
                        FROM expense_splits es
                        JOIN expenses e ON e.id = es.expense_id
                        WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=? AND es.member != ''
                        GROUP BY es.member, es.category
                    )
                    GROUP BY member, category
                    """,
                    (this_month, this_month),
                ).fetchall()
            }

            household_budget_cats = {b["category"] for b in monthly_budgets if not b["member"]}

            for b in monthly_budgets:
                if not b["member"]:
                    monthly_budget_cents += int(b["amount_cents"])
                    monthly_budget_actual_cents += actual_by_category.get(b["category"], 0)
                elif b["category"] not in household_budget_cats:
                    monthly_budget_cents += int(b["amount_cents"])
                    monthly_budget_actual_cents += member_actual_by_key.get((b["member"], b["category"]), 0)

        active_goals = rows_to_list(conn.execute(
            "SELECT * FROM goals WHERE is_active=1 ORDER BY name LIMIT 5"
        ).fetchall())

        for goal in active_goals:
            goal["target"] = cents_to_float(goal["target_cents"])
            goal["current"] = cents_to_float(goal["current_cents"])
            goal["progress_pct"] = round(
                (goal["current_cents"] / goal["target_cents"] * 100), 1
            ) if goal["target_cents"] > 0 else 0

        health_score = 100
        recommendations = []

        if savings_rate < 0:
            health_score -= 30
            recommendations.append({
                "severity": "high",
                "title": "Spending is above income this month",
                "detail": "Review flexible categories and pause non-essential spending until cash flow is positive.",
            })
        elif savings_rate < 10:
            health_score -= 15
            recommendations.append({
                "severity": "medium",
                "title": "Savings rate is below 10%",
                "detail": "Try routing a small automatic transfer to savings after each paycheck.",
            })

        settings = load_settings()
        target_pct = float(settings.get("savings_target_pct", 20) or 20)
        if monthly_income_cents and savings_rate < target_pct:
            recommendations.append({
                "severity": "info",
                "title": "Savings target gap",
                "detail": f"Current savings rate is {savings_rate}%; household target is {target_pct:g}%.",
            })

        if overdue_count:
            health_score -= min(35, overdue_count * 7)
            recommendations.append({
                "severity": "high",
                "title": f"{overdue_count} overdue bill{'s' if overdue_count != 1 else ''}",
                "detail": "Prioritize overdue balances to avoid fees and service interruptions.",
            })

        if monthly_budget_cents and monthly_budget_actual_cents > monthly_budget_cents:
            health_score -= 15
            recommendations.append({
                "severity": "medium",
                "title": "Monthly budget exceeded",
                "detail": "Compare budget categories against actual spending and adjust envelopes for the next month.",
            })

        if not recommendations:
            recommendations.append({
                "severity": "success",
                "title": "Finances look steady",
                "detail": "Keep tracking transactions and review goals weekly to maintain momentum.",
            })

        health_score = max(0, min(100, health_score))

        return jsonify({
            "monthly_income": cents_to_float(monthly_income_cents),
            "monthly_expenses": cents_to_float(monthly_outflow_cents),
            "monthly_savings": cents_to_float(monthly_savings_cents),
            "savings_rate": savings_rate,
            "pending_bills": cents_to_float(pending_bills_cents),
            "overdue_count": overdue_count,
            "overdue_amount": cents_to_float(overdue_amount_cents),
            "payee_count": payee_count,
            "bill_count": bill_count,
            "income_count": income_count,
            "expense_count": expense_count,
            "annual_income": cents_to_float(annual_income_cents),
            "annual_expenses": cents_to_float(annual_expense_cents + annual_bill_payment_cents),
            "monthly": monthly,
            "top_cats": top_cats,
            "recent_expenses": recent_expenses,
            "recent_income": recent_income,
            "budget_summary": {
                "monthly_budget": cents_to_float(monthly_budget_cents),
                "monthly_actual": cents_to_float(monthly_budget_actual_cents),
                "monthly_remaining": cents_to_float(monthly_budget_cents - monthly_budget_actual_cents),
            },
            "goals": active_goals,
            "financial_health": {
                "score": health_score,
                "status": "strong" if health_score >= 80 else "watch" if health_score >= 60 else "needs_attention",
                "recommendations": recommendations,
            },
        })

    @app.get("/api/insights")
    @api_login_required
    def insights():
        dashboard_resp = dashboard()
        data = dashboard_resp.get_json()
        return jsonify({
            "financial_health": data["financial_health"],
            "budget_summary": data["budget_summary"],
            "top_categories": data["top_cats"],
            "monthly_trend": data["monthly"],
            "goals": data["goals"],
        })

    # -----------------------------------------------------------------------
    # Reports
    # -----------------------------------------------------------------------

    @app.get("/api/reports")
    @api_login_required
    def reports():
        year = str(request.args.get("year", str(date.today().year)))

        if not re.match(r"^\d{4}$", year):
            raise ValidationError("year must be YYYY")

        conn = get_db()

        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
        ]

        year_start_iso = f"{year}-01-01"
        next_year_start_iso = f"{int(year) + 1}-01-01"

        income_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                """
                SELECT strftime('%Y-%m', income_date) AS ym, SUM(amount_cents) AS total
                FROM income
                WHERE is_deleted=0 AND income_date >= ? AND income_date < ?
                GROUP BY ym
                """,
                (year_start_iso, next_year_start_iso),
            ).fetchall()
        }

        expense_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                """
                SELECT strftime('%Y-%m', expense_date) AS ym, SUM(amount_cents) AS total
                FROM expenses
                WHERE is_deleted=0 AND expense_date >= ? AND expense_date < ?
                GROUP BY ym
                """,
                (year_start_iso, next_year_start_iso),
            ).fetchall()
        }

        bill_payment_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                f"""
                SELECT strftime('%Y-%m', bp.payment_date) AS ym, SUM(bp.amount_cents) AS total
                {VALID_BILL_PAYMENT_FROM}
                WHERE bp.payment_date >= ? AND bp.payment_date < ?
                GROUP BY ym
                """,
                (year_start_iso, next_year_start_iso),
            ).fetchall()
        }

        monthly = []

        for m in range(1, 13):
            month_key = f"{year}-{str(m).zfill(2)}"

            inc = income_by_month.get(month_key, 0)
            exp = expense_by_month.get(month_key, 0)
            bp = bill_payment_by_month.get(month_key, 0)

            total_out = exp + bp
            savings = inc - total_out
            savings_rate = round((savings / inc * 100), 1) if inc > 0 else 0

            monthly.append({
                "month": month_names[m - 1],
                "income": cents_to_float(inc),
                "expenses": cents_to_float(total_out),
                "savings": cents_to_float(savings),
                "savings_rate": savings_rate,
            })

        expense_cats = rows_to_list(conn.execute(
            """
            SELECT category, SUM(amount_cents) AS total_cents
            FROM expenses
            WHERE is_deleted=0 AND strftime('%Y', expense_date)=?
            GROUP BY category
            ORDER BY total_cents DESC
            """,
            (year,),
        ).fetchall())

        income_cats = rows_to_list(conn.execute(
            """
            SELECT category, SUM(amount_cents) AS total_cents
            FROM income
            WHERE is_deleted=0 AND strftime('%Y', income_date)=?
            GROUP BY category
            ORDER BY total_cents DESC
            """,
            (year,),
        ).fetchall())

        bill_cats = rows_to_list(conn.execute(
            f"""
            SELECT b.bill_category AS category, SUM(bp.amount_cents) AS total_cents
            {VALID_BILL_PAYMENT_FROM}
            WHERE strftime('%Y', bp.payment_date)=?
            GROUP BY b.bill_category
            ORDER BY total_cents DESC
            """,
            (year,),
        ).fetchall())

        for dataset in (expense_cats, income_cats, bill_cats):
            for row in dataset:
                row["total"] = cents_to_float(row.pop("total_cents"))

        status_breakdown = rows_to_list(conn.execute(
            """
            SELECT status, COUNT(*) AS count, SUM(total_cents) AS total_cents
            FROM bills
            WHERE is_deleted=0
            GROUP BY status
            """,
        ).fetchall())

        for row in status_breakdown:
            row["total"] = cents_to_float(row.pop("total_cents"))

        total_inc = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents),0)
            FROM income
            WHERE is_deleted=0 AND strftime('%Y', income_date)=?
            """,
            (year,),
        ).fetchone()[0]

        total_exp = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents),0)
            FROM expenses
            WHERE is_deleted=0 AND strftime('%Y', expense_date)=?
            """,
            (year,),
        ).fetchone()[0]

        total_bill_payments = conn.execute(
            f"""
            SELECT COALESCE(SUM(bp.amount_cents),0)
            {VALID_BILL_PAYMENT_FROM}
            WHERE strftime('%Y', bp.payment_date)=?
            """,
            (year,),
        ).fetchone()[0]

        total_out = total_exp + total_bill_payments
        net_savings = total_inc - total_out
        savings_rate = round((net_savings / total_inc * 100), 1) if total_inc > 0 else 0

        income_years = [
            r[0] for r in conn.execute(
                """
                SELECT DISTINCT strftime('%Y', income_date)
                FROM income
                WHERE is_deleted=0 AND income_date IS NOT NULL
                ORDER BY 1 DESC
                """
            ).fetchall() if r[0]
        ]

        expense_years = [
            r[0] for r in conn.execute(
                """
                SELECT DISTINCT strftime('%Y', expense_date)
                FROM expenses
                WHERE is_deleted=0 AND expense_date IS NOT NULL
                ORDER BY 1 DESC
                """
            ).fetchall() if r[0]
        ]

        bill_years = [
            r[0] for r in conn.execute(
                f"""
                SELECT DISTINCT strftime('%Y', bp.payment_date)
                {VALID_BILL_PAYMENT_FROM}
                WHERE bp.payment_date IS NOT NULL
                ORDER BY 1 DESC
                """
            ).fetchall() if r[0]
        ]

        all_years = sorted(set(income_years + expense_years + bill_years), reverse=True)

        if str(date.today().year) not in all_years:
            all_years.insert(0, str(date.today().year))

        return jsonify({
            "monthly": monthly,
            "expense_cats": expense_cats,
            "income_cats": income_cats,
            "bill_cats": bill_cats,
            "status_breakdown": status_breakdown,
            "total_income": cents_to_float(total_inc),
            "total_expenses": cents_to_float(total_out),
            "net_savings": cents_to_float(net_savings),
            "savings_rate": savings_rate,
            "available_years": all_years,
        })

    @app.get("/api/reports/budget")
    @api_login_required
    def report_budget():
        period = request.args.get("period", "monthly")

        if period not in {"monthly", "yearly"}:
            raise ValidationError("period must be monthly or yearly")

        conn = get_db()

        if period == "monthly":
            month = request.args.get("month", date.today().strftime("%Y-%m"))

            if not re.match(r"^\d{4}-\d{2}$", month):
                raise ValidationError("month must be YYYY-MM")

            budgets = rows_to_list(conn.execute(
                "SELECT * FROM budgets WHERE is_active=1 AND period='monthly'",
            ).fetchall())

            # Actuals respect expense_splits: an expense without splits counts
            # toward its own category, an expense WITH splits counts each
            # split's category instead (same no-double-count pattern the
            # allowance tracker uses). Bill payments count too.
            date_filter = "strftime('%Y-%m', %s)=?"
            expense_date_col = "e.expense_date"
            payment_date_col = "bp.payment_date"
            range_param = month

        else:
            year = request.args.get("year", str(date.today().year))

            if not re.match(r"^\d{4}$", year):
                raise ValidationError("year must be YYYY")

            budgets = rows_to_list(conn.execute(
                "SELECT * FROM budgets WHERE is_active=1 AND period='yearly'",
            ).fetchall())

            date_filter = "strftime('%Y', %s)=?"
            expense_date_col = "e.expense_date"
            payment_date_col = "bp.payment_date"
            range_param = year

        actual_rows = conn.execute(
            f"""
            SELECT category, SUM(cents) AS actual_cents
            FROM (
                SELECT e.category AS category, SUM(e.amount_cents) AS cents
                FROM expenses e
                WHERE e.is_deleted=0 AND {date_filter.replace("%s", expense_date_col)}
                  AND NOT EXISTS (SELECT 1 FROM expense_splits es WHERE es.expense_id=e.id)
                GROUP BY e.category

                UNION ALL

                SELECT es.category AS category, SUM(es.amount_cents) AS cents
                FROM expense_splits es
                JOIN expenses e ON e.id = es.expense_id
                WHERE e.is_deleted=0 AND {date_filter.replace("%s", expense_date_col)}
                GROUP BY es.category

                UNION ALL

                SELECT b.bill_category AS category, SUM(bp.amount_cents) AS cents
                {VALID_BILL_PAYMENT_FROM}
                WHERE {date_filter.replace("%s", payment_date_col)}
                GROUP BY b.bill_category
            )
            GROUP BY category
            """,
            (range_param, range_param, range_param),
        ).fetchall()

        # Member-attributed actuals for member-specific budgets use the same
        # split-aware pattern.
        member_expense_rows = conn.execute(
            f"""
            SELECT member, category, SUM(cents) AS cents
            FROM (
                SELECT e.member AS member, e.category AS category, SUM(e.amount_cents) AS cents
                FROM expenses e
                WHERE e.is_deleted=0 AND {date_filter.replace("%s", expense_date_col)} AND e.member != ''
                  AND NOT EXISTS (SELECT 1 FROM expense_splits es WHERE es.expense_id=e.id)
                GROUP BY e.member, e.category

                UNION ALL

                SELECT es.member AS member, es.category AS category, SUM(es.amount_cents) AS cents
                FROM expense_splits es
                JOIN expenses e ON e.id = es.expense_id
                WHERE e.is_deleted=0 AND {date_filter.replace("%s", expense_date_col)} AND es.member != ''
                GROUP BY es.member, es.category
            )
            GROUP BY member, category
            """,
            (range_param, range_param),
        ).fetchall()

        actual_by_category = {
            row["category"]: int(row["actual_cents"]) for row in actual_rows
        }

        member_actual_by_key = {}
        for row in member_expense_rows:
            key = (row["member"], row["category"])
            member_actual_by_key[key] = member_actual_by_key.get(key, 0) + int(row["cents"])

        result = []

        for budget in budgets:
            if budget["member"]:
                actual_cents = member_actual_by_key.get((budget["member"], budget["category"]), 0)
            else:
                actual_cents = actual_by_category.get(budget["category"], 0)

            remaining_cents = budget["amount_cents"] - actual_cents

            result.append({
                "id": budget["id"],
                "category": budget["category"],
                "member": budget["member"],
                "period": budget["period"],
                "budget": cents_to_float(budget["amount_cents"]),
                "actual": cents_to_float(actual_cents),
                "remaining": cents_to_float(remaining_cents),
                "used_pct": round((actual_cents / budget["amount_cents"] * 100), 1)
                if budget["amount_cents"] > 0 else 0,
            })

        return jsonify({
            "period": period,
            "items": result,
        })

    @app.get("/api/reports/cash-flow")
    @api_login_required
    def report_cash_flow():
        months = min(max(parse_int(request.args.get("months"), "months", default=6, minimum=1), 1), 24)

        conn = get_db()
        current_month_start = date.today().replace(day=1)
        range_start_iso = add_months(current_month_start, -(months - 1)).isoformat()
        range_end_iso = add_months(current_month_start, 1).isoformat()

        income_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                """
                SELECT strftime('%Y-%m', income_date) AS ym, SUM(amount_cents) AS total
                FROM income
                WHERE is_deleted=0 AND income_date >= ? AND income_date < ?
                GROUP BY ym
                """,
                (range_start_iso, range_end_iso),
            ).fetchall()
        }

        expense_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                """
                SELECT strftime('%Y-%m', expense_date) AS ym, SUM(amount_cents) AS total
                FROM expenses
                WHERE is_deleted=0 AND expense_date >= ? AND expense_date < ?
                GROUP BY ym
                """,
                (range_start_iso, range_end_iso),
            ).fetchall()
        }

        bill_payment_by_month = {
            row["ym"]: row["total"]
            for row in conn.execute(
                f"""
                SELECT strftime('%Y-%m', bp.payment_date) AS ym, SUM(bp.amount_cents) AS total
                {VALID_BILL_PAYMENT_FROM}
                WHERE bp.payment_date >= ? AND bp.payment_date < ?
                GROUP BY ym
                """,
                (range_start_iso, range_end_iso),
            ).fetchall()
        }

        result = []

        for i in range(months - 1, -1, -1):
            d = add_months(current_month_start, -i)
            month_key = d.strftime("%Y-%m")

            inc = income_by_month.get(month_key, 0)
            exp = expense_by_month.get(month_key, 0)
            bill_payments = bill_payment_by_month.get(month_key, 0)

            outflow = exp + bill_payments
            savings = inc - outflow

            result.append({
                "month": month_key,
                "income": cents_to_float(inc),
                "expenses": cents_to_float(exp),
                "bill_payments": cents_to_float(bill_payments),
                "outflow": cents_to_float(outflow),
                "savings": cents_to_float(savings),
                "savings_rate": round((savings / inc * 100), 1) if inc > 0 else 0,
            })

        return jsonify(result)

    @app.get("/api/reports/payees")
    @api_login_required
    def report_payees():
        year = request.args.get("year", str(date.today().year))

        if not re.match(r"^\d{4}$", year):
            raise ValidationError("year must be YYYY")

        conn = get_db()

        rows = conn.execute(
            f"""
            SELECT b.payee_name AS payee,
                   b.bill_category AS category,
                   COUNT(DISTINCT b.id) AS bill_count,
                   SUM(bp.amount_cents) AS paid_cents
            {VALID_BILL_PAYMENT_FROM}
            WHERE strftime('%Y', bp.payment_date)=?
            GROUP BY b.payee_name, b.bill_category
            ORDER BY paid_cents DESC
            """,
            (year,),
        ).fetchall()

        items = rows_to_list(rows)

        for item in items:
            item["paid"] = cents_to_float(item.pop("paid_cents"))

        return jsonify(items)

    @app.get("/api/reports/subscriptions")
    @api_login_required
    def report_subscriptions():
        conn = get_db()

        rows = conn.execute(
            """
            SELECT payee_name,
                   bill_category,
                   COUNT(*) AS bill_count,
                   SUM(total_cents) AS total_cents,
                   MAX(due_date) AS latest_due_date
            FROM bills
            WHERE is_deleted=0
              AND status != 'Void'
              AND (
                    bill_category LIKE '%Subscription%'
                 OR bill_category LIKE '%Internet%'
                 OR bill_category LIKE '%Phone%'
              )
            GROUP BY payee_name, bill_category
            ORDER BY total_cents DESC
            """,
        ).fetchall()

        items = rows_to_list(rows)

        for item in items:
            item["total"] = cents_to_float(item.pop("total_cents"))

        return jsonify(items)

    @app.get("/api/reports/net-worth")
    @api_login_required
    def report_net_worth():
        conn = get_db()

        accounts = rows_to_list(conn.execute(
            "SELECT * FROM accounts WHERE is_active=1 ORDER BY name"
        ).fetchall())

        balances = compute_all_account_balances(conn, accounts)

        net_worth_cents = 0

        for account in accounts:
            entry = balances[account["id"]]

            account["balance_cents"] = entry["balance_cents"]
            account["balance"] = cents_to_float(entry["balance_cents"])
            account["net_worth_cents"] = entry["net_worth_cents"]
            account["net_worth"] = cents_to_float(entry["net_worth_cents"])

            net_worth_cents += entry["net_worth_cents"]

        return jsonify({
            "accounts": accounts,
            "net_worth": cents_to_float(net_worth_cents),
        })

    @app.get("/api/reports/debt-payoff")
    @api_login_required
    def report_debt_payoff():
        conn = get_db()
        extra_monthly_payment = parse_amount_from_data(
            request.args, field="extra_payment", default_cents=0, allow_zero=True
        )

        rows = conn.execute(
            """
            SELECT * FROM accounts
            WHERE is_active=1 AND account_type IN ('Credit Card', 'Loan')
            """
        ).fetchall()

        accounts = rows_to_list(rows)
        balances = compute_all_account_balances(conn, accounts)

        debts = []
        for acc in accounts:
            balance_cents = balances[acc["id"]]["balance_cents"]
            if balance_cents > 0:
                debts.append({
                    "id": acc["id"],
                    "name": acc["name"],
                    "account_type": acc["account_type"],
                    "balance_cents": balance_cents,
                    "balance": cents_to_float(balance_cents),
                    "interest_rate": float(acc.get("interest_rate") or 0.0),
                    "minimum_payment_cents": int(acc.get("minimum_payment_cents") or 0),
                    "minimum_payment": cents_to_float(acc.get("minimum_payment_cents") or 0),
                })

        def simulate(strategy, debt_list, extra_cents):
            import copy
            debts_copy = copy.deepcopy(debt_list)
            if strategy == "snowball":
                debts_copy.sort(key=lambda x: x["balance_cents"])
            elif strategy == "avalanche":
                debts_copy.sort(key=lambda x: x["interest_rate"], reverse=True)

            months = 0
            total_interest_cents = 0

            # Total monthly payoff fuel stays fixed for the whole plan: the
            # configured extra plus every starting minimum. When a debt dies,
            # its freed-up minimum therefore keeps flowing to the remaining
            # debts (the snowball/avalanche rollover) instead of vanishing.
            monthly_budget = int(extra_cents) + sum(
                max(0, int(d["minimum_payment_cents"] or 0)) for d in debts_copy
            )

            while debts_copy and months < 360:
                months += 1

                # Interest accrues on every surviving debt first.
                month_interest = 0
                for d in debts_copy:
                    m_interest = int(round(d["balance_cents"] * (d["interest_rate"] / 100.0 / 12.0)))
                    month_interest += m_interest
                    d["balance_cents"] += m_interest

                total_interest_cents += month_interest

                budget = monthly_budget

                # Each debt pays its user-configured minimum — which may
                # legitimately be $0 (e.g. an interest-free family loan).
                # The old hardcoded $10/month floor is gone.
                for d in debts_copy:
                    min_pay = max(0, int(d["minimum_payment_cents"] or 0))
                    pay = min(d["balance_cents"], min_pay)
                    d["balance_cents"] -= pay
                    budget -= pay

                # Leftover budget cascades down the strategy order until the
                # money runs out, instead of being dropped after one debt.
                for d in debts_copy:
                    if budget <= 0:
                        break
                    pay = min(d["balance_cents"], budget)
                    d["balance_cents"] -= pay
                    budget -= pay

                debts_copy = [d for d in debts_copy if d["balance_cents"] > 0]

            result = {
                "months": months,
                "years": round(months / 12.0, 1),
                "total_interest": cents_to_float(total_interest_cents),
                "total_interest_cents": total_interest_cents,
                "will_never_be_paid_off": bool(debts_copy),
            }

            if debts_copy:
                result["warning"] = (
                    "These debts are not paid off within 360 months at these "
                    "payment levels — monthly interest outpaces the payments."
                )

            return result

        return jsonify({
            "debts": debts,
            "extra_payment": cents_to_float(extra_monthly_payment),
            "snowball": simulate("snowball", debts, extra_monthly_payment) if debts else None,
            "avalanche": simulate("avalanche", debts, extra_monthly_payment) if debts else None,
        })

    @app.get("/api/reports/forecast")
    @api_login_required
    def report_forecast():
        months = min(max(parse_int(request.args.get("months"), "months", default=12, minimum=1), 1), 36)
        conn = get_db()

        accounts = rows_to_list(conn.execute("SELECT * FROM accounts WHERE is_active=1").fetchall())
        balances = compute_all_account_balances(conn, accounts)
        current_net_worth_cents = sum(
            entry["net_worth_cents"] for entry in balances.values()
        )

        today = date.today()
        today_str = today.isoformat()
        m3_ago = (today - timedelta(days=90)).isoformat()

        avg_income_cents = conn.execute(
            "SELECT CAST(COALESCE(SUM(amount_cents), 0) AS FLOAT) / 3.0 FROM income WHERE is_deleted=0 AND income_date >= ? AND income_date <= ?",
            (m3_ago, today_str)
        ).fetchone()[0]

        avg_expense_cents = conn.execute(
            "SELECT CAST(COALESCE(SUM(amount_cents), 0) AS FLOAT) / 3.0 FROM expenses WHERE is_deleted=0 AND expense_date >= ? AND expense_date <= ?",
            (m3_ago, today_str)
        ).fetchone()[0]

        avg_bill_cents = conn.execute(
            f"SELECT CAST(COALESCE(SUM(bp.amount_cents), 0) AS FLOAT) / 3.0 {VALID_BILL_PAYMENT_FROM} WHERE bp.payment_date >= ? AND bp.payment_date <= ?",
            (m3_ago, today_str)
        ).fetchone()[0]

        monthly_net_cents = avg_income_cents - (avg_expense_cents + avg_bill_cents)

        # --- Forward-looking components -------------------------------------
        # Active recurring rules, normalized to a per-month impact, so the
        # projection reflects known schedules rather than only the noisy
        # backward-looking average.
        PERIOD_DAYS = {"daily": 1.0, "weekly": 7.0, "monthly": 30.4375, "yearly": 365.25}
        recurring_income_cents = 0.0
        recurring_outflow_cents = 0.0
        rule_count = 0

        for rule in conn.execute(
            "SELECT entity_type, payload, frequency, interval_value FROM recurring_rules WHERE is_active=1"
        ).fetchall():
            try:
                payload = json.loads(rule["payload"])
            except Exception:
                continue

            if not isinstance(payload, dict):
                continue

            try:
                if rule["entity_type"] == "bill" and "amount" not in payload and "amount_cents" not in payload:
                    bill_items = payload.get("items") or []
                    if not isinstance(bill_items, list) or not bill_items:
                        continue
                    subtotal_cents = 0
                    for item in bill_items:
                        if not isinstance(item, dict):
                            continue
                        qty = parse_quantity(item.get("quantity", 1), "quantity")
                        up_cents = parse_amount_from_data(item, field="unit_price", default_cents=0, allow_zero=True)
                        subtotal_cents += int(
                            (qty * Decimal(up_cents)).to_integral_value(rounding=ROUND_HALF_UP)
                        )
                    disc_pct = parse_percent(payload.get("discount_pct", 0), "discount_pct")
                    t_rate = parse_percent(payload.get("tax_rate", 0), "tax_rate")
                    disc_cents = int(
                        (Decimal(subtotal_cents) * Decimal(str(disc_pct)) / Decimal("100"))
                        .to_integral_value(rounding=ROUND_HALF_UP)
                    )
                    taxable_cents = subtotal_cents - disc_cents
                    t_cents = int(
                        (Decimal(taxable_cents) * Decimal(str(t_rate)) / Decimal("100"))
                        .to_integral_value(rounding=ROUND_HALF_UP)
                    )
                    amount_cents = taxable_cents + t_cents
                else:
                    amount_cents = parse_amount_from_data(
                        payload, "amount", default_cents=0, allow_zero=True
                    )
            except ValidationError:
                continue

            interval = max(int(rule["interval_value"] or 1), 1)
            days_per_period = PERIOD_DAYS.get(rule["frequency"], 30.4375) * interval
            per_month_cents = amount_cents * (30.4375 / days_per_period)

            if rule["entity_type"] == "income":
                recurring_income_cents += per_month_cents
            else:
                recurring_outflow_cents += per_month_cents
                # Bills and expenses both land on the outflow side.
            rule_count += 1

        # Already-issued, not-yet-paid bills become scheduled outflows in
        # their due month (bills due in the remainder of the current month are
        # folded into Month 1 so they affect the projection and keep totals exact).
        next_month_key = add_months(today.replace(day=1), 1).strftime("%Y-%m")
        horizon_end_iso = add_months(today.replace(day=1), months + 1).isoformat()
        scheduled_by_month = {}
        scheduled_count = 0

        for row in conn.execute(
            """
            SELECT b.due_date AS due_date_key, (b.total_cents - b.paid_cents) AS due_cents
            FROM bills b
            WHERE b.is_deleted=0 AND b.status IN ('Pending', 'Partially Paid')
              AND b.due_date >= ? AND b.due_date < ?
            """,
            (today_str, horizon_end_iso),
        ).fetchall():
            ym = str(row["due_date_key"])[:7]
            target_ym = next_month_key if ym < next_month_key else ym
            scheduled_by_month[target_ym] = scheduled_by_month.get(target_ym, 0) + row["due_cents"]
            scheduled_count += 1

        # Blend: with active rules, trust the schedules half as much as the
        # recent history; otherwise fall back to pure history.
        weight_history = 0.5 if rule_count else 1.0

        projection = []
        sim_nw_cents = current_net_worth_cents
        cur_date = today.replace(day=1)

        for i in range(1, months + 1):
            cur_date = add_months(cur_date, 1)
            month_key = cur_date.strftime("%Y-%m")

            est_income = (
                weight_history * avg_income_cents
                + (1 - weight_history) * recurring_income_cents
            )
            est_outflow = (
                weight_history * (avg_expense_cents + avg_bill_cents)
                + (1 - weight_history) * recurring_outflow_cents
                + scheduled_by_month.get(month_key, 0)
            )

            net_change = est_income - est_outflow
            sim_nw_cents += net_change

            projection.append({
                "month": cur_date.strftime("%Y-%m"),
                "estimated_income": cents_to_float(est_income),
                "estimated_expense": cents_to_float(est_outflow),
                "scheduled_bills": cents_to_float(scheduled_by_month.get(month_key, 0)),
                "estimated_net_change": cents_to_float(net_change),
                "projected_net_worth": cents_to_float(sim_nw_cents),
            })

        return jsonify({
            "current_net_worth": cents_to_float(current_net_worth_cents),
            "historical_monthly_income": cents_to_float(avg_income_cents),
            "historical_monthly_expense": cents_to_float(avg_expense_cents + avg_bill_cents),
            "monthly_net_change": cents_to_float(monthly_net_cents),
            "recurring_monthly_income": cents_to_float(recurring_income_cents),
            "recurring_monthly_outflow": cents_to_float(recurring_outflow_cents),
            "recurring_rule_count": rule_count,
            "scheduled_bills_total": cents_to_float(sum(scheduled_by_month.values())),
            "scheduled_bills_count": scheduled_count,
            "projection": projection,
        })

    # -----------------------------------------------------------------------
    # Reminders
    # -----------------------------------------------------------------------

    @app.get("/api/reminders")
    @api_login_required
    def reminders():
        conn = get_db()

        # One authoritative "today" computed in Python and passed into SQL as
        # a bound parameter — mixing Python's local date with SQLite's
        # date('now') (UTC) made the filter and the displayed day-counts
        # disagree by a day near midnight on non-UTC servers.
        today = date.today()
        today_str = today.isoformat()
        in_14_days = (today + timedelta(days=14)).isoformat()

        overdue = rows_to_list(conn.execute(
            """
            SELECT id, bill_number, payee_name, due_date, total_cents, paid_cents,
                   status, bill_category
            FROM bills
            WHERE is_deleted=0
              AND status IN ('Pending', 'Partially Paid')
              AND due_date < ?
            ORDER BY due_date
            """,
            (today_str,),
        ).fetchall())

        upcoming = rows_to_list(conn.execute(
            """
            SELECT id, bill_number, payee_name, due_date, total_cents, paid_cents,
                   status, bill_category
            FROM bills
            WHERE is_deleted=0
              AND status IN ('Pending', 'Partially Paid')
              AND due_date BETWEEN ? AND ?
            ORDER BY due_date
            """,
            (today_str, in_14_days),
        ).fetchall())

        for row in overdue:
            row["days_overdue"] = max(0, (today - date.fromisoformat(row["due_date"])).days)

        for row in upcoming:
            row["days_left"] = max(0, (date.fromisoformat(row["due_date"]) - today).days)

        for row in overdue + upcoming:
            total_cents = row.pop("total_cents", 0)
            paid_cents = row.pop("paid_cents", 0)

            row["total_amount"] = cents_to_float(total_cents)
            row["paid_amount"] = cents_to_float(paid_cents)
            row["balance_amount"] = cents_to_float(total_cents - paid_cents)

        return jsonify({
            "overdue": overdue,
            "upcoming": upcoming,
        })

    # -----------------------------------------------------------------------
    # Admin
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Allowances & CSV Import & Restore
    # -----------------------------------------------------------------------

    @app.get("/api/allowances")
    @api_login_required
    def allowances_list():
        conn = get_db()
        rows = conn.execute(
            """
            SELECT a.*, m.name AS member_name
            FROM allowances a
            JOIN members m ON m.id = a.member_id
            ORDER BY m.name
            """
        ).fetchall()

        this_month = date.today().strftime("%Y-%m")

        # Expenses without splits count their full amount toward their member;
        # expenses WITH splits count only each split's share (no double counting).
        # Match by member_id first so renaming a member never zeroes out their total.
        spent_by_member_id = {}
        spent_by_name = {}
        for row in conn.execute(
            """
            SELECT COALESCE(e.member_id, 0) AS mid, e.member AS member, SUM(e.amount_cents) AS total
            FROM expenses e
            WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=?
              AND NOT EXISTS (SELECT 1 FROM expense_splits es WHERE es.expense_id=e.id)
            GROUP BY mid, e.member
            """,
            (this_month,),
        ).fetchall():
            if row["mid"]:
                spent_by_member_id[row["mid"]] = spent_by_member_id.get(row["mid"], 0) + row["total"]
            elif row["member"]:
                spent_by_name[row["member"]] = spent_by_name.get(row["member"], 0) + row["total"]

        for row in conn.execute(
            """
            SELECT COALESCE(es.member_id, 0) AS mid, es.member AS member, SUM(es.amount_cents) AS total
            FROM expense_splits es
            JOIN expenses e ON e.id = es.expense_id
            WHERE e.is_deleted=0 AND strftime('%Y-%m', e.expense_date)=?
            GROUP BY mid, es.member
            """,
            (this_month,),
        ).fetchall():
            if row["mid"]:
                spent_by_member_id[row["mid"]] = spent_by_member_id.get(row["mid"], 0) + row["total"]
            elif row["member"]:
                spent_by_name[row["member"]] = spent_by_name.get(row["member"], 0) + row["total"]

        items = rows_to_list(rows)

        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

            spent_cents = (
                spent_by_member_id.get(item["member_id"], 0)
                + spent_by_name.get(item["member_name"], 0)
            )
            item["spent_this_month"] = cents_to_float(spent_cents)
            item["spent_this_month_cents"] = spent_cents

            # A weekly rule's amount covers one week, but spending is being
            # compared against a whole month. Scale the weekly amount by the
            # average number of weeks per month (52/12 ≈ 4.333) so
            # "remaining" reflects the true monthly allowance instead of
            # going negative after the first $20 of monthly spend.
            if item["frequency"] == "weekly":
                period_budget_cents = int(round(item["amount_cents"] * 52 / 12))
            else:
                period_budget_cents = int(item["amount_cents"])

            item["monthly_equivalent_cents"] = period_budget_cents
            item["monthly_equivalent"] = cents_to_float(period_budget_cents)
            item["remaining_this_month"] = cents_to_float(period_budget_cents - spent_cents)

        return jsonify(items)

    @app.post("/api/allowances")
    @require_roles("Admin", "Editor")
    def allowance_create():
        data = json_payload()
        member_id = ensure_reference_exists(
            get_db(), "members", data.get("member_id"), "member_id", require_active=True
        )
        if not member_id:
            raise ValidationError("member_id is required")

        frequency = str(data.get("frequency") or "monthly").strip()
        if frequency not in {"weekly", "monthly"}:
            raise ValidationError("frequency must be weekly or monthly")

        amount_cents = parse_amount_from_data(data, "amount", allow_zero=False)

        conn = get_db()

        # One active rule per member per frequency; the IMMEDIATE transaction
        # closes the check-then-insert race and a UNIQUE index backs this up.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")

        try:
            exists = conn.execute(
                "SELECT 1 FROM allowances WHERE member_id=? AND frequency=?",
                (member_id, frequency),
            ).fetchone()

            if exists:
                conn.rollback()
                raise ValidationError(
                    f"An active {frequency} allowance already exists for this member"
                )

            cur = conn.execute(
                """
                INSERT INTO allowances (member_id, amount_cents, frequency, notes)
                VALUES (?, ?, ?, ?)
                """,
                (member_id, amount_cents, frequency, str(data.get("notes") or "")),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        audit("allowance_created", "allowance", member_id)
        return jsonify({"success": True, "id": cur.lastrowid})

    @app.put("/api/allowances/<int:alid>")
    @require_roles("Admin", "Editor")
    def allowance_update(alid):
        data = json_payload()
        member_id = ensure_reference_exists(
            get_db(), "members", data.get("member_id"), "member_id", require_active=True
        )
        if not member_id:
            raise ValidationError("member_id is required")

        frequency = str(data.get("frequency") or "monthly").strip()
        if frequency not in {"weekly", "monthly"}:
            raise ValidationError("frequency must be weekly or monthly")

        amount_cents = parse_amount_from_data(data, "amount", allow_zero=False)

        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM allowances WHERE id=?", (alid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            clash = conn.execute(
                "SELECT 1 FROM allowances WHERE member_id=? AND frequency=? AND id != ?",
                (member_id, frequency, alid),
            ).fetchone()

            if clash:
                raise ValidationError(
                    f"An active {frequency} allowance already exists for this member"
                )

            conn.execute(
                """
                UPDATE allowances
                SET member_id=?, amount_cents=?, frequency=?, notes=?
                WHERE id=?
                """,
                (member_id, amount_cents, frequency, str(data.get("notes") or ""), alid),
            )

        audit("allowance_updated", "allowance", alid)
        return jsonify({"success": True})

    @app.delete("/api/allowances/<int:alid>")
    @require_roles("Admin", "Editor")
    def allowance_delete(alid):
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM allowances WHERE id=?", (alid,)).fetchone():
                return jsonify({"error": "Not found"}), 404

            conn.execute("DELETE FROM allowances WHERE id=?", (alid,))
        audit("allowance_deleted", "allowance", alid)
        return jsonify({"success": True})

    @app.post("/api/import/csv/preview")
    @require_roles("Admin", "Editor")
    def csv_import_preview():
        if "file" not in request.files:
            raise ValidationError("CSV file is required in form under field 'file'")
        file = request.files["file"]
        if not file.filename or not file.filename.endswith(".csv"):
            raise ValidationError("Uploaded file must be a .csv file")

        content = file.read().decode("utf-8-sig", errors="replace")
        csv_reader = csv.reader(io.StringIO(content))
        rows = list(csv_reader)

        if not rows:
            raise ValidationError("CSV file is empty")

        headers = [h.strip() for h in rows[0]]
        sample_rows = rows[1:10]

        return jsonify({
            "headers": headers,
            "sample_rows": sample_rows,
            "total_rows": len(rows) - 1,
        })

    @app.post("/api/import/csv/commit")
    @require_roles("Admin", "Editor")
    def csv_import_commit():
        data = json_payload()
        items = data.get("items") or []
        target_type = str(data.get("target_type") or "expense").strip()

        if target_type not in {"expense", "income"}:
            raise ValidationError("target_type must be expense or income")

        if not isinstance(items, list) or not items:
            raise ValidationError("items list is required")

        imported_count = 0
        failed = []
        conn = get_db()

        for row_index, item in enumerate(items):
            # Row-isolated SAVEPOINT (same hardening recurring_run uses): one
            # bad row rolls back only itself and is reported, instead of
            # aborting and rolling back the whole file.
            conn.execute("SAVEPOINT csv_row")

            try:
                title = str(item.get("title") or item.get("description") or "Imported Item").strip()
                cat = str(item.get("category") or "Other").strip()
                amt_cents = parse_amount_from_data(item, "amount")
                tx_date = parse_flexible_date(item.get("date"), "date")
                account_id = item.get("account_id") or None
                notes = str(item.get("notes") or "").strip()

                record = {
                    "title": title,
                    "category": cat,
                    "amount_cents": amt_cents,
                    "notes": notes,
                    "account_id": account_id,
                    "expense_date" if target_type == "expense" else "income_date": tx_date,
                }

                if target_type == "expense":
                    insert_expense(conn, record, require_active_refs=True)
                else:
                    insert_income(conn, record, require_active_refs=True)

                imported_count += 1
                conn.execute("RELEASE SAVEPOINT csv_row")
            except ValidationError as exc:
                conn.execute("ROLLBACK TO SAVEPOINT csv_row")
                conn.execute("RELEASE SAVEPOINT csv_row")
                failed.append({"row": row_index, "error": exc.message})
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT csv_row")
                conn.execute("RELEASE SAVEPOINT csv_row")
                current_app.logger.exception("CSV import row %s failed", row_index)
                failed.append({"row": row_index, "error": str(exc)})

        audit(
            "csv_imported",
            target_type,
            "",
            f"Imported {imported_count} record(s), {len(failed)} failed",
        )

        return jsonify({
            "success": True,
            "imported_count": imported_count,
            "failed_rows": len(failed),
            "failed": failed,
            "total_rows": len(items),
        })

    @app.post("/api/admin/restore")
    @require_roles("Admin")
    def admin_restore():
        if "file" not in request.files:
            raise ValidationError("Backup file is required in form under field 'file'")
        file = request.files["file"]
        if not file.filename or not file.filename.endswith(".db"):
            raise ValidationError("Uploaded file must be a SQLite .db file")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp_path = tmp.name
        tmp.close()
        file.save(tmp_path)

        try:
            # Test opening upload db and confirm it looks like a FamilyFinance backup.
            test_conn = sqlite3.connect(tmp_path)

            try:
                table_names = {
                    row[0]
                    for row in test_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            except sqlite3.DatabaseError:
                raise ValidationError("Uploaded file is not a valid SQLite database")
            finally:
                test_conn.close()

            missing_tables = {"users", "settings", "bills", "expenses"} - table_names

            if missing_tables:
                raise ValidationError(
                    "Not a valid FamilyFinance backup (missing tables: "
                    + ", ".join(sorted(missing_tables))
                    + ")"
                )

            # Backup upload over live db
            db_path = current_app.config["DATABASE"]

            # Close existing thread connection
            close_db()

            src = sqlite3.connect(tmp_path)
            dst = sqlite3.connect(db_path)
            src.backup(dst)
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst.close()
            src.close()

            # Upgrade restored older schemas with any newly added tables/columns/indexes
            init_db()

            audit("database_restored", "database", "", file.filename)

            # Other threads/workers may still hold connections with cached
            # WAL state pointed at pre-restore data. The endpoint closes this
            # request's connection only — restarting the process is the
            # reliable way to drop every stale handle.
            return jsonify({
                "success": True,
                "restart_recommended": True,
                "message": "Database restored. Restart the FamilyFinance process to guarantee all workers pick up the restored data.",
            })
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    @app.get("/api/admin/backup")
    @require_roles("Admin")
    def admin_backup():
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp_path = tmp.name
        tmp.close()

        try:
            src = get_db()
            dst = sqlite3.connect(tmp_path)

            src.backup(dst)

            dst.close()

            data = Path(tmp_path).read_bytes()

            filename = f"familyfinance-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"

            audit("backup_created", "backup", filename)

            return send_file(
                io.BytesIO(data),
                as_attachment=True,
                download_name=filename,
                mimetype="application/octet-stream",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    @app.get("/api/admin/audit")
    @require_roles("Admin")
    def admin_audit_logs():
        limit, offset = get_pagination()
        action_filter = str(request.args.get("action") or "").strip()
        conn = get_db()

        where = "WHERE 1=1"
        params = []
        if action_filter:
            where += " AND action LIKE ? ESCAPE '\\'"
            params.append(f"%{escape_like(action_filter)}%")

        total = conn.execute(f"SELECT COUNT(*) FROM audit_logs {where}", params).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT * FROM audit_logs
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        return jsonify({
            "items": rows_to_list(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
        })


# ---------------------------------------------------------------------------
# Printable bill HTML
# ---------------------------------------------------------------------------

def generate_print_bill(b, items, s):
    # Settings are admin-editable and stored raw, so they must be escaped
    # before being interpolated into the print view.
    sym = html.escape(str(s.get("currency_symbol", "$")))

    status_colors = {
        "Pending": "#f59e0b",
        "Partially Paid": "#3b82f6",
        "Paid": "#10b981",
        "Void": "#94a3b8",
    }

    status_color = status_colors.get(b.get("status", "Pending"), "#f59e0b")

    rows_html = ""

    for item in items:
        item_name = html.escape(str(item.get("item_name", "")))
        desc_val = html.escape(str(item.get("description", "")))

        desc = f"<br><small style='color:#64748b'>{desc_val}</small>" if desc_val else ""

        rows_html += f"""
        <tr>
          <td>{item_name}{desc}</td>
          <td style="text-align:center">{item['quantity']:g}</td>
          <td style="text-align:right">{sym}{fmt_cents(item['unit_price_cents'])}</td>
          <td style="text-align:right;font-weight:600">{sym}{fmt_cents(item['total_cents'])}</td>
        </tr>
        """

    discount_row = ""

    if int(b.get("discount_cents", 0)) > 0:
        discount_row = f"""
        <tr>
          <td>Discount ({b['discount_pct']}%)</td>
          <td style="text-align:right;color:#f43f5e">-{sym}{fmt_cents(b['discount_cents'])}</td>
        </tr>
        """

    notes = html.escape(str(b.get("notes") or s.get("family_notes", "")))
    family_name = html.escape(str(s.get("family_name", "Our Family")))
    family_address = html.escape(str(s.get("family_address", ""))).replace("\n", "<br>")
    primary_email = html.escape(str(s.get("primary_email", "")))
    primary_phone = html.escape(str(s.get("primary_phone", "")))

    bill_number = html.escape(str(b.get("bill_number", "")))
    status = html.escape(str(b.get("status", "Pending")))
    bill_date = html.escape(str(b.get("bill_date", "")))
    due_date = html.escape(str(b.get("due_date", "")))
    bill_category = html.escape(str(b.get("bill_category", "Other")))

    payee_name = html.escape(str(b.get("payee_name", "")))
    payee_email = html.escape(str(b.get("payee_email", "")))
    payee_address = html.escape(str(b.get("payee_address", ""))).replace("\n", "<br>")

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Bill {bill_number}</title>
</head>
<body style="font-family:system-ui,-apple-system,sans-serif;color:#1e293b;max-width:860px;margin:0 auto;padding:32px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:32px;">
    <div>
      <div style="font-size:26px;font-weight:700;">🏠 {family_name}</div>
      <div style="color:#64748b;margin-top:8px;line-height:1.7;">
        {family_address}
        {'<br>' + primary_email if primary_email else ''}
        {'<br>' + primary_phone if primary_phone else ''}
      </div>
    </div>

    <div style="text-align:right;">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#94a3b8;">Bill / Payment</div>
      <div style="font-size:32px;font-weight:700;">#{bill_number}</div>
      <div style="margin-top:8px;">
        <span style="display:inline-block;padding:4px 14px;border-radius:100px;color:#fff;font-size:11px;font-weight:700;background:{status_color};text-transform:uppercase;">
          {status}
        </span>
      </div>
      <div style="color:#64748b;margin-top:12px;line-height:1.8;">
        Bill Date: <strong>{bill_date}</strong><br>
        Due Date: <strong>{due_date}</strong><br>
        Category: <strong>{bill_category}</strong>
      </div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-bottom:24px;">
    <div>
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#94a3b8;margin-bottom:10px;">Billed By / Payee</div>
      <div style="line-height:1.8;">
        <strong>{payee_name}</strong><br>
        {payee_email}<br>
        {payee_address}
      </div>
    </div>

    <div>
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#94a3b8;margin-bottom:10px;">Category</div>
      <div>{bill_category}</div>
    </div>
  </div>

  <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
    <thead>
      <tr style="background:#1e1b4b;color:#fff;">
        <th scope="col" style="padding:12px 16px;text-align:left;">Description</th>
        <th scope="col" style="padding:12px 16px;text-align:center;">Qty</th>
        <th scope="col" style="padding:12px 16px;text-align:right;">Unit Price</th>
        <th scope="col" style="padding:12px 16px;text-align:right;">Amount</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>

  <div style="display:flex;justify-content:flex-end;margin-bottom:32px;">
    <table style="width:320px;border-collapse:collapse;">
      <tr>
        <td style="padding:8px 16px;color:#475569;">Subtotal</td>
        <td style="padding:8px 16px;text-align:right;">{sym}{fmt_cents(b['subtotal_cents'])}</td>
      </tr>
      {discount_row}
      <tr>
        <td style="padding:8px 16px;color:#475569;">Tax ({b['tax_rate']}%)</td>
        <td style="padding:8px 16px;text-align:right;">{sym}{fmt_cents(b['tax_cents'])}</td>
      </tr>
      <tr>
        <td style="padding:8px 16px;color:#475569;">Total</td>
        <td style="padding:8px 16px;text-align:right;">{sym}{fmt_cents(b['total_cents'])}</td>
      </tr>
      <tr>
        <td style="padding:8px 16px;color:#475569;">Paid</td>
        <td style="padding:8px 16px;text-align:right;">{sym}{fmt_cents(b['paid_cents'])}</td>
      </tr>
      <tr>
        <td style="padding:8px 16px;font-weight:700;border-top:2px solid #1e1b4b;">Balance Due</td>
        <td style="padding:8px 16px;text-align:right;font-weight:700;border-top:2px solid #1e1b4b;">
          {sym}{fmt_cents(b['total_cents'] - b['paid_cents'])}
        </td>
      </tr>
    </table>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;">
    <div>
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#94a3b8;margin-bottom:8px;">Notes</div>
      <div style="color:#475569;line-height:1.7;">{notes or 'Thank you!'}</div>
    </div>

    <div>
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#94a3b8;margin-bottom:8px;">Currency</div>
      <div style="color:#475569;line-height:1.7;">{html.escape(str(s.get('currency_code','USD')))} — {sym}</div>
    </div>
  </div>

  <div style="margin-top:32px;">
    <button onclick="window.print()" style="padding:12px 24px;background:#4f46e5;color:#fff;border:none;border-radius:8px;cursor:pointer;font-weight:600;">
      🖨️ Print / Save PDF
    </button>
  </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()

    host = os.getenv("FF_HOST", "127.0.0.1")
    port = int(os.getenv("FF_PORT", "5000"))
    debug = os.getenv("FF_DEBUG", "0") == "1"

    print()
    print("=" * 72)
    print("  🏠 FamilyFinance Enhanced — Personal & Family Budget Tracker")
    print(f"  🌐 Open: http://{host}:{port}")
    print("  🔐 Login endpoint: POST /api/auth/login")
    print("=" * 72)
    print()

    app.run(debug=debug, host=host, port=port)
