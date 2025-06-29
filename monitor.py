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

# Load env vars
load_dotenv()
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
TRONGRID_API_KEYS = os.getenv("TRONGRID_API_KEY", "").split(",")
FUNDING_PRIVATE_KEY = os.getenv("FUNDING_PRIVATE_KEY")

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
SKIP_CONTRACT_ADDRESSES = [
    USDT_CONTRACT_ADDRESS,
    "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",  # USDC
    "TXpw8TnQoAA6ZySoj53zJTZonKGr2DYZNA",  # WBTC
]
SKIP_WALLET_ADDRESSES = set([
    *WALLET_ADDRESSES,
    *VANITY_ADDRESSES,
    "TU4vEruvZwLLkSfV9bNw12EJTPvNr7Pvaa",
])

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER, FUNDING_PRIVATE_KEY]):
    print("Missing required config")
    exit(1)

if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(VANITY_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Wallets, vanity addresses or keys count mismatch")
    exit(1)

trongrid_key_cycle = itertools.cycle(TRONGRID_API_KEYS)
client = Tron(HTTPProvider(endpoint_uri="https://api.trongrid.io"))
last_tx_ids = {}

def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Email sent.")
    except Exception as e:
        print("Email error:", e)

def is_contract_address(address):
    try:
        info = client.get_account(address)
        return 'contract' in info and info['contract']
    except:
        return False

def has_public_name(address):
    try:
        headers = {
            "TRON-PRO-API-KEY": next(trongrid_key_cycle),
            "accept": "application/json"
        }
        url = f"https://api.trongrid.io/v1/accounts/{address}"
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json().get("data", [])
        return bool(data and data[0].get("name"))
    except:
        return False

def get_latest_trc20_transaction(address):
    try:
        headers = {
            "TRON-PRO-API-KEY": next(trongrid_key_cycle),
            "accept": "application/json"
        }
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json().get("data", [])
        if not data:
            return None
        tx = data[0]
        if tx.get("token_info", {}).get("address") not in SKIP_CONTRACT_ADDRESSES:
            return None
        return {
            "transaction_id": tx.get("transaction_id"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": tx.get("value")
        }
    except Exception as e:
        print("TRC20 fetch error:", e)
        return None

def get_trx_usd_price():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd", timeout=10)
        return Decimal(r.json().get("tron", {}).get("usd", 0))
    except:
        return Decimal("0")

def fund_vanity_wallet_if_low(i):
    addr = VANITY_ADDRESSES[i]
    balance = client.get_account_balance(addr)
    print(f"Vanity {addr} balance: {balance}")
    if balance < 1:
        price = get_trx_usd_price()
        if not price:
            return
        amount = (Decimal("3") / price).quantize(Decimal("0.000001"))
        send_trx_from_funding_wallet(addr, amount)

def send_trx_from_funding_wallet(to, amount):
    try:
        priv = PrivateKey(bytes.fromhex(FUNDING_PRIVATE_KEY))
        from_addr = priv.public_key.to_base58check_address()
        balance = client.get_account_balance(from_addr)
        if balance < amount:
            print("Funding wallet has low balance")
            return
        tx = client.trx.transfer(from_addr, to, int(amount * 1_000_000)).memo("fund").build().sign(priv)
        res = tx.broadcast().wait()
        print("Funding TX sent:", res.get("id"))
    except Exception as e:
        print("Funding send error:", e)

def send_trx(from_addr, priv_hex, to, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to):
            print("Recipient is a contract address. Skipping.")
            return
        priv = PrivateKey(bytes.fromhex(priv_hex))
        tx = client.trx.transfer(from_addr, to, int(amount * 1_000_000)).memo("reward").build().sign(priv)
        res = tx.broadcast().wait()
        print("Sent TRX. TxID:", res.get("id"))
    except Exception as e:
        print("TRX send failed:", e)

print("Monitoring started.")

if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    send_email("Monitor Active", "TRON wallet monitor is running.")

while True:
    try:
        for i, addr in enumerate(WALLET_ADDRESSES):
            fund_vanity_wallet_if_low(i)
            tx = get_latest_trc20_transaction(addr)
            if not tx:
                print(f"No TRC20 tx for {addr}")
                time.sleep(1)
                continue

            txid = tx.get("transaction_id")
            if last_tx_ids.get(addr) == txid:
                print("No new tx.")
                continue

            last_tx_ids[addr] = txid
            sender, receiver = tx["from"], tx["to"]
            amount = int(tx["value"]) / 1e6
            if amount < 1:
                print("Skip tx < 1 USDT")
                continue

            other_addr = sender if receiver == addr else receiver
            if other_addr in SKIP_WALLET_ADDRESSES or is_contract_address(other_addr) or has_public_name(other_addr):
                print("Ineligible address.")
                continue

            body = f"""
New USDT TRC20 tx:
Wallet: {addr}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {txid}
View: https://tronscan.org/#/transaction/{txid}
"""
            send_email(f"V {addr}", body)
            send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], other_addr)

            time.sleep(1.5)  # respect rate limit
    except Exception as e:
        print("Loop error:", e)

    print("Sleeping 60s...\n")
    time.sleep(1)
