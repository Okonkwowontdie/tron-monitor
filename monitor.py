import requests
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.keys import PrivateKey
from decimal import Decimal

# Load .env variables
load_dotenv()

# Load environment variables
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")

# Debug: Print loaded ENV status
print("Loaded ENV:")
print(f"EMAIL_SENDER: {EMAIL_SENDER}")
print(f"EMAIL_RECEIVER: {EMAIL_RECEIVER}")
print(f"WALLET_ADDRESSES: {WALLET_ADDRESSES}")

# Basic validation
if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("Missing one or more email environment variables. Exiting.")
    exit(1)
if not WALLET_ADDRESSES or WALLET_ADDRESSES == ['']:
    print("No wallet addresses provided. Exiting.")
    exit(1)
if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Wallets, vanity addresses, and keys count mismatch. Exiting.")
    exit(1)

# Transaction tracking
last_tx_ids = {}

# Track previously rewarded addresses
rewarded_addresses = set()

# Load rewarded addresses from file
if os.path.exists("rewarded_addresses.txt"):
    with open("rewarded_addresses.txt", "r") as f:
        rewarded_addresses = set(line.strip() for line in f if line.strip())

# Helper: Save rewarded addresses to file
def save_rewarded_addresses():
    with open("rewarded_addresses.txt", "w") as f:
        for addr in rewarded_addresses:
            f.write(addr + "\n")

# Helper: Get latest transaction
def get_latest_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&count=true&limit=1&start=0&address={wallet_address}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"API failed for {wallet_address}. HTTP {response.status_code}")
            return None
        data = response.json()
        transactions = data.get("data", [])
        return transactions[0] if transactions else None
    except Exception as e:
        print(f"Error fetching transaction for {wallet_address}: {e}")
        return None

# Helper: Send email
def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Email sent.\n")
    except Exception as e:
        print("Email sending failed:", e)

# Helper: Send TRX reward with balance check
def send_trx(from_address, priv_key_hex, recipient, amount=Decimal("0.00001")):
    try:
        print(f"Preparing to send {amount} TRX from {from_address} to {recipient}")
        client = Tron()
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        print(f"Current balance of {from_address}: {balance} TRX")

        if balance < amount:
            print(f"Insufficient balance. Required: {amount}, Available: {balance}")
            return

        txn = (
            client.trx.transfer(from_address, recipient, int(amount * Decimal("1000000")))
            .memo("auto-reward")
            .build()
            .sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"Reward sent! Transaction ID: {result['id'] if 'id' in result else result}")
    except Exception as e:
        print("Failed to send TRX:", e)

# Optional: Test email on start
if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    print("Sending test email...")
    send_email("Monitor Started", "Test email from TRON monitor on Render.")

# Main loop
print("Starting wallet monitor loop...\n")

while True:
    try:
        for i, address in enumerate(WALLET_ADDRESSES):
            print(f"Checking {address}...")
            tx = get_latest_transaction(address)
            if tx:
                tx_id = tx.get("hash")
                if last_tx_ids.get(address) != tx_id:
                    last_tx_ids[address] = tx_id
                    amount = int(tx.get("contractData", {}).get("amount", 0)) / 1e6
                    sender = tx.get("ownerAddress")
                    receiver = tx.get("toAddress")

                    subject = f"New USDT Transaction for {address}"
                    body = f"""
New USDT transaction detected:

Wallet: {address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}

View: https://tronscan.org/#/transaction/{tx_id}
"""
                    print("New transaction found. Sending email...")
                    print(body)
                    send_email(subject, body)

                    # Only reward new sender addresses
                    if sender and sender not in rewarded_addresses:
                        vanity_address = VANITY_ADDRESSES[i]
                        vanity_key = VANITY_PRIVATE_KEYS[i]
                        send_trx(vanity_address, vanity_key, sender)
                        rewarded_addresses.add(sender)
                        save_rewarded_addresses()
                    else:
                        print(f"No reward sent. {sender} has already been rewarded.")
                else:
                    print("No new transaction.")
            time.sleep(1)
    except Exception as e:
        print("Error in monitoring loop:", e)

    print("Sleeping 30s...\n")
    time.sleep(30)
