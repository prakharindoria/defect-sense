"""Registration is persisted before an authenticated session is issued."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_registration_persists_to_the_configured_user_store(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "db-backed-user@ds.com",
            "display_name": "Database Backed User",
            "role": "qa",
            "password": "strong-demo-password",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["username"] == "db-backed-user@ds.com"

    # A second login reads the persisted user directory, not the form state.
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "db-backed-user@ds.com", "password": "strong-demo-password"},
    )
    assert login.status_code == 200, login.text


def test_registration_refuses_a_duplicate_username(client: TestClient) -> None:
    request = {
        "username": "unique-directory-user@ds.com",
        "display_name": "Directory User",
        "role": "shop_floor_worker",
        "password": "strong-demo-password",
    }
    assert client.post("/api/v1/auth/register", json=request).status_code == 200
    assert client.post("/api/v1/auth/register", json=request).status_code == 409
