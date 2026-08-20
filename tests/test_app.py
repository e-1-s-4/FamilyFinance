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
