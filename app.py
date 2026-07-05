#!/usr/bin/env python3
"""
FamilyFinance — Personal & Family Budget Tracker
Run:  pip install flask && python app.py
Open: http://localhost:5000
"""

from flask import Flask, jsonify, request, render_template, make_response
import sqlite3, json, csv, io, re, os, html, secrets, base64
from datetime import datetime, date, timedelta
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FF_SECRET_KEY", secrets.token_hex(16))

DB_PATH       = Path(__file__).parent / "familyfinance.db"

DEFAULT_SETTINGS = {
    "family_name":         "Our Family",
    "family_address":      "123 Home Street\nCity, State 00000",
    "primary_email":       "family@example.com",
    "primary_phone":       "",
    "currency_symbol":     "$",
    "currency_code":       "USD",
    "monthly_income_goal": 5000.0,
    "savings_target_pct":  20.0,
    "bill_prefix":         "BILL",
    "family_notes":        "Track your family finances with ease!",
}

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

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS payees (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            email        TEXT,
            phone        TEXT,
            address      TEXT,
            category     TEXT,
            notes        TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_number     TEXT UNIQUE NOT NULL,
            payee_id        INTEGER REFERENCES payees(id) ON DELETE SET NULL,
            payee_name      TEXT NOT NULL,
            payee_email     TEXT,
            payee_address   TEXT,
            bill_category   TEXT DEFAULT 'Other',
            bill_date       DATE NOT NULL,
            due_date        DATE NOT NULL,
            subtotal        REAL DEFAULT 0,
            discount_pct    REAL DEFAULT 0,
            discount_amount REAL DEFAULT 0,
            tax_rate        REAL DEFAULT 0,
            tax_amount      REAL DEFAULT 0,
            total_amount    REAL DEFAULT 0,
            status          TEXT DEFAULT 'Pending',
            notes           TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at         TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bill_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id     INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
            item_name   TEXT NOT NULL,
            description TEXT,
            quantity    REAL DEFAULT 1,
            unit_price  REAL DEFAULT 0,
            total       REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            category     TEXT NOT NULL,
            amount       REAL NOT NULL,
            expense_date DATE NOT NULL,
            store        TEXT,
            receipt_ref  TEXT,
            notes        TEXT,
            member       TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS income (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            category     TEXT NOT NULL,
            amount       REAL NOT NULL,
            income_date  DATE NOT NULL,
            source       TEXT,
            notes        TEXT,
            member       TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key          TEXT PRIMARY KEY,
            value        TEXT NOT NULL,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role         TEXT NOT NULL DEFAULT 'Admin',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_bills_status_due ON bills(status, due_date);
        CREATE INDEX IF NOT EXISTS idx_bills_payee ON bills(payee_id);
        CREATE INDEX IF NOT EXISTS idx_bills_created ON bills(created_at);
        CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date);
        CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);
        CREATE INDEX IF NOT EXISTS idx_income_date ON income(income_date);
        CREATE INDEX IF NOT EXISTS idx_income_category ON income(category);
        """)
        admin_user = os.getenv("FF_ADMIN_USER", "admin")
        admin_pass = os.getenv("FF_ADMIN_PASS", "admin123")
        exists = conn.execute("SELECT 1 FROM users WHERE username=?", (admin_user,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'Admin')",
                (admin_user, generate_password_hash(admin_pass))
            )

def rows_to_list(rows):
    return [dict(r) for r in rows]

def row_to_dict(row):
    return dict(row) if row else None

# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

def load_settings():
    s = DEFAULT_SETTINGS.copy()
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    for r in rows:
        key = r["key"]
        if key in DEFAULT_SETTINGS:
            try:
                s[key] = json.loads(r["value"])
            except Exception:
                s[key] = r["value"]
    return s

def save_settings_to_db(data):
    with get_db() as conn:
        for k, v in data.items():
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                (k, json.dumps(v))
            )

def as_decimal(val, default="0"):
    try:
        return Decimal(str(val if val is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)

def q2(val):
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def require_api_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not request.path.startswith("/api/"):
            return fn(*args, **kwargs)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            resp = jsonify({"error": "Authentication required"})
            resp.status_code = 401
            resp.headers["WWW-Authenticate"] = 'Basic realm="FamilyFinance"'
            return resp
        try:
            raw = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
            username, password = raw.split(":", 1)
        except Exception:
            return jsonify({"error": "Invalid auth header"}), 401
        with get_db() as conn:
            user = conn.execute(
                "SELECT username, password_hash FROM users WHERE username=?",
                (username,)
            ).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Invalid credentials"}), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_cookie = request.cookies.get("csrf_token", "")
            csrf_header = request.headers.get("X-CSRF-Token", "")
            if not csrf_cookie or csrf_cookie != csrf_header:
                return jsonify({"error": "CSRF validation failed"}), 403
        return fn(*args, **kwargs)
    return wrapped

@app.before_request
def api_security_guard():
    if not request.path.startswith("/api/"):
        return None
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        resp = jsonify({"error": "Authentication required"})
        resp.status_code = 401
        resp.headers["WWW-Authenticate"] = 'Basic realm="FamilyFinance"'
        return resp
    try:
        raw = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        username, password = raw.split(":", 1)
    except Exception:
        return jsonify({"error": "Invalid auth header"}), 401
    with get_db() as conn:
        user = conn.execute(
            "SELECT username, password_hash FROM users WHERE username=?",
            (username,)
        ).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        csrf_cookie = request.cookies.get("csrf_token", "")
        csrf_header = request.headers.get("X-CSRF-Token", "")
        if not csrf_cookie or csrf_cookie != csrf_header:
            return jsonify({"error": "CSRF validation failed"}), 403
    return None

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def next_bill_number(conn, prefix="BILL"):
    row = conn.execute("SELECT bill_number FROM bills ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        nums = re.findall(r'\d+', row[0])
        num = int(nums[-1]) + 1 if nums else 1
    else:
        num = 1
    return f"{prefix}-{num:04d}"

def calc_bill_totals(items, discount_pct, tax_rate):
    subtotal = sum(as_decimal(i.get("quantity", 1)) * as_decimal(i.get("unit_price", 0)) for i in items)
    disc_pct = as_decimal(discount_pct or 0)
    disc_amt = subtotal * disc_pct / Decimal("100")
    taxable  = subtotal - disc_amt
    tax_pct  = as_decimal(tax_rate or 0)
    tax_amt  = taxable * tax_pct / Decimal("100")
    total    = taxable + tax_amt
    return q2(subtotal), q2(disc_pct), q2(disc_amt), q2(tax_pct), q2(tax_amt), q2(total)

def csv_response(filename, rows, headers):
    """Return a UTF-8 CSV response with BOM for Excel compatibility."""
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL)
    # Metadata header
    w.writerow([f"FamilyFinance Export — {filename}", "", "", "", f"Generated: {date.today().isoformat()}"])
    w.writerow([])
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    # UTF-8 BOM for Excel
    content = '\ufeff' + out.getvalue()
    resp = make_response(content)
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    resp.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    return resp

# ─────────────────────────────────────────────────────────────────────────────
# MAIN ROUTE
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    if not request.cookies.get("csrf_token"):
        resp.set_cookie("csrf_token", secrets.token_urlsafe(24), samesite="Lax")
    return resp

# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    with get_db() as conn:
        today_str  = date.today().isoformat()
        month_start = date.today().replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        year_start = date(date.today().year, 1, 1)
        year_end = date(date.today().year, 12, 31)

        # ── Monthly metrics ──
        monthly_income = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM income WHERE income_date BETWEEN ? AND ?",
            (month_start.isoformat(), month_end.isoformat())
        ).fetchone()[0]

        monthly_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date BETWEEN ? AND ?",
            (month_start.isoformat(), month_end.isoformat())
        ).fetchone()[0]

        monthly_bills_paid = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE status='Paid' AND paid_at BETWEEN ? AND ?",
            (month_start.isoformat(), f"{month_end.isoformat()} 23:59:59")
        ).fetchone()[0]

        monthly_outflow = float(monthly_expenses) + float(monthly_bills_paid)
        monthly_savings = float(monthly_income) - monthly_outflow
        savings_rate    = round(monthly_savings / float(monthly_income) * 100, 1) if float(monthly_income) > 0 else 0

        # ── Bills ──
        pending_bills = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE status='Pending'"
        ).fetchone()[0]

        overdue_count = conn.execute(
            "SELECT COUNT(*) FROM bills WHERE status='Pending' AND due_date < ?", (today_str,)
        ).fetchone()[0]

        overdue_amount = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE status='Pending' AND due_date < ?", (today_str,)
        ).fetchone()[0]

        # ── Counts ──
        payee_count   = conn.execute("SELECT COUNT(*) FROM payees").fetchone()[0]
        bill_count    = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
        income_count  = conn.execute("SELECT COUNT(*) FROM income").fetchone()[0]
        expense_count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]

        # ── Annual totals ──
        annual_income   = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM income WHERE income_date BETWEEN ? AND ?",
            (year_start.isoformat(), year_end.isoformat())
        ).fetchone()[0]
        annual_expenses = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE expense_date BETWEEN ? AND ?",
            (year_start.isoformat(), year_end.isoformat())
        ).fetchone()[0]
        annual_bills = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE status='Paid' AND paid_at BETWEEN ? AND ?",
            (year_start.isoformat(), f"{year_end.isoformat()} 23:59:59")
        ).fetchone()[0]

        # ── Last 6 months trend ──
        monthly = []
        for i in range(5, -1, -1):
            d = date.today().replace(day=1) - timedelta(days=i * 30)
            m = d.strftime("%Y-%m")
            inc = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM income WHERE strftime('%Y-%m',income_date)=?", (m,)
            ).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%Y-%m',expense_date)=?", (m,)
            ).fetchone()[0]
            bp = conn.execute(
                "SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE status='Paid' AND strftime('%Y-%m',paid_at)=?", (m,)
            ).fetchone()[0]
            total_out = round(float(exp) + float(bp), 2)
            savings   = round(float(inc) - total_out, 2)
            monthly.append({
                "month": d.strftime("%b"), "income": round(float(inc), 2),
                "expenses": total_out, "savings": savings
            })

        # ── Top expense categories this month ──
        top_cats = rows_to_list(conn.execute(
            "SELECT category, ROUND(SUM(amount),2) as total FROM expenses "
            "WHERE strftime('%Y-%m',expense_date)=? GROUP BY category ORDER BY total DESC LIMIT 6", (this_month,)
        ).fetchall())

        # ── Recent activity ──
        recent_expenses = rows_to_list(conn.execute(
            "SELECT id, title, category, amount, expense_date, store FROM expenses ORDER BY created_at DESC LIMIT 6"
        ).fetchall())

        recent_income = rows_to_list(conn.execute(
            "SELECT id, title, category, amount, income_date, source FROM income ORDER BY created_at DESC LIMIT 4"
        ).fetchall())

        return jsonify({
            "monthly_income":   round(float(monthly_income), 2),
            "monthly_expenses": round(monthly_outflow, 2),
            "monthly_savings":  round(monthly_savings, 2),
            "savings_rate":     savings_rate,
            "pending_bills":    round(float(pending_bills), 2),
            "overdue_count":    overdue_count,
            "overdue_amount":   round(float(overdue_amount), 2),
            "payee_count":      payee_count,
            "bill_count":       bill_count,
            "income_count":     income_count,
            "expense_count":    expense_count,
            "annual_income":    round(float(annual_income), 2),
            "annual_expenses":  round(float(annual_expenses) + float(annual_bills), 2),
            "monthly":          monthly,
            "top_cats":         top_cats,
            "recent_expenses":  recent_expenses,
            "recent_income":    recent_income,
        })

# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        s = load_settings()
        s["expense_categories"] = EXPENSE_CATEGORIES
        s["income_categories"]  = INCOME_CATEGORIES
        s["bill_categories"]    = BILL_CATEGORIES
        return jsonify(s)
    data = request.get_json(silent=True) or {}
    current = load_settings()
    current.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    save_settings_to_db(current)
    return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────────────────────
# PAYEES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/payees", methods=["GET", "POST"])
def api_payees():
    with get_db() as conn:
        if request.method == "GET":
            q = request.args.get("q", "")
            where = ""
            params = []
            if q:
                lq = f"%{q}%"
                where = "WHERE p.name LIKE ? OR p.email LIKE ? OR p.category LIKE ?"
                params = [lq, lq, lq]
            rows = conn.execute(
                f"""SELECT p.*,
                           COUNT(b.id) AS bill_count,
                           COALESCE(SUM(CASE WHEN b.status='Paid' THEN b.total_amount ELSE 0 END), 0) AS total_paid
                    FROM payees p
                    LEFT JOIN bills b ON b.payee_id = p.id
                    {where}
                    GROUP BY p.id
                    ORDER BY p.name""",
                params
            ).fetchall()
            return jsonify(rows_to_list(rows))
        d = request.get_json(silent=True) or {}
        if not d.get("name"):
            return jsonify({"error": "Name is required"}), 400
        conn.execute(
            "INSERT INTO payees (name,email,phone,address,category,notes) VALUES (?,?,?,?,?,?)",
            (d["name"], d.get("email",""), d.get("phone",""), d.get("address",""),
             d.get("category",""), d.get("notes",""))
        )
        return jsonify({"success": True})

@app.route("/api/payees/<int:pid>", methods=["GET", "PUT", "DELETE"])
def api_payee(pid):
    with get_db() as conn:
        if request.method == "GET":
            row = conn.execute("SELECT * FROM payees WHERE id=?", (pid,)).fetchone()
            if not row: return jsonify({"error": "Not found"}), 404
            p = row_to_dict(row)
            p["bills"] = rows_to_list(conn.execute(
                "SELECT id,bill_number,total_amount,status,bill_date FROM bills WHERE payee_id=? ORDER BY created_at DESC", (pid,)
            ).fetchall())
            return jsonify(p)
        if request.method == "DELETE":
            conn.execute("DELETE FROM payees WHERE id=?", (pid,))
            return jsonify({"success": True})
        d = request.get_json(silent=True) or {}
        if not d.get("name"):
            return jsonify({"error": "Name is required"}), 400
        conn.execute(
            "UPDATE payees SET name=?,email=?,phone=?,address=?,category=?,notes=? WHERE id=?",
            (d["name"], d.get("email",""), d.get("phone",""), d.get("address",""),
             d.get("category",""), d.get("notes",""), pid)
        )
        return jsonify({"success": True})

# ─────────────────────────────────────────────────────────────────────────────
# BILLS
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/bills", methods=["GET", "POST"])
def api_bills():
    with get_db() as conn:
        if request.method == "GET":
            q      = request.args.get("q", "")
            status = request.args.get("status", "")
            sql    = "SELECT * FROM bills WHERE 1=1"
            params = []
            if q:
                sql += " AND (bill_number LIKE ? OR payee_name LIKE ?)"
                params += [f"%{q}%", f"%{q}%"]
            if status and status != "All":
                if status == "Overdue":
                    sql += " AND status='Pending' AND due_date < date('now')"
                else:
                    sql += " AND status=?"
                    params.append(status)
            sql += " ORDER BY created_at DESC"
            return jsonify(rows_to_list(conn.execute(sql, params).fetchall()))

        d     = request.get_json(silent=True) or {}
        required = ["payee_name", "bill_date", "due_date"]
        missing = [k for k in required if not d.get(k)]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
        s     = load_settings()
        items = d.pop("items", [])
        subtotal, disc_pct, disc_amt, tax_rate, tax_amt, total = calc_bill_totals(
            items, d.get("discount_pct", 0), d.get("tax_rate", 0)
        )
        bill_num = next_bill_number(conn, s.get("bill_prefix", "BILL"))
        cur = conn.execute(
            """INSERT INTO bills (bill_number,payee_id,payee_name,payee_email,payee_address,
               bill_category,bill_date,due_date,subtotal,discount_pct,discount_amount,tax_rate,
               tax_amount,total_amount,status,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bill_num, d.get("payee_id"), d["payee_name"], d.get("payee_email",""),
             d.get("payee_address",""), d.get("bill_category","Other"),
             d["bill_date"], d["due_date"], float(subtotal), float(disc_pct), float(disc_amt),
             float(tax_rate), float(tax_amt), float(total), d.get("status","Pending"), d.get("notes",""))
        )
        bill_id = cur.lastrowid
        for item in items:
            qty   = float(as_decimal(item.get("quantity", 1)))
            price = float(as_decimal(item.get("unit_price", 0)))
            conn.execute(
                "INSERT INTO bill_items (bill_id,item_name,description,quantity,unit_price,total) VALUES (?,?,?,?,?,?)",
                (bill_id, item["item_name"], item.get("description",""), qty, price, qty * price)
            )
        return jsonify({"success": True, "bill_number": bill_num, "id": bill_id})

@app.route("/api/bills/<int:bid>", methods=["GET", "PUT", "DELETE"])
def api_bill(bid):
    with get_db() as conn:
        if request.method == "GET":
            row = conn.execute("SELECT * FROM bills WHERE id=?", (bid,)).fetchone()
            if not row: return jsonify({"error": "Not found"}), 404
            b = row_to_dict(row)
            b["items"] = rows_to_list(conn.execute(
                "SELECT * FROM bill_items WHERE bill_id=? ORDER BY id", (bid,)
            ).fetchall())
            return jsonify(b)

        if request.method == "DELETE":
            conn.execute("DELETE FROM bills WHERE id=?", (bid,))
            return jsonify({"success": True})

        d = request.get_json(silent=True) or {}
        if "action" in d and d["action"] == "status":
            paid_at = datetime.now().isoformat() if d["status"] == "Paid" else None
            conn.execute("UPDATE bills SET status=?,paid_at=? WHERE id=?", (d["status"], paid_at, bid))
            return jsonify({"success": True})
        required = ["payee_name", "bill_date", "due_date"]
        missing = [k for k in required if not d.get(k)]
        if missing:
            return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

        items = d.pop("items", [])
        subtotal, disc_pct, disc_amt, tax_rate, tax_amt, total = calc_bill_totals(
            items, d.get("discount_pct", 0), d.get("tax_rate", 0)
        )
        conn.execute(
            """UPDATE bills SET payee_id=?,payee_name=?,payee_email=?,payee_address=?,
               bill_category=?,bill_date=?,due_date=?,subtotal=?,discount_pct=?,discount_amount=?,
               tax_rate=?,tax_amount=?,total_amount=?,status=?,notes=? WHERE id=?""",
            (d.get("payee_id"), d["payee_name"], d.get("payee_email",""), d.get("payee_address",""),
             d.get("bill_category","Other"), d["bill_date"], d["due_date"],
             float(subtotal), float(disc_pct), float(disc_amt), float(tax_rate), float(tax_amt), float(total),
             d.get("status","Pending"), d.get("notes",""), bid)
        )
        conn.execute("DELETE FROM bill_items WHERE bill_id=?", (bid,))
        for item in items:
            qty   = float(item.get("quantity", 1))
            price = float(item.get("unit_price", 0))
            conn.execute(
                "INSERT INTO bill_items (bill_id,item_name,description,quantity,unit_price,total) VALUES (?,?,?,?,?,?)",
                (bid, item["item_name"], item.get("description",""), qty, price, qty * price)
            )
        return jsonify({"success": True})

@app.route("/api/bills/<int:bid>/print")
def api_bill_print(bid):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM bills WHERE id=?", (bid,)).fetchone()
        if not row: return "Bill not found", 404
        b = row_to_dict(row)
        items = rows_to_list(conn.execute(
            "SELECT * FROM bill_items WHERE bill_id=? ORDER BY id", (bid,)
        ).fetchall())
    s = load_settings()
    return generate_print_bill(b, items, s)

def generate_print_bill(b, items, s):
    sym = s.get("currency_symbol", "$")
    status_colors = {
        "Pending": "#f59e0b", "Paid": "#10b981",
        "Overdue": "#f43f5e", "Void": "#94a3b8"
    }
    sc = status_colors.get(b.get("status","Pending"), "#f59e0b")
    rows_html = ""
    for item in items:
        item_name = html.escape(str(item.get("item_name", "")))
        desc_val = html.escape(str(item.get("description", "")))
        desc = f"<br><small style='color:#64748b'>{desc_val}</small>" if desc_val else ""
        rows_html += f"""<tr>
            <td>{item_name}{desc}</td>
            <td style="text-align:center">{item['quantity']:g}</td>
            <td style="text-align:right">{sym}{float(item['unit_price']):,.2f}</td>
            <td style="text-align:right;font-weight:600">{sym}{float(item['total']):,.2f}</td>
        </tr>"""
    discount_row = ""
    if float(b.get("discount_amount", 0)) > 0:
        discount_row = f"<tr><td>Discount ({b['discount_pct']}%)</td><td style='text-align:right;color:#f43f5e'>-{sym}{float(b['discount_amount']):,.2f}</td></tr>"
    notes = html.escape(str(b.get("notes","") or s.get("family_notes","")))
    family_name = html.escape(str(s.get('family_name','Our Family')))
    family_address = html.escape(str(s.get('family_address',''))).replace("\n", "<br>")
    primary_email = html.escape(str(s.get('primary_email','')))
    primary_phone = html.escape(str(s.get('primary_phone','')))
    bill_number = html.escape(str(b.get('bill_number', '')))
    status = html.escape(str(b.get('status', 'Pending')))
    bill_date = html.escape(str(b.get('bill_date', '')))
    due_date = html.escape(str(b.get('due_date', '')))
    bill_category = html.escape(str(b.get('bill_category','Other')))
    payee_name = html.escape(str(b.get('payee_name', '')))
    payee_email = html.escape(str(b.get('payee_email','')))
    payee_address = html.escape(str(b.get('payee_address',''))).replace("\n", "<br>")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bill {bill_number}</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Plus Jakarta Sans',sans-serif;background:#fff;color:#1e293b;padding:48px;max-width:860px;margin:0 auto;font-size:14px}}
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
.totals{{width:300px}}
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
      <tr><td>Subtotal</td><td style="text-align:right">{sym}{float(b['subtotal']):,.2f}</td></tr>
      {discount_row}
      <tr><td>Tax ({b['tax_rate']}%)</td><td style="text-align:right">{sym}{float(b['tax_amount']):,.2f}</td></tr>
      <tr><td><strong>Total Due</strong></td><td style="text-align:right">{sym}{float(b['total_amount']):,.2f}</td></tr>
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
# EXPENSES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/expenses", methods=["GET", "POST"])
def api_expenses():
    with get_db() as conn:
        if request.method == "GET":
            q   = request.args.get("q", "")
            cat = request.args.get("category", "")
            limit = min(int(request.args.get("limit", 25)), 200)
            offset = max(int(request.args.get("offset", 0)), 0)
            sql = "SELECT * FROM expenses WHERE 1=1"
            params = []
            if q:
                sql += " AND (title LIKE ? OR store LIKE ? OR notes LIKE ? OR member LIKE ?)"
                params += [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
            if cat:
                sql += " AND category=?"
                params.append(cat)
            total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
            sql += " ORDER BY expense_date DESC, created_at DESC LIMIT ? OFFSET ?"
            rows = conn.execute(sql, params + [limit, offset]).fetchall()
            return jsonify({"items": rows_to_list(rows), "total": total, "limit": limit, "offset": offset})

        d = request.get_json(silent=True) or {}
        if not d.get("title") or not d.get("expense_date"):
            return jsonify({"error": "Title and expense_date are required"}), 400
        amount = as_decimal(d.get("amount"))
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than 0"}), 400
        conn.execute(
            "INSERT INTO expenses (title,category,amount,expense_date,store,receipt_ref,notes,member) VALUES (?,?,?,?,?,?,?,?)",
            (d["title"], d.get("category","Other"), float(q2(amount)),
             d["expense_date"], d.get("store",""), d.get("receipt_ref",""),
             d.get("notes",""), d.get("member",""))
        )
        return jsonify({"success": True})

@app.route("/api/expenses/<int:eid>", methods=["GET", "PUT", "DELETE"])
def api_expense(eid):
    with get_db() as conn:
        if request.method == "GET":
            row = conn.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            return jsonify(row_to_dict(row))
        if request.method == "DELETE":
            conn.execute("DELETE FROM expenses WHERE id=?", (eid,))
            return jsonify({"success": True})
        d = request.get_json(silent=True) or {}
        if not d.get("title") or not d.get("expense_date"):
            return jsonify({"error": "Title and expense_date are required"}), 400
        amount = as_decimal(d.get("amount"))
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than 0"}), 400
        conn.execute(
            "UPDATE expenses SET title=?,category=?,amount=?,expense_date=?,store=?,receipt_ref=?,notes=?,member=? WHERE id=?",
            (d["title"], d.get("category","Other"), float(q2(amount)),
             d["expense_date"], d.get("store",""), d.get("receipt_ref",""),
             d.get("notes",""), d.get("member",""), eid)
        )
        return jsonify({"success": True})

@app.route("/api/expenses/export")
def api_expenses_export():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM expenses ORDER BY expense_date DESC").fetchall()
    total = sum(float(r["amount"]) for r in rows)
    data_rows = [[r["id"],r["title"],r["category"],r["amount"],r["expense_date"],
                  r["store"],r["receipt_ref"],r["member"],r["notes"]] for r in rows]
    data_rows.append([])
    data_rows.append(["", "TOTAL", "", f"{total:.2f}", "", "", "", "", ""])
    return csv_response(
        "expenses.csv", data_rows,
        ["ID","Title","Category","Amount","Date","Store / Vendor","Receipt Ref","Family Member","Notes"]
    )

# ─────────────────────────────────────────────────────────────────────────────
# INCOME
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/income", methods=["GET", "POST"])
def api_income():
    with get_db() as conn:
        if request.method == "GET":
            q   = request.args.get("q", "")
            cat = request.args.get("category", "")
            limit = min(int(request.args.get("limit", 25)), 200)
            offset = max(int(request.args.get("offset", 0)), 0)
            sql = "SELECT * FROM income WHERE 1=1"
            params = []
            if q:
                sql += " AND (title LIKE ? OR source LIKE ? OR notes LIKE ? OR member LIKE ?)"
                params += [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
            if cat:
                sql += " AND category=?"
                params.append(cat)
            total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
            sql += " ORDER BY income_date DESC, created_at DESC LIMIT ? OFFSET ?"
            rows = conn.execute(sql, params + [limit, offset]).fetchall()
            return jsonify({"items": rows_to_list(rows), "total": total, "limit": limit, "offset": offset})

        d = request.get_json(silent=True) or {}
        if not d.get("title") or not d.get("income_date"):
            return jsonify({"error": "Title and income_date are required"}), 400
        amount = as_decimal(d.get("amount"))
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than 0"}), 400
        conn.execute(
            "INSERT INTO income (title,category,amount,income_date,source,notes,member) VALUES (?,?,?,?,?,?,?)",
            (d["title"], d.get("category","Other"), float(q2(amount)),
             d["income_date"], d.get("source",""), d.get("notes",""), d.get("member",""))
        )
        return jsonify({"success": True})

@app.route("/api/income/<int:iid>", methods=["GET", "PUT", "DELETE"])
def api_income_item(iid):
    with get_db() as conn:
        if request.method == "GET":
            row = conn.execute("SELECT * FROM income WHERE id=?", (iid,)).fetchone()
            if not row:
                return jsonify({"error": "Not found"}), 404
            return jsonify(row_to_dict(row))
        if request.method == "DELETE":
            conn.execute("DELETE FROM income WHERE id=?", (iid,))
            return jsonify({"success": True})
        d = request.get_json(silent=True) or {}
        if not d.get("title") or not d.get("income_date"):
            return jsonify({"error": "Title and income_date are required"}), 400
        amount = as_decimal(d.get("amount"))
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than 0"}), 400
        conn.execute(
            "UPDATE income SET title=?,category=?,amount=?,income_date=?,source=?,notes=?,member=? WHERE id=?",
            (d["title"], d.get("category","Other"), float(q2(amount)),
             d["income_date"], d.get("source",""), d.get("notes",""), d.get("member",""), iid)
        )
        return jsonify({"success": True})

@app.route("/api/income/export")
def api_income_export():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM income ORDER BY income_date DESC").fetchall()
    total = sum(float(r["amount"]) for r in rows)
    data_rows = [[r["id"],r["title"],r["category"],r["amount"],r["income_date"],
                  r["source"],r["member"],r["notes"]] for r in rows]
    data_rows.append([])
    data_rows.append(["", "TOTAL", "", f"{total:.2f}", "", "", "", ""])
    return csv_response(
        "income.csv", data_rows,
        ["ID","Title","Category","Amount","Date","Source","Family Member","Notes"]
    )

# ─────────────────────────────────────────────────────────────────────────────
# BILLS EXPORT
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/bills/export")
def api_bills_export():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM bills ORDER BY created_at DESC").fetchall()
    total_paid    = sum(float(r["total_amount"]) for r in rows if r["status"]=="Paid")
    total_pending = sum(float(r["total_amount"]) for r in rows if r["status"]!="Paid")
    data_rows = [[r["bill_number"],r["payee_name"],r["payee_email"],r["bill_category"],
                  r["bill_date"],r["due_date"],r["subtotal"],r["discount_amount"],
                  r["tax_amount"],r["total_amount"],r["status"]] for r in rows]
    data_rows.append([])
    data_rows.append(["SUMMARY","","","","","",
                      "","","",f"Paid: {total_paid:.2f} | Pending: {total_pending:.2f}",""])
    return csv_response(
        "bills.csv", data_rows,
        ["Bill #","Payee","Email","Category","Bill Date","Due Date",
         "Subtotal","Discount","Tax","Total","Status"]
    )

# ─────────────────────────────────────────────────────────────────────────────
# REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/reports")
def api_reports():
    year = request.args.get("year", str(date.today().year))
    with get_db() as conn:
        month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        monthly = []
        for m in range(1, 13):
            ms  = f"{year}-{str(m).zfill(2)}"
            inc = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM income WHERE strftime('%Y-%m',income_date)=?", (ms,)
            ).fetchone()[0]
            exp = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%Y-%m',expense_date)=?", (ms,)
            ).fetchone()[0]
            bp = conn.execute(
                "SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE status='Paid' AND strftime('%Y-%m',paid_at)=?", (ms,)
            ).fetchone()[0]
            total_out = round(float(exp) + float(bp), 2)
            savings   = round(float(inc) - total_out, 2)
            srate     = round(savings / float(inc) * 100, 1) if float(inc) > 0 else 0
            monthly.append({
                "month": month_names[m-1], "income": round(float(inc),2),
                "expenses": total_out, "savings": savings, "savings_rate": srate
            })

        expense_cats = rows_to_list(conn.execute(
            "SELECT category, ROUND(SUM(amount),2) as total FROM expenses "
            "WHERE strftime('%Y',expense_date)=? GROUP BY category ORDER BY total DESC", (year,)
        ).fetchall())

        income_cats = rows_to_list(conn.execute(
            "SELECT category, ROUND(SUM(amount),2) as total FROM income "
            "WHERE strftime('%Y',income_date)=? GROUP BY category ORDER BY total DESC", (year,)
        ).fetchall())

        bill_cats = rows_to_list(conn.execute(
            "SELECT bill_category as category, ROUND(SUM(total_amount),2) as total FROM bills "
            "WHERE status='Paid' AND strftime('%Y',paid_at)=? GROUP BY bill_category ORDER BY total DESC", (year,)
        ).fetchall())

        status_breakdown = rows_to_list(conn.execute(
            "SELECT status, COUNT(*) as count, ROUND(SUM(total_amount),2) as total FROM bills GROUP BY status"
        ).fetchall())

        total_inc = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM income WHERE strftime('%Y',income_date)=?", (year,)
        ).fetchone()[0]
        total_exp = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE strftime('%Y',expense_date)=?", (year,)
        ).fetchone()[0]
        total_bills = conn.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM bills WHERE status='Paid' AND strftime('%Y',paid_at)=?", (year,)
        ).fetchone()[0]
        total_out  = float(total_exp) + float(total_bills)
        net_savings = float(total_inc) - total_out
        savings_rate = round(net_savings / float(total_inc) * 100, 1) if float(total_inc) > 0 else 0

        available_years = [r[0] for r in conn.execute(
            "SELECT DISTINCT strftime('%Y',income_date) FROM income ORDER BY 1 DESC"
        ).fetchall() if r[0]]
        exp_years = [r[0] for r in conn.execute(
            "SELECT DISTINCT strftime('%Y',expense_date) FROM expenses ORDER BY 1 DESC"
        ).fetchall() if r[0]]
        all_years = sorted(set(available_years + exp_years), reverse=True)
        if str(date.today().year) not in all_years:
            all_years.insert(0, str(date.today().year))

        return jsonify({
            "monthly":          monthly,
            "expense_cats":     expense_cats,
            "income_cats":      income_cats,
            "bill_cats":        bill_cats,
            "status_breakdown": status_breakdown,
            "total_income":     round(float(total_inc), 2),
            "total_expenses":   round(total_out, 2),
            "net_savings":      round(net_savings, 2),
            "savings_rate":     savings_rate,
            "available_years":  all_years,
        })

# ─────────────────────────────────────────────────────────────────────────────
# REMINDERS
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/reminders")
def api_reminders():
    with get_db() as conn:
        today_str = date.today().isoformat()
        in14days  = (date.today() + timedelta(days=14)).isoformat()
        overdue = rows_to_list(conn.execute(
            "SELECT id, bill_number, payee_name, due_date, total_amount, status, bill_category, "
            "CAST(julianday('now')-julianday(due_date) AS INTEGER) as days_overdue "
            "FROM bills WHERE status='Pending' AND due_date < ? ORDER BY due_date", (today_str,)
        ).fetchall())
        upcoming = rows_to_list(conn.execute(
            "SELECT id, bill_number, payee_name, due_date, total_amount, status, bill_category, "
            "CAST(julianday(due_date)-julianday('now') AS INTEGER) as days_left "
            "FROM bills WHERE status='Pending' AND due_date BETWEEN ? AND ? ORDER BY due_date",
            (today_str, in14days)
        ).fetchall())
        return jsonify({"overdue": overdue, "upcoming": upcoming})

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    host = os.getenv("FF_HOST", "127.0.0.1")
    port = int(os.getenv("FF_PORT", "5000"))
    debug = os.getenv("FF_DEBUG", "0") == "1"
    print("\n" + "="*56)
    print("  🏠  FamilyFinance — Personal Budget Tracker")
    print("  📂  Database: familyfinance.db")
    print(f"  🌐  Open: http://{host}:{port}")
    print("="*56 + "\n")
    app.run(debug=debug, port=port, host=host)
