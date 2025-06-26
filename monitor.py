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

load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
FUNDING_PRIVATE_KEY = os.getenv("FUNDING_PRIVATE_KEY")

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

client = Tron(HTTPProvider(endpoint_uri="https://api.trongrid.io"))
funding_account = PrivateKey(bytes.fromhex(FUNDING_PRIVATE_KEY))
last_tx_ids = {}

def is_contract_address(address):
    try:
        account_info = client.get_account(address)
        return 'contract' in account_info and account_info['contract']
    except Exception as e:
        print(f"Error checking contract: {e}")
        return False

def get_latest_transaction(wallet_address):
    try:
        url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"Failed to fetch tx from TronGrid: {response.status_code}")
            return None
        data = response.json()
        txs = data.get("data", [])
        return txs[0] if txs else None
    except Exception as e:
        print(f"TronGrid error: {e}")
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
        print(f"⚠️ {address} balance is low ({balance} TRX). Funding...")
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
            print(f"❌ Failed to fund {address}: {e}")

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            print(f"Abort: {to_address} is a contract.")
            return

        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        if balance < amount:
            print(f"Insufficient TRX. Balance: {balance}")
            return

        txn = (
            client.trx.transfer(from_address, to_address, int(amount * 1_000_000))
            .memo(f"reuse_usdt_address_{from_address}")
            .build().sign(priv_key)
        )
        result = txn.broadcast().wait()
        print(f"TRX sent to {to_address}. TxID: {result.get('id', 'n/a')}")
    except Exception as e:
        print("TRX send error:", e)

print("🚀 TRON monitor started.")

while True:
    try:
        for i, monitored_address in enumerate(WALLET_ADDRESSES):
            print(f"🔍 Checking: {monitored_address}")

            # Ensure vanity address is funded
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
                        print("🛑 Ignoring small USDT tx:", amount)
                        continue

                    interacting_address = receiver if sender == monitored_address else sender
                    if interacting_address in WALLET_ADDRESSES or interacting_address in VANITY_ADDRESSES or interacting_address == USDT_CONTRACT_ADDRESS:
                        print("➡️ Internal or contract address.")
                        continue

                    if is_contract_address(interacting_address):
                        print("🚫 Contract interaction skipped.")
                        continue

                    body = f"""
New USDT transaction:
Wallet: {monitored_address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}
View: https://tronscan.org/#/transaction/{tx_id}
"""
                    send_email(f"New USDT TX for {monitored_address}", body)
                    send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)
            else:
                print("No new tx found.")

            time.sleep(1)

    except Exception as e:
        print("Main loop error:", e)

    print("⏳ Sleeping 30s...\n")
    time.sleep(30)
