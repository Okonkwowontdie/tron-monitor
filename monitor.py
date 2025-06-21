import os
import time
import requests
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.keys import PrivateKey
from decimal import Decimal

load_dotenv()
print(f"Loaded TRONGRID_API_KEY: '{TRONGRID_API_KEY}'")


EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER, TRONGRID_API_KEY]):
    print("❌ Missing required environment variables. Exiting.")
    exit(1)

if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("❌ Wallet, vanity, and private keys counts mismatch. Exiting.")
    exit(1)

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
        print("✅ Email sent.")
    except Exception as e:
        print(f"❌ Email sending failed: {e}")

def get_latest_transaction(wallet_address):
    url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&sort=-timestamp"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 401:
            print("❌ Unauthorized: Check TronGrid API key.")
            return None
        if response.status_code == 429:
            print("⚠️ Rate limit hit. Sleeping 60 seconds...")
            time.sleep(60)
            return None
        if response.status_code != 200:
            print(f"⚠️ HTTP {response.status_code} for {wallet_address}")
            return None

        data = response.json()
        txs = data.get("data", [])
        if not txs:
            print(f"ℹ️ No transactions found for {wallet_address}")
            return None

        return txs[0]
    except Exception as e:
        print(f"❌ Error fetching transactions: {e}")
        return None

def send_trx(from_address, priv_key_hex, recipient, amount=Decimal("0.00001")):
    try:
        client = Tron()
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        txn = (
            client.trx.transfer(from_address, recipient, int(amount * 1e6))
            .memo("auto-reward")
            .build()
            .sign(priv_key)
        )
        result = txn.broadcast().wait()
        if result['result']:
            print(f"✅ Sent {amount} TRX from {from_address} to {recipient}. TxID: {result['txid']}")
        else:
            print(f"❌ Failed to send TRX: {result}")
    except Exception as e:
        print(f"❌ Exception in send_trx: {e}")

print("🚀 Starting monitor...\n")

while True:
    try:
        for i, wallet in enumerate(WALLET_ADDRESSES):
            print(f"🔎 Checking wallet {wallet}")
            tx = get_latest_transaction(wallet)
            if not tx:
                print("ℹ️ No transaction data.")
                continue

            tx_id = tx.get("transaction_id")
            contract_data = tx.get("contract_data", {})
            amount = int(contract_data.get("value", 0)) / 1e6
            sender = contract_data.get("from")
            receiver = contract_data.get("to")

            if last_tx_ids.get(wallet) != tx_id:
                last_tx_ids[wallet] = tx_id
                print(f"🔔 New transaction detected! TxID: {tx_id}")

                subject = f"New USDT Transaction on {wallet}"
                body = f"""
New USDT transaction detected:

Wallet: {wallet}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}

https://tronscan.org/#/transaction/{tx_id}
"""
                send_email(subject, body)

                # Reward sender with a tiny amount of TRX from vanity wallet
                if sender and sender != wallet:
                    print(f"💸 Sending reward TRX to sender: {sender}")
                    vanity_addr = VANITY_ADDRESSES[i]
                    vanity_key = VANITY_PRIVATE_KEYS[i]
                    send_trx(vanity_addr, vanity_key, sender)

            else:
                print("ℹ️ No new transaction.")

            time.sleep(2)  # avoid spamming API

    except Exception as e:
        print(f"❌ Error in main loop: {e}")

    print("⏳ Sleeping 30 seconds...\n")
    time.sleep(30)
