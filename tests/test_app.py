import io
import os
import sqlite3
import tempfile
import threading
from datetime import date
from pathlib import Path

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.db"),
            "SECRET_KEY": "test-secret",
            "ADMIN_USER": "admin",
            "ADMIN_PASSWORD": "test-password-123",
        }
    )
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client):
    resp = client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": "test-password-123",
        },
    )

    assert resp.status_code == 200

    data = resp.get_json()
    assert data["success"] is True
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "Admin"
    assert "csrf_token" in data

    return data["csrf_token"]


def auth_headers(token):
    return {"X-CSRF-Token": token}


def test_health(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_ready(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


def test_login_me_and_logout(client):
    token = login(client)

    resp = client.get("/api/me")
    assert resp.status_code == 200

    data = resp.get_json()
    assert data["user"]["username"] == "admin"
    assert "csrf_token" in data

    resp = client.post("/api/auth/logout", headers=auth_headers(token))
    assert resp.status_code == 200

    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_authentication_required(client):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 401

    resp = client.post("/api/expenses", json={"title": "x"})
    assert resp.status_code == 401


def test_csrf_required_for_mutating_requests(client):
    login(client)

    payload = {
        "title": "No CSRF",
        "amount": "10.00",
        "expense_date": "2026-06-20",
    }

    resp = client.post("/api/expenses", json=payload)
    assert resp.status_code == 403


def test_expense_income_bill_budget_goal_flow(client):
    token = login(client)
    headers = auth_headers(token)

    # Create income.
    resp = client.post(
        "/api/income",
        headers=headers,
        json={
            "title": "Salary",
            "category": "Salary / Wages",
            "amount": "2500.00",
            "income_date": "2026-06-01",
            "source": "Employer",
            "member": "Alice",
        },
    )
    assert resp.status_code == 200
    income_id = resp.get_json()["id"]

    resp = client.get(f"/api/income/{income_id}")
    assert resp.status_code == 200
    assert resp.get_json()["amount_cents"] == 250000

    # Create expense.
    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Supermarket",
            "category": "Groceries & Food",
            "amount": "42.50",
            "expense_date": "2026-06-20",
            "store": "Market",
            "member": "Alice",
        },
    )
    assert resp.status_code == 200
    expense_id = resp.get_json()["id"]

    resp = client.get(f"/api/expenses/{expense_id}")
    assert resp.status_code == 200
    assert resp.get_json()["amount_cents"] == 4250

    # Create bill.
    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "Electric Company",
            "bill_category": "Electricity",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "discount_pct": "0",
            "tax_rate": "0",
            "items": [
                {
                    "item_name": "Electricity usage",
                    "quantity": 1,
                    "unit_price": "86.20",
                }
            ],
        },
    )
    assert resp.status_code == 200
    bill_id = resp.get_json()["id"]

    resp = client.get(f"/api/bills/{bill_id}")
    assert resp.status_code == 200

    bill = resp.get_json()
    assert bill["total_cents"] == 8620
    assert bill["status"] == "Pending"

    # Pay part of the bill.
    resp = client.post(
        f"/api/bills/{bill_id}/pay",
        headers=headers,
        json={
            "amount": "20.00",
            "payment_date": "2026-06-05",
            "notes": "Partial payment",
        },
    )
    assert resp.status_code == 200

    resp = client.get(f"/api/bills/{bill_id}")
    bill = resp.get_json()
    assert bill["paid_cents"] == 2000
    assert bill["status"] == "Partially Paid"

    # Create budget.
    resp = client.post(
        "/api/budgets",
        headers=headers,
        json={
            "category": "Groceries & Food",
            "period": "monthly",
            "amount": "600.00",
        },
    )
    assert resp.status_code == 200

    # Create goal.
    resp = client.post(
        "/api/goals",
        headers=headers,
        json={
            "name": "Emergency Fund",
            "target": "10000.00",
            "current": "1500.00",
            "target_date": "2027-12-31",
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/goals")
    goals = resp.get_json()
    assert len(goals) == 1
    goal_id = goals[0]["id"]

    resp = client.post(
        f"/api/goals/{goal_id}/contribute",
        headers=headers,
        json={"amount": "250.00"},
    )
    assert resp.status_code == 200

    resp = client.get("/api/goals")
    goals = resp.get_json()
    assert goals[0]["current_cents"] == 175000

    # Dashboard and reports should work.
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200

    resp = client.get("/api/reports?year=2026")
    assert resp.status_code == 200

    resp = client.get("/api/reports/budget?period=monthly&month=2026-06")
    assert resp.status_code == 200

    resp = client.get("/api/reports/cash-flow?months=6")
    assert resp.status_code == 200

    resp = client.get("/api/reminders")
    assert resp.status_code == 200


def test_admin_backup_and_audit(client):
    login(client)

    resp = client.get("/api/admin/backup")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/octet-stream")

    resp = client.get("/api/admin/audit")
    assert resp.status_code == 200

    data = resp.get_json()
    assert "items" in data
    assert "total" in data


def test_household_user_management(client):
    token = login(client)
    headers = auth_headers(token)

    # A second household login can be created with a role.
    resp = client.post(
        "/api/users",
        headers=headers,
        json={"username": "mom", "password": "momspassword1", "role": "Editor"},
    )
    assert resp.status_code == 200
    mom_id = resp.get_json()["id"]

    resp = client.get("/api/users", headers=headers)
    assert resp.status_code == 200
    usernames = {u["username"] for u in resp.get_json()}
    assert {"admin", "mom"} <= usernames

    # Duplicate usernames are rejected.
    resp = client.post(
        "/api/users",
        headers=headers,
        json={"username": "mom", "password": "whatever12", "role": "Viewer"},
    )
    assert resp.status_code == 400

    # Passwords under 8 characters are rejected.
    resp = client.post(
        "/api/users",
        headers=headers,
        json={"username": "dad", "password": "short", "role": "Editor"},
    )
    assert resp.status_code == 400

    # The new Editor can log in and is subject to Editor/Admin-only checks.
    editor_client = client
    editor_resp = editor_client.post(
        "/api/auth/login",
        json={"username": "mom", "password": "momspassword1"},
    )
    assert editor_resp.status_code == 200
    editor_token = editor_resp.get_json()["csrf_token"]

    # Editors cannot manage users.
    resp = editor_client.get("/api/users", headers=auth_headers(editor_token))
    assert resp.status_code == 403

    # Editors can still create financial records.
    resp = editor_client.post(
        "/api/expenses",
        headers=auth_headers(editor_token),
        json={
            "title": "Snacks",
            "category": "Groceries & Food",
            "amount": "12.00",
            "expense_date": "2026-06-21",
        },
    )
    assert resp.status_code == 200

    # Log back in as admin to finish the lifecycle.
    admin_token = login(client)
    admin_headers = auth_headers(admin_token)

    resp = client.put(
        f"/api/users/{mom_id}",
        headers=admin_headers,
        json={"role": "Viewer"},
    )
    assert resp.status_code == 200

    # Admins can't delete their own active account.
    resp = client.get("/api/users", headers=admin_headers)
    admin_id = next(u["id"] for u in resp.get_json() if u["username"] == "admin")
    resp = client.delete(f"/api/users/{admin_id}", headers=admin_headers)
    assert resp.status_code == 400

    resp = client.delete(f"/api/users/{mom_id}", headers=admin_headers)
    assert resp.status_code == 200


def test_category_partial_update_toggles_active(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/categories",
        headers=headers,
        json={"name": "Home Office", "type": "expense"},
    )
    assert resp.status_code == 200

    resp = client.get("/api/categories?type=expense&active_only=0", headers=headers)
    cat = next(c for c in resp.get_json() if c["name"] == "Home Office")

    # Toggling is_active alone (no name in the payload) should not fail
    # and must not clear the category's name.
    resp = client.put(
        f"/api/categories/{cat['id']}",
        headers=headers,
        json={"is_active": 0},
    )
    assert resp.status_code == 200

    resp = client.get("/api/categories?type=expense&active_only=0", headers=headers)
    updated = next(c for c in resp.get_json() if c["id"] == cat["id"])
    assert updated["name"] == "Home Office"
    assert updated["is_active"] == 0


def test_notifications_report_unread_count(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/recurring",
        headers=headers,
        json={
            "name": "Weekly Coffee",
            "entity_type": "expense",
            "frequency": "weekly",
            "interval_value": 1,
            "next_run_date": "2026-06-01",
            "payload": {
                "title": "Coffee",
                "amount": "5.00",
                "category": "Groceries & Food",
            },
        },
    )
    assert resp.status_code == 200

    resp = client.post("/api/recurring/run", headers=headers, json={})
    assert resp.status_code == 200
    assert len(resp.get_json()["created"]) > 0

    resp = client.get("/api/notifications?limit=5", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert "unread_count" in data
    assert data["unread_count"] >= 1

    resp = client.post("/api/notifications/read-all", headers=headers)
    assert resp.status_code == 200

    resp = client.get("/api/notifications?limit=5", headers=headers)
    assert resp.get_json()["unread_count"] == 0


def test_split_transactions_expenses_and_income(client):
    token = login(client)
    headers = auth_headers(token)

    # Split Expense
    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Supermarket & Pharmacy",
            "category": "Groceries & Food",
            "amount": "100.00",
            "expense_date": "2026-06-25",
            "splits": [
                {"category": "Groceries & Food", "amount": "70.00"},
                {"category": "Healthcare & Medical", "amount": "30.00"},
            ],
        },
    )
    assert resp.status_code == 200
    exp_id = resp.get_json()["id"]

    resp = client.get(f"/api/expenses/{exp_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["splits"]) == 2
    assert data["splits"][0]["amount_cents"] == 7000

    # Split mismatch error
    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Bad Split",
            "category": "Groceries & Food",
            "amount": "100.00",
            "expense_date": "2026-06-25",
            "splits": [
                {"category": "Groceries & Food", "amount": "50.00"},
            ],
        },
    )
    assert resp.status_code == 400


def test_debt_payoff_and_forecast_reports(client):
    token = login(client)
    headers = auth_headers(token)

    # Create credit card account
    resp = client.post(
        "/api/accounts",
        headers=headers,
        json={
            "name": "Visa Credit Card",
            "account_type": "Credit Card",
            "opening_balance": "1000.00",
            "interest_rate": 18.5,
            "minimum_payment": 50.0,
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/reports/debt-payoff?extra_payment=50.00", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["debts"]) >= 1
    assert data["snowball"]["months"] > 0
    assert data["avalanche"]["months"] > 0

    resp = client.get("/api/reports/forecast?months=6", headers=headers)
    assert resp.status_code == 200
    fc = resp.get_json()
    assert len(fc["projection"]) == 6


def test_allowances_crud(client):
    token = login(client)
    headers = auth_headers(token)

    # Create member
    m_resp = client.post("/api/members", headers=headers, json={"name": "Kid Junior"})
    assert m_resp.status_code == 200

    m_list = client.get("/api/members", headers=headers).get_json()
    kid_id = next(m["id"] for m in m_list if m["name"] == "Kid Junior")

    # Create allowance
    resp = client.post(
        "/api/allowances",
        headers=headers,
        json={"member_id": kid_id, "amount": "20.00", "frequency": "monthly"},
    )
    assert resp.status_code == 200

    resp = client.get("/api/allowances", headers=headers)
    assert resp.status_code == 200
    al_list = resp.get_json()
    assert len(al_list) == 1
    assert al_list[0]["amount_cents"] == 2000


def test_csv_import_and_reports(client):
    token = login(client)
    headers = auth_headers(token)

    # Commit CSV import for expenses
    commit_payload = {
        "target_type": "expense",
        "items": [
            {
                "title": "Bakery",
                "category": "Groceries & Food",
                "amount": "15.50",
                "date": "2026-06-22",
            },
            {
                "title": "Pharmacy",
                "category": "Healthcare & Medical",
                "amount": "22.00",
                "date": "2026-06-23",
            },
        ],
    }

    resp = client.post("/api/import/csv/commit", headers=headers, json=commit_payload)
    assert resp.status_code == 200
    assert resp.get_json()["imported_count"] == 2

    # Check that imported expenses appear in list
    resp = client.get("/api/expenses", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["total"] >= 2


def test_invalid_account_references_are_validation_errors(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Bad account",
            "category": "Other",
            "amount": "10.00",
            "expense_date": "2026-06-20",
            "account_id": 9999,
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "account_id does not exist"


def test_dashboard_and_insights_include_financial_health(client):
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/income",
        headers=headers,
        json={
            "title": "Paycheck",
            "category": "Salary / Wages",
            "amount": "4000.00",
            "income_date": "2026-08-01",
        },
    )
    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Groceries",
            "category": "Groceries & Food",
            "amount": "450.00",
            "expense_date": "2026-08-05",
        },
    )

    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    health = resp.get_json()["financial_health"]
    assert 0 <= health["score"] <= 100
    assert health["status"] in {"strong", "watch", "needs_attention"}
    assert health["recommendations"]

    resp = client.get("/api/insights")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["financial_health"] == health
    assert "monthly_trend" in data


# ---------------------------------------------------------------------------
# Regression tests for bugs found during review
# ---------------------------------------------------------------------------


def test_payee_search_does_not_hit_ambiguous_column(client):
    """Regression: GET /api/payees?q=... used to fail with an sqlite
    'ambiguous column name: notes' error once bills existed in the join."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/payees",
        headers=headers,
        json={"name": "Electric Co", "notes": "power company", "category": "Utilities"},
    )
    assert resp.status_code == 200
    payee_id = client.get("/api/payees").get_json()["items"][0]["id"]

    # A linked bill must exist so the payee listing actually joins the bills table.
    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_id": payee_id,
            "bill_category": "Electricity",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "items": [{"item_name": "Usage", "quantity": 1, "unit_price": "10.00"}],
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/payees?q=Electric", headers=headers)
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Electric Co"
    assert items[0]["bill_count"] == 1

    resp = client.get("/api/payees?category=Utilities", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 1

    resp = client.get("/api/payees?q=nomatch", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 0


def test_void_missing_or_deleted_bill_returns_404(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post("/api/bills/999/void", headers=headers)
    assert resp.status_code == 404


def test_bill_payment_validates_account_reference(client):
    """Regression: a bad account_id on bill payment used to surface as a raw
    FOREIGN KEY constraint 500 instead of a clean validation error."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "P",
            "bill_category": "Other",
            "bill_date": "2026-01-01",
            "due_date": "2026-02-01",
            "items": [{"item_name": "x", "quantity": 1, "unit_price": "50.00"}],
        },
    )
    bill_id = resp.get_json()["id"]

    resp = client.post(
        f"/api/bills/{bill_id}/pay",
        headers=headers,
        json={"amount": "10.00", "account_id": 424242},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "account_id does not exist"

    # The bill must remain untouched.
    resp = client.get(f"/api/bills/{bill_id}")
    assert resp.get_json()["paid_cents"] == 0

    # Paying with no account still works.
    resp = client.post(f"/api/bills/{bill_id}/pay", headers=headers, json={"amount": "10.00"})
    assert resp.status_code == 200


def test_recurring_run_isolates_broken_rules(client):
    """Regression: one malformed rule used to abort the entire recurring run
    and roll back every other rule's created items."""
    token = login(client)
    headers = auth_headers(token)

    for name, payload in (
        ("good", {"title": "Good", "amount": "5.00", "category": "Other"}),
        ("bad", {"amount": "5.00"}),  # missing title -> ValidationError
    ):
        resp = client.post(
            "/api/recurring",
            headers=headers,
            json={
                "name": name,
                "entity_type": "expense",
                "frequency": "monthly",
                "interval_value": 1,
                "next_run_date": "2026-08-01",
                "payload": payload,
            },
        )
        assert resp.status_code == 200

    resp = client.post("/api/recurring/run", headers=headers, json={})
    assert resp.status_code == 200

    data = resp.get_json()
    assert len(data["created"]) == 1
    assert data["created"][0]["rule"] == "good"
    assert len(data["failed"]) == 1
    assert data["failed"][0]["rule"] == "bad"
    assert "title" in data["failed"][0]["error"]

    # The good rule's expense must have been persisted despite the bad rule.
    resp = client.get("/api/expenses", headers=headers)
    assert resp.get_json()["total"] == 1

    # The broken rule stays due so the failure keeps being surfaced.
    resp = client.get("/api/recurring", headers=headers)
    rules = {r["name"]: r for r in resp.get_json()}
    assert rules["bad"]["next_run_date"] <= "2026-08-01"


def test_restore_rejects_invalid_backup_files(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/admin/restore",
        headers=headers,
        content_type="multipart/form-data",
        data={"file": (io.BytesIO(b"this is not a database"), "x.db")},
    )
    assert resp.status_code == 400

    # Valid SQLite file, but not a FamilyFinance database.
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    with open(tmp, "rb") as f:
        resp = client.post(
            "/api/admin/restore",
            headers=headers,
            content_type="multipart/form-data",
            data={"file": (io.BytesIO(f.read()), "y.db")},
        )
    assert resp.status_code == 400
    assert "Not a valid FamilyFinance backup" in resp.get_json()["error"]


def test_backup_restore_roundtrip(client):
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Persisted item",
            "category": "Other",
            "amount": "12.34",
            "expense_date": "2026-06-20",
        },
    )

    resp = client.get("/api/admin/backup")
    assert resp.status_code == 200
    backup_bytes = resp.data

    resp = client.post(
        "/api/admin/restore",
        headers=headers,
        content_type="multipart/form-data",
        data={"file": (io.BytesIO(backup_bytes), "backup.db")},
    )
    assert resp.status_code == 200

    # App remains functional after restore.
    resp = client.get("/readyz")
    assert resp.status_code == 200

    resp = client.get("/api/expenses", headers=headers)
    titles = [i["title"] for i in resp.get_json()["items"]]
    assert "Persisted item" in titles


# ---------------------------------------------------------------------------
# Broader endpoint coverage
# ---------------------------------------------------------------------------


def test_csv_exports_are_downloadable(client):
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Coffee",
            "category": "Dining Out",
            "amount": "4.50",
            "expense_date": "2026-06-20",
        },
    )

    for path in ("/api/expenses/export", "/api/income/export", "/api/bills/export"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("text/csv")
        assert "attachment" in resp.headers["Content-Disposition"]
        body = resp.get_data(as_text=True)
        assert "FamilyFinance Export" in body


def test_bill_print_view_renders_html(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "Water Corp <script>alert(1)</script>",
            "bill_category": "Water & Sewage",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "notes": "June water",
            "items": [{"item_name": "Usage", "description": "m3", "quantity": 2, "unit_price": "30.00"}],
        },
    )
    bill_id = resp.get_json()["id"]

    resp = client.get(f"/api/bills/{bill_id}/print")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # User-supplied content must be escaped in the printable view.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Water Corp" in html
    assert "$60.00" in html

    resp = client.get("/api/bills/999/print")
    assert resp.status_code == 404


def test_settings_validation_and_roundtrip(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/settings",
        headers=headers,
        json={
            "family_name": "The Testers",
            "currency_symbol": "€",
            "currency_code": "eur",
            "savings_target_pct": 25,
        },
    )
    assert resp.status_code == 200

    resp = client.get("/api/settings")
    settings = resp.get_json()
    assert settings["family_name"] == "The Testers"
    assert settings["currency_symbol"] == "€"
    assert settings["currency_code"] == "EUR"
    assert settings["savings_target_pct"] == 25

    # Invalid values are rejected.
    resp = client.post("/api/settings", headers=headers, json={"savings_target_pct": 150})
    assert resp.status_code == 400


def test_password_change_flow(client):
    token = login(client)
    headers = auth_headers(token)

    # Wrong current password is rejected.
    resp = client.post(
        "/api/auth/password",
        headers=headers,
        json={"current_password": "wrong", "new_password": "new-password-123"},
    )
    assert resp.status_code == 400

    # Too-short new password is rejected.
    resp = client.post(
        "/api/auth/password",
        headers=headers,
        json={"current_password": "test-password-123", "new_password": "short"},
    )
    assert resp.status_code == 400

    resp = client.post(
        "/api/auth/password",
        headers=headers,
        json={"current_password": "test-password-123", "new_password": "brand-new-pass-1"},
    )
    assert resp.status_code == 200

    # Old password no longer works; new one does.
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password-123"},
    )
    assert resp.status_code == 401

    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "brand-new-pass-1"},
    )
    assert resp.status_code == 200


def test_budget_report_honors_member_filter(client):
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Alice food",
            "category": "Groceries & Food",
            "amount": "80.00",
            "expense_date": "2026-06-02",
            "member": "Alice",
        },
    )
    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Bob food",
            "category": "Groceries & Food",
            "amount": "20.00",
            "expense_date": "2026-06-03",
            "member": "Bob",
        },
    )

    client.post(
        "/api/budgets",
        headers=headers,
        json={"category": "Groceries & Food", "member": "Alice", "period": "monthly", "amount": "100.00"},
    )
    client.post(
        "/api/budgets",
        headers=headers,
        json={"category": "Groceries & Food", "period": "monthly", "amount": "100.00"},
    )

    resp = client.get("/api/reports/budget?period=monthly&month=2026-06", headers=headers)
    assert resp.status_code == 200

    by_kind = {(i["member"], i["actual"]) for i in resp.get_json()["items"]}
    # Member budget counts only that member's spending.
    assert ("Alice", 80.0) in by_kind
    # Household budget counts everyone.
    assert ("", 100.0) in by_kind


def test_account_balances_across_endpoints(client):
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/accounts",
        headers=headers,
        json={"name": "Checking", "account_type": "Checking", "opening_balance": "500.00"},
    )
    assert resp.status_code == 200
    account_id = client.get("/api/accounts").get_json()[0]["id"]

    client.post(
        "/api/income",
        headers=headers,
        json={
            "title": "Deposit",
            "category": "Salary / Wages",
            "amount": "300.00",
            "income_date": "2026-06-01",
            "account_id": account_id,
        },
    )
    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Withdrawal",
            "category": "Other",
            "amount": "120.50",
            "expense_date": "2026-06-02",
            "account_id": account_id,
        },
    )

    expected_balance = 500.00 + 300.00 - 120.50

    accounts = client.get("/api/accounts").get_json()
    assert accounts[0]["balance"] == round(expected_balance, 2)

    net_worth = client.get("/api/reports/net-worth").get_json()
    assert net_worth["net_worth"] == round(expected_balance, 2)

    forecast = client.get("/api/reports/forecast?months=3").get_json()
    assert forecast["current_net_worth"] == round(expected_balance, 2)
    assert len(forecast["projection"]) == 3

    debt = client.get("/api/reports/debt-payoff").get_json()
    assert debt["debts"] == []


def test_cash_flow_report_shape(client):
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/income",
        headers=headers,
        json={
            "title": "Pay",
            "category": "Salary / Wages",
            "amount": "2000.00",
            "income_date": date.today().replace(day=1).isoformat(),
        },
    )

    resp = client.get("/api/reports/cash-flow?months=12")
    assert resp.status_code == 200

    data = resp.get_json()
    assert len(data) == 12

    current = data[-1]
    assert current["income"] == 2000.0
    assert current["savings_rate"] == 100.0
    for key in ("month", "income", "expenses", "bill_payments", "outflow", "savings", "savings_rate"):
        assert key in current


# ---------------------------------------------------------------------------
# Regression tests for review pass 2 (bugs 1.1-1.9, 2.x-4.x, features)
# ---------------------------------------------------------------------------


def test_delete_endpoints_return_404_for_missing_ids(client):
    """Bug 1.1: category/payee/bill/expense/income deletes used to report
    success even for nonexistent ids."""
    token = login(client)
    headers = auth_headers(token)

    for path in (
        "/api/categories/9999",
        "/api/payees/9999",
        "/api/bills/9999",
        "/api/expenses/9999",
        "/api/income/9999",
    ):
        resp = client.delete(path, headers=headers)
        assert resp.status_code == 404, f"{path} returned {resp.status_code}"
        assert resp.get_json()["error"] == "Not found"


def test_partial_update_preserves_splits(client):
    """Bug 1.2: a PUT that omits "splits" must not delete existing splits."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Split expense",
            "category": "Other",
            "amount": "100.00",
            "expense_date": "2026-06-25",
            "splits": [
                {"category": "Groceries & Food", "amount": "60.00"},
                {"category": "Healthcare & Medical", "amount": "40.00"},
            ],
        },
    )
    assert resp.status_code == 200
    eid = resp.get_json()["id"]

    # Partial update touching ONLY notes.
    resp = client.put(f"/api/expenses/{eid}", headers=headers, json={"notes": "updated"})
    assert resp.status_code == 200

    data = client.get(f"/api/expenses/{eid}").get_json()
    assert data["notes"] == "updated"
    assert len(data["splits"]) == 2
    assert {s["category"] for s in data["splits"]} == {
        "Groceries & Food",
        "Healthcare & Medical",
    }

    # An explicit empty list still means "clear the splits".
    resp = client.put(f"/api/expenses/{eid}", headers=headers, json={"splits": []})
    assert resp.status_code == 200
    data = client.get(f"/api/expenses/{eid}").get_json()
    assert data["splits"] == []

    # Same guarantee for income.
    resp = client.post(
        "/api/income",
        headers=headers,
        json={
            "title": "Split income",
            "category": "Other",
            "amount": "90.00",
            "income_date": "2026-06-26",
            "splits": [
                {"category": "Salary / Wages", "amount": "50.00"},
                {"category": "Other", "amount": "40.00"},
            ],
        },
    )
    iid = resp.get_json()["id"]
    resp = client.put(f"/api/income/{iid}", headers=headers, json={"notes": "kept"})
    assert resp.status_code == 200
    data = client.get(f"/api/income/{iid}").get_json()
    assert data["notes"] == "kept"
    assert len(data["splits"]) == 2


def test_invalid_payee_id_with_payee_name_returns_400(client):
    """Bug 1.3: an invalid payee_id plus a free-text payee_name used to skip
    validation entirely and hit a raw foreign-key 500."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_id": 9999,
            "payee_name": "Fallback Name",
            "bill_category": "Other",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "items": [{"item_name": "x", "quantity": 1, "unit_price": "10.00"}],
        },
    )
    assert resp.status_code == 400
    assert "payee_id does not exist" in resp.get_json()["error"]

    # Same protection on update.
    create_resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "Real Bill",
            "bill_category": "Other",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "items": [{"item_name": "x", "quantity": 1, "unit_price": "10.00"}],
        },
    )
    bid = create_resp.get_json()["id"]

    resp = client.put(
        f"/api/bills/{bid}",
        headers=headers,
        json={
            "payee_id": 9999,
            "payee_name": "Fallback Name",
            "bill_category": "Other",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "items": [{"item_name": "x", "quantity": 1, "unit_price": "10.00"}],
        },
    )
    assert resp.status_code == 400


def test_recurring_bill_rule_resolves_payee_name_from_id(client):
    """Bug 1.4: recurring bill rules with only payee_id produced bills with a
    blank payee_name; they now resolve it like manual creation."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post("/api/payees", headers=headers, json={"name": "City Power"})
    assert resp.status_code == 200

    payees = client.get("/api/payees?limit=10", headers=headers).get_json()
    payee_id = next(p["id"] for p in payees["items"] if p["name"] == "City Power")

    resp = client.post(
        "/api/recurring",
        headers=headers,
        json={
            "name": "Power rule",
            "entity_type": "bill",
            "frequency": "monthly",
            "interval_value": 1,
            "next_run_date": "2026-08-01",
            "payload": {
                "payee_id": payee_id,
                "bill_category": "Electricity",
                "items": [{"item_name": "Usage", "quantity": 1, "unit_price": "75.00"}],
            },
        },
    )
    assert resp.status_code == 200

    resp = client.post("/api/recurring/run", headers=headers, json={})
    assert resp.status_code == 200
    assert not resp.get_json()["failed"], resp.get_json()["failed"]
    created_bills = [c for c in resp.get_json()["created"] if c["entity_type"] == "bill"]
    assert created_bills

    bills = client.get("/api/bills", headers=headers).get_json()
    generated = next(b for b in bills["items"] if b["payee_id"] == payee_id)
    assert generated["payee_name"] == "City Power"


def test_csv_import_row_isolation(client):
    """Bug 1.5: one bad row must not abort or roll back the whole import."""
    token = login(client)
    headers = auth_headers(token)

    items = [
        {"title": "Good One", "category": "Other", "amount": "10.00", "date": "2026-06-01"},
        {"title": "Bad Date", "category": "Other", "amount": "20.00", "date": "not-a-date"},
        {"title": "Bad Amount", "category": "Other", "amount": "-5.00", "date": "2026-06-03"},
        {"title": "Good Two", "category": "Other", "amount": "30.00", "date": "2026-06-04"},
    ]

    resp = client.post(
        "/api/import/csv/commit",
        headers=headers,
        json={"target_type": "expense", "items": items},
    )
    assert resp.status_code == 200

    data = resp.get_json()
    assert data["imported_count"] == 2
    assert len(data["failed"]) == 2
    assert [f["row"] for f in data["failed"]] == [1, 2]
    assert "date" in data["failed"][0]["error"]

    titles = [i["title"] for i in client.get("/api/expenses", headers=headers).get_json()["items"]]
    assert {"Good One", "Good Two"} <= set(titles)
    assert "Bad Date" not in titles


def test_csv_import_accepts_common_bank_date_formats(client):
    """Bug 1.6: MM/DD/YYYY (and other common formats) must import cleanly."""
    token = login(client)
    headers = auth_headers(token)

    items = [
        {"title": "US format", "amount": "12.00", "date": "06/22/2026"},
        {"title": "Slash ISO", "amount": "13.00", "date": "2026/06/23"},
        {"title": "Dotted", "amount": "14.00", "date": "24.06.2026"},
    ]

    resp = client.post(
        "/api/import/csv/commit",
        headers=headers,
        json={"target_type": "expense", "items": items},
    )
    assert resp.status_code == 200
    assert resp.get_json()["imported_count"] == 3, resp.get_json()

    expenses = client.get(
        "/api/expenses?from=2026-06-01&to=2026-06-30", headers=headers
    ).get_json()
    dates = {i["title"]: i["expense_date"] for i in expenses["items"]}
    assert dates["US format"] == "2026-06-22"
    assert dates["Slash ISO"] == "2026-06-23"
    assert dates["Dotted"] == "2026-06-24"


def test_budget_report_counts_split_categories(client):
    """Bug 1.7: split allocations drive category actuals instead of the
    parent expense's own category."""
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Mall trip",
            "category": "Other",
            "amount": "100.00",
            "expense_date": "2026-06-10",
            "splits": [
                {"category": "Groceries & Food", "amount": "60.00"},
                {"category": "Healthcare & Medical", "amount": "40.00"},
            ],
        },
    )

    client.post(
        "/api/budgets",
        headers=headers,
        json={"category": "Groceries & Food", "period": "monthly", "amount": "50.00"},
    )
    client.post(
        "/api/budgets",
        headers=headers,
        json={"category": "Healthcare & Medical", "period": "monthly", "amount": "30.00"},
    )

    resp = client.get(
        "/api/reports/budget?period=monthly&month=2026-06", headers=headers
    )
    assert resp.status_code == 200

    actuals = {i["category"]: i["actual"] for i in resp.get_json()["items"]}
    assert actuals["Groceries & Food"] == 60.0
    assert actuals["Healthcare & Medical"] == 40.0


def test_dashboard_top_categories_include_splits_and_bill_payments(client):
    """Bug 1.7: dashboard top categories cover splits and bill payments."""
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Split big box store",
            "category": "Clothing & Shopping",
            "amount": "80.00",
            "expense_date": date.today().replace(day=2).isoformat(),
            "splits": [
                {"category": "Groceries & Food", "amount": "50.00"},
                {"category": "Entertainment & Fun", "amount": "30.00"},
            ],
        },
    )

    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "Fiber ISP",
            "bill_category": "Internet & Cable",
            "bill_date": date.today().replace(day=3).isoformat(),
            "due_date": date.today().replace(day=3).isoformat(),
            "status": "Paid",
            "items": [{"item_name": "Monthly fiber", "quantity": 1, "unit_price": "70.00"}],
        },
    )
    assert resp.status_code == 200

    data = client.get("/api/dashboard", headers=headers).get_json()
    cats = {c["category"]: c["total"] for c in data["top_cats"]}
    assert cats.get("Internet & Cable") == 70.0
    assert cats.get("Groceries & Food") == 50.0
    assert cats.get("Entertainment & Fun") == 30.0
    assert "Clothing & Shopping" not in cats


def test_weekly_allowance_remaining_uses_monthly_equivalent(client):
    """Bug 1.8: a weekly allowance compares against ~52/12 of itself, not its
    raw weekly amount."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post("/api/members", headers=headers, json={"name": "Weekly Kid"})
    assert resp.status_code == 200

    members = client.get("/api/members", headers=headers).get_json()
    kid_id = next(m["id"] for m in members if m["name"] == "Weekly Kid")
    kid_name = "Weekly Kid"

    client.post(
        "/api/allowances",
        headers=headers,
        json={"member_id": kid_id, "amount": "20.00", "frequency": "weekly"},
    )

    # $25 of spending this month: more than the $20 weekly amount, but well
    # under the ~$86.67 monthly equivalent.
    client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Toys",
            "category": "Kids & Family",
            "amount": "25.00",
            "expense_date": date.today().isoformat(),
            "member_id": kid_id,
        },
    )

    allowances = client.get("/api/allowances", headers=headers).get_json()
    row = next(a for a in allowances if a["member_name"] == kid_name)

    expected_monthly_cents = round(2000 * 52 / 12)  # 8667
    assert row["monthly_equivalent_cents"] == expected_monthly_cents
    assert row["spent_this_month_cents"] == 2500
    assert row["remaining_this_month"] == round((expected_monthly_cents - 2500) / 100, 2)


def test_debt_payoff_extra_cascades_and_minimums_roll_over(client):
    """Bug 1.9: freed-up minimum payments roll into later months, and leftover
    extra cascades across multiple debts within one month."""
    token = login(client)
    headers = auth_headers(token)

    def add_loan(name, opening, minimum):
        resp = client.post(
            "/api/accounts",
            headers=headers,
            json={
                "name": name,
                "account_type": "Loan",
                "opening_balance": opening,
                "interest_rate": 0,
                "minimum_payment": minimum,
            },
        )
        assert resp.status_code == 200

    add_loan("Cascade Loan A", "1000.00", "100.00")
    add_loan("Cascade Loan B", "2000.00", "100.00")

    # Baseline, no extra: A clears at month 10; its freed $100 then doubles
    # B's pace (rollover), finishing at month 15 — not month 20.
    data = client.get("/api/reports/debt-payoff?extra_payment=0", headers=headers).get_json()
    snowball = data["snowball"]
    assert snowball["months"] == 15
    assert snowball["will_never_be_paid_off"] is False

    # With a $500 extra, the cascade applies leftovers to the NEXT debt in
    # the same month, finishing far sooner than minimums alone.
    data = client.get(
        "/api/reports/debt-payoff?extra_payment=500.00", headers=headers
    ).get_json()
    assert data["snowball"]["months"] < snowball["months"] * 0.5

    avalanche = client.get(
        "/api/reports/debt-payoff?extra_payment=500.00", headers=headers
    ).get_json()["avalanche"]
    assert avalanche["months"] > 0
    assert avalanche["total_interest_cents"] >= 0


def test_debt_payoff_honors_zero_minimum_and_flags_never_paid_off(client):
    """Bug 1.9: a $0 configured minimum is respected (no forced $10 floor),
    and hopeless plans are flagged instead of reporting month 360 as real."""
    token = login(client)
    headers = auth_headers(token)

    # Zero-interest family loan with an intentional $0 minimum: the extra
    # payment is the only thing paying it down ($100/month -> 10 months).
    resp = client.post(
        "/api/accounts",
        headers=headers,
        json={
            "name": "Family loan",
            "account_type": "Loan",
            "opening_balance": "1000.00",
            "interest_rate": 0,
            "minimum_payment": "0.00",
        },
    )
    assert resp.status_code == 200

    data = client.get(
        "/api/reports/debt-payoff?extra_payment=100.00", headers=headers
    ).get_json()
    assert data["snowball"]["months"] == 10
    assert data["avalanche"]["months"] == 10

    # Now a runaway debt: 100% APR dwarfs the payments -> never paid off.
    resp = client.post(
        "/api/accounts",
        headers=headers,
        json={
            "name": "Runaway card",
            "account_type": "Credit Card",
            "opening_balance": "100000.00",
            "interest_rate": 100,
            "minimum_payment": "50.00",
        },
    )
    assert resp.status_code == 200

    data = client.get("/api/reports/debt-payoff?extra_payment=0", headers=headers).get_json()
    assert data["snowball"]["will_never_be_paid_off"] is True
    assert data["avalanche"]["will_never_be_paid_off"] is True
    assert "warning" in data["snowball"]


def test_bill_payment_list_and_reversal_endpoints(client):
    """3.2: individual payments can be listed and reversed, with paid_cents
    and status recomputed automatically."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "Reversible Utilities",
            "bill_category": "Utilities",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "items": [{"item_name": "Service", "quantity": 1, "unit_price": "100.00"}],
        },
    )
    bid = resp.get_json()["id"]

    client.post(f"/api/bills/{bid}/pay", headers=headers, json={"amount": "40.00"})
    client.post(f"/api/bills/{bid}/pay", headers=headers, json={"amount": "60.00"})

    resp = client.get(f"/api/bills/{bid}/payments", headers=headers)
    assert resp.status_code == 200
    payments = resp.get_json()["items"]
    assert len(payments) == 2
    assert resp.get_json()["total_cents"] == 10000

    payment_id = payments[0]["id"]
    resp = client.delete(f"/api/bills/{bid}/payments/{payment_id}", headers=headers)
    assert resp.status_code == 200

    bill = client.get(f"/api/bills/{bid}").get_json()
    remaining = sum(p["amount_cents"] for p in bill["payments"])
    assert bill["paid_cents"] == remaining
    assert bill["status"] == ("Paid" if remaining >= bill["total_cents"] else "Partially Paid" if remaining else "Pending")

    # Reversing the second payment returns the bill to Pending.
    resp = client.delete(
        f"/api/bills/{bid}/payments/{payments[1]['id']}", headers=headers
    )
    assert resp.status_code == 200
    bill = client.get(f"/api/bills/{bid}").get_json()
    assert bill["paid_cents"] == 0
    assert bill["status"] == "Pending"
    assert bill["paid_at"] is None

    # Unknown bill / payment -> 404.
    assert client.delete(f"/api/bills/999/payments/1", headers=headers).status_code == 404
    assert client.delete(f"/api/bills/{bid}/payments/9999", headers=headers).status_code == 404


def test_duplicate_active_allowances_and_budgets_rejected(client):
    """3.3: two active rules for the same member/frequency (or budgets for the
    same category/member/period) are rejected with a clean 400."""
    token = login(client)
    headers = auth_headers(token)

    client.post("/api/members", headers=headers, json={"name": "Dup Kid"})
    members = client.get("/api/members", headers=headers).get_json()
    kid_id = next(m["id"] for m in members if m["name"] == "Dup Kid")

    resp = client.post(
        "/api/allowances",
        headers=headers,
        json={"member_id": kid_id, "amount": "5.00", "frequency": "weekly"},
    )
    assert resp.status_code == 200

    resp = client.post(
        "/api/allowances",
        headers=headers,
        json={"member_id": kid_id, "amount": "6.00", "frequency": "weekly"},
    )
    assert resp.status_code == 400
    assert "already exists" in resp.get_json()["error"]

    # Same frequency but different member is fine.
    client.post("/api/members", headers=headers, json={"name": "Dup Sib"})
    members = client.get("/api/members", headers=headers).get_json()
    sib_id = next(m["id"] for m in members if m["name"] == "Dup Sib")
    resp = client.post(
        "/api/allowances",
        headers=headers,
        json={"member_id": sib_id, "amount": "6.00", "frequency": "weekly"},
    )
    assert resp.status_code == 200

    # Budget duplicates.
    payload = {"category": "Groceries & Food", "period": "monthly", "amount": "10.00"}
    assert client.post("/api/budgets", headers=headers, json=payload).status_code == 200
    resp = client.post("/api/budgets", headers=headers, json=payload)
    assert resp.status_code == 400
    assert "already exists" in resp.get_json()["error"]

    # Deactivate the first, then creating a replacement succeeds.
    budgets = client.get(
        "/api/budgets?period=monthly&category=Groceries+%26+Food", headers=headers
    ).get_json()
    target = budgets[0]["id"]
    client.put(f"/api/budgets/{target}", headers=headers, json={**payload, "is_active": False})
    resp = client.post("/api/budgets", headers=headers, json=payload)
    assert resp.status_code == 200


def test_new_records_cannot_reference_deactivated_account(client):
    """3.4: creates validate is_active on referenced accounts."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/accounts",
        headers=headers,
        json={"name": "Closing Soon", "account_type": "Checking"},
    )
    assert resp.status_code == 200
    account_id = client.get("/api/accounts").get_json()[0]["id"]

    resp = client.delete(f"/api/accounts/{account_id}", headers=headers)
    assert resp.status_code == 200

    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Against closed account",
            "category": "Other",
            "amount": "5.00",
            "expense_date": "2026-06-20",
            "account_id": account_id,
        },
    )
    assert resp.status_code == 400
    assert "inactive" in resp.get_json()["error"]


def test_expense_member_string_links_to_member_id(client):
    """3.1: legacy free-text member values link to matching active members."""
    token = login(client)
    headers = auth_headers(token)

    client.post("/api/members", headers=headers, json={"name": "Linky"})
    members = client.get("/api/members", headers=headers).get_json()
    member_id = next(m["id"] for m in members if m["name"] == "Linky")

    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Linked by name",
            "category": "Other",
            "amount": "5.00",
            "expense_date": "2026-06-21",
            "member": "linky",  # case-insensitive match
        },
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/expenses/{resp.get_json()['id']}").get_json()
    assert detail["member"] == "Linky"
    assert detail["member_id"] == member_id

    # Explicit member_id wins and backfills the canonical name.
    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Linked by id",
            "category": "Other",
            "amount": "6.00",
            "expense_date": "2026-06-21",
            "member_id": member_id,
        },
    )
    detail = client.get(f"/api/expenses/{resp.get_json()['id']}").get_json()
    assert detail["member_id"] == member_id
    assert detail["member"] == "Linky"


def test_personal_access_tokens_bearer_auth_flow(app, client):
    """4.4: scoped, revocable bearer tokens authenticate without cookies or
    CSRF, and cannot exceed their owner's role."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/auth/tokens",
        headers=headers,
        json={"name": "cron job", "role": "Editor"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    plain_token = body["token"]
    assert plain_token.startswith("ffpat_")

    bare_client = app.test_client()  # fresh client: no session cookie

    # Read works with just the bearer header.
    resp = bare_client.get(
        "/api/dashboard", headers={"Authorization": f"Bearer {plain_token}"}
    )
    assert resp.status_code == 200

    # Writes work WITHOUT CSRF because there is no cookie to forge.
    resp = bare_client.post(
        "/api/expenses",
        headers={"Authorization": f"Bearer {plain_token}"},
        json={
            "title": "From automation",
            "category": "Other",
            "amount": "3.00",
            "expense_date": "2026-07-01",
        },
    )
    assert resp.status_code == 200

    # The scoped Editor role cannot manage users even though the owner is Admin.
    resp = bare_client.get("/api/users", headers={"Authorization": f"Bearer {plain_token}"})
    assert resp.status_code == 403

    # An invalid bearer credential must not silently fall back to any cookie.
    resp = bare_client.get("/api/dashboard", headers={"Authorization": "Bearer bogus"})
    assert resp.status_code == 401

    # Listing shows metadata but never the hash/plaintext.
    listing = client.get("/api/auth/tokens", headers=headers).get_json()
    entry = next(t for t in listing if t["id"] == body["id"])
    assert entry["revoked"] == 0
    assert "token_hash" not in entry and "token" not in entry

    # Revocation kills access immediately.
    resp = client.delete(f"/api/auth/tokens/{body['id']}", headers=headers)
    assert resp.status_code == 200

    resp = bare_client.get(
        "/api/dashboard", headers={"Authorization": f"Bearer {plain_token}"}
    )
    assert resp.status_code == 401


def test_goal_auto_allocation_on_income(client):
    """Section 6 sinking funds: a percentage of each income deposit flows to
    goals configured with allocation_pct."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/goals",
        headers=headers,
        json={"name": "Car fund", "target": "5000.00", "allocation_pct": 10},
    )
    assert resp.status_code == 200

    goal = client.get("/api/goals", headers=headers).get_json()[0]
    assert goal["allocation_pct"] == 10

    before = goal["current_cents"]

    resp = client.post(
        "/api/income",
        headers=headers,
        json={
            "title": "Paycheck with allocation",
            "category": "Salary / Wages",
            "amount": "250.00",
            "income_date": "2026-07-02",
        },
    )
    assert resp.status_code == 200

    goal = client.get("/api/goals", headers=headers).get_json()[0]
    assert goal["current_cents"] == before + 2500  # 10% of $250


def test_multi_currency_fields_roundtrip(client):
    """Section 6 multi-currency: original currency fields persist and settle
    amount_cents stays in the primary currency."""
    token = login(client)
    headers = auth_headers(token)

    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Euro purchase",
            "category": "Other",
            "amount": "108.00",
            "expense_date": "2026-07-05",
            "original_currency": "EUR",
            "exchange_rate": 1.08,
            "original_amount": "100.00",
        },
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/expenses/{resp.get_json()['id']}").get_json()
    assert detail["original_currency"] == "EUR"
    assert detail["exchange_rate"] == 1.08
    assert detail["original_amount_cents"] == 10000
    assert detail["amount_cents"] == 10800  # settled in primary currency

    # Invalid currency code rejected.
    resp = client.post(
        "/api/expenses",
        headers=headers,
        json={
            "title": "Bad fx",
            "category": "Other",
            "amount": "1.00",
            "expense_date": "2026-07-05",
            "original_currency": "EURO",
        },
    )
    assert resp.status_code == 400


def test_forecast_includes_recurring_rules_and_scheduled_bills(client):
    """Section 6 smarter forecast: recurring rules and unpaid bill balances
    feed forward-looking projections."""
    token = login(client)
    headers = auth_headers(token)

    client.post(
        "/api/recurring",
        headers=headers,
        json={
            "name": "Forecast salary",
            "entity_type": "income",
            "frequency": "monthly",
            "interval_value": 1,
            "next_run_date": "2026-09-01",
            "payload": {"title": "Salary", "amount": "3000.00", "category": "Other"},
        },
    )

    resp = client.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "Scheduled Landlord",
            "bill_category": "Mortgage / Rent",
            "bill_date": "2026-09-01",
            "due_date": "2026-09-05",
            "items": [{"item_name": "Rent", "quantity": 1, "unit_price": "900.00"}],
        },
    )
    assert resp.status_code == 200

    data = client.get("/api/reports/forecast?months=6", headers=headers).get_json()
    assert data["recurring_rule_count"] >= 1
    assert data["recurring_monthly_income"] == 3000.0
    assert data["scheduled_bills_total"] >= 900.0
    scheduled_months = [p["scheduled_bills"] for p in data["projection"]]
    assert any(s > 0 for s in scheduled_months)


def test_concurrent_bill_payments_cannot_overpay(app):
    """Race-condition guard: BEGIN IMMEDIATE locking means parallel payment
    attempts can never push paid_cents past total_cents."""
    from concurrent.futures import ThreadPoolExecutor

    setup = app.test_client()
    resp = setup.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password-123"},
    )
    headers = auth_headers(resp.get_json()["csrf_token"])

    resp = setup.post(
        "/api/bills",
        headers=headers,
        json={
            "payee_name": "Racy Bill",
            "bill_category": "Other",
            "bill_date": "2026-06-01",
            "due_date": "2026-06-15",
            "items": [{"item_name": "Item", "quantity": 1, "unit_price": "100.00"}],
        },
    )
    bill_id = resp.get_json()["id"]

    barrier = threading.Barrier(8)

    def attempt(i):
        c = app.test_client()
        login_resp = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-password-123"},
        )
        csrf = login_resp.get_json()["csrf_token"]
        barrier.wait(timeout=10)
        return c.post(
            f"/api/bills/{bill_id}/pay",
            headers={"X-CSRF-Token": csrf},
            json={"amount": "60.00"},  # $60 against a $100 bill: only one fits
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(attempt, range(8)))

    bill = setup.get(f"/api/bills/{bill_id}").get_json()
    assert bill["paid_cents"] <= bill["total_cents"]
    assert codes.count(200) == 1
    assert codes.count(400) == 7
