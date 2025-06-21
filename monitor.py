import requests
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.keys import PrivateKey
from decimal import Decimal

load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER, TRONGRID_API_KEY]):
    print("❌ Missing required env vars. Exiting.")
    exit(1)
if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("❌ Wallets, vanity addresses, and private keys count mismatch. Exiting.")
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
        print("❌ Email sending failed:", e)

def get_latest_transaction(wallet_address):
    try:
        url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&sort=-timestamp"
        headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 429:
            print("⚠️ Rate limit hit! Sleeping 60s...")
            time.sleep(60)
            return None
        if response.status_code != 200:
            print(f"API call failed with status {response.status_code}")
            return None
        data = response.json()
        txs = data.get("data", [])
        if not txs:
            return None
        return txs[0]
    except Exception as e:
        print(f"❌ Error fetching transaction for {wallet_address}: {e}")
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
        print(f"✅ Sent {amount} TRX to {recipient} from {from_address}. TxID: {result}")
    except Exception as e:
        print("❌ Failed to send TRX:", e)

print("🚀 Starting monitor loop...")

while True:
    try:
        for i, address in enumerate(WALLET_ADDRESSES):
            print(f"🔍 Checking wallet: {address}")
            tx = get_latest_transaction(address)
            if tx:
                tx_id = tx.get("transaction_id")
                contract_data = tx.get("contract_data", {})
                amount = int(contract_data.get("value", 0)) / 1e6
                sender = contract_data.get("from")
                receiver = contract_data.get("to")
                if last_tx_ids.get(address) != tx_id:
                    last_tx_ids[address] = tx_id

                    subject = f"🔔 New USDT Transaction for {address}"
                    body = f"""
New USDT transaction detected:

Wallet: {address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}

https://tronscan.org/#/transaction/{tx_id}
"""
                    print("📨 Sending email alert...")
                    send_email(subject, body)

                    vanity_address = VANITY_ADDRESSES[i]
                    vanity_key = VANITY_PRIVATE_KEYS[i]
                    if sender:
                        send_trx(vanity_address, vanity_key, sender)
                else:
                    print("ℹ️ No new transactions.")
            time.sleep(1)  # avoid spamming requests
    except Exception as e:
        print(f"❌ Error in main loop: {e}")
    print("⏳ Sleeping 30 seconds...\n")
    time.sleep(30)
