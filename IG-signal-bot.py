#!/usr/bin/env python3
"""
Grid / averaging bot for IG using trading_ig wrapper.

Features added:
- BASE_DIRECTION: "BUY", "SELL" or "BOTH"
- Only opens one direction at a time (ignores opposite signals while positions exist)
- Opens additional same-direction market orders when price moves away from the
  current average entry by GRID_DISTANCE
- Limits total concurrent same-direction orders to MAX_TRADES
- Computes weighted average price and combined unrealised profit across positions
- Closes all same-direction positions when combined profit target is reached
- Uses exponential size scaling (same as your compute_size) or constant size
- Defensive API handling for common trading_ig method names
"""

import time
import logging
from trading_ig import IGService
from trading_ig.config import config # if you use config; otherwise ignore
import json

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")


EPIC_SEARCH = "Germany 40"  # symbol to search for (human-friendly)
EPIC = None  # if you know the epic string, put it here to skip search
CURRENCY = "GBP"  # account currency / trade currency

# Grid / sizing
BASE_SIZE = 0.05  # base stake (units the IG API expects)
USE_MARTINGALE = True  # if True, next lots = BASE_SIZE * (2**level); else always BASE_SIZE
LOT_MULTIPLIER = 1.1  # multiplier for martingale sizing (if used)
MAX_LEVEL = 10  # max doubling level (ignored if USE_MARTINGALE False)
MAX_TRADES = 10 # max concurrent same-direction trades allowed
GRID_DISTANCE = 200.0  # price distance (in instrument price units, e.g., points) to open next trade
POLL_INTERVAL = 10  # seconds between main loop polls

# Direction controls: "BUY", "SELL", "BOTH"
BASE_DIRECTION = "BOTH"

# Close conditions
CLOSE_ON_COMBINED_PROFIT = 2  # close all same-direction trades when combined unrealised profit (currency) >= this (None to disable)
# Optionally you can use point-based target: CLOSE_ON_COMBINED_PROFIT_PTS = 2.0  # points from avg price to close
CLOSE_ON_COMBINED_PROFIT_PTS = None

# Order extras (adapt to your wrapper / instrument)
TAKE_PROFIT_PTS = None
STOP_LOSS_PTS = None

# Safety
MAX_EXPOSURE_PER_ACCOUNT = None  # optional (currency), not implemented automatically
# -------------------------

# State
state = {
    "level": 0,  # martingale level for next new standalone trade (if you want to preserve across cycles)
    "open_direction": None,  # "BUY" or "SELL" while there are open positions
    "epic": EPIC
}


def create_ig_session():
    ig = IGService(
    config.username,
    config.password,
    config.api_key,
    config.acc_type       # "DEMO" or "LIVE"
   )
    ig.create_session()
    logging.info("Logged in to IG (%s)", config.acc_type)
    return ig


def find_epic(ig, search_text):
    logging.info("Searching for epic matching '%s' ...", search_text)
    try:
        markets = ig.search_markets(search_text)
    except Exception as e:
        logging.exception("search_markets failed: %s", e)
        return None

    if markets is None:
        logging.warning("fetch_markets returned None")
        return None

    if hasattr(markets, "empty"):  # DataFrame-like
        if markets.empty:
            logging.warning("No markets found for search text: %s", search_text)
            return None
        markets = markets.to_dict("records")

    if isinstance(markets, list):
        for m in markets:
            if isinstance(m, dict):
                name = (m.get("instrumentName") or "").upper()
                epic = m.get("epic")
            else:
                name = str(m).upper()
                epic = str(m)
            if search_text.upper() in name or (isinstance(epic, str) and search_text.upper() in epic.upper()):
                logging.info("Found epic: %s (%s)", epic, name)
                return epic
        # fallback
        first = markets[0]
        if isinstance(first, dict):
            return first.get("epic")
        else:
            return first

    logging.warning("Unrecognized markets format: %s", type(markets))
    return None


def compute_size(level):
    if USE_MARTINGALE:
        return round(BASE_SIZE * (LOT_MULTIPLIER ** level), 2)
    else:
        return BASE_SIZE


def place_market_open(ig, epic, direction, size, currency=CURRENCY):
    logging.info("Placing MARKET open: %s %s units on %s", direction, size, epic)
    try:
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
        logging.debug("Open position response: %s", resp)
        return resp
    except Exception as e:
        logging.exception("Failed to place market open: %s", e)
        return None


def close_position(ig, position):
    try:
        deal_id = position.get("dealId")
        direction = position.get("direction")
        size = float(position.get("size", 0))
        epic = position.get("epic")
        level = float(position.get("level") or position.get("openLevel"))
        close_dir = "SELL" if direction.upper() == "BUY" else "BUY"
        order_type = "MARKET"
        expiry = "DFB"

        if not all([deal_id, direction, size, epic, level]):
            logging.warning("Missing required data for closing position: %s", position)
            return
        
        # Fetch quote_id before closing
        market = ig.fetch_market_by_epic(epic)
        quote_id = market.get("snapshot", {}).get("quoteId") or market.get("quoteId")
        if not quote_id:
            logging.warning("Could not get quote_id for epic %s, skipping close", epic)
            return

        resp = ig.close_open_position(
            deal_id, close_dir, size, epic, expiry, level, order_type, quote_id
        )
        logging.info("Closed deal %s via market (%s, size=%s)", deal_id, close_dir, size)
        return resp

    except Exception as e:
        logging.error("Failed to close position %s: %s", position.get("dealId"), e)
        return None



def fetch_open_positions(ig):
    """Return open positions as a list of dicts (defensive for DataFrame/list responses)."""
    positions = ig.fetch_open_positions()
    if positions is None:
        return []
    if hasattr(positions, "empty"):
        if positions.empty:
            return []
        return positions.to_dict("records")
    if isinstance(positions, list):
        return positions
    # sometimes dict wrapper
    if isinstance(positions, dict):
        return positions.get("positions", [])
    logging.warning("Unknown positions type: %s", type(positions))
    return []


def get_positions_for_epic_and_direction(ig, epic, direction):
    """Return list of position dicts matching the epic and direction."""
    direction = direction.upper()
    allpos = fetch_open_positions(ig)
    res = []
    for p in allpos:
        # different wrappers return nested structures; try common keys
        try:
            pos = p.get("position") if isinstance(p, dict) and p.get("position") else p
        except Exception:
            pos = p
        # typical fields: 'instrumentName', 'epic', 'direction', 'size', 'dealId', 'level', 'openLevel', 'profit'
        epic_in_pos = pos.get("epic") or pos.get("instrument") or pos.get("instrumentName") or pos.get("marketName")
        pos_direction = (pos.get("direction") or "").upper()
        if epic_in_pos and isinstance(epic_in_pos, str) and epic_in_pos.upper().find(str(epic).upper()) >= 0:
            if pos_direction == direction:
                res.append(pos)
        else:
            # fallback: some wrappers include 'position' with nested 'instrument'
            # try more robust check
            instrument = pos.get("instrument")
            if instrument and isinstance(instrument, dict):
                if instrument.get("epic") == epic and pos_direction == direction:
                    res.append(pos)
    return res


def get_market_price(ig, epic):
    """Try multiple wrapper method names to obtain the current market price.
    Returns mid price (average of bid/offer) when available, else last price.
    """
    try:
        # common method
        market = None
        for fn in ("fetch_market_by_epic", "fetch_market", "fetch_prices", "fetch_market_by_epic"):
            if hasattr(ig, fn):
                try:
                    market = getattr(ig, fn)(epic)
                    break
                except Exception:
                    continue
        if market is None:
            # try ig.fetch_markets and find epic
            if hasattr(ig, "fetch_markets") or hasattr(ig, "search_markets"):
                markets = ig.search_markets(epic)
                if hasattr(markets, "empty"):
                    markets = markets.to_dict("records")
                if isinstance(markets, list) and markets:
                    market = markets[0]
        if market is None:
            logging.warning("Could not fetch market for epic %s with available methods", epic)
            return None

        # market might be a dict with 'snapshot' or 'bid', 'offer'
        bid = None
        offer = None
        last = None
        if isinstance(market, dict):
            # nested snapshot
            snap = market.get("snapshot") or market.get("market") or market
            if isinstance(snap, dict):
                bid = snap.get("bid") or snap.get("offer") and None
                # Actually some wrappers use 'bid' and 'offer' keys
                bid = snap.get("bid") or snap.get("bestBid")
                offer = snap.get("offer") or snap.get("bestOffer")
                last = snap.get("last") or snap.get("mid")
            # fallback direct keys
            if bid is None:
                bid = market.get("bid")
            if offer is None:
                offer = market.get("offer")
            if last is None:
                last = market.get("last")
        # try numeric conversion
        try:
            bidf = float(bid) if bid is not None else None
        except Exception:
            bidf = None
        try:
            offf = float(offer) if offer is not None else None
        except Exception:
            offf = None
        try:
            lastf = float(last) if last is not None else None
        except Exception:
            lastf = None

        if bidf is not None and offf is not None:
            return (bidf + offf) / 2.0
        if lastf is not None:
            return lastf

    except Exception as e:
        logging.exception("get_market_price failed: %s", e)
    return None

def compute_profit(position):
    direction = position["direction"]
    size = float(position["size"])
    open_price = float(position["level"])  # entry
    bid = float(position["bid"])
    offer = float(position["offer"])
    
    if direction == "BUY":
        close_price = bid
        profit = (close_price - open_price) * size
    else:  # SELL
        close_price = offer
        profit = (open_price - close_price) * size

    return profit

def combined_positions_summary(positions):
    total_size = 0.0
    weighted_sum = 0.0
    combined_profit = 0.0
    found_prices = 0

    for p in positions:
        # size
        size = (
            p.get("size")
            or p.get("positionSize")
            or p.get("position", {}).get("size")
        )

        # entry price
        open_level = (
            p.get("openLevel")
            or p.get("level")
            or p.get("position", {}).get("openLevel")
        )

        # unrealised profit (IG uses MANY names)
        profit = (
            p.get("unrealisedProfit")
            or p.get("profitAndLoss")
            or p.get("pnl")
            or p.get("runningPnl")
            or p.get("profit")
            or p.get("position", {}).get("profit")
            or 0
        )
        
        try: s = float(size)
        except: s = 0.0

        try: ol = float(open_level) if open_level is not None else None
        except: ol = None

        try: pf = float(profit)
        except: pf = 0.0

        total_size += s
        #combined_profit += pf
        profit = compute_profit(p)
        combined_profit += profit

        if ol is not None and s > 0:
            weighted_sum += ol * s
            found_prices += s
            avg_price = (weighted_sum / found_prices) if found_prices > 0 else None
        #print("DEBUG POSITION:", json.dumps(p, indent=4))
    return {
        "total_size": total_size,
        "avg_price": avg_price,
        "combined_unrealised_profit": combined_profit
    }


def open_initial_if_signal(ig, epic):
    """Open initial trade when there's no position & an entry signal exists"""
    # Your previous entry signal dummy behaviour: alternate buy/sell for demo.
    # Now respect BASE_DIRECTION setting.
    # For production replace this with real signal logic (indicator/external).
    if BASE_DIRECTION.upper() == "BOTH":
        # demo: choose BUY if level even else SELL
        sig = "BUY" if state["level"] % 2 == 0 else "SELL"
    else:
        sig = BASE_DIRECTION.upper()

    size = compute_size(state["level"])
    resp = place_market_open(ig, epic, sig, size)
    if resp:
        deal_ref = resp.get("dealReference") or resp.get("dealId") or resp.get("dealStatus")
        state["open_direction"] = sig
        logging.info("Initial trade opened: %s %s (deal_ref=%s)", sig, size, deal_ref)
        return resp
    return None


def close_all_same_direction(ig, epic,  direction):
    """
    Closes all positions for this EPIC in the given direction.
    Works with list returned by fetch_open_positions().
    """
    try:
        positions = fetch_open_positions(ig)
        if not positions:
            logging.info("No open positions at all.")
            return

        epic_positions = [p for p in positions if p.get("epic") == epic and p.get("direction") == direction]
        if not epic_positions:
            logging.info(f"No open {direction} positions for {epic}")
            return

        logging.info(f"Found {len(epic_positions)} positions to close.")
        # Opposite order required to close
        opposite = "SELL" if direction == "BUY" else "BUY"

        for pos in epic_positions:
            deal_id = pos.get("dealId")
            size = pos.get("size", 0)
            level = pos.get("level", 0)
            expiry = pos.get("expiry")
            
            
            logging.info(f"Closing deal {deal_id} size={size} opposite={opposite}")

            try:
                resp = ig.close_open_position(
                    deal_id=deal_id,
                    direction=opposite,
                    epic=None,
                    expiry=None,
                    level=None,
                    order_type="MARKET",
                    quote_id=None,
                    size=size
                    
                )
                logging.info(f"Close response: {resp}")
            except Exception as e:
                logging.error(f"Failed to close {deal_id}: {e}")

    except Exception as e:
        logging.error("Error inside close_all_same_direction()")
        logging.exception(e)

def maybe_open_additional(ig, epic):
    """If there are existing positions in state['open_direction'], check grid distance and open new one if needed."""
    direction = state.get("open_direction")
    if not direction:
        return

    positions = get_positions_for_epic_and_direction(ig, epic, direction)
    # If no found positions (maybe wrapper didn't match epic), reset state
    if not positions:
        logging.info("State says open_direction=%s but no positions found. Resetting state.", direction)
        state["open_direction"] = None
        state["level"] = 0
        return

    # Limit check
    if len(positions) >= MAX_TRADES:
        logging.debug("Already at or above MAX_TRADES (%s). Not opening more.", MAX_TRADES)
        return

    summary = combined_positions_summary(positions)
    avg_price = summary["avg_price"]
    avg_price = round(avg_price, 2) if avg_price is not None else None
    total_size = summary["total_size"]
    combined_profit = summary["combined_unrealised_profit"]
    logging.info("Positions summary: count=%s total_size=%s avg_price=%s combined_profit=%s",
                 len(positions), total_size, avg_price, combined_profit)

    # Get current price
    cur_price = get_market_price(ig, epic)
    if cur_price is None:
        logging.warning("Could not get market price; skipping additional open check.")
        return

    logging.debug("Current market price: %s", cur_price)

    # If avg_price not available (we couldn't parse open prices), approximate using last opened price
    # try to find openLevel or open price from latest position
    if avg_price is None:
        # try to use openLevel or level from first position
        for p in positions:
            open_level = p.get("openLevel") or p.get("level") or p.get("position", {}).get("openLevel")
            if open_level is not None:
                try:
                    avg_price = float(open_level)
                    break
                except Exception:
                    continue

    if avg_price is None:
        logging.warning("Could not determine average entry price. Skipping grid open.")
        return

    # For BUY: open new buy when price has dropped by GRID_DISTANCE or more from avg_price
    # For SELL: open new sell when price has risen by GRID_DISTANCE or more from avg_price
    need_open = False
    if direction.upper() == "BUY":
        if (avg_price - cur_price) >= GRID_DISTANCE:
            need_open = True
    else:  # SELL
        if (cur_price - avg_price) >= GRID_DISTANCE:
            need_open = True

    if need_open:
        # determine next size
        # Use level as number of additional opens so far (we'll set it from number of positions)
        next_level = len(positions)  # 0-based: first additional will be level 1 -> compute_size handles
        if next_level > MAX_LEVEL:
            logging.warning("Next level %s exceeds MAX_LEVEL %s - will not open", next_level, MAX_LEVEL)
            return
        size = compute_size(next_level)
        resp = place_market_open(ig, epic, direction, size)
        if resp:
            state["level"] = next_level  # keep track
            logging.info("Opened additional %s at size %s (level %s)", direction, size, next_level)
def get_market_expiry(ig, epic):
    """
    Returns the correct expiry for a given epic.
    Empty string for DFB/TODAY products.
    """
    try:
        m = ig.fetch_market_by_epic(epic)
        expiry = m["instrument"]["expiry"]
        if expiry is None or expiry in ("DFB", "TODAY", "", "-"):
            return ""
        return expiry
    except Exception as e:
        print(f"⚠ Failed to get expiry for {epic}: {e}")
        return ""


def maybe_close_on_combined_profit(ig, epic, profit_target=CLOSE_ON_COMBINED_PROFIT):
    """
    Checks combined profit for all open positions of a given epic.
    If combined profit >= profit_target, closes all positions in that direction.

    Parameters:
        ig            : IGService instance
        epic          : str, market epic to check
        profit_target : float, profit threshold to close positions
    """
    try:
        # 1. Fetch all open positions
        positions_resp = ig.fetch_open_positions()

        # Handle both list and DataFrame
        if positions_resp is None or (hasattr(positions_resp, "empty") and positions_resp.empty) or len(positions_resp) == 0:
            print("INFO: No open positions found at all.")
            return

        # Filter positions for this epic
        epic_positions = []
        if isinstance(positions_resp, list):
            epic_positions = [p for p in positions_resp if p.get("epic") == epic]
        else:  # assume DataFrame
            epic_positions = positions_resp[positions_resp["epic"] == epic].to_dict("records")

        if not epic_positions:
            print(f"INFO: No open positions found for epic {epic}.")
            return

        # 2. Auto detect direction (BUY or SELL)
        direction = epic_positions[0].get("direction", "BUY").upper()

        # 3. Calculate combined profit
       # combined_profit = sum(float(p.get("netChange", 0)) * float(p.get("size", 0))
                             # for p in epic_positions)
        
        combined_profit = sum(compute_profit(p) for p in epic_positions)
        
        combined_profit = round(combined_profit, 2)

        total_size = sum(float(p.get("size", 0)) for p in epic_positions)
        avg_price = sum(float(p.get("level", 0)) * float(p.get("size", 0))  for p in epic_positions) / total_size

        print(f"INFO: Combined profit={combined_profit} (avg_price={avg_price})")

        # 4. Check if combined profit target is reached
        if combined_profit >= profit_target:
            print(f"INFO: Combined profit target reached (>= {profit_target}). Closing all positions for {direction}.")
            close_all_same_direction(ig, epic, direction)

    except Exception as e:
        print(f"ERROR: Error checking CLOSE_ON_COMBINED_PROFIT: {e}")



def main_loop():
    ig = create_ig_session()
    global EPIC
    if not EPIC:
        EPIC = find_epic(ig, EPIC_SEARCH)
        if not EPIC:
            logging.error("Could not find epic for %s - exiting", EPIC_SEARCH)
            return
    state["epic"] = EPIC
    logging.info("Trading epic set to: %s", EPIC)

    # read account once (optional)
    accounts = ig.fetch_accounts()
    try:
        # normalize accounts structure
        if hasattr(accounts, "iloc"):
            account_info = accounts.iloc[0].to_dict()
        elif isinstance(accounts, list):
            account_info = accounts[0]
        elif isinstance(accounts, dict):
            account_info = list(accounts.values())[0]
        else:
            account_info = {}
        logging.info("Using account: %s", account_info.get("accountName"))
    except Exception:
        logging.warning("Could not parse account info.")

    try:
        while True:
            epic = state["epic"]
            # 1) If no open_direction/state, we may open initial trade
            if state.get("open_direction") is None:
                # See if there are existing positions in the market from prior runs
                # If the bot restarts and positions already exist, set open_direction accordingly
                # We check both BUY and SELL positions for the epic
                buys = get_positions_for_epic_and_direction(ig, epic, "BUY")
                sells = get_positions_for_epic_and_direction(ig, epic, "SELL")
                if buys and not sells:
                    state["open_direction"] = "BUY"
                    logging.info("Detected existing BUY positions on start - adopting open_direction=BUY")
                elif sells and not buys:
                    state["open_direction"] = "SELL"
                    logging.info("Detected existing SELL positions on start - adopting open_direction=SELL")
                elif buys and sells:
                    # both exist: log and skip opening until cleaned manually
                    logging.warning("Both BUY and SELL positions exist for epic. Bot will not open new trades until manual cleanup.")
                    state["open_direction"] = None
                else:
                    # no positions: open initial if there's a base direction
                    # Use open_initial_if_signal (demo signal replacement) - replace with your real signal logic
                    open_initial_if_signal(ig, epic)

            else:
                # We have an active direction; manage grid and exits
                maybe_open_additional(ig, epic)
                maybe_close_on_combined_profit(ig, epic)

            # safety: if level exceeds max, stop
            if state.get("level", 0) > MAX_LEVEL:
                logging.error("level (%s) > MAX_LEVEL (%s). Stopping to avoid runaway.", state.get("level"), MAX_LEVEL)
                break

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Interrupted by user - exiting.")
    except Exception as e:
        logging.exception("Unhandled exception in main loop: %s", e)
    finally:
        try:
            ig.logout()
            logging.info("Logged out.")
        except Exception:
            pass


if __name__ == "__main__":
    main_loop()

