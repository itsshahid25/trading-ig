from trading_ig import IGService
from trading_ig.config import config
import pandas as pd

# -------------------------
# 1. Create IG Service
# -------------------------
ig_service = IGService(
    config.username,
    config.password,
    config.api_key,
    config.acc_type       # "DEMO" or "LIVE"
)

# -------------------------
# 2. Login and create session
# -------------------------
session = ig_service.create_session()
print("Session created:", session)

# -------------------------
# 3. Switch account (optional)
# -------------------------
account_info = ig_service.switch_account(config.acc_number, default_account=False)
print("\nAccount Info:")
print(account_info)

# -------------------------
# 4. Fetch open positions
# -------------------------
open_positions = ig_service.fetch_open_positions()
print("\nOpen Positions:")
print(open_positions)

# -------------------------
# 5. Fetch historical prices
# -------------------------
epic = "CS.D.EURUSD.MINI.IP"   # EURUSD Mini Contract
resolution = "D"               # Timeframe: Daily candles
num_points = 10                # Last 10 candles

response = ig_service.fetch_historical_prices_by_epic_and_num_points(
    epic, resolution, num_points
)

# IG returns bid/ask streams separately:
df_ask = response["prices"]["ask"]
df_bid = response["prices"]["bid"]

print("\nAsk Prices:")
print(df_ask)

print("\nBid Prices:")
print(df_bid)
print("ACC TYPE:", config.acc_type, type(config.acc_type))
