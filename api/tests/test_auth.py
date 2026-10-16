def test_me_without_cookie_is_401(client):
    client.cookies.clear()
    assert client.get("/me").status_code == 401


def test_forged_cookie_is_401(client):
    client.cookies.set("session", "forged.cookie.value")
    assert client.get("/me").status_code == 401
    client.cookies.clear()


def test_dev_login_rejects_unknown_email(client):
    assert client.post("/auth/dev-login", json={"email": "nobody@newtonite.dev"}).status_code == 404


def test_login_then_me_returns_teams_and_roles(client):
    r = client.post("/auth/dev-login", json={"email": "Ava.Okafor@newtonite.dev"})
    assert r.status_code == 200
    me = client.get("/me").json()
    assert me["email"] == "ava.okafor@newtonite.dev"
    assert me["is_admin"] is True
    assert {(t["name"], t["role"]) for t in me["teams"]} == {
        ("Payments", "lead"),
        ("Onboarding", "viewer"),
    }
