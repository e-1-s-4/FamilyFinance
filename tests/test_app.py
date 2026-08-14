import pytest
from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.db"),
        "SECRET_KEY": "test-secret",
        "ADMIN_USER": "admin",
        "ADMIN_PASSWORD": "test-password-123",
    })
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password-123"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "csrf_token" in data
    return data["csrf_token"]


def test_health(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_login_dashboard_and_expense_flow(client):
    token = login(client)
    headers = {"X-CSRF-Token": token}

    resp = client.get("/api/dashboard")
    assert resp.status_code == 200

    payload = {
        "title": "Supermarket",
        "category": "Groceries & Food",
        "amount": "42.50",
        "expense_date": "2026-06-20",
        "store": "Market",
        "member": "Alice",
    }

    resp = client.post("/api/expenses", json=payload, headers=headers)
    assert resp.status_code == 200

    resp = client.get("/api/expenses")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Supermarket"
    assert data["items"][0]["amount_cents"] == 4250


def test_csrf_rejected(client):
    login(client)

    payload = {
        "title": "No CSRF",
        "amount": "10.00",
        "expense_date": "2026-06-20",
    }

    resp = client.post("/api/expenses", json=payload)
    assert resp.status_code == 403
