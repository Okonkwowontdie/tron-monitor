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

# Load environment variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("❌ Missing email config.")
    exit(1)
if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("❌ Mismatch in wallet, vanity addresses, or keys.")
    exit(1)

# Use tronstack provider (not Trongrid)
client = Tron(HTTPProvider(endpoint_uri="https://api.tronstack.io"))
last_tx_ids = {}

def is_contract_address(address):
    try:
        info = client.get_account(address)
        return 'contract' in info and info['contract']
    except Exception as e:
        print(f"Error checking if contract: {e}")
        return False

def get_latest_trc20_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/token_trc20/transfers?limit=1&sort=-timestamp&filterTokenValue=1&relatedAddress={wallet_address}&token={USDT_CONTRACT_ADDRESS}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Tronscan API error: {response.status_code}")
            return None
        txs = response.json().get("data", [])
        return txs[0] if txs else None
    except Exception as e:
        print(f"Error fetching TRC20 tx: {e}")
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
        print("📧 Email sent.")
    except Exception as e:
        print("❌ Email error:", e)

def freeze_trx_for_bandwidth(address, private_key_hex, freeze_amount=Decimal("10")):
    try:
        print(f"⚙️ Freezing {freeze_amount} TRX for bandwidth on: {address}")
        priv_key = PrivateKey(bytes.fromhex(private_key_hex))
        txn = (
            client.trx.freeze_balance(
                owner_address=address,
                amount=int(freeze_amount * 1_000_000),
                duration=3,
                resource="BANDWIDTH"
            ).build().sign(priv_key)
        )
        result = txn.broadcast().wait()
        print("✅ Bandwidth frozen. TxID:", result.get("id", "n/a"))
    except Exception as e:
        print("❌ Freeze error:", e)

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            print(f"⚠️ Skipping contract address: {to_address}")
            return

        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)

        if balance < amount:
            print("⚠️ Low TRX balance. Freezing for bandwidth...")
            freeze_trx_for_bandwidth(from_address, priv_key_hex)
            return

        print(f"🚀 Sending {amount} TRX to {to_address}")
        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo(f"Thanks for interacting with {from_address}")
            .build().sign(priv_key)
        )
        result = txn.broadcast().wait()
        print("✅ TRX sent. TxID:", result.get("id", "n/a"))
    except Exception as e:
        print("❌ TRX send error:", e)

# --- Main Loop ---
print("🔍 USDT Monitor started...\n")

while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            print(f"👀 Checking: {my_address}")
            tx = get_latest_trc20_transaction(my_address)

            if not tx:
                print("⏸ No transaction found.")
                continue

            tx_id = tx.get("transaction_id")
            if last_tx_ids.get(my_address) == tx_id:
                print("⏸ Already processed.")
                continue

            last_tx_ids[my_address] = tx_id

            sender = tx.get("from")
            receiver = tx.get("to")
            amount = Decimal(tx.get("value", "0")) / Decimal("1e6")

            # Only respond to inbound USDT transfers
            if receiver != my_address:
                print("⛔ Not an inbound transfer.")
                continue

            if sender in WALLET_ADDRESSES or sender in VANITY_ADDRESSES:
                print("⚠️ Skipping internal sender.")
                continue

            if is_contract_address(sender):
                print("⚠️ Sender is a contract. Skipping.")
                continue

            print(f"✅ USDT received: {amount} from {sender}")

            # Send email alert
            subject = f"USDT Received at {my_address}"
            body = f"""
🔔 USDT Transfer Detected:

To Wallet: {my_address}
Amount: {amount} USDT
From: {sender}
TxID: {tx_id}
View: https://tronscan.org/#/transaction/{tx_id}
"""
            send_email(subject, body)

            # Send reward
            send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], sender)

        print("🕒 Sleeping for 30s...\n")
        time.sleep(30)

    except Exception as e:
        print("❌ Monitoring error:", e)
        time.sleep(30)
