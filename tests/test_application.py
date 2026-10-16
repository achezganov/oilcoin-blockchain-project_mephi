from concurrent.futures import ThreadPoolExecutor
from conftest import post, register
from database import connect, unit_of_work
from storage import load_blockchain


def test_public_pages_and_missing_templates(client):
    for path in ("/", "/app", "/tokenomics", "/explorer", "/demo", "/balance", "/validate", "/debug", "/register", "/login", "/health", "/api/stats"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.data[:200])
    assert client.get("/health").json["status"] == "ok"


def test_auth_csrf_and_debug_do_not_allow_spending(client):
    assert client.post("/mine").status_code == 400
    assert post(client,"/mine").status_code == 302
    assert post(client,"/transaction", {"sender":"OIL_"+"a"*32}).status_code == 302
    assert client.post("/debug").status_code == 405
    assert client.get("/logout").status_code == 405
    register(client)
    assert post(client,"/demo/generate").status_code == 403
    assert client.get("/api/demo/status").status_code == 404


def test_register_password_constraints_and_key_encryption(client, application):
    assert post(client,"/register",{"password":"short", "password_confirm":"short"}).status_code == 400
    address = register(client)
    with connect(application.config["DATABASE_PATH"]) as conn:
        row = dict(conn.execute("SELECT * FROM accounts WHERE address=?", (address,)).fetchone())
    assert row["password_hash"].startswith("scrypt:")
    assert row["encrypted_private_key"].startswith("gAAAA")
    assert "private_key" not in client.get("/api/chain").get_data(as_text=True)
    assert "encrypted_private_key" not in client.get("/dashboard").get_data(as_text=True)


def test_gameplay_signature_confirmation_upgrade_and_restart(client, application):
    miner = register(client)
    receiver_client = application.test_client()
    receiver = register(receiver_client,"Recipient")
    assert post(client,"/mine/quick").status_code == 302
    assert client.get(f"/api/balance/{miner}").json["confirmed_balance"] == 50
    assert client.get("/api/game").json["xp"] == 75
    assert post(client,"/transaction", {"sender":receiver,"recipient":receiver,"amount":"5","fee":"1"}).status_code == 302
    pending = client.get("/api/pending").json
    assert len(pending) == 1 and pending[0]["sender"] == miner
    assert pending[0]["signature"] and pending[0]["public_key"]
    assert client.get("/api/explorer/search",query_string={"q":pending[0]["tx_id"]}).json["type"] == "transaction"
    assert client.get(f"/api/wallet/{miner}/stats").json["total_sent"] == 0
    assert client.get(f"/api/balance/{miner}").json["available_balance"] == 44
    assert post(client,"/game/upgrade").status_code == 302
    assert client.get("/api/game").json["upgrade_pending"]
    post(client,"/mine/quick")
    assert client.get(f"/api/balance/{receiver}").json["confirmed_balance"] == 5
    game = client.get("/api/game").json
    assert game["rig_level"] == 1 and not game["upgrade_pending"]
    assert all(mission["completed"] for mission in game["missions"])
    before = game["xp"]
    post(client,"/mine/quick")
    assert client.get("/api/game").json["xp"] == before+30
    from app import create_app
    restarted = create_app(dict(application.config)).test_client()
    assert restarted.get(f"/api/balance/{receiver}").json["confirmed_balance"] == 5
    for path in ("/dashboard", "/mine", "/transaction", "/block/1", f"/tx/{pending[0]['tx_id']}", f"/address/{miner}"):
        assert client.get(path).status_code == 200


def test_parallel_writers_do_not_lose_blocks(application):
    clients = [application.test_client(), application.test_client()]
    addresses = [register(client,str(index)) for index,client in enumerate(clients)]
    tokens = [client.get("/api/csrf").json["csrf_token"] for client in clients]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda pair: pair[0].post("/mine/quick",data={"csrf_token":pair[1]}),zip(clients,tokens)))
    assert all(response.status_code == 302 for response in responses)
    with unit_of_work(application.config["DATABASE_PATH"],write=False):
        chain = load_blockchain()
        assert len(chain.chain) == 3
        assert all(chain.get_balance(address) == 50 for address in addresses)
        assert chain.is_chain_valid()[0]


def test_corruption_is_not_silently_replaced(client, application):
    with connect(application.config["DATABASE_PATH"]) as conn:
        conn.execute("UPDATE blocks SET payload=replace(payload, 'GENESIS_TX', 'TAMPERED') WHERE height=0")
    response = client.get("/health")
    assert response.status_code == 503
    with connect(application.config["DATABASE_PATH"]) as conn:
        assert "TAMPERED" in conn.execute("SELECT payload FROM blocks WHERE height=0").fetchone()[0]


def test_demo_opt_in_is_atomic_and_idempotent(application):
    application.config["ENABLE_DEMO"] = True
    client = application.test_client()
    register(client)
    assert post(client,"/demo/generate").status_code == 302
    status = client.get("/api/demo/status").json
    assert status["demo_wallet_count"] == 5 and status["pending_transactions"] == 2
    assert client.get("/demo").status_code == 200
    with unit_of_work(application.config["DATABASE_PATH"],write=False):
        assert load_blockchain().is_chain_valid()[0]


def test_production_secret_required(tmp_path):
    from app import create_app
    import pytest
    with pytest.raises(RuntimeError):
        create_app({"APP_ENV":"production", "SECRET_KEY":"weak", "DATABASE_PATH":str(tmp_path/"prod.db")})


def test_malformed_csrf_and_long_search_are_client_errors(client):
    register(client)
    assert client.post('/mine',data={'csrf_token':'неверный-токен'}).status_code == 400
    assert client.get('/api/explorer/search',query_string={'q':'9'*5000}).json['type'] == 'invalid'
