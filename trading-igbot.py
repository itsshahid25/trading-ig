import time
import logging
from trading_ig import IGService
from trading_ig import config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# -------------------------
# CONFIG - CHANGE THESE
# -------------------------
USERNAME = "itsshahid25"  # CHANGE ME
PASSWORD = "S621541@74i"  # CHANGE ME
API_KEY = "fa51b09392954a22298b67d2dfd765cfeac8fe26"  # CHANGE ME
ACC_TYPE = "DEMO"  # "DEMO" or "LIVE" - use DEMO for testing
EPIC_SEARCH = "Gold"  # symbol to search for (human-friendly)
EPIC = None  # if you know the epic string, put it here to skip search
CURRENCY = "GBP"  # account currency / trade currency
BASE_SIZE = 0.10  # base stake (lots / units depending on instrument) - CHANGE to sensible amount for demo
MAX_LEVEL = 6  # maximum martingale doubling steps (e.g., 6 => up to 2^6 * BASE_SIZE)
MAX_TRADES = 1  # how many concurrent trades the bot allows (simple single-position martingale)
POLL_INTERVAL = 5  # seconds between checks
TAKE_PROFIT_PTS = None  # example: set to a price level if you want to use limit exit (None = no take profit)
STOP_LOSS_PTS = None  # example: set to a price level if you want to use stop loss (None = no stop loss)
# -------------------------

# Basic state
state = {
    "level": 0,  # current martingale level (0 = base)
    "current_size": BASE_SIZE,
    "open_deal_ref": None,
    "open_direction": None,
    "account": None
}


# Helper: create IG session
def create_ig_session():
    ig = IGService(username=USERNAME,
                   password=PASSWORD,
                   api_key=API_KEY)
    ig.create_session()
    logging.info("Logged in to IG (%s)", ACC_TYPE)
    return ig


# Helper: find epic by symbol search (simple)
def find_epic(ig, search_text):
    logging.info("Searching for epic matching '%s' ...", search_text)
    markets = ig.search_markets(search_text)

    # Handle different return types
    if markets is None:
        logging.warning("fetch_markets returned None")
        return None

    # If it's a pandas DataFrame
    if hasattr(markets, "empty"):
        if markets.empty:
            logging.warning("No markets found for search text: %s", search_text)
            return None
        markets = markets.to_dict("records")

    # If it's a list of dicts or strings
    if isinstance(markets, list):
        for m in markets:
            # If it's already a dict
            if isinstance(m, dict):
                name = (m.get("instrumentName") or "").upper()
                epic = m.get("epic")
            else:
                # If it’s a plain string
                name = str(m).upper()
                epic = str(m)

            if search_text.upper() in name or search_text.upper() in epic:
                logging.info("Found epic: %s (%s)", epic, name)
                return epic

        # fallback to first if available
        first = markets[0]
        if isinstance(first, dict):
            logging.info("Fallback pick epic: %s", first.get("epic"))
            return first.get("epic")
        else:
            logging.info("Fallback pick epic string: %s", first)
            return first

    logging.warning("Unrecognized markets format: %s", type(markets))
    return None

def compute_size(level):
    return round(BASE_SIZE * (2**level),
                 8)  # rounding; adjust precision as needed


# Place a market order (open position)
def place_market_open(ig, epic, direction, size, currency=CURRENCY):
    logging.info("Placing %s market order: epic=%s size=%s", direction, epic,
                 size)
    # trading-ig wrapper: create_open_position
    # parameter names follow library: currency_code, direction, epic, expiry, order_type, size, force_open, guaranteed_stop, etc.
    resp = ig.create_open_position(
        currency_code=currency,
        direction=direction.upper(),  # "BUY" or "SELL"
        epic=epic,
        order_type="MARKET",
		expiry="DFB",  # "-" for non-expiry instruments
        force_open="false",
		guaranteed_stop='false',
		size=size,
        level=None,
        limit_distance=None,
		limit_level=None,
        quote_id=None,
        stop_level=None,
        stop_distance=None,
        trailing_stop=None,
        trailing_stop_increment=None
    )
    logging.info("Open position response: %s", resp)
    return resp


# Close position by dealId (if you want to programmatically close)
def close_position(ig, deal_id, direction, epic, size):
    logging.info("Closing deal %s by sending opposite order", deal_id)
    resp = ig.close_open_position_via_market(
        deal_id=deal_id,
        direction=direction,  # should be opposite direction on close
        epic=epic,
        expiry="-",
        size=size)
    logging.info("Close response: %s", resp)
    return resp


# Check the status/profit of a deal (basic)

def get_deal_profit(ig, deal_ref):
    """
    Try to find an open position with the given deal_ref (dealId or dealReference).
    Returns the matching position dict if found, otherwise None.
    Works with DataFrame or list responses from trading-ig.
    """
    positions = ig.fetch_open_positions()

    if positions is None:
        return None

    # If DataFrame
    if hasattr(positions, "empty"):
        if positions.empty:
            return None
        # Convert to list of dicts
        positions = positions.to_dict("records")

    # If list of strings, can't match
    if isinstance(positions, list):
        for pos in positions:
            if isinstance(pos, dict):
                if deal_ref in (pos.get("dealId"), pos.get("dealReference")):
                    return pos
            elif isinstance(pos, str):
                if deal_ref in pos:
                    return {"dealId": pos, "dealReference": pos}
    elif isinstance(positions, dict):
        # some rare versions return dict with 'positions' key
        inner = positions.get("positions")
        if inner:
            for p in inner:
                deal_id = p.get("position", {}).get("dealId")
                if deal_id == deal_ref:
                    return p
    else:
        # unexpected type
        logging.warning("Unknown positions type: %s", type(positions))

    return None

# Example entry logic - replace with your real signal
def entry_signal_dummy(last_trade_was_closed=True):
    """
    Very simple signal:
    - If we have no open trade, return a direction ("BUY" or "SELL") to place an order.
    - Here we alternate BUY/SELL for demo purposes.
    """
    # For a basic deterministic demo: choose BUY when level even, SELL when odd
    # Only return a signal if there is no open trade
    if state["open_deal_ref"] is None:
        return "BUY" if state["level"] % 2 == 0 else "SELL"
    return None


def main_loop():
    ig = create_ig_session()
    global EPIC
    if not EPIC:
        EPIC = find_epic(ig, EPIC_SEARCH)
        if not EPIC:
            logging.error("Could not find epic for %s - exiting", EPIC_SEARCH)
            return

    # read account details once
    accounts = ig.fetch_accounts()

# Handle different return types
    if hasattr(accounts, "iloc"):          # It's a DataFrame
        if accounts.empty:
            logging.error("No accounts returned from IG.")
            return
        account_info = accounts.iloc[0].to_dict()
    elif isinstance(accounts, list):       # Older versions return list of dicts
        if not accounts:
            logging.error("No accounts returned from IG.")
            return
        account_info = accounts[0]
    else:
        logging.error("Unexpected accounts format: %s", type(accounts))
        return

    state["account"] = account_info
    logging.info("Using account: %s", account_info.get("accountName"))

    try:
        while True:
            # 1) If there is no open trade, ask for a signal and open position
            if state["open_deal_ref"] is None:
                sig = entry_signal_dummy()
                if sig:
                    size = compute_size(state["level"])
                    if state["level"] >= MAX_LEVEL:
                        logging.warning(
                            "Reached max martingale level (%s). Resetting to 0.",
                            MAX_LEVEL)
                        state["level"] = 0
                        size = compute_size(state["level"])
                    # Limit check: don't allow runaway size
                    if state["level"] > MAX_LEVEL:
                        logging.error(
                            "Level exceeds MAX_LEVEL; skipping trade")
                    else:
                        resp = place_market_open(ig, EPIC, sig, size)
                        # trading-ig returns a structure; find deal reference if present
                        deal_ref = resp.get("dealReference") or resp.get(
                            "dealId") or resp.get("dealStatus")
                        state["open_deal_ref"] = deal_ref
                        state["open_direction"] = sig
                        state["current_size"] = size
                        logging.info(
                            "Trade opened: deal_ref=%s size=%s direction=%s",
                            deal_ref, size, sig)
                else:
                    logging.debug("No entry signal right now.")
            else:
                # 2) Poll open positions to see if position closed (in demo we simulate by checking open positions)
                open_pos = get_deal_profit(ig, state["open_deal_ref"])
                if open_pos is None:
                    # Trade closed — now we must find if it was a win or loss.
                    # For simplicity: query account activity / deal history to find closed result (wrapper may provide this)
                    # trading-ig: ig.get_account_activity() returns recent deals — search for dealReference
                    activities = ig.get_account_activity(
                        from_date=None
                    )  # wrapper params vary; may return last 30 entries
                    closed_item = None
                    for item in activities:
                        if item.get("dealReference"
                                    ) == state["open_deal_ref"] or item.get(
                                        "dealId") == state["open_deal_ref"]:
                            closed_item = item
                            break
                    if closed_item:
                        profit = closed_item.get("profit") or closed_item.get(
                            "profitAndLoss") or closed_item.get("profitLoss")
                        # numeric convert safe
                        try:
                            pf = float(profit)
                        except Exception:
                            pf = None
                        logging.info("Closed trade found: deal=%s profit=%s",
                                     state["open_deal_ref"], pf)
                        if pf is not None and pf > 0:
                            # win — reset martingale
                            state["level"] = 0
                            state["current_size"] = compute_size(0)
                            logging.info("Trade was a WIN — reset level to 0.")
                        else:
                            # loss or zero — increase level
                            state["level"] += 1
                            state["current_size"] = compute_size(
                                state["level"])
                            logging.info(
                                "Trade was a LOSS or breakeven — bump level to %s (next size=%s)",
                                state["level"], state["current_size"])
                    else:
                        logging.info(
                            "Trade closed but no activity record found; treating as unknown and resetting state."
                        )
                        state["level"] = 0
                        state["current_size"] = compute_size(0)

                    # clear open deal
                    state["open_deal_ref"] = None
                    state["open_direction"] = None

            # safety stop: if level exceeds max, pause/stop loop
            if state["level"] > MAX_LEVEL:
                logging.error(
                    "Exceeded MAX_LEVEL (%s). Stopping bot to avoid large exposure.",
                    MAX_LEVEL)
                break

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Interrupted by user -- exiting.")
    except Exception as e:
        logging.exception("Unhandled exception: %s", e)
    finally:
        try:
            ig.logout()
            logging.info("Logged out.")
        except Exception:
            pass


if __name__ == "__main__":
    main_loop()
