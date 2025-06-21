import requests
import smtplib
import os
import time
import itertools
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.providers import HTTPProvider
from tronpy.keys import PrivateKey
from decimal import Decimal

# Load .env variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
TRON_API_KEYS = os.getenv("TRON_API_KEYS", "").split(",")

# Validate ENV
if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("❌ Missing email credentials. Exiting.")
    exit(1)
if not WALLET_ADDRESSES or WALLET_ADDRESSES == [""]:
    print("❌ WALLET_ADDRESSES missing. Exiting.")
    exit(1)
if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("❌ Wallet, vanity, or private key counts mismatch. Exiting.")
    exit(1)
if not TRON_API_KEYS or TRON_API_KEYS == [""]:
    print("❌ TRON_API_KEYS missing. Exiting.")
    exit(1)

# API Key Rotator
api_key_cycle = itertools.cycle(TRON_API_KEYS)

def get_tron_client():
    api_key = next(api_key_cycle).strip()
    headers = {
        "TRON-PRO-API-KEY": api_key
    }
    return Tron(provider=HTTPProvider(endpoint_uri="https://api.trongrid.io", headers=headers))

# Store last known txid per wallet
last_tx_ids = {}

def get_latest_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&count=true&limit=1&start=0&address={wallet_address}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ API error for {wallet_address}: {response.status_code}")
            return None
        data = response.json()
        return data.get("data", [None])[0]
    except Exception as e:
        print(f"❌ Error getting tx for {wallet_address}: {e}")
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
        print("✅ Email sent.")
    except Exception as e:
        print("❌ Email send error:", e)

def send_trx(from_address, priv_key_hex, recipient, amount=Decimal("0.00001")):
    try:
        print(f"🔁 Sending {amount} TRX from {from_address} to {recipient}")
        client = get_tron_client()
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)

        if balance < amount:
            print(f"⚠️ Not enough balance. Need {amount}, have {balance}")
            return

        txn = (
            client.trx.transfer(from_address, recipient, int(amount * Decimal("1000000")))
            .memo("auto-reward")
            .build()
            .sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"✅ TRX Sent. TxID: {result.get('id', result)}")
    except Exception as e:
        print("❌ Failed to send TRX:", e)

# Optional test email
if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    print("📧 Sending test email...")
    send_email("✅ TRON Monitor Running", "Your TRON monitor is now active.")

# Monitor loop
print("🚀 Starting monitor...\n")

while True:
    try:
        for i, address in enumerate(WALLET_ADDRESSES):
            print(f"🔍 Checking wallet: {address}")
            tx = get_latest_transaction(address)
            if not tx:
                continue

            tx_id = tx.get("hash")
            if last_tx_ids.get(address) == tx_id:
                print("ℹ️ No new tx.")
                continue

            # Mark new tx
            last_tx_ids[address] = tx_id

            amount = int(tx.get("contractData", {}).get("amount", 0)) / 1e6
            sender = tx.get("ownerAddress")
            receiver = tx.get("toAddress")

            subject = f"🔔 New USDT Transaction for {address}"
            body = f"""
New USDT transaction detected:

Wallet: {address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}

🔗 https://tronscan.org/#/transaction/{tx_id}
"""

            send_email(subject, body)

            # Send reward
            if sender:
                vanity_address = VANITY_ADDRESSES[i]
                vanity_key = VANITY_PRIVATE_KEYS[i]
                send_trx(vanity_address, vanity_key, sender)

            time.sleep(2)  # spacing between wallets

    except Exception as e:
        print("❌ Monitor loop error:", e)

    print("⏳ Waiting 60s...\n")
    time.sleep(60)
