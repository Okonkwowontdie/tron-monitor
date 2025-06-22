import requests
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.providers import HTTPProvider
from tronpy.keys import PrivateKey
from decimal import Decimal

# Load environment variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")

# Initialize Tron client with API key
client = Tron(provider=HTTPProvider(api_key=TRONGRID_API_KEY))

# Validate environment setup
if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("Missing email configuration. Exiting.")
    exit(1)
if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Mismatch in number of wallet addresses, vanity addresses, or private keys.")
    exit(1)

# Track last seen transactions
last_tx_ids = {}

# Get latest transaction
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

# Send email notification
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

# Send TRX reward
def send_trx(from_address, priv_key_hex, recipient, amount=Decimal("0.00001")):
    try:
        print(f"Sending {amount} TRX from {from_address} to {recipient}")
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        print(f"Balance of {from_address}: {balance} TRX")

        if balance < amount:
            print("Insufficient balance to send reward.")
            return

        txn = (
            client.trx.transfer(from_address, recipient, int(amount * Decimal("1000000")))
            .memo("auto-reward")
            .build()
            .sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"Reward sent! TxID: {result.get('id', 'unknown')}")
    except Exception as e:
        print("Failed to send TRX:", e)

# Optional test email
if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    print("Sending test email...")
    send_email("Monitor Started", "TRON monitoring is active.")

# Main monitoring loop
print("Starting TRON wallet monitor...\n")

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
                    print("New transaction detected. Sending email...")
                    print(body)
                    send_email(subject, body)

                    # Determine which address is the external interacting one
                    interacting_address = sender if sender != address else receiver

                    # Only avoid sending to self
                    if interacting_address and interacting_address not in WALLET_ADDRESSES and interacting_address not in VANITY_ADDRESSES:
                        vanity_address = VANITY_ADDRESSES[i]
                        vanity_key = VANITY_PRIVATE_KEYS[i]
                        send_trx(vanity_address, vanity_key, interacting_address)
                    else:
                        print(f"No reward sent. {interacting_address} is a system wallet.")
                else:
                    print("No new transaction.")
            time.sleep(1)
    except Exception as e:
        print("Monitoring error:", e)

    print("Sleeping 30 seconds...\n")
    time.sleep(30)
