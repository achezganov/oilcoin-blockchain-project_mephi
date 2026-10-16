"""Explicit opt-in demonstration, using the same atomic transaction as the web app."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app
from accounts import load_wallet_accounts, save_wallet_accounts
from database import unit_of_work
from demo import DEMO_PASSWORD, create_demo_data
from game import settle_missions
from storage import load_blockchain, save_blockchain

def main():
    if not app.config["ENABLE_DEMO"]:
        raise SystemExit("Set ENABLE_DEMO=true on a disposable educational instance.")
    with app.app_context(), unit_of_work(app.config["DATABASE_PATH"]):
        chain = load_blockchain()
        wallets = load_wallet_accounts()
        ok, message, summary = create_demo_data(chain, wallets)
        if not ok:
            raise SystemExit(message)
        save_wallet_accounts(wallets)
        save_blockchain(chain)
        settle_missions(chain)
    print(message)
    print("Demo password:", DEMO_PASSWORD)
    for label, address in summary["demo_addresses"].items():
        print(label, address)

if __name__ == "__main__":
    main()
