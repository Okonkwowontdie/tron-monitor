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
from datetime import datetime, timedelta

# Load .env config
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
FUNDING_PRIVATE_KEY = os.getenv("FUNDING_PRIVATE_KEY")
AVOID_ADDRESSES = set(os.getenv("AVOID_ADDRESSES", "").split(","))

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("Missing email config.")
    exit(1)

if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(VANITY_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Mismatch in address/key counts.")
    exit(1)

client = Tron(HTTPProvider(endpoint_uri="https://api.trongrid.io"))
funding_account = PrivateKey(bytes.fromhex(FUNDING_PRIVATE_KEY))

last_tx_ids = {}
last_reward_time = {}
REWARD_INTERVAL = timedelta(hours=1)

def is_contract_address(address):
    try:
        info = client.get_account(address)
        return 'contract' in info and info['contract']
    except Exception as e:
        print(f"Contract check error: {e}")
        return False

def get_latest_transaction(address):
    try:
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"
        response = requests.get(url, timeout=50)
        if response.status_code != 200:
            print(f"TronGrid API failed for {address}. Status: {response.status_code}")
            return None
        data = response.json()
        txs = data.get("data", [])
        return txs[0] if txs else None
    except Exception as e:
        print(f"Fetch tx error for {address}: {e}")
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
        print("Email error:", e)

def get_trx_balance(address):
    try:
        return client.get_account_balance(address)
    except Exception as e:
        print(f"Balance error: {e}")
        return Decimal("0")

def fund_address_if_needed(address):
    balance = get_trx_balance(address)
    if balance < Decimal("1"):
        print(f"⚠️ {address} balance low ({balance} TRX). Funding...")
        try:
            txn = (
                client.trx.transfer(
                    funding_account.public_key.to_base58check_address(),
                    address,
                    int(Decimal("1.5") * 1_000_000)
                ).memo("auto-fund").build().sign(funding_account)
            )
            result = txn.broadcast().wait()
            print(f"✅ Funded {address}. TxID: {result.get('id')}")
        except Exception as e:
            print(f"❌ Funding failed: {e}")

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            print(f"🚫 Skipping contract address: {to_address}")
            return
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        if balance < amount:
            print(f"❌ Not enough TRX to send from {from_address}. Balance: {balance}")
            return
        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo(f"reward_from_{from_address}")
            .build().sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"✅ Sent {amount} TRX to {to_address}. TxID: {result.get('id', 'n/a')}")
    except Exception as e:
        print("TRX send error:", e)

print("🚀 TRON monitor running...")

if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    send_email("TRON Monitor Active", "Monitoring has started successfully.")

while True:
    try:
        for i, monitored_address in enumerate(WALLET_ADDRESSES):
            print(f"🔎 Checking: {monitored_address}")
            fund_address_if_needed(VANITY_ADDRESSES[i])
            tx = get_latest_transaction(monitored_address)

            if tx:
                tx_id = tx.get("transaction_id")
                if last_tx_ids.get(monitored_address) != tx_id:
                    last_tx_ids[monitored_address] = tx_id

                    sender = tx.get("from")
                    receiver = tx.get("to")
                    amount = int(tx.get("value", "0")) / 1e6

                    if amount < 1:
                        print("💤 Skipping small transaction.")
                        continue

                    interacting_address = receiver if sender == monitored_address else sender
                    interacting_address = interacting_address.strip()

                    if (interacting_address in WALLET_ADDRESSES or
                        interacting_address in VANITY_ADDRESSES or
                        interacting_address == USDT_CONTRACT_ADDRESS or
                        interacting_address in AVOID_ADDRESSES):
                        print("⏩ Ignored address (internal, USDT contract, or avoid list).")
                        continue

                    if is_contract_address(interacting_address):
                        print("⛔ Contract address skipped.")
                        continue

                    body = f"""
New USDT Transaction:
Wallet: {monitored_address}
Amount: {amount:.6f} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}
View: https://tronscan.org/#/transaction/{tx_id}
"""
                    send_email(f"USDT TX on {monitored_address}", body)

                    now = datetime.utcnow()
                    last_time = last_reward_time.get(interacting_address)
                    if not last_time or (now - last_time) > REWARD_INTERVAL:
                        send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)
                        last_reward_time[interacting_address] = now
                    else:
                        wait_min = int((REWARD_INTERVAL - (now - last_time)).total_seconds() / 60)
                        print(f"⏳ {interacting_address} rewarded recently ({wait_min} min ago)")
                else:
                    print("⏸ No new transaction.")
            else:
                print("⛔ No transaction data returned.")

            time.sleep(1)

    except Exception as e:
        print("💥 Error in main loop:", e)

    print("⏲️ Sleeping 30 seconds...\n")
    time.sleep(2)
