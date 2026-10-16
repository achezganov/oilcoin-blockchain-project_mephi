from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    g,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from accounts import (
    create_wallet_account,
    decrypt_private_key,
    find_wallet_account,
    load_wallet_accounts,
    save_wallet_accounts,
    verify_wallet_password,
)
from analytics import get_chart_data, get_tokenomics_summary, get_total_transaction_count
from demo import DEMO_LABELS, DEMO_PASSWORD, create_demo_data, is_demo_wallet
from explorer_utils import (
    find_block_by_index,
    find_transaction_location,
    get_address_details,
    search_explorer,
    summarize_operations,
)
from storage import load_blockchain, save_blockchain
from wallet import is_valid_address


import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import timedelta
from werkzeug.local import LocalProxy
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException
from database import active_connection, connect, initialize, default_path
from documentation import blueprint as documentation_blueprint
from game import get_game_state, leaderboard, purchase_upgrade, record_mining, rig_state, settle_missions
import district
import layout
import market
import colony
import arena
import engine
import npc
import campaign
import chests
import card_builds

def create_app(config=None):
    app = Flask(__name__)
    app.register_blueprint(documentation_blueprint)
    app.config.update(
        DATABASE_PATH=default_path(),
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        APP_ENV=os.environ.get("APP_ENV", "development"),
        MINING_DIFFICULTY=int(os.environ.get("MINING_DIFFICULTY", "3")),
        TRUST_PROXY=os.environ.get("TRUST_PROXY", "false").lower() == "true",
        ENABLE_DEMO=os.environ.get("ENABLE_DEMO", "false").lower() == "true",
        ALLOW_MANUAL_MINING=os.environ.get("ALLOW_MANUAL_MINING", "false").lower() == "true",
        ALLOW_LEGACY_PRODUCTION=False,
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        app.config.update(config)
    if app.config["APP_ENV"] == "production" and len(app.config["SECRET_KEY"] or "") < 32:
        raise RuntimeError("Production requires a random SECRET_KEY of at least 32 characters")
    if not app.config["SECRET_KEY"]:
        secret_path = Path(app.config["DATABASE_PATH"]).parent / ".secret_key"
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(secret_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as output:
                output.write(secrets.token_hex(32))
        app.config["SECRET_KEY"] = secret_path.read_text().strip()
    if not 1 <= app.config["MINING_DIFFICULTY"] <= 4:
        raise ValueError("MINING_DIFFICULTY must be between 1 and 4 for this single-node server")
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    BLOCKCHAIN_FILE = WALLETS_FILE = app.config["DATABASE_PATH"]
    initialize(BLOCKCHAIN_FILE)
    load_blockchain(BLOCKCHAIN_FILE, difficulty=app.config["MINING_DIFFICULTY"])
    with app.app_context():
        accounts = load_wallet_accounts(WALLETS_FILE)["wallets"]
        if accounts:
            try:
                decrypt_private_key(accounts[0])
            except Exception as error:
                raise RuntimeError("SECRET_KEY cannot decrypt this database; restore its original secret") from error
    blockchain = LocalProxy(lambda: g.blockchain)

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return session["csrf_token"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def begin_request():
        if request.endpoint is None or request.endpoint == "static" or request.endpoint.startswith("documentation."):
            return
        if request.method == "POST":
            submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not hmac.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8")):
                abort(400, description="Недействительный CSRF-токен. Обновите страницу и повторите действие.")
        g.db = connect(BLOCKCHAIN_FILE)
        g.db_token = active_connection.set(g.db)
        g.db.execute("BEGIN IMMEDIATE" if request.method == "POST" else "BEGIN")
        if request.method == "POST":
            limit = 10 if request.endpoint in {"mine_page", "mine_quick"} else 20
            if request.endpoint == "base_action":
                limit = 60
            if request.endpoint in {"colony_action", "colony_sync", "arena_action", "campaign_action"}:
                limit = 180
            if request.endpoint == "demo_generate_page":
                limit = 1
            window = int(time.time()) // 60
            key = hashlib.sha256(((request.remote_addr or "local") + ":" + str(request.endpoint)).encode()).hexdigest()
            row = g.db.execute("SELECT attempts,window FROM rate_limits WHERE key=?", (key,)).fetchone()
            attempts = row[0] + 1 if row and row[1] == window else 1
            g.db.execute("INSERT INTO rate_limits VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET window=excluded.window,attempts=excluded.attempts", (key,window,attempts))
            g.db.execute("DELETE FROM rate_limits WHERE window < ?", (window-10,))
            if attempts > limit:
                abort(429, description="Слишком много запросов. Подождите минуту.")
        g.blockchain = load_blockchain(BLOCKCHAIN_FILE)
        if session.get("wallet_address") and not find_wallet_account(session["wallet_address"], WALLETS_FILE):
            session.clear()

    @app.after_request
    def finish_request(response):
        if hasattr(g, "db"):
            if response.status_code < 500:
                g.db.commit()
            else:
                g.db.rollback()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.teardown_request
    def close_request(error):
        if hasattr(g, "db"):
            g.db.rollback()
            g.db.close()
            active_connection.reset(g.db_token)

    @app.errorhandler(sqlite3.DatabaseError)
    def database_busy(error):
        app.logger.error("Database operation failed: %s", error)
        return jsonify(error="База данных недоступна. Повторите запрос позже."), 503

    @app.errorhandler(ValueError)
    def invalid_ledger(error):
        app.logger.error("Ledger validation failed: %s", error)
        return jsonify(error="Данные не прошли проверку целостности. Обратитесь к администратору."), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        explanations = {
            400: "Проверьте данные формы и обновите страницу перед повторной отправкой.",
            403: "Это действие недоступно в текущем режиме приложения.",
            404: "Запрошенная страница не найдена.",
            405: "Этот способ обращения к странице не поддерживается.",
            413: "Слишком большой запрос. Сократите содержимое формы.",
            429: "Слишком много действий за минуту. Подождите и повторите запрос.",
        }
        message = explanations.get(error.code, "Не удалось обработать запрос.")
        if request.path.startswith("/api/"):
            return jsonify(error=message), error.code
        return render_template("error.html", code=error.code, message=message), error.code

    def _user_message(message):
        return {
            "Signed transaction added to pending pool.": "Перевод подписан и добавлен в очередь. Подтвердите его новым блоком.",
            "Block mined successfully.": "Блок добыт и сохранён. Переводы подтверждены, награда начислена по правилам сети.",
            "Insufficient available balance (confirmed balance minus pending reserves).": "Недостаточно доступных OIL с учётом ожидающих переводов и комиссии.",
            "Sender or recipient address format is invalid.": "Проверьте адрес получателя: OIL_ и 32 шестнадцатеричных символа.",
            "Sender and recipient cannot be the same.": "Нельзя отправить перевод самому себе.",
            "Amount must be positive and fee cannot be negative.": "Сумма должна быть положительной, комиссия — неотрицательной.",
            "Amounts support at most 8 decimal places; no rounding is allowed.": "В сумме и комиссии допустимо не более 8 знаков после запятой.",
            "Amount must be a finite number.": "Введите корректное число.",
            "Amount must be a finite number with at most 8 decimal places.": "Введите число с точностью до 8 знаков после запятой.",
            "Monetary value is non-finite or outside the supported range.": "Сумма выходит за допустимый диапазон.",
        }.get(message, message)

    def _mine_for_account(address):
        rig_level, _ = rig_state(blockchain, address)
        ok, message, block = blockchain.mine_pending_transactions(address)
        if ok:
            record_mining(blockchain, address, block, rig_level)
            district.confirm_construction(blockchain)
            market.settle(blockchain)
            chests.settle(blockchain)
        return ok, message, block

    def _short_address(address: str) -> str:
        if not address or len(address) < 14:
            return address
        return f"{address[:8]}...{address[-4:]}"


    def short_id(value: str, start: int = 10, end: int = 6) -> str:
        text = str(value or "")
        if len(text) <= start + end + 3:
            return text
        return f"{text[:start]}...{text[-end:]}"


    def get_current_wallet_address() -> str | None:
        address = session.get("wallet_address")
        if not isinstance(address, str) or not address:
            return None
        return address


    def get_current_wallet_account() -> dict | None:
        address = get_current_wallet_address()
        if not address:
            return None
        return find_wallet_account(address, str(WALLETS_FILE))


    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not get_current_wallet_account():
                flash("Сначала войдите в кошелёк.", "error")
                return redirect(url_for("login_page"))
            return view(*args, **kwargs)

        return wrapped


    @app.context_processor
    def inject_auth_context():
        wallet_address = get_current_wallet_address()
        return {
            "current_wallet_address": wallet_address,
            "current_wallet_short": _short_address(wallet_address or ""),
            "is_logged_in": bool(wallet_address),
            "short_id": short_id,
            "demo_enabled": app.config["ENABLE_DEMO"],
        }


    def _supply_stats() -> dict:
        total_minted = blockchain.calculate_total_supply()
        return {
            "total_minted": total_minted,
            "circulating_supply": blockchain.calculate_circulating_supply(),
            "burned_supply": round(blockchain.burned_supply, 8),
        }


    def _balance_snapshot(address: str) -> dict:
        confirmed = blockchain.get_balance(address)
        reserved = blockchain.get_reserved_pending(address)
        return {
            "confirmed_balance": round(confirmed, 8),
            "reserved_pending": round(reserved, 8),
            "available_balance": round(confirmed - reserved, 8),
        }


    def get_wallet_operations(address: str) -> list[dict]:
        operations: list[dict] = []

        for block in blockchain.chain:
            for tx in block.transactions:
                if tx.get("sender") == address or tx.get("recipient") == address:
                    operations.append(
                        {
                            "status": "confirmed",
                            "block_index": block.index,
                            "transaction": tx,
                        }
                    )

        for tx in blockchain.pending_transactions:
            if tx.get("sender") == address or tx.get("recipient") == address:
                operations.append(
                    {
                        "status": "pending",
                        "block_index": None,
                        "transaction": tx,
                    }
                )

        operations.sort(
            key=lambda item: item["transaction"].get("timestamp", ""),
            reverse=True,
        )
        return operations


    def get_wallet_stats(address: str) -> dict:
        operations = get_wallet_operations(address)
        stats = summarize_operations(operations, address)
        return {key: value for key, value in stats.items() if key.startswith("total_")} | {
            "operation_count": len(operations)
        }


    def _collect_latest_confirmed_transactions(limit: int = 10) -> list[dict]:
        result = []
        for block in reversed(blockchain.chain):
            for tx in reversed(block.transactions):
                if tx.get("type") == "genesis":
                    continue
                result.append(
                    {
                        "status": "confirmed",
                        "block_index": block.index,
                        "transaction": tx,
                    }
                )
                if len(result) >= limit:
                    return result
        return result


    def _build_explorer_overview(limit: int = 10) -> dict:
        summary = get_tokenomics_summary(blockchain)
        valid, validation_message = blockchain.is_chain_valid()

        latest_blocks = list(reversed(blockchain.chain))[:limit]
        latest_confirmed_transactions = _collect_latest_confirmed_transactions(limit)
        pending_transactions = list(reversed(blockchain.pending_transactions))

        return {
            "summary": {
                "total_blocks": len(blockchain.chain),
                "confirmed_transactions": get_total_transaction_count(blockchain),
                "pending_transactions": len(blockchain.pending_transactions),
                "total_minted": summary.get("total_minted_supply", 0.0),
                "burned_supply": summary.get("burned_supply", 0.0),
            },
            "valid": valid,
            "validation_message": validation_message,
            "latest_blocks": latest_blocks,
            "latest_confirmed_transactions": latest_confirmed_transactions,
            "pending_transactions": pending_transactions,
        }


    def _demo_status_data() -> dict:
        wallet_data = load_wallet_accounts(str(WALLETS_FILE))
        demo_wallets = [w for w in wallet_data.get("wallets", [])
                        if is_demo_wallet(w) and w.get("label") in DEMO_LABELS]
        summary = get_tokenomics_summary(blockchain)
        valid, validation_message = blockchain.is_chain_valid()
        return {
            "enabled": app.config["ENABLE_DEMO"],
            "demo_wallets": demo_wallets,
            "demo_wallets_exist": len(demo_wallets) > 0,
            "demo_wallet_count": len(demo_wallets),
            "demo_addresses": [w.get("address") for w in demo_wallets],
            "stats": {
                "blocks": len(blockchain.chain),
                "pending_transactions": len(blockchain.pending_transactions),
                "total_minted": summary.get("total_minted_supply", 0.0),
                "burned_supply": summary.get("burned_supply", 0.0),
                "total_transfer_volume": summary.get("total_transfer_volume", 0.0),
            },
            "chain_valid": valid,
            "validation_message": validation_message,
            "demo_password_hint": DEMO_PASSWORD,
        }


    def _build_debug_payload(address: str = "") -> dict:
        latest_block = blockchain.get_latest_block()
        valid, validation_message = blockchain.is_chain_valid()

        payload = {
            "debug": True,
            "chain_length": len(blockchain.chain),
            "pending_count": len(blockchain.pending_transactions),
            "difficulty": blockchain.difficulty,
            "mining_reward": blockchain.mining_reward,
            "max_supply": blockchain.max_supply,
            "total_minted": blockchain.calculate_total_supply(),
            "circulating_supply": blockchain.calculate_circulating_supply(),
            "burned_supply": blockchain.burned_supply,
            "is_chain_valid": valid,
            "validation_message": validation_message,
            "latest_block": {
                "index": latest_block.index,
                "hash": latest_block.hash,
                "previous_hash": latest_block.previous_hash,
                "nonce": latest_block.nonce,
                "tx_count": len(latest_block.transactions),
                "timestamp": latest_block.timestamp,
            },
            "pending_transactions": blockchain.pending_transactions,
        }

        if address:
            if is_valid_address(address):
                payload["address_snapshot"] = {
                    "address": address,
                    **_balance_snapshot(address),
                }
            else:
                payload["address_snapshot_error"] = "Invalid address format."

        return payload


    @app.route("/")
    def index():
        summary = get_tokenomics_summary(blockchain)
        is_valid, validation_message = blockchain.is_chain_valid()
        return render_template(
            "landing.html",
            landing_stats={
                "blocks": summary.get("total_blocks", 0),
                "total_minted": summary.get("total_minted_supply", 0.0),
                "burned": summary.get("burned_supply", 0.0),
                "circulating": summary.get("circulating_supply", 0.0),
                "pending": summary.get("total_pending_transactions", 0),
            },
            is_chain_valid=is_valid,
            validation_message=validation_message,
        )


    @app.route("/landing")
    def landing_page():
        return redirect(url_for("index"))


    @app.route("/app")
    def app_home_page():
        stats = _supply_stats()
        return render_template(
            "index.html",
            block_count=len(blockchain.chain),
            pending_count=len(blockchain.pending_transactions),
            difficulty=blockchain.difficulty,
            mining_reward=blockchain.mining_reward,
            max_supply=blockchain.max_supply,
            total_minted=stats["total_minted"],
            circulating_supply=stats["circulating_supply"],
            burned_supply=stats["burned_supply"],
        )


    @app.route("/demo")
    def demo_page():
        return render_template("demo.html", demo=_demo_status_data() if app.config["ENABLE_DEMO"] else {"enabled": False})


    @app.route("/demo/generate", methods=["POST"])
    @login_required
    def demo_generate_page():
        if not app.config["ENABLE_DEMO"]:
            abort(403)
        wallet_data = load_wallet_accounts(str(WALLETS_FILE))
        ok, message, summary = create_demo_data(blockchain, wallet_data)
        if ok:
            save_wallet_accounts(wallet_data, str(WALLETS_FILE))
            save_blockchain(blockchain, str(BLOCKCHAIN_FILE))
            settle_missions(blockchain)
            flash(
                f"{message} Wallets created: {summary.get('wallets_created', 0)}, "
                f"Blocks mined: {summary.get('blocks_mined', 0)}, "
                f"Confirmed TX: {summary.get('transactions_confirmed', 0)}, "
                f"Pending TX: {summary.get('transactions_pending', 0)}",
                "success",
            )
        else:
            flash(_user_message(message), "error")
        return redirect(url_for("demo_page"))


    @app.route("/tokenomics")
    def tokenomics_page():
        summary = get_tokenomics_summary(blockchain)
        charts = get_chart_data(blockchain)
        is_valid, validation_message = blockchain.is_chain_valid()
        return render_template(
            "tokenomics.html",
            summary=summary,
            charts=charts,
            is_valid=is_valid,
            validation_message=validation_message,
        )


    @app.route("/register", methods=["GET", "POST"])
    def register_page():
        if request.method == "POST":
            label = request.form.get("label", "")
            password = request.form.get("password", "")
            password_confirm = request.form.get("password_confirm", "")

            if not password:
                flash("Введите пароль.", "error")
                return render_template("register.html")
            if password != password_confirm:
                flash("Пароли не совпадают.", "error")
                return render_template("register.html")

            try:
                account = create_wallet_account(label, password, str(WALLETS_FILE))
            except ValueError as error:
                flash(str(error), "error")
                return render_template("register.html"), 400
            session.clear()
            session.permanent = True
            session["wallet_address"] = account["address"]
            district.ensure_base(account["address"])
            flash("Добро пожаловать! У вас есть база и стартовые ресурсы. Соберите нефть и выберите первую цель.", "success")
            return redirect(url_for("base_page"))

        return render_template("register.html")


    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if request.method == "POST":
            address = request.form.get("address", "").strip()
            password = request.form.get("password", "")

            if len(password) > 256 or not verify_wallet_password(address, password, str(WALLETS_FILE)):
                flash("Неверный адрес или пароль.", "error")
                return render_template("login.html"), 401
            session.clear()
            session.permanent = True
            session["wallet_address"] = address
            flash("Вы вошли в кошелёк.", "success")
            return redirect(url_for("base_page"))

        return render_template("login.html")


    @app.route("/logout", methods=["POST"])
    def logout_page():
        session.clear()
        flash("Вы вышли из кошелька.", "success")
        return redirect(url_for("index"))


    @app.route("/dashboard")
    @login_required
    def dashboard_page():
        address = get_current_wallet_address()
        account = get_current_wallet_account()
        if not account:
            session.pop("wallet_address", None)
            flash("Кошелёк не найден. Войдите снова.", "error")
            return redirect(url_for("login_page"))

        snapshot = _balance_snapshot(address)
        stats = get_wallet_stats(address)
        operations = get_wallet_operations(address)

        return render_template(
            "dashboard.html",
            account=account,
            address=address,
            snapshot=snapshot,
            stats=stats,
            operations=operations,
            game=get_game_state(blockchain, address),
            leaderboard=leaderboard(blockchain),
        )


    @app.route("/wallet", methods=["GET", "POST"])
    def wallet_page():
        return redirect(url_for("dashboard_page" if get_current_wallet_account() else "register_page"))


    @app.route("/transaction", methods=["GET", "POST"])
    @login_required
    def transaction_page():
        current_wallet = get_current_wallet_address()
        sender_prefill = current_wallet or ""

        if request.method == "POST":
            sender = current_wallet
            recipient = request.form.get("recipient", "")
            amount = request.form.get("amount", "")
            fee = request.form.get("fee", "")

            ok, message = blockchain.add_transaction(sender, recipient, amount, fee, private_key=decrypt_private_key(get_current_wallet_account()))
            if ok:
                save_blockchain(blockchain, str(BLOCKCHAIN_FILE))
                flash(_user_message(message), "success")
                if current_wallet:
                    return redirect(url_for("base_page" if request.form.get("return_to") == "base" else "dashboard_page"))
            else:
                flash(_user_message(message), "error")

        return render_template(
            "transaction.html",
            pending_transactions=blockchain.pending_transactions,
            sender_prefill=sender_prefill,
            snapshot=_balance_snapshot(current_wallet),
            is_logged_in=bool(current_wallet),
        )


    @app.route("/mine", methods=["GET", "POST"])
    @login_required
    def mine_page():
        mined_block = None
        current_wallet = get_current_wallet_address()

        if request.method == "POST":
            if not app.config['ALLOW_MANUAL_MINING']:
                flash('Блоки добываются автоматически каждые 30 секунд, пока игроки активны или есть платежи.', 'success')
                return redirect(url_for('base_page'))
            miner_address = current_wallet
            ok, message, block = _mine_for_account(miner_address)
            if ok:
                save_blockchain(blockchain, str(BLOCKCHAIN_FILE))
                flash(_user_message(message), "success")
                if current_wallet:
                    return redirect(url_for("base_page" if request.form.get("return_to") == "base" else "dashboard_page"))
                mined_block = block
            else:
                flash(_user_message(message), "error")

        return render_template("mine.html", mined_block=mined_block, is_logged_in=bool(current_wallet), difficulty=blockchain.difficulty, mining_reward=blockchain.mining_reward, pending_count=len(blockchain.pending_transactions))


    @app.route("/mine/quick", methods=["POST"])
    @login_required
    def mine_quick():
        if not app.config['ALLOW_MANUAL_MINING']:
            abort(403)
        miner_address = get_current_wallet_address()
        ok, message, _block = _mine_for_account(miner_address)
        if ok:
            save_blockchain(blockchain, str(BLOCKCHAIN_FILE))
            flash(_user_message(message), "success")
        else:
            flash(_user_message(message), "error")
        return redirect(url_for("dashboard_page"))


    @app.route("/balance", methods=["GET", "POST"])
    def balance_page():
        address = get_current_wallet_address() or ""
        snapshot = _balance_snapshot(address) if address else None

        if request.method == "POST":
            address = request.form.get("address", "").strip()
            if not address:
                flash("Введите адрес кошелька.", "error")
                snapshot = None
            elif not is_valid_address(address):
                flash("Неверный формат адреса кошелька.", "error")
                snapshot = None
            else:
                snapshot = _balance_snapshot(address)
                flash("Баланс рассчитан по подтверждённой цепочке.", "success")

        return render_template("balance.html", address=address, snapshot=snapshot)


    @app.route("/explorer")
    def explorer_page():
        overview = _build_explorer_overview()
        result_type = request.args.get("result_type", "")
        result_message = request.args.get("result_message", "")
        result_target = request.args.get("result_target", "")
        return render_template(
            "explorer.html",
            **overview,
            search_result={
                "type": result_type,
                "message": result_message,
                "target": result_target,
            }
            if result_message
            else None,
        )


    @app.route("/explorer/search")
    def explorer_search_page():
        query = request.args.get("q", "")
        result = search_explorer(blockchain, query)
        if result.get("redirect_url"):
            return redirect(result["redirect_url"])

        overview = _build_explorer_overview()
        return render_template(
            "explorer.html",
            **overview,
            search_result=result,
            query=query,
        )


    @app.route("/block/<int:index>")
    def block_detail_page(index: int):
        block = find_block_by_index(blockchain, index)
        if not block:
            flash("Блок не найден.", "error")
            return redirect(url_for("explorer_page"))

        previous_block = find_block_by_index(blockchain, index - 1) if index > 0 else None
        next_block = find_block_by_index(blockchain, index + 1)

        return render_template(
            "block_detail.html",
            block=block,
            previous_block=previous_block,
            next_block=next_block,
            difficulty=blockchain.difficulty,
        )


    @app.route("/tx/<tx_id>")
    def transaction_detail_page(tx_id: str):
        location = find_transaction_location(blockchain, tx_id)
        if not location:
            flash("Транзакция не найдена.", "error")
            return redirect(url_for("explorer_page"))

        return render_template(
            "transaction_detail.html",
            tx=location["transaction"],
            status=location["status"],
            block_index=location["block_index"],
        )


    @app.route("/address/<address>")
    def address_detail_page(address: str):
        details = get_address_details(blockchain, address)
        return render_template("address_detail.html", details=details)


    @app.route("/validate")
    def validate_page():
        valid, message = blockchain.is_chain_valid()
        return render_template("validate.html", valid=valid, message=message)


    @app.route("/api/chain")
    def api_chain():
        return jsonify(blockchain.to_dict())


    @app.route("/api/pending")
    def api_pending():
        return jsonify(blockchain.pending_transactions)


    @app.route("/api/balance/<address>")
    def api_balance(address: str):
        if not is_valid_address(address):
            return jsonify({"error": "Invalid address format.", "address": address}), 400
        data = _balance_snapshot(address)
        return jsonify({"address": address, **data})


    @app.route("/api/wallet/<address>/operations")
    def api_wallet_operations(address: str):
        if not is_valid_address(address):
            return jsonify({"error": "Invalid address format.", "address": address}), 400
        return jsonify({"address": address, "operations": get_wallet_operations(address)})


    @app.route("/api/wallet/<address>/stats")
    def api_wallet_stats(address: str):
        if not is_valid_address(address):
            return jsonify({"error": "Invalid address format.", "address": address}), 400
        snapshot = _balance_snapshot(address)
        return jsonify({"address": address, **snapshot, **get_wallet_stats(address)})


    @app.route("/api/block/<int:index>")
    def api_block(index: int):
        block = find_block_by_index(blockchain, index)
        if not block:
            return jsonify({"found": False, "error": "Block not found."}), 404
        return jsonify({"found": True, "block": block.to_dict()})


    @app.route("/api/tx/<tx_id>")
    def api_tx(tx_id: str):
        location = find_transaction_location(blockchain, tx_id)
        if not location:
            return jsonify({"found": False, "error": "Transaction not found."}), 404
        return jsonify(
            {
                "found": True,
                "status": location["status"],
                "block_index": location["block_index"],
                "transaction": location["transaction"],
            }
        )


    @app.route("/api/address/<address>")
    def api_address(address: str):
        if not is_valid_address(address):
            return jsonify(error="Invalid address format.", address=address), 400
        details = get_address_details(blockchain, address)
        return jsonify(details)


    @app.route("/api/explorer/search")
    def api_explorer_search():
        query = request.args.get("q", "")
        return jsonify(search_explorer(blockchain, query))


    @app.route("/api/tokenomics")
    def api_tokenomics():
        is_valid, validation_message = blockchain.is_chain_valid()
        return jsonify(
            {
                "summary": get_tokenomics_summary(blockchain),
                "charts": get_chart_data(blockchain),
                "validation": {
                    "is_valid": is_valid,
                    "message": validation_message,
                },
            }
        )


    @app.route("/api/stats")
    def api_stats():
        return jsonify(get_tokenomics_summary(blockchain))


    @app.route("/api/demo/status")
    def api_demo_status():
        if not app.config["ENABLE_DEMO"]:
            abort(404)
        data = _demo_status_data()
        return jsonify(
            {
                "demo_wallets_exist": data["demo_wallets_exist"],
                "demo_wallet_count": data["demo_wallet_count"],
                "demo_addresses": data["demo_addresses"],
                "blocks": data["stats"]["blocks"],
                "pending_transactions": data["stats"]["pending_transactions"],
            }
        )


    @app.route("/debug/state")
    def debug_state():
        address = request.args.get("address", "").strip()
        return jsonify(_build_debug_payload(address))


    @app.route("/debug")
    def debug_page():
        return render_template("debug.html", payload=_build_debug_payload(), address="", mined_block=None)


    @app.route("/game/upgrade", methods=["POST"])
    @login_required
    def upgrade_rig():
        ok, message = purchase_upgrade(blockchain, get_current_wallet_account())
        if ok:
            save_blockchain(blockchain, str(BLOCKCHAIN_FILE))
        flash(_user_message(message), "success" if ok else "error")
        return redirect(url_for("dashboard_page"))


    @app.route("/api/game")
    @login_required
    def api_game():
        return jsonify(get_game_state(blockchain, get_current_wallet_address()))


    @app.get("/base")
    @login_required
    def base_page():
        address = get_current_wallet_address()
        return render_template("colony.html", farm=district.get_base(blockchain, address), world=colony.view(address),
                               account=get_current_wallet_account(), game=get_game_state(blockchain, address),
                               board=district.rankings(address), visiting=False)

    @app.get("/base/<address>")
    def visit_base(address):
        account = find_wallet_account(address, str(WALLETS_FILE)) if is_valid_address(address) else None
        if account is None:
            abort(404)
        return render_template("colony.html", farm=district.get_base(blockchain, address), world=colony.view(address),
                               account=account, game=get_game_state(blockchain, address),
                               board=district.rankings(address), visiting=True)

    @app.get("/rankings")
    def rankings_page():
        mode = request.args.get("mode", "pvp")
        if mode not in {"week", "base", "xp", "pvp"}:
            abort(400)
        return render_template("rankings.html", board=district.rankings(get_current_wallet_address(), mode))

    @app.get("/api/base")
    @login_required
    def api_base():
        return jsonify(district.get_base(blockchain, get_current_wallet_address()))

    @app.post("/base/action/<action>")
    @login_required
    def base_action(action):
        address = get_current_wallet_address()
        if action == 'produce' and not app.config['ALLOW_LEGACY_PRODUCTION']:
            flash('Производство теперь автоматическое: соедините станции трубами и задайте доли нефти.', 'success')
            return redirect(url_for('base_page', _anchor='flow'))
        actions = {
            "layout": lambda: layout.save(address, request.form.get("layout"), request.form.get("revision")),
            "collect": lambda: district.collect(address),
            "build": lambda: district.start_build(blockchain, get_current_wallet_account(), request.form.get("kind"), request.form.get("target")),
            "finish-build": lambda: district.finish_build(address, request.form.get("job_id")),
            "produce": lambda: district.start_production(address, request.form.get("recipe"), request.form.get("batches")),
            "claim-production": lambda: district.claim_production(address, request.form.get("job_id")),
            "contract": lambda: district.complete_contract(address, request.form.get("contract"), request.form.get("day")),
            "expedition": lambda: district.start_expedition(blockchain, address, request.form.get("sector"), request.form.get("approach")),
            "claim-expedition": lambda: district.claim_expedition(address, request.form.get("job_id")),
        }
        if action not in actions:
            abort(404)
        ok, message = actions[action]()
        if ok and action == "build":
            save_blockchain(blockchain, str(BLOCKCHAIN_FILE))
        flash(message, "success" if ok else "error")
        anchor = {"produce": "production", "claim-production": "production", "contract": "contracts",
                  "expedition": "expeditions", "claim-expedition": "expeditions"}.get(action)
        return redirect(url_for("base_page", _anchor=anchor))

    @app.get("/market")
    @login_required
    def market_page():
        resource = request.args.get("resource", "")
        if resource and resource not in district.RESOURCES:
            abort(400)
        try:
            page = int(request.args.get("page", "1"))
        except ValueError:
            abort(400)
        address = get_current_wallet_address()
        return render_template("market.html", market=market.view(blockchain, address, resource, page), caravan=npc.offers(blockchain,address), address=address)

    @app.post("/market/<action>")
    @login_required
    def market_action(action):
        address = get_current_wallet_address()
        actions = {
            "npc": lambda: npc.trade(blockchain, get_current_wallet_account(), request.form.get('slot'), request.form.get('offer')),
            "sell": lambda: market.create(address, request.form.get("resource"), request.form.get("quantity"), request.form.get("price")),
            "buy": lambda: market.buy(blockchain, get_current_wallet_account(), request.form.get("order_id")),
            "cancel": lambda: market.cancel(address, request.form.get("order_id")),
        }
        if action not in actions:
            abort(404)
        ok, message = actions[action]()
        if ok and action in {"buy","npc"}:
            save_blockchain(blockchain, str(BLOCKCHAIN_FILE))
        flash(message, "success" if ok else "error")
        return redirect(url_for("market_page"))

    @app.post('/api/colony/sync')
    @login_required
    def colony_sync():
        address=get_current_wallet_address()
        colony.touch(address);engine.finish_ready(address)
        return jsonify(farm=district.get_base(blockchain,address),world=colony.view(address))

    @app.post('/colony/action/<action>')
    @login_required
    def colony_action(action):
        address=get_current_wallet_address()
        if action=='collect':ok,message=colony.collect_all(address)
        else:ok,message=colony.action(address,action,request.form)
        colony.touch(address)
        flash(message,'success' if ok else 'error')
        return redirect(url_for('profile_page' if action=='avatar' else 'base_page',_anchor='flow' if action=='allocation' else 'shop' if action in {'buy','builder','expand'} else None))

    @app.get('/profile')
    @login_required
    def profile_page():
        return render_template('profile.html',world=colony.view(get_current_wallet_address()),account=get_current_wallet_account())

    @app.route('/cards',methods=['GET','POST'])
    @login_required
    def cards_page():
        address=get_current_wallet_address()
        if request.method=='POST':
            action=request.form.get('action')
            if action=='chest':
                if request.form.get('payment')=='oil':
                    ok,message=chests.buy(blockchain,get_current_wallet_account(),request.form.get('kind'),request.form.get('request_id'),request.form.get('quote'))
                    if ok:save_blockchain(blockchain,str(BLOCKCHAIN_FILE))
                else:ok,message=arena.open_chest(address,request.form.get('kind'),request.form.get('request_id'))
            elif action=='build':ok,message=card_builds.choose(address,request.form.get('card_id'),request.form.get('branch'),request.form.get('leaf'))
            elif action=='deck':ok,message=arena.deck_action(address,request.form)
            elif action=='upgrade':ok,message=arena.upgrade(address,request.form.get('card_id'))
            else:abort(400)
            flash(message,'success' if ok else 'error');return redirect(url_for('cards_page'))
        return render_template('cards.html',collection=arena.collection(address,blockchain),world=colony.view(address))

    @app.get('/api/cards/orders')
    @login_required
    def card_orders():
        rows=g.db.execute('SELECT id,status FROM chest_orders WHERE address=? ORDER BY id DESC LIMIT 10',(get_current_wallet_address(),)).fetchall()
        return jsonify(orders=[dict(row) for row in rows])

    @app.get('/campaign')
    @login_required
    def campaign_page():
        return render_template('campaign.html',arena=campaign.snapshot(get_current_wallet_address()))

    @app.get('/api/campaign')
    @login_required
    def campaign_state():
        return jsonify(campaign.snapshot(get_current_wallet_address()))

    @app.post('/campaign/<action>')
    @login_required
    def campaign_action(action):
        address=get_current_wallet_address()
        if action=='heartbeat':
            colony.touch(address);return jsonify(ok=True)
        if action not in ('start','play','surrender'):abort(404)
        ok,message=campaign.command(address,action,request.form)
        return jsonify(ok=ok,message=message,state=campaign.snapshot(address)),200 if ok else 409

    @app.get('/arena')
    @login_required
    def arena_page():
        return render_template('arena.html',arena=arena.snapshot(get_current_wallet_address()))

    @app.get('/api/arena')
    @login_required
    def arena_state():
        return jsonify(arena.snapshot(get_current_wallet_address(),request.args.get('match_id')))

    @app.post('/arena/<action>')
    @login_required
    def arena_action(action):
        address=get_current_wallet_address()
        if action=='heartbeat':
            arena.heartbeat(address);colony.touch(address);return jsonify(ok=True)
        if action=='queue':ok,message=arena.queue(address,request.form.get('mode','join'),request.form.get('room',''))
        elif action=='play':ok,message=arena.play(address,request.form.get('match_id'),request.form.get('card_id'),request.form.get('lane'),request.form.get('revision'))
        elif action=='surrender':ok,message=arena.surrender(address,request.form.get('match_id'))
        else:abort(404)
        return jsonify(ok=ok,message=message,state=arena.snapshot(address)),200 if ok else 409

    @app.route("/health")
    def health():
        return jsonify(status="ok", database="sqlite", height=blockchain.get_latest_block().index)


    @app.route("/api/csrf")
    def api_csrf():
        return jsonify(csrf_token=csrf_token())
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
