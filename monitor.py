import os
import time
import requests

# Read wallet addresses from environment
WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")

if not WALLET_ADDRESSES or WALLET_ADDRESSES == ['']:
    print("❌ WALLET_ADDRESSES not set or empty. Exiting.")
    exit(1)

# Track last transaction hash per wallet
last_tx_ids = {}

def get_latest_transaction(wallet_address):
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&count=true&limit=1&start=0&address={wallet_address}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ API error for {wallet_address} (HTTP {response.status_code})")
            return None
        data = response.json()
        transactions = data.get("data", [])
        if not transactions:
            return None
        return transactions[0]
    except Exception as e:
        print(f"❌ Error fetching transaction for {wallet_address}: {e}")
        return None

print("🚀 Wallet monitor started.")
print("🧾 Monitoring wallets:", WALLET_ADDRESSES)
print()

while True:
    for address in WALLET_ADDRESSES:
        print(f"🔍 Checking {address}...")
        tx = get_latest_transaction(address)
        if tx:
            tx_id = tx.get("hash")
            if last_tx_ids.get(address) != tx_id:
                last_tx_ids[address] = tx_id
                amount = int(tx.get("contractData", {}).get("amount", 0)) / 1e6
                sender = tx.get("ownerAddress")
                receiver = tx.get("toAddress")

                print(f"""
🔔 New transaction detected!

Wallet: {address}
Amount: {amount} USDT
From: {sender}
To: {receiver}
TxID: {tx_id}
🔗 https://tronscan.org/#/transaction/{tx_id}
""")
            else:
                print("⏸️ No new transaction.")
        else:
            print("ℹ️ No transactions found.")
        time.sleep(1)

    print("⏱️ Sleeping 30s...\n")
    time.sleep(30)
