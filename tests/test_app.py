import io
import os
import sqlite3
import tempfile
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
