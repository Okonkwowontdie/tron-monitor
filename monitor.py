import requests
import smtplib
import os
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# Load environment variables
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")

# Debug: Print environment variable status
print("📦 Loaded ENV:")
print(f"EMAIL_SENDER: {EMAIL_SENDER}")
print(f"EMAIL_RECEIVER: {EMAIL_RECEIVER}")
print(f"WALLET_ADDRESSES: {WALLET_ADDRESSES}\n")

# Sanity check for required vars
if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
    print("❌ Missing one or more email environment variables. Exiting.")
    exit(1)
if not WALLET_ADDRESSES or WALLET_ADDRESSES == ['']:
    print("❌ No wallet addresses provided. Exiting.")
    exit(1)

def get_latest_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&count=true&limit=1&start=0&address={wallet_address}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ API call failed for {wallet_address}. Status Code: {response.status_code}")
            return None
        data = response.json()
        transactions = data.get("data", [])
        if not transactions:
            print(f"ℹ️ No transactions found for {wallet_address}")
            return None
        return transactions[0]
    except Exception as e:
        print(f"❌ Error fetching transaction for {wallet_address}:", e)
        return None

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    try:
        print("📤 Sending email...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("✅ Email sent.\n")
    except Exception as e:
        print("❌ Email sending failed:", e)

# Optionally send a test email at startup
if os.getenv("SEND_TEST_EMAIL", "false").lower() == "true":
    print("🧪 Sending test email...")
    send_email("✅ Monitor Started", "This is a test email from your TRON monitor script on Render.")
    print("✅ Test email complete.\n")

# Transaction tracking
last_tx_ids = {}

print("🚀 Starting wallet monitoring loop...\n")
while True:
    try:
        for address in WALLET_ADDRESSES:
            print(f"🔍 Checking latest transaction for wallet: {address}")
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
New USDT transaction detected on TRON (TRC20):

Wallet: {address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}

🔗 View on TRONScan:
https://tronscan.org/#/transaction/{tx_id}
"""
                    print("📨 New transaction detected, preparing to send email...")
                    print(body)
                    send_email(subject, body)
                else:
                    print("ℹ️ No new transactions.")
            time.sleep(1)  # Slight delay between API calls
    except Exception as main_loop_error:
        print("❌ Error in main loop:", main_loop_error)

    print("⏳ Sleeping for 30 seconds...\n")
    time.sleep(30)
