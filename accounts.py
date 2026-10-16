"""Custodial accounts: scrypt passwords and encrypted Ed25519 private keys."""
import base64
import hashlib
import os
from datetime import datetime, timezone
from cryptography.fernet import Fernet
from flask import current_app, has_app_context
from werkzeug.security import check_password_hash, generate_password_hash
from database import unit_of_work
from wallet import generate_wallet_keys

def _cipher():
    secret = current_app.config["SECRET_KEY"] if has_app_context() else os.environ.get("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY is required to encrypt wallet keys")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256((str(secret) + ":oilcoin-wallet-v1").encode()).digest()))

def encrypt_private_key(private_key):
    return _cipher().encrypt(private_key.encode()).decode()

def decrypt_private_key(account):
    return _cipher().decrypt(account["encrypted_private_key"].encode()).decode()

def new_wallet_account(label, password, is_demo=False):
    keys = generate_wallet_keys()
    return {"address": keys["address"], "public_key": keys["public_key"],
        "encrypted_private_key": encrypt_private_key(keys["private_key"]),
        "password_hash": generate_password_hash(password, method="scrypt"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": (label or "").strip()[:80] or "Новый нефтяник", "is_demo": bool(is_demo)}

def load_wallet_accounts(filename=None):
    with unit_of_work(filename, write=False) as conn:
        return {"wallets": [dict(row) for row in conn.execute("SELECT * FROM accounts ORDER BY created_at")]}

def save_wallet_accounts(data, filename=None):
    with unit_of_work(filename) as conn:
        for account in data["wallets"]:
            conn.execute("""INSERT INTO accounts(address,label,password_hash,public_key,encrypted_private_key,created_at,is_demo)
                VALUES (:address,:label,:password_hash,:public_key,:encrypted_private_key,:created_at,:is_demo)
                ON CONFLICT(address) DO NOTHING""", account)

def find_wallet_account(address, filename=None):
    with unit_of_work(filename, write=False) as conn:
        row = conn.execute("SELECT * FROM accounts WHERE address=?", ((address or "").strip(),)).fetchone()
        return dict(row) if row else None

def create_wallet_account(label, password, filename=None, is_demo=False):
    if not 10 <= len(password) <= 256:
        raise ValueError("Пароль должен содержать от 10 до 256 символов.")
    account = new_wallet_account(label, password, is_demo)
    save_wallet_accounts({"wallets": [account]}, filename)
    return account

def verify_wallet_password(address, password, filename=None):
    account = find_wallet_account(address, filename)
    return bool(account and check_password_hash(account["password_hash"], password or ""))
