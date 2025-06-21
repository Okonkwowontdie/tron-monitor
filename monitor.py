import requests
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")

def get_latest_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&count=true&limit=1&start=0&address={wallet_address}"
        response = requests.get(url)
        data = response.json()
        transactions = data.get("data", [])
        if not transactions:
            return None
        return transactions[0]
    except Exception as e:
        print(f"Error fetching transaction for {wallet_address}:", e)
        return None

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("✅ Email sent.")
    except Exception as e:
        print("❌ Email sending failed:", e)

last_tx_ids = {}

print("🔍 Monitoring wallets:", WALLET_ADDRESSES)
while True:
    for address in WALLET_ADDRESSES:
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
New transaction detected on TRON (TRC20):

Wallet: {address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}

Check on TRONScan: https://tronscan.org/#/transaction/{tx_id}
"""
                print(body)
                send_email(subject, body)
    time.sleep(30)
