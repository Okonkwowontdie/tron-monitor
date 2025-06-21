import requests
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.keys import PrivateKey
from decimal import Decimal

# Load environment variables from .env file
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")

print("Loaded environment variables:")
print(f"EMAIL_SENDER: {EMAIL_SENDER}")
print(f"EMAIL_RECEIVER: {EMAIL_RECEIVER}")
print(f"WALLET_ADDRESSES: {WALLET_ADDRESSES}")
print(f"VANITY_ADDRESSES: {VANITY_ADDRESSES}")

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("❌ Missing email configuration in environment variables. Exiting.")
    exit(1)

if not WALLET_ADDRESSES or WALLET_ADDRESSES == ['']:
    print("❌ No wallet addresses provided. Exiting.")
    exit(1)

if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("❌ Count mismatch between WALLET_ADDRESSES, VANITY_ADDRESSES, and VANITY_PRIVATE_KEYS. Exiting.")
    exit(1)

last_tx_ids = {}

def get_latest_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&count=true&limit=1&start=0&address={wallet_address}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ API error for {wallet_address}: HTTP {response.status_code}")
            return None
        data = response.json()
        transactions = data.get("data", [])
        if not transactions:
            print(f"ℹ️ No transactions found for {wallet_address}")
            return None
        return transactions[0]
    except Exception as e:
        print(f"❌ Error fetching transaction for {wallet_address}: {e}")
        return None

def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER

        print("📤 Sending email...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("✅ Email sent.\n")
    except Exception as e:
        print(f"❌ Email sending failed: {e}")

def send_trx(from_address, priv_key_hex, recipient, amount=Decimal("0.00001")):
    try:
        print(f"🚀 Preparing to send {amount} TRX from {from_address} to {recipient}...")

        client = Tron()
        priv_key = PrivateKey.fromhex(priv_key_hex)

        # Check balance first
        balance = client.get_account_balance(from_address)
        print(f"💰 Vanity address {from_address} balance: {balance} TRX")
        if balance < amount + Decimal("0.001"):  # keep some margin for fees
            print(f"⚠️ Insufficient balance in vanity address {from_address}. Needed: {amount} + fee")
            return None

        txn = (
            client.trx.transfer(from_address, recipient, int(amount * Decimal(1e6)))
            .memo("auto-reward")
            .build()
            .sign(priv_key)
        )

        print("📡 Broadcasting transaction...")
        result = txn.broadcast()
        print(f"📬 Broadcast response: {result}")

        print("⏳ Waiting for transaction confirmation...")
        receipt = txn.wait()
        txid = receipt.get('transaction', {}).get('txID', 'unknown') if isinstance(receipt, dict) else 'unknown'
        print(f"✅ Transaction confirmed! TxID: {txid}")

        return receipt
    except Exception as e:
        print(f"❌ Failed to send TRX: {e}")
        return None

# Optionally send a test email at start
if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    print("🧪 Sending test email...")
    send_email("✅ Monitor Started", "This is a test email from your TRON monitor on Render.")
    print("✅ Test email sent.\n")

print("🚀 Starting wallet monitoring loop...\n")

while True:
    try:
        for i, address in enumerate(WALLET_ADDRESSES):
            print(f"🔍 Checking latest transaction for wallet: {address}")
            tx = get_latest_transaction(address)
            if tx:
                tx_id = tx.get("hash")
                if last_tx_ids.get(address) != tx_id:
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

View transaction: https://tronscan.org/#/transaction/{tx_id}
"""
                    print("📨 New transaction detected, sending email...")
                    send_email(subject, body)

                    # Send reward TRX from vanity address to sender if sender exists
                    vanity_address = VANITY_ADDRESSES[i]
                    vanity_key = VANITY_PRIVATE_KEYS[i]
                    if sender:
                        print(f"💸 Sending reward TRX to sender: {sender}")
                        send_trx(vanity_address, vanity_key, sender)
                    else:
                        print("⚠️ No sender found in transaction, skipping TRX send.")
                else:
                    print("ℹ️ No new transaction.")
            else:
                print("ℹ️ No transactions found or error fetching.")

            time.sleep(1)  # delay between wallet checks
    except Exception as e:
        print(f"❌ Error in monitoring loop: {e}")

    print("⏳ Sleeping 30 seconds...\n")
    time.sleep(30)
