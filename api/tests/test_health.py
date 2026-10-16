def test_health_reaches_the_database(client):
    assert client.get("/health").json() == {"ok": True}
