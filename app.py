#!/usr/bin/env python3
"""
FamilyFinance — Personal & Family Budget Tracker (Enhanced Edition)

Run locally:
    pip install flask
    python app.py

Default behavior:
    - If FF_ADMIN_PASS is not set, a one-time admin password is generated
      and printed to the console on first startup.
    - For production, set FF_SECRET_KEY and FF_ADMIN_PASS.

This implementation focuses on a secure, maintainable backend.
If you have a frontend template at templates/index.html, it will be used.
Otherwise, a minimal API home page is rendered.
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
import html
import secrets
import time
import logging
import tempfile
import calendar
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from functools import wraps
from urllib.parse import quote
from werkzeug.security import generate_password_hash, check_password_hash


BASE_DIR = Path(__file__).parent
RATE_STORE = {}

BILL_STATUSES = {"Pending", "Partially Paid", "Paid", "Void"}
ASSET_ACCOUNT_TYPES = {"Checking", "Savings", "Cash", "Investment", "Other"}
LIABILITY_ACCOUNT_TYPES = {"Credit Card", "Loan"}
ACCOUNT_TYPES = ASSET_ACCOUNT_TYPES | LIABILITY_ACCOUNT_TYPES

EXPENSE_CATEGORIES = [
    "Groceries & Food", "Housing & Rent", "Utilities", "Transportation",
    "Healthcare & Medical", "Education", "Entertainment & Fun", "Dining Out",
    "Clothing & Shopping", "Personal Care", "Insurance", "Subscriptions",
    "Debt Payments", "Gifts & Donations", "Pet Care", "Travel & Vacation",
    "Kids & Family", "Home Maintenance", "Savings & Investments", "Other",
]

INCOME_CATEGORIES = [
    "Salary / Wages", "Freelance / Gig Work", "Business Income",
    "Investments & Dividends", "Rental Income", "Government Benefits",
    "Gift / Inheritance", "Tax Refund", "Other",
]

BILL_CATEGORIES = [
    "Mortgage / Rent", "Electricity", "Water & Sewage", "Gas / Heating",
    "Internet & Cable", "Phone / Mobile", "Insurance Premium",
    "Subscription Service", "Credit Card Payment", "Loan Repayment",
    "School / Tuition Fees", "Other",
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
    notes TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
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
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    tags TEXT DEFAULT '',
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
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    tags TEXT DEFAULT '',
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
"""

MINIMAL_INDEX = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FamilyFinance API</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 40px auto; max-width: 900px; line-height: 1.6; padding: 0 16px; }
    code { background: #f1f5f9; padding: 2px 6px; border-radius: 6px; }
    .card { border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin: 16px 0; }
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


class ValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# ─────────────────────────────────────────────────────────────────────────────
# Generic helpers
# ─────────────────────────────────────────────────────────────────────────────

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

        if not allow_zero and cents <= 0:
            raise ValidationError(f"{field} must be greater than 0")
        if cents < 0:
            raise ValidationError(f"{field} cannot be negative")

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


def cents_to_float(cents):
    return round(int(cents) / 100, 2)


def fmt_cents(cents):
    return f"{Decimal(int(cents)) / Decimal(100):,.2f}"


def cents_to_plain(cents):
    return f"{Decimal(int(cents)) / Decimal(100):.2f}"


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


def rate_limit(key: str, limit: int, window: int):
    now = time.time()
    entries = [ts for ts in RATE_STORE.get(key, []) if ts > now - window]

    if len(entries) >= limit:
        RATE_STORE[key] = entries
        return False

    entries.append(now)
    RATE_STORE[key] = entries
    return True


def get_pagination():
    try:
        limit = min(max(int(request.args.get("limit", 25)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        raise ValidationError("Invalid pagination parameters")

    return limit, offset


def json_payload():
    return request.get_json(silent=True) or {}


# ─────────────────────────────────────────────────────────────────────────────
# Flask / DB plumbing
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA journal_mode=WAL")
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
        "MAX_CONTENT_LENGTH": 5 * 1024 * 1024,
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

    @app.errorhandler(500)
    def handle_500(e):
        app.logger.exception("Internal server error")
        return jsonify({"error": "Internal server error"}), 500

    @app.after_request
    def set_security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

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
        if request.path.startswith("/api/"):
            ip = request.remote_addr or "unknown"

            if not rate_limit(f"api:{ip}", limit=900, window=60):
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

                token = request.headers.get("X-CSRF-Token", "")
                if not token:
                    payload = json_payload()
                    token = payload.get("_csrf", "")

                if not token or token != session.get("_csrf"):
                    return jsonify({"error": "CSRF validation failed"}), 403

        return None

    with app.app_context():
        init_db()

    register_routes(app)
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Init / default data
# ─────────────────────────────────────────────────────────────────────────────

def init_db():
    conn = get_db()
    with conn:
        conn.executescript(SCHEMA)

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


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Auth / audit helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_current_user():
    username = session.get("username")
    if not username:
        return None

    conn = get_db()
    return conn.execute(
        "SELECT id, username, role FROM users WHERE username=?",
        (username,),
    ).fetchone()


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
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    username, action, entity_type, entity_id, details, ip_address, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.get("username", "anonymous"),
                    action,
                    entity_type,
                    str(entity_id),
                    details,
                    request.remote_addr or "",
                    request.headers.get("User-Agent", ""),
                ),
            )
    except Exception:
        current_app.logger.exception("Audit logging failed")


def notify(title, body="", link="", username=""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO notifications (username, title, body, link) VALUES (?, ?, ?, ?)",
            (username, title, body, link),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Money / transaction helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_transaction_payload(data, date_field):
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValidationError("title is required")

    transaction_date = parse_iso_date(data.get(date_field), date_field)
    amount_cents = parse_amount_from_data(data, "amount")

    category = str(data.get("category") or "Other").strip() or "Other"
    member = str(data.get("member") or "").strip()
    notes = str(data.get("notes") or "").strip()
    account_id = data.get("account_id") or None

    tags = data.get("tags") or ""
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
        "notes": notes,
        "account_id": account_id,
        "tags": tags,
    }


def insert_expense(conn, data):
    payload = parse_transaction_payload(data, "expense_date")

    cur = conn.execute(
        """
        INSERT INTO expenses (
            title, category, amount_cents, expense_date, store, receipt_ref,
            notes, member, account_id, tags
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            payload["account_id"],
            payload["tags"],
        ),
    )

    return cur.lastrowid


def insert_income(conn, data):
    payload = parse_transaction_payload(data, "income_date")

    cur = conn.execute(
        """
        INSERT INTO income (
            title, category, amount_cents, income_date, source,
            notes, member, account_id, tags
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["title"],
            payload["category"],
            payload["amount_cents"],
            payload["date"],
            str(data.get("source") or "").strip(),
            payload["notes"],
            payload["member"],
            payload["account_id"],
            payload["tags"],
        ),
    )

    return cur.lastrowid


def parse_bill_payload(data):
    payee_name = str(data.get("payee_name") or "").strip()
    payee_id = data.get("payee_id") or None

    if not payee_name and not payee_id:
        raise ValidationError("payee_name or payee_id is required")

    bill_date = parse_iso_date(data.get("bill_date"), "bill_date")
    due_date = parse_iso_date(data.get("due_date"), "due_date")

    status = str(data.get("status") or "Pending").strip()
    if status not in BILL_STATUSES:
        raise ValidationError("Invalid bill status")

    bill_category = str(data.get("bill_category") or "Other").strip() or "Other"
    notes = str(data.get("notes") or "").strip()
    account_id = data.get("account_id") or None

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
        "payee_email": str(data.get("payee_email") or "").strip(),
        "payee_address": str(data.get("payee_address") or "").strip(),
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

    conn.execute(
        "INSERT OR IGNORE INTO bill_sequences (prefix, next_number) VALUES (?, 1)",
        (prefix,),
    )

    row = conn.execute(
        "SELECT next_number FROM bill_sequences WHERE prefix=?",
        (prefix,),
    ).fetchone()

    number = int(row["next_number"]) if row else 1

    conn.execute(
        "UPDATE bill_sequences SET next_number=? WHERE prefix=?",
        (number + 1, prefix),
    )

    return f"{prefix}-{number:04d}"


def insert_bill(conn, payload, bill_number, paid_cents=0, status="Pending", paid_at=None):
    cur = conn.execute(
        """
        INSERT INTO bills (
            bill_number, payee_id, payee_name, payee_email, payee_address,
            bill_category, bill_date, due_date, subtotal_cents, discount_pct,
            discount_cents, tax_rate, tax_cents, total_cents, paid_cents,
            status, notes, account_id, paid_at
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
                bill_id, item_name, description, quantity, unit_price_cents, total_cents
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
                bill_id, amount_cents, payment_date, account_id, notes
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                bill_id,
                paid_cents,
                date.today().isoformat(),
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


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

def register_routes(app):

    # ─────────────────────────────────────────────────────────────────────────
    # Home / health
    # ─────────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────────
    # Auth
    # ─────────────────────────────────────────────────────────────────────────

    @app.post("/api/auth/login")
    def auth_login():
        ip = request.remote_addr or "unknown"
        if not rate_limit(f"login:{ip}", limit=10, window=60):
            return jsonify({"error": "Too many login attempts"}), 429

        data = json_payload()
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

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

    # ─────────────────────────────────────────────────────────────────────────
    # Settings
    # ─────────────────────────────────────────────────────────────────────────

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
            settings["currency_symbol"] = str(settings["currency_symbol"]).strip()[:8]

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

    # ─────────────────────────────────────────────────────────────────────────
    # Categories
    # ─────────────────────────────────────────────────────────────────────────

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
                "INSERT INTO categories (name, type, is_default, is_active) VALUES (?, ?, 0, 1)",
                (name, category_type),
            )

        audit("category_created", "category", name)
        return jsonify({"success": True})

    @app.put("/api/categories/<int:cid>")
    @require_roles("Admin", "Editor")
    def category_update(cid):
        data = json_payload()

        name = str(data.get("name") or "").strip()
        is_active = 1 if data.get("is_active", True) else 0

        if not name:
            raise ValidationError("Category name is required")

        with get_db() as conn:
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
            conn.execute("UPDATE categories SET is_active=0 WHERE id=?", (cid,))

        audit("category_deactivated", "category", cid)
        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # Members
    # ─────────────────────────────────────────────────────────────────────────

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
            conn.execute(
                "INSERT INTO members (name, email, role) VALUES (?, ?, ?)",
                (name, str(data.get("email") or ""), str(data.get("role") or "Member")),
            )

        audit("member_created", "member", name)
        return jsonify({"success": True})

    @app.put("/api/members/<int:mid>")
    @require_roles("Admin", "Editor")
    def member_update(mid):
        data = json_payload()
        name = str(data.get("name") or "").strip()

        if not name:
            raise ValidationError("Member name is required")

        with get_db() as conn:
            conn.execute(
                "UPDATE members SET name=?, email=?, role=?, is_active=? WHERE id=?",
                (
                    name,
                    str(data.get("email") or ""),
                    str(data.get("role") or "Member"),
                    1 if data.get("is_active", True) else 0,
                    mid,
                ),
            )

        audit("member_updated", "member", mid)
        return jsonify({"success": True})

    @app.delete("/api/members/<int:mid>")
    @require_roles("Admin")
    def member_delete(mid):
        with get_db() as conn:
            conn.execute("UPDATE members SET is_active=0 WHERE id=?", (mid,))

        audit("member_deactivated", "member", mid)
        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # Accounts
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/accounts")
    @api_login_required
    def accounts_list():
        conn = get_db()
        accounts = rows_to_list(conn.execute(
            "SELECT * FROM accounts WHERE is_active=1 ORDER BY name"
        ).fetchall())

        for account in accounts:
            income_cents = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM income WHERE is_deleted=0 AND account_id=?",
                (account["id"],),
            ).fetchone()[0]

            expense_cents = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM expenses WHERE is_deleted=0 AND account_id=?",
                (account["id"],),
            ).fetchone()[0]

            payment_cents = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM bill_payments WHERE account_id=?",
                (account["id"],),
            ).fetchone()[0]

            balance_cents = (
                account["opening_balance_cents"]
                + income_cents
                - expense_cents
                - payment_cents
            )

            account["balance_cents"] = balance_cents
            account["balance"] = cents_to_float(balance_cents)

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

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (name, account_type, opening_balance_cents, notes)
                VALUES (?, ?, ?, ?)
                """,
                (name, account_type, opening_balance_cents, str(data.get("notes") or "")),
            )

        audit("account_created", "account", name)
        return jsonify({"success": True})

    @app.put("/api/accounts/<int:aid>")
    @require_roles("Admin", "Editor")
    def account_update(aid):
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

        with get_db() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET name=?, account_type=?, opening_balance_cents=?, notes=?, is_active=?
                WHERE id=?
                """,
                (
                    name,
                    account_type,
                    opening_balance_cents,
                    str(data.get("notes") or ""),
                    1 if data.get("is_active", True) else 0,
                    aid,
                ),
            )

        audit("account_updated", "account", aid)
        return jsonify({"success": True})

    @app.delete("/api/accounts/<int:aid>")
    @require_roles("Admin")
    def account_delete(aid):
        with get_db() as conn:
            conn.execute("UPDATE accounts SET is_active=0 WHERE id=?", (aid,))

        audit("account_deactivated", "account", aid)
        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # Payees
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/payees")
    @api_login_required
    def payees_list():
        q = str(request.args.get("q") or "").strip()
        category = str(request.args.get("category") or "").strip()
        limit, offset = get_pagination()

        where = "WHERE 1=1"
        params = []

        if q:
            like = f"%{q}%"
            where += " AND (name LIKE ? OR email LIKE ? OR phone LIKE ? OR notes LIKE ?)"
            params.extend([like, like, like, like])

        if category:
            where += " AND category=?"
            params.append(category)

        conn = get_db()

        total = conn.execute(
            f"SELECT COUNT(*) FROM payees {where}",
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
            conn.execute(
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
        return jsonify({"success": True})

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
            conn.execute("DELETE FROM payees WHERE id=?", (pid,))

        audit("payee_deleted", "payee", pid)
        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # Bills
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/bills")
    @api_login_required
    def bills_list():
        q = str(request.args.get("q") or "").strip()
        status = str(request.args.get("status") or "").strip()
        payee_id = request.args.get("payee_id")
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
            like = f"%{q}%"
            sql += " AND (b.bill_number LIKE ? OR b.payee_name LIKE ? OR b.notes LIKE ?)"
            params.extend([like, like, like])

        if status:
            if status == "Overdue":
                sql += " AND b.status IN ('Pending', 'Partially Paid') AND b.due_date < date('now')"
            elif status in BILL_STATUSES:
                sql += " AND b.status=?"
                params.append(status)
            else:
                raise ValidationError("Invalid status filter")

        if payee_id:
            sql += " AND b.payee_id=?"
            params.append(payee_id)

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

        total = conn.execute(
            f"SELECT COUNT(*) FROM ({sql})",
            params,
        ).fetchone()[0]

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
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/bills")
    @require_roles("Admin", "Editor")
    def bill_create():
        data = json_payload()
        payload = parse_bill_payload(data)

        with get_db() as conn:
            if payload.get("payee_id") and not payload.get("payee_name"):
                payee = conn.execute(
                    "SELECT * FROM payees WHERE id=?",
                    (payload["payee_id"],),
                ).fetchone()

                if not payee:
                    raise ValidationError("payee_id does not exist")

                payload["payee_name"] = payee["name"]
                payload["payee_email"] = payload["payee_email"] or payee["email"]
                payload["payee_address"] = payload["payee_address"] or payee["address"]

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

            with conn:
                if new_status == "Paid":
                    balance = row["total_cents"] - row["paid_cents"]

                    if balance > 0:
                        conn.execute(
                            """
                            INSERT INTO bill_payments (
                                bill_id, amount_cents, payment_date, account_id, notes
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

            audit("bill_status_changed", "bill", bid, new_status)
            return jsonify({"success": True})

        payload = parse_bill_payload(data)

        if payload.get("payee_id") and not payload.get("payee_name"):
            payee = conn.execute(
                "SELECT * FROM payees WHERE id=?",
                (payload["payee_id"],),
            ).fetchone()

            if not payee:
                raise ValidationError("payee_id does not exist")

            payload["payee_name"] = payee["name"]
            payload["payee_email"] = payload["payee_email"] or payee["email"]
            payload["payee_address"] = payload["payee_address"] or payee["address"]

        if not payload["payee_name"]:
            raise ValidationError("payee_name is required")

        paid_cents = row["paid_cents"]
        paid_at = row["paid_at"]

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
                        bill_id, item_name, description, quantity, unit_price_cents, total_cents
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

        bill = conn.execute(
            "SELECT * FROM bills WHERE id=? AND is_deleted=0",
            (bid,),
        ).fetchone()

        if not bill:
            return jsonify({"error": "Not found"}), 404

        if bill["status"] == "Void":
            raise ValidationError("Cannot pay a void bill")

        balance_cents = bill["total_cents"] - bill["paid_cents"]
        if balance_cents <= 0:
            raise ValidationError("Bill is already fully paid")

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

        with conn:
            conn.execute(
                """
                INSERT INTO bill_payments (
                    bill_id, amount_cents, payment_date, account_id, notes
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    bid,
                    amount_cents,
                    payment_date,
                    data.get("account_id") or bill["account_id"],
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

        audit("bill_payment_recorded", "bill", bid, str(amount_cents))
        return jsonify({"success": True})

    @app.post("/api/bills/<int:bid>/void")
    @require_roles("Admin", "Editor")
    def bill_void(bid):
        with get_db() as conn:
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

        total_paid_cents = sum(r["paid_cents"] for r in rows)
        total_outstanding_cents = sum((r["total_cents"] - r["paid_cents"]) for r in rows if r["status"] != "Void")

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
            cents_to_plain(total_paid_cents),
            cents_to_plain(total_outstanding_cents),
            "",
            "",
        ])

        return csv_response(
            "bills.csv",
            data_rows,
            [
                "Bill #", "Payee", "Email", "Category", "Bill Date", "Due Date",
                "Subtotal", "Discount", "Tax", "Total", "Paid", "Balance", "Status",
            ],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Expenses
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/expenses")
    @api_login_required
    def expenses_list():
        q = str(request.args.get("q") or "").strip()
        category = str(request.args.get("category") or "").strip()
        member = str(request.args.get("member") or "").strip()
        date_from = parse_iso_date(request.args.get("from"), "from", allow_none=True)
        date_to = parse_iso_date(request.args.get("to"), "to", allow_none=True)
        limit, offset = get_pagination()

        sql = "SELECT * FROM expenses WHERE is_deleted=0"
        params = []

        if q:
            like = f"%{q}%"
            sql += " AND (title LIKE ? OR store LIKE ? OR notes LIKE ? OR member LIKE ? OR tags LIKE ?)"
            params.extend([like, like, like, like, like])

        if category:
            sql += " AND category=?"
            params.append(category)

        if member:
            sql += " AND member=?"
            params.append(member)

        if date_from:
            sql += " AND expense_date >= ?"
            params.append(date_from)

        if date_to:
            sql += " AND expense_date <= ?"
            params.append(date_to)

        conn = get_db()

        total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]

        sql += " ORDER BY expense_date DESC, created_at DESC LIMIT ? OFFSET ?"
        rows = conn.execute(sql, params + [limit, offset]).fetchall()

        items = rows_to_list(rows)
        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

        return jsonify({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/expenses")
    @require_roles("Admin", "Editor")
    def expense_create():
        data = json_payload()

        with get_db() as conn:
            expense_id = insert_expense(conn, data)
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
        return jsonify(expense)

    @app.put("/api/expenses/<int:eid>")
    @require_roles("Admin", "Editor")
    def expense_update(eid):
        data = json_payload()
        conn = get_db()

        row = conn.execute(
            "SELECT 1 FROM expenses WHERE id=? AND is_deleted=0",
            (eid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        payload = parse_transaction_payload(data, "expense_date")

        with conn:
            conn.execute(
                """
                UPDATE expenses
                SET title=?, category=?, amount_cents=?, expense_date=?, store=?,
                    receipt_ref=?, notes=?, member=?, account_id=?, tags=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
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
                    payload["account_id"],
                    payload["tags"],
                    eid,
                ),
            )

        audit("expense_updated", "expense", eid)
        return jsonify({"success": True})

    @app.delete("/api/expenses/<int:eid>")
    @require_roles("Admin", "Editor")
    def expense_delete(eid):
        with get_db() as conn:
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
                "ID", "Title", "Category", "Amount", "Date",
                "Store / Vendor", "Receipt Ref", "Family Member", "Tags", "Notes",
            ],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Income
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/income")
    @api_login_required
    def income_list():
        q = str(request.args.get("q") or "").strip()
        category = str(request.args.get("category") or "").strip()
        member = str(request.args.get("member") or "").strip()
        date_from = parse_iso_date(request.args.get("from"), "from", allow_none=True)
        date_to = parse_iso_date(request.args.get("to"), "to", allow_none=True)
        limit, offset = get_pagination()

        sql = "SELECT * FROM income WHERE is_deleted=0"
        params = []

        if q:
            like = f"%{q}%"
            sql += " AND (title LIKE ? OR source LIKE ? OR notes LIKE ? OR member LIKE ? OR tags LIKE ?)"
            params.extend([like, like, like, like, like])

        if category:
            sql += " AND category=?"
            params.append(category)

        if member:
            sql += " AND member=?"
            params.append(member)

        if date_from:
            sql += " AND income_date >= ?"
            params.append(date_from)

        if date_to:
            sql += " AND income_date <= ?"
            params.append(date_to)

        conn = get_db()

        total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]

        sql += " ORDER BY income_date DESC, created_at DESC LIMIT ? OFFSET ?"
        rows = conn.execute(sql, params + [limit, offset]).fetchall()

        items = rows_to_list(rows)
        for item in items:
            item["amount"] = cents_to_float(item["amount_cents"])

        return jsonify({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/income")
    @require_roles("Admin", "Editor")
    def income_create():
        data = json_payload()

        with get_db() as conn:
            income_id = insert_income(conn, data)
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
        return jsonify(income_item)

    @app.put("/api/income/<int:iid>")
    @require_roles("Admin", "Editor")
    def income_update(iid):
        data = json_payload()
        conn = get_db()

        row = conn.execute(
            "SELECT 1 FROM income WHERE id=? AND is_deleted=0",
            (iid,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Not found"}), 404

        payload = parse_transaction_payload(data, "income_date")

        with conn:
            conn.execute(
                """
                UPDATE income
                SET title=?, category=?, amount_cents=?, income_date=?, source=?,
                    notes=?, member=?, account_id=?, tags=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    payload["title"],
                    payload["category"],
                    payload["amount_cents"],
                    payload["date"],
                    str(data.get("source") or "").strip(),
                    payload["notes"],
                    payload["member"],
                    payload["account_id"],
                    payload["tags"],
                    iid,
                ),
            )

        audit("income_updated", "income", iid)
        return jsonify({"success": True})

    @app.delete("/api/income/<int:iid>")
    @require_roles("Admin", "Editor")
    def income_delete(iid):
        with get_db() as conn:
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
        data_rows.append(["", "TOTAL", "", cents_to_plain(total_cents), "", "", "", "", "", ""])

        return csv_response(
            "income.csv",
            data_rows,
            [
                "ID", "Title", "Category", "Amount", "Date",
                "Source", "Family Member", "Tags", "Notes",
            ],
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Budgets
    # ─────────────────────────────────────────────────────────────────────────

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

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO budgets (category, member, period, amount_cents, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    category,
                    str(data.get("member") or "").strip(),
                    period,
                    amount_cents,
                    str(data.get("notes") or ""),
                ),
            )

        audit("budget_created", "budget", category)
        return jsonify({"success": True})

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

        with get_db() as conn:
            conn.execute(
                """
                UPDATE budgets
                SET category=?, member=?, period=?, amount_cents=?, notes=?, is_active=?
                WHERE id=?
                """,
                (
                    category,
                    str(data.get("member") or "").strip(),
                    period,
                    amount_cents,
                    str(data.get("notes") or ""),
                    1 if data.get("is_active", True) else 0,
                    bud_id,
                ),
            )

        audit("budget_updated", "budget", bud_id)
        return jsonify({"success": True})

    @app.delete("/api/budgets/<int:bud_id>")
    @require_roles("Admin", "Editor")
    def budget_delete(bud_id):
        with get_db() as conn:
            conn.execute("DELETE FROM budgets WHERE id=?", (bud_id,))

        audit("budget_deleted", "budget", bud_id)
        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # Goals
    # ─────────────────────────────────────────────────────────────────────────

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

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO goals (name, target_cents, current_cents, target_date, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name,
                    target_cents,
                    current_cents,
                    target_date,
                    str(data.get("notes") or ""),
                ),
            )

        audit("goal_created", "goal", name)
        return jsonify({"success": True})

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

        with get_db() as conn:
            conn.execute(
                """
                UPDATE goals
                SET name=?, target_cents=?, current_cents=?, target_date=?, notes=?, is_active=?
                WHERE id=?
                """,
                (
                    name,
                    target_cents,
                    current_cents,
                    target_date,
                    str(data.get("notes") or ""),
                    1 if data.get("is_active", True) else 0,
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
            conn.execute("UPDATE goals SET is_active=0 WHERE id=?", (gid,))

        audit("goal_deactivated", "goal", gid)
        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # Recurring rules
    # ─────────────────────────────────────────────────────────────────────────

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
        interval_value = int(data.get("interval_value", 1) or 1)
        next_run_date = parse_iso_date(data.get("next_run_date"), "next_run_date")
        payload = data.get("payload") or {}

        if not name:
            raise ValidationError("name is required")

        if entity_type not in {"expense", "income", "bill"}:
            raise ValidationError("entity_type must be expense, income, or bill")

        if frequency not in {"daily", "weekly", "monthly", "yearly"}:
            raise ValidationError("Invalid frequency")

        if interval_value <= 0:
            raise ValidationError("interval_value must be greater than 0")

        if not isinstance(payload, dict):
            raise ValidationError("payload must be an object")

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO recurring_rules (
                    name, entity_type, payload, frequency, interval_value, next_run_date
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    entity_type,
                    json.dumps(payload),
                    frequency,
                    interval_value,
                    next_run_date,
                ),
            )

        audit("recurring_created", "recurring", name)
        return jsonify({"success": True})

    @app.put("/api/recurring/<int:rid>")
    @require_roles("Admin", "Editor")
    def recurring_update(rid):
        data = json_payload()

        name = str(data.get("name") or "").strip()
        entity_type = str(data.get("entity_type") or "").strip()
        frequency = str(data.get("frequency") or "monthly").strip()
        interval_value = int(data.get("interval_value", 1) or 1)
        next_run_date = parse_iso_date(data.get("next_run_date"), "next_run_date")
        payload = data.get("payload") or {}
        is_active = 1 if data.get("is_active", True) else 0

        if not name:
            raise ValidationError("name is required")

        if entity_type not in {"expense", "income", "bill"}:
            raise ValidationError("entity_type must be expense, income, or bill")

        if frequency not in {"daily", "weekly", "monthly", "yearly"}:
            raise ValidationError("Invalid frequency")

        if interval_value <= 0:
            raise ValidationError("interval_value must be greater than 0")

        if not isinstance(payload, dict):
            raise ValidationError("payload must be an object")

        with get_db() as conn:
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
            conn.execute("DELETE FROM recurring_rules WHERE id=?", (rid,))

        audit("recurring_deleted", "recurring", rid)
        return jsonify({"success": True})

    @app.post("/api/recurring/run")
    @require_roles("Admin", "Editor")
    def recurring_run():
        today = date.today().isoformat()
        created = []

        with get_db() as conn:
            rules = conn.execute(
                """
                SELECT * FROM recurring_rules
                WHERE is_active=1 AND next_run_date <= ?
                ORDER BY next_run_date
                """,
                (today,),
            ).fetchall()

            for rule in rules:
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

                    if rule["entity_type"] == "expense":
                        payload.setdefault("expense_date", rule["next_run_date"])
                        entity_id = insert_expense(conn, payload)
                        created.append({"entity_type": "expense", "id": entity_id})

                    elif rule["entity_type"] == "income":
                        payload.setdefault("income_date", rule["next_run_date"])
                        entity_id = insert_income(conn, payload)
                        created.append({"entity_type": "income", "id": entity_id})

                    elif rule["entity_type"] == "bill":
                        payload.setdefault("bill_date", rule["next_run_date"])
                        payload.setdefault("due_date", rule["next_run_date"])
                        parsed_bill = parse_bill_payload(payload)

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
                        created.append({"entity_type": "bill", "id": entity_id})

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

                    # Refresh rule date for catch-up loop
                    rule = dict(rule)
                    rule["next_run_date"] = new_next_run_date

                notify(
                    title=f"Recurring rule processed: {rule['name']}",
                    body=f"Created recurring {rule['entity_type']} item(s).",
                    link="/api/recurring",
                )

        audit("recurring_run", "recurring", "", f"Created {len(created)} item(s)")
        return jsonify({"success": True, "created": created})

    # ─────────────────────────────────────────────────────────────────────────
    # Notifications
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/notifications")
    @api_login_required
    def notifications_list():
        limit, offset = get_pagination()
        conn = get_db()

        total = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM notifications
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

        return jsonify({
            "items": rows_to_list(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @app.post("/api/notifications/<int:nid>/read")
    @api_login_required
    def notification_mark_read(nid):
        with get_db() as conn:
            conn.execute(
                "UPDATE notifications SET is_read=1 WHERE id=?",
                (nid,),
            )

        return jsonify({"success": True})

    @app.post("/api/notifications/read-all")
    @api_login_required
    def notifications_mark_all_read():
        with get_db() as conn:
            conn.execute("UPDATE notifications SET is_read=1")

        return jsonify({"success": True})

    # ─────────────────────────────────────────────────────────────────────────
    # Dashboard
    # ─────────────────────────────────────────────────────────────────────────

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
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM bill_payments
            WHERE payment_date >= ? AND payment_date < ?
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
            """
            SELECT COALESCE(SUM(amount_cents), 0)
            FROM bill_payments
            WHERE payment_date >= ? AND payment_date < ?
            """,
            (year_start_iso, next_year_iso),
        ).fetchone()[0]

        monthly = []
        current_month_start = today.replace(day=1)

        for i in range(5, -1, -1):
            d = add_months(current_month_start, -i)
            m = d.strftime("%Y-%m")

            inc = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM income
                WHERE is_deleted=0 AND strftime('%Y-%m', income_date)=?
                """,
                (m,),
            ).fetchone()[0]

            exp = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM expenses
                WHERE is_deleted=0 AND strftime('%Y-%m', expense_date)=?
                """,
                (m,),
            ).fetchone()[0]

            bp = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM bill_payments
                WHERE strftime('%Y-%m', payment_date)=?
                """,
                (m,),
            ).fetchone()[0]

            total_out = exp + bp
            savings = inc - total_out

            monthly.append({
                "month": d.strftime("%b"),
                "income": cents_to_float(inc),
                "expenses": cents_to_float(total_out),
                "savings": cents_to_float(savings),
            })

        top_cats = rows_to_list(conn.execute(
            """
            SELECT category, SUM(amount_cents) AS total_cents
            FROM expenses
            WHERE is_deleted=0 AND strftime('%Y-%m', expense_date)=?
            GROUP BY category
            ORDER BY total_cents DESC
            LIMIT 6
            """,
            (this_month,),
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

        monthly_budget_cents = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents),0)
            FROM budgets
            WHERE is_active=1 AND period='monthly'
            """,
        ).fetchone()[0]

        monthly_budget_actual_cents = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents),0)
            FROM expenses
            WHERE is_deleted=0 AND strftime('%Y-%m', expense_date)=?
            """,
            (this_month,),
        ).fetchone()[0] + conn.execute(
            """
            SELECT COALESCE(SUM(bp.amount_cents),0)
            FROM bill_payments bp
            JOIN bills b ON b.id = bp.bill_id AND b.is_deleted=0
            WHERE strftime('%Y-%m', bp.payment_date)=?
            """,
            (this_month,),
        ).fetchone()[0]

        active_goals = rows_to_list(conn.execute(
            "SELECT * FROM goals WHERE is_active=1 ORDER BY name LIMIT 5"
        ).fetchall())

        for goal in active_goals:
            goal["target"] = cents_to_float(goal["target_cents"])
            goal["current"] = cents_to_float(goal["current_cents"])
            goal["progress_pct"] = round(
                (goal["current_cents"] / goal["target_cents"] * 100), 1
            ) if goal["target_cents"] > 0 else 0

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
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Reports
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/reports")
    @api_login_required
    def reports():
        year = str(request.args.get("year", str(date.today().year)))

        if not re.match(r"^\d{4}$", year):
            raise ValidationError("year must be YYYY")

        conn = get_db()
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        monthly = []

        for m in range(1, 13):
            month_key = f"{year}-{str(m).zfill(2)}"

            inc = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM income
                WHERE is_deleted=0 AND strftime('%Y-%m', income_date)=?
                """,
                (month_key,),
            ).fetchone()[0]

            exp = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM expenses
                WHERE is_deleted=0 AND strftime('%Y-%m', expense_date)=?
                """,
                (month_key,),
            ).fetchone()[0]

            bp = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM bill_payments
                WHERE strftime('%Y-%m', payment_date)=?
                """,
                (month_key,),
            ).fetchone()[0]

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
            """
            SELECT b.bill_category AS category, SUM(bp.amount_cents) AS total_cents
            FROM bill_payments bp
            JOIN bills b ON b.id = bp.bill_id AND b.is_deleted=0
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
            """
            SELECT COALESCE(SUM(amount_cents),0)
            FROM bill_payments
            WHERE strftime('%Y', payment_date)=?
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
                """
                SELECT DISTINCT strftime('%Y', payment_date)
                FROM bill_payments
                WHERE payment_date IS NOT NULL
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

            actual_rows = conn.execute(
                """
                SELECT category, SUM(cents) AS actual_cents
                FROM (
                    SELECT category, SUM(amount_cents) AS cents
                    FROM expenses
                    WHERE is_deleted=0 AND strftime('%Y-%m', expense_date)=?
                    GROUP BY category

                    UNION ALL

                    SELECT b.bill_category AS category, SUM(bp.amount_cents) AS cents
                    FROM bill_payments bp
                    JOIN bills b ON b.id = bp.bill_id AND b.is_deleted=0
                    WHERE strftime('%Y-%m', bp.payment_date)=?
                    GROUP BY b.bill_category
                )
                GROUP BY category
                """,
                (month, month),
            ).fetchall()

        else:
            year = request.args.get("year", str(date.today().year))

            if not re.match(r"^\d{4}$", year):
                raise ValidationError("year must be YYYY")

            budgets = rows_to_list(conn.execute(
                "SELECT * FROM budgets WHERE is_active=1 AND period='yearly'",
            ).fetchall())

            actual_rows = conn.execute(
                """
                SELECT category, SUM(cents) AS actual_cents
                FROM (
                    SELECT category, SUM(amount_cents) AS cents
                    FROM expenses
                    WHERE is_deleted=0 AND strftime('%Y', expense_date)=?
                    GROUP BY category

                    UNION ALL

                    SELECT b.bill_category AS category, SUM(bp.amount_cents) AS cents
                    FROM bill_payments bp
                    JOIN bills b ON b.id = bp.bill_id AND b.is_deleted=0
                    WHERE strftime('%Y', bp.payment_date)=?
                    GROUP BY b.bill_category
                )
                GROUP BY category
                """,
                (year, year),
            ).fetchall()

        actual_by_category = {
            row["category"]: int(row["actual_cents"]) for row in actual_rows
        }

        result = []
        for budget in budgets:
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
        months = min(max(int(request.args.get("months", 6)), 1), 24)
        conn = get_db()

        current_month_start = date.today().replace(day=1)
        result = []

        for i in range(months - 1, -1, -1):
            d = add_months(current_month_start, -i)
            month_key = d.strftime("%Y-%m")

            inc = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM income
                WHERE is_deleted=0 AND strftime('%Y-%m', income_date)=?
                """,
                (month_key,),
            ).fetchone()[0]

            exp = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM expenses
                WHERE is_deleted=0 AND strftime('%Y-%m', expense_date)=?
                """,
                (month_key,),
            ).fetchone()[0]

            bill_payments = conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0)
                FROM bill_payments
                WHERE strftime('%Y-%m', payment_date)=?
                """,
                (month_key,),
            ).fetchone()[0]

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
            """
            SELECT b.payee_name AS payee,
                   b.bill_category AS category,
                   COUNT(DISTINCT b.id) AS bill_count,
                   SUM(bp.amount_cents) AS paid_cents
            FROM bill_payments bp
            JOIN bills b ON b.id = bp.bill_id AND b.is_deleted=0
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

        net_worth_cents = 0

        for account in accounts:
            income_cents = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM income WHERE is_deleted=0 AND account_id=?",
                (account["id"],),
            ).fetchone()[0]

            expense_cents = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM expenses WHERE is_deleted=0 AND account_id=?",
                (account["id"],),
            ).fetchone()[0]

            payment_cents = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM bill_payments WHERE account_id=?",
                (account["id"],),
            ).fetchone()[0]

            balance_cents = (
                account["opening_balance_cents"]
                + income_cents
                - expense_cents
                - payment_cents
            )

            account["balance_cents"] = balance_cents
            account["balance"] = cents_to_float(balance_cents)

            net_worth_cents += balance_cents

        return jsonify({
            "accounts": accounts,
            "net_worth": cents_to_float(net_worth_cents),
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Reminders
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/reminders")
    @api_login_required
    def reminders():
        conn = get_db()
        today_str = date.today().isoformat()
        in_14_days = (date.today() + timedelta(days=14)).isoformat()

        overdue = rows_to_list(conn.execute(
            """
            SELECT id, bill_number, payee_name, due_date, total_cents, paid_cents,
                   status, bill_category,
                   CAST(julianday(date('now')) - julianday(due_date) AS INTEGER) AS days_overdue
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
                   status, bill_category,
                   CAST(julianday(due_date) - julianday(date('now')) AS INTEGER) AS days_left
            FROM bills
            WHERE is_deleted=0
              AND status IN ('Pending', 'Partially Paid')
              AND due_date BETWEEN ? AND ?
            ORDER BY due_date
            """,
            (today_str, in_14_days),
        ).fetchall())

        for row in overdue + upcoming:
            row["total_amount"] = cents_to_float(row.pop("total_cents"))
            row["paid_amount"] = cents_to_float(row.pop("paid_cents"))
            row["balance_amount"] = cents_to_float(row["total_cents"] if "total_cents" in row else 0)

            # Recompute balance cleanly after popping values.
            # SQLite row dict already lost cents fields, so derive from floats carefully.
            row["balance_amount"] = round(row["total_amount"] - row["paid_amount"], 2)

        return jsonify({
            "overdue": overdue,
            "upcoming": upcoming,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # Admin
    # ─────────────────────────────────────────────────────────────────────────

    @app.get("/api/admin/backup")
    @require_roles("Admin")
    def admin_backup():
        db_path = current_app.config["DATABASE"]

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp_path = tmp.name
        tmp.close()

        try:
            src = sqlite3.connect(db_path)
            dst = sqlite3.connect(tmp_path)
            src.backup(dst)
            dst.close()
            src.close()

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
        conn = get_db()

        total = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM audit_logs
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

        return jsonify({
            "items": rows_to_list(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
        })


# ─────────────────────────────────────────────────────────────────────────────
# Printable bill HTML
# ─────────────────────────────────────────────────────────────────────────────

def generate_print_bill(b, items, s):
    sym = s.get("currency_symbol", "$")

    status_colors = {
        "Pending": "#f59e0b",
        "Partially Paid": "#3b82f6",
        "Paid": "#10b981",
        "Void": "#94a3b8",
    }

    sc = status_colors.get(b.get("status", "Pending"), "#f59e0b")

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
          <td style='text-align:right;color:#f43f5e'>-{sym}{fmt_cents(b['discount_cents'])}</td>
        </tr>
        """

    notes = html.escape(str(b.get("notes", "") or s.get("family_notes", "")))
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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Bill {bill_number}</title>
  <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:system-ui,-apple-system,sans-serif;background:#fff;color:#1e293b;padding:48px;max-width:860px;margin:0 auto;font-size:14px}}
    .header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:48px}}
    .fam-name{{font-size:26px;font-weight:700;color:#0f172a;letter-spacing:-0.5px}}
    .fam-details{{color:#64748b;margin-top:8px;line-height:1.7}}
    .bill-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:#94a3b8;margin-bottom:4px}}
    .bill-number{{font-size:32px;font-weight:700;color:#0f172a;text-align:right}}
    .status-badge{{display:inline-block;padding:4px 14px;border-radius:100px;color:#fff;font-size:11px;font-weight:700;background:{sc};text-transform:uppercase;letter-spacing:0.5px;margin-top:8px}}
    .dates{{text-align:right;color:#64748b;margin-top:12px;line-height:1.8}}
    .dates span{{color:#1e293b;font-weight:600}}
    .divider{{height:1px;background:#e2e8f0;margin:32px 0}}
    .parties{{display:grid;grid-template-columns:1fr 1fr;gap:40px;margin-bottom:32px}}
    .party-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#94a3b8;margin-bottom:10px}}
    .party-value{{line-height:1.8;color:#1e293b}}
    .party-value strong{{font-size:15px;font-weight:700}}
    table{{width:100%;border-collapse:collapse;margin-bottom:24px}}
    thead{{background:#1e1b4b}}
    thead th{{padding:12px 16px;text-align:left;color:#fff;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px}}
    tbody tr:nth-child(even){{background:#f8fafc}}
    tbody td{{padding:13px 16px;border-bottom:1px solid #f1f5f9;vertical-align:top}}
    .totals-wrap{{display:flex;justify-content:flex-end;margin-bottom:40px}}
    .totals{{width:320px}}
    .totals table{{margin:0}}
    .totals td{{padding:8px 16px;color:#475569}}
    .totals tr:last-child td{{font-size:16px;font-weight:700;color:#0f172a;border-top:2px solid #1e1b4b;padding-top:12px}}
    .footer{{display:grid;grid-template-columns:1fr 1fr;gap:40px}}
    .section-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#94a3b8;margin-bottom:8px}}
    .section-value{{color:#475569;line-height:1.7}}
    .print-btn{{position:fixed;bottom:24px;right:24px;padding:12px 24px;background:#4f46e5;color:#fff;border:none;border-radius:8px;cursor:pointer;font-family:inherit;font-size:14px;font-weight:600;box-shadow:0 4px 12px rgba(79,70,229,.3)}}
    .print-btn:hover{{background:#4338ca}}
    @media print{{.print-btn{{display:none}}body{{padding:20px}}}}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <div class="fam-name">🏠 {family_name}</div>
      <div class="fam-details">
        {family_address}
        {'<br>' + primary_email if primary_email else ''}
        {'<br>' + primary_phone if primary_phone else ''}
      </div>
    </div>

    <div>
      <div class="bill-label">Bill / Payment</div>
      <div class="bill-number">#{bill_number}</div>
      <div style="text-align:right"><span class="status-badge">{status}</span></div>
      <div class="dates">
        Bill Date: <span>{bill_date}</span><br>
        Due Date: <span>{due_date}</span><br>
        Category: <span>{bill_category}</span>
      </div>
    </div>
  </div>

  <div class="parties">
    <div>
      <div class="party-label">Billed By / Payee</div>
      <div class="party-value">
        <strong>{payee_name}</strong><br>
        {payee_email}<br>
        {payee_address}
      </div>
    </div>

    <div>
      <div class="party-label">Category</div>
      <div class="party-value">{bill_category}</div>
    </div>
  </div>

  <div class="divider"></div>

  <table>
    <thead>
      <tr>
        <th>Description</th>
        <th style="text-align:center">Qty</th>
        <th style="text-align:right">Unit Price</th>
        <th style="text-align:right">Amount</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>

  <div class="totals-wrap">
    <div class="totals">
      <table>
        <tr><td>Subtotal</td><td style="text-align:right">{sym}{fmt_cents(b['subtotal_cents'])}</td></tr>
        {discount_row}
        <tr><td>Tax ({b['tax_rate']}%)</td><td style="text-align:right">{sym}{fmt_cents(b['tax_cents'])}</td></tr>
        <tr><td>Total</td><td style="text-align:right">{sym}{fmt_cents(b['total_cents'])}</td></tr>
        <tr><td>Paid</td><td style="text-align:right">{sym}{fmt_cents(b['paid_cents'])}</td></tr>
        <tr><td><strong>Balance Due</strong></td><td style="text-align:right">{sym}{fmt_cents(b['total_cents'] - b['paid_cents'])}</td></tr>
      </table>
    </div>
  </div>

  <div class="footer">
    <div>
      <div class="section-label">Notes</div>
      <div class="section-value">{notes or 'Thank you!'}</div>
    </div>
    <div>
      <div class="section-label">Currency</div>
      <div class="section-value">{s.get('currency_code','USD')} — {s.get('currency_symbol','$')}</div>
    </div>
  </div>

  <button class="print-btn" onclick="window.print()">🖨️ Print / Save PDF</button>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = create_app()

    host = os.getenv("FF_HOST", "127.0.0.1")
    port = int(os.getenv("FF_PORT", "5000"))
    debug = os.getenv("FF_DEBUG", "0") == "1"

    print("\n" + "=" * 72)
    print("  🏠 FamilyFinance Enhanced — Personal & Family Budget Tracker")
    print(f"  🌐 Open: http://{host}:{port}")
    print("  🔐 Login endpoint: POST /api/auth/login")
    print("=" * 72 + "\n")

    app.run(debug=debug, host=host, port=port)
