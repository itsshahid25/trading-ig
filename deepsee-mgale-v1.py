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
import os
from trading_ig import IGService
import json

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# -------------------------
# CONFIG - CHANGE THESE
# -------------------------
# Use environment variables for security
USERNAME = "itsshahid25"  # CHANGE ME
PASSWORD = "S621541@74i"  # CHANGE ME
API_KEY = "fa51b09392954a22298b67d2dfd765cfeac8fe26"  # CHANGE ME
ACC_TYPE = "DEMO"  # "DEMO" or "LIVE"

EPIC_SEARCH = "Gold"  # symbol to search for (human-friendly)
EPIC = None  # if you know the epic string, put it here to skip search
CURRENCY = "GBP"  # account currency / trade currency

# Grid / sizing
BASE_SIZE = 0.10  # base stake (units the IG API expects)
USE_MARTINGALE = True  # if True, next lots = BASE_SIZE * (2**level); else always BASE_SIZE
MAX_LEVEL = 10  # max doubling level (ignored if USE_MARTINGALE False)
MAX_TRADES = 10 # max concurrent same-direction trades allowed
GRID_DISTANCE = 5.0  # price distance (in instrument price units, e.g., points) to open next trade
POLL_INTERVAL = 5  # seconds between main loop polls

# Direction controls: "BUY", "SELL", "BOTH"
BASE_DIRECTION = "BOTH"

# Close conditions
CLOSE_ON_COMBINED_PROFIT = 6.0  # close all same-direction trades when combined unrealised profit (currency) >= this (None to disable)
CLOSE_ON_COMBINED_PROFIT_PTS = None  # points from avg price to close

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


def validate_config():
    """Validate configuration before starting"""
    if not all([USERNAME, PASSWORD, API_KEY]):
        raise ValueError("Missing IG API credentials - set environment variables: IG_USERNAME, IG_PASSWORD, IG_API_KEY")
    
    if BASE_DIRECTION.upper() not in ["BUY", "SELL", "BOTH"]:
        raise ValueError("BASE_DIRECTION must be 'BUY', 'SELL', or 'BOTH'")
    
    if GRID_DISTANCE <= 0:
        raise ValueError("GRID_DISTANCE must be positive")
    
    if BASE_SIZE <= 0:
        raise ValueError("BASE_SIZE must be positive")
    
    if MAX_TRADES <= 0:
        raise ValueError("MAX_TRADES must be positive")


def create_ig_session():
    """Create and return authenticated IG session with error handling"""
    try:
        ig = IGService(username=USERNAME, password=PASSWORD, api_key=API_KEY, acc_type=ACC_TYPE)
        ig.create_session()
        logging.info("Logged in to IG (%s)", ACC_TYPE)
        return ig
    except Exception as e:
        logging.error("Failed to create IG session: %s", e)
        raise


def find_epic(ig, search_text):
    """Find epic for given search text"""
    logging.info("Searching for epic matching '%s' ...", search_text)
    try:
        markets = ig.search_markets(search_text)
    except Exception as e:
        logging.exception("search_markets failed: %s", e)
        return None

    if markets is None:
        logging.warning("fetch_markets returned None")
        return None

    # Handle different response formats
    if hasattr(markets, "empty"):  # DataFrame
        if markets.empty:
            logging.warning("No markets found for search text: %s", search_text)
            return None
        markets = markets.to_dict("records")

    if isinstance(markets, list) and markets:
        # Try to find exact match first
        for m in markets:
            if isinstance(m, dict):
                name = (m.get("instrumentName") or "").upper()
                epic = m.get("epic")
                if search_text.upper() in name and epic:
                    logging.info("Found epic: %s (%s)", epic, name)
                    return epic
        
        # Fallback to first result
        first = markets[0]
        if isinstance(first, dict):
            epic = first.get("epic")
            name = first.get("instrumentName", "Unknown")
            if epic:
                logging.info("Using first result: %s (%s)", epic, name)
                return epic

    logging.warning("No valid epic found for: %s", search_text)
    return None


def compute_size(level):
    """Compute trade size based on current level"""
    if USE_MARTINGALE:
        return round(BASE_SIZE * (2 ** level), 8)
    else:
        return BASE_SIZE


def place_market_open(ig, epic, direction, size, currency=CURRENCY):
    """Place market order to open position"""
    logging.info("Placing MARKET open: %s %s units on %s", direction, size, epic)
    try:
        resp = ig.create_open_position(
            currency_code=currency,
            direction=direction.upper(),
            epic=epic,
            order_type="MARKET",
            expiry="DFB",
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
        logging.info("Open position response: %s", resp.get("dealReference", "Unknown"))
        return resp
    except Exception as e:
        logging.error("Failed to place market open: %s", e)
        return None


def fetch_open_positions(ig):
    """Return open positions as list of dicts"""
    try:
        positions = ig.fetch_open_positions()
    except Exception as e:
        logging.error("Failed to fetch open positions: %s", e)
        return []

    if positions is None:
        return []
    
    if hasattr(positions, "empty"):  # DataFrame
        return positions.to_dict("records") if not positions.empty else []
    
    if isinstance(positions, list):
        return positions
    
    if isinstance(positions, dict):
        return positions.get("positions", [])
    
    logging.warning("Unknown positions type: %s", type(positions))
    return []


def get_positions_for_epic_and_direction(ig, epic, direction):
    """Get positions for specific epic and direction"""
    all_positions = fetch_open_positions(ig)
    matching_positions = []
    
    for pos in all_positions:
        pos_epic = pos.get("epic")
        pos_direction = (pos.get("direction") or "").upper()
        
        if pos_epic == epic and pos_direction == direction.upper():
            matching_positions.append(pos)
            
    return matching_positions


def get_market_price(ig, epic):
    """Get current market price (mid price) for epic"""
    try:
        market = ig.fetch_market_by_epic(epic)
        if not market:
            return None
            
        snapshot = market.get("snapshot", {})
        bid = snapshot.get("bid")
        offer = snapshot.get("offer")
        
        if bid is not None and offer is not None:
            return (float(bid) + float(offer)) / 2.0
        else:
            # Fallback to last traded price
            last = snapshot.get("lastTradedPrice") or snapshot.get("last")
            if last is not None:
                return float(last)
                
    except Exception as e:
        logging.error("Failed to get market price for %s: %s", epic, e)
    
    return None


def compute_position_profit(position, current_price):
    """Compute profit for a single position based on current price"""
    try:
        direction = position.get("direction", "").upper()
        size = float(position.get("size", 0))
        open_price = float(position.get("level") or position.get("openLevel", 0))
        
        if direction == "BUY":
            profit = (current_price - open_price) * size
        else:  # SELL
            profit = (open_price - current_price) * size
            
        return profit
    except (TypeError, ValueError) as e:
        logging.warning("Could not compute profit for position: %s", e)
        return 0.0


def combined_positions_summary(ig, positions, epic):
    """Calculate combined summary for positions"""
    if not positions:
        return {"total_size": 0.0, "avg_price": 0.0, "combined_unrealised_profit": 0.0}
    
    total_size = 0.0
    weighted_sum = 0.0
    combined_profit = 0.0
    
    # Get current market price once
    current_price = get_market_price(ig, epic)
    if current_price is None:
        logging.warning("Could not get current price for profit calculation")
        current_price = 0.0
    
    for pos in positions:
        try:
            size = float(pos.get("size", 0))
            open_price = float(pos.get("level") or pos.get("openLevel", 0))
            
            total_size += size
            weighted_sum += open_price * size
            
            # Calculate profit for this position
            profit = compute_position_profit(pos, current_price)
            combined_profit += profit
            
        except (TypeError, ValueError) as e:
            logging.warning("Skipping invalid position data: %s", e)
            continue
    
    avg_price = weighted_sum / total_size if total_size > 0 else 0.0
    
    return {
        "total_size": total_size,
        "avg_price": avg_price,
        "combined_unrealised_profit": combined_profit
    }


def get_quote_id(ig, epic):
    """Get quote_id from market data with multiple fallback methods"""
    try:
        market = ig.fetch_market_by_epic(epic)
        
        # Try multiple possible locations for quote_id
        possible_paths = [
            market.get("snapshot", {}).get("quoteId"),
            market.get("snapshot", {}).get("marketStatus"),  # Sometimes quoteId is here
            market.get("quoteId"),
            market.get("market", {}).get("quoteId"),
            market.get("instrument", {}).get("quoteId"),
        ]
        
        for quote_id in possible_paths:
            if quote_id:
                return quote_id
        
        # If no quote_id found, try to generate one from the epic or use a default
        logging.warning("No quote_id found in market data, using fallback")
        return f"DF_{epic}"  # Fallback quote_id format
        
    except Exception as e:
        logging.error("Error getting quote_id for %s: %s", epic, e)
        return f"DF_{epic}"  # Emergency fallback


def close_all_same_direction(ig, epic, direction):
    """Close all positions for given epic and direction using direct REST API"""
    positions = get_positions_for_epic_and_direction(ig, epic, direction)
    if not positions:
        logging.info("No %s positions to close for %s", direction, epic)
        return

    logging.info("Closing %d %s positions for %s", len(positions), direction, epic)

    success_count = 0
    for position in positions:
        try:
            deal_id = position.get("dealId")
            size = position.get("size")
            close_direction = "SELL" if direction == "BUY" else "BUY"

            # Get current market data
            market = ig.fetch_market_by_epic(epic)
            quote_id = market.get("snapshot", {}).get("quoteId")
            current_price = get_market_price(ig, epic)

            # Build the payload for the delete request
            payload = {
                "dealId": deal_id,
                "direction": close_direction,
                "epic": epic,
                "expiry": "DFB",
                "orderType": "MARKET",
                "size": size,
                "timeInForce": "EXECUTE_AND_ELIMINATE"
            }

            # Add either quoteId or level, but not both
            if quote_id:
                payload["quoteId"] = quote_id
            else:
                if current_price is None:
                    logging.error("Cannot close position %s: no quote_id and no current price", deal_id)
                    continue
                payload["level"] = current_price

            # Make the request
            endpoint = f"/positions/otc/{deal_id}"
            response = ig.dreq("DELETE", endpoint, params=payload)

            if response and response.get("dealStatus") in ["ACCEPTED", "OPEN"]:
                success_count += 1
                logging.info("Successfully closed position %s", deal_id)
            else:
                logging.warning("Close failed for %s: %s", deal_id, response)

        except Exception as e:
            logging.error("Error closing position %s: %s", position.get("dealId"), e)

    logging.info("Closed %d/%d %s positions", success_count, len(positions), direction)

    # Reset state if all closed
    if success_count > 0:
        time.sleep(2)  # Wait for API to update
        remaining = get_positions_for_epic_and_direction(ig, epic, direction)
        if not remaining:
            state["open_direction"] = None
            state["level"] = 0
            logging.info("All positions closed, resetting state")

def open_initial_if_signal(ig, epic):
    """Open initial trade based on signal"""
    # Simple demo signal - replace with your actual trading logic
    if BASE_DIRECTION.upper() == "BOTH":
        signal = "BUY" if state["level"] % 2 == 0 else "SELL"
    else:
        signal = BASE_DIRECTION.upper()
    
    size = compute_size(state["level"])
    resp = place_market_open(ig, epic, signal, size)
    
    if resp:
        state["open_direction"] = signal
        logging.info("Initial trade opened: %s %s units", signal, size)
        return True
    else:
        logging.error("Failed to open initial trade")
        return False


def maybe_open_additional(ig, epic):
    """Check if conditions met to open additional position"""
    direction = state.get("open_direction")
    if not direction:
        return
    
    positions = get_positions_for_epic_and_direction(ig, epic, direction)
    if not positions:
        logging.info("No positions found for direction %s, resetting state", direction)
        state["open_direction"] = None
        state["level"] = 0
        return
    
    # Check max trades limit
    if len(positions) >= MAX_TRADES:
        logging.debug("Max trades reached (%d), skipping additional", MAX_TRADES)
        return
    
    summary = combined_positions_summary(ig, positions, epic)
    avg_price = summary["avg_price"]
    current_price = get_market_price(ig, epic)
    
    if avg_price is None or current_price is None:
        logging.warning("Could not get prices for grid calculation")
        return
    
    # Check grid distance condition
    need_open = False
    if direction == "BUY":
        if (avg_price - current_price) >= GRID_DISTANCE:
            need_open = True
            logging.info("Grid condition met: price dropped %.2f from average", avg_price - current_price)
    else:  # SELL
        if (current_price - avg_price) >= GRID_DISTANCE:
            need_open = True
            logging.info("Grid condition met: price rose %.2f from average", current_price - avg_price)
    
    if need_open:
        next_level = len(positions)  # 0-based level for next trade
        if next_level >= MAX_LEVEL:
            logging.warning("Max level reached (%d), not opening additional", MAX_LEVEL)
            return
        
        size = compute_size(next_level)
        resp = place_market_open(ig, epic, direction, size)
        
        if resp:
            state["level"] = next_level
            logging.info("Opened additional %s position (level %d, size %s)", direction, next_level, size)


def maybe_close_on_combined_profit(ig, epic):
    """Check if combined profit target reached and close positions"""
    direction = state.get("open_direction")
    if not direction or not CLOSE_ON_COMBINED_PROFIT:
        return
    
    positions = get_positions_for_epic_and_direction(ig, epic, direction)
    if not positions:
        return
    
    summary = combined_positions_summary(ig, positions, epic)
    combined_profit = summary["combined_unrealised_profit"]
    
    if combined_profit >= CLOSE_ON_COMBINED_PROFIT:
        logging.info("Profit target reached (%.2f >= %.2f), closing all %s positions", 
                    combined_profit, CLOSE_ON_COMBINED_PROFIT, direction)
        close_all_same_direction(ig, epic, direction)
        state["open_direction"] = None
        state["level"] = 0


def recover_session(ig):
    """Attempt to recover IG session"""
    try:
        ig.logout()
    except:
        pass
    
    time.sleep(2)
    
    try:
        new_ig = create_ig_session()
        logging.info("Session recovered successfully")
        return new_ig
    except Exception as e:
        logging.error("Failed to recover session: %s", e)
        return None


def detect_existing_positions(ig, epic):
    """Detect existing positions on startup and set state accordingly"""
    buy_positions = get_positions_for_epic_and_direction(ig, epic, "BUY")
    sell_positions = get_positions_for_epic_and_direction(ig, epic, "SELL")
    
    if buy_positions and not sell_positions:
        state["open_direction"] = "BUY"
        state["level"] = len(buy_positions) - 1  # Set level based on existing positions
        logging.info("Detected existing BUY positions (%d), adopting state", len(buy_positions))
        return True
    elif sell_positions and not buy_positions:
        state["open_direction"] = "SELL"
        state["level"] = len(sell_positions) - 1
        logging.info("Detected existing SELL positions (%d), adopting state", len(sell_positions))
        return True
    elif buy_positions and sell_positions:
        logging.warning("Both BUY and SELL positions exist - manual cleanup required")
        return False
    else:
        logging.info("No existing positions detected")
        return True


def main_loop():
    """Main trading loop"""
    # Validate configuration first
    try:
        validate_config()
    except ValueError as e:
        logging.error("Configuration error: %s", e)
        return
    
    # Create initial session
    ig = create_ig_session()
    if not ig:
        return
    
    # Find epic if not provided
    global EPIC
    if not EPIC:
        EPIC = find_epic(ig, EPIC_SEARCH)
        if not EPIC:
            logging.error("Could not find epic for %s", EPIC_SEARCH)
            return
    state["epic"] = EPIC
    logging.info("Using epic: %s", EPIC)
    
    # Detect existing positions
    if not detect_existing_positions(ig, EPIC):
        logging.error("Mixed positions detected, exiting for manual cleanup")
        return
    
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    try:
        while True:
            try:
                epic = state["epic"]
                
                # Main trading logic
                if state.get("open_direction") is None:
                    # No active direction - try to open initial trade
                    open_initial_if_signal(ig, epic)
                else:
                    # Active direction - manage grid and check exits
                    maybe_open_additional(ig, epic)
                    maybe_close_on_combined_profit(ig, epic)
                
                # Safety checks
                if state.get("level", 0) >= MAX_LEVEL:
                    logging.error("Max level reached (%d), stopping", MAX_LEVEL)
                    break
                
                consecutive_errors = 0  # Reset error counter on successful iteration
                time.sleep(POLL_INTERVAL)
                
            except Exception as e:
                consecutive_errors += 1
                logging.error("Error in trading cycle (%d/%d): %s", 
                             consecutive_errors, max_consecutive_errors, e)
                
                if consecutive_errors >= max_consecutive_errors:
                    logging.error("Too many consecutive errors, stopping")
                    break
                
                # Attempt session recovery
                logging.info("Attempting session recovery...")
                new_ig = recover_session(ig)
                if new_ig:
                    ig = new_ig
                    consecutive_errors = 0
                else:
                    time.sleep(10)  # Wait before retry
                    
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    except Exception as e:
        logging.exception("Unexpected error in main loop: %s", e)
    finally:
        try:
            ig.logout()
            logging.info("Logged out")
        except:
            pass


if __name__ == "__main__":
    main_loop()