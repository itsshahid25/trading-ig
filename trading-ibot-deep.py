import time
import logging
import math
from trading_ig import IGService
from trading_ig import config
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# -------------------------
# USER CONFIGURATION
# -------------------------
USERNAME = "itsshahid25"  # CHANGE ME
PASSWORD = "S621541@74i"  # CHANGE ME
API_KEY = "fa51b09392954a22298b67d2dfd765cfeac8fe26"  # CHANGE ME
ACC_TYPE = "DEMO"  # "DEMO" or "LIVE"

# Trading Parameters
EPIC_SEARCH = "Gold"  # Symbol to search for
EPIC = None  # Leave as None to auto-detect
CURRENCY = "GBP"  # Account currency

# Grid Trading Parameters
BASE_SIZE = 0.10  # Base position size
NUM_ORDERS = 5  # Number of orders to place in each direction
ORDER_DISTANCE = 100  # Distance between orders in points
AVERAGE_PROFIT = 5  # Target profit in points

# Risk Management
MAX_POSITION_SIZE = 2.0  # Maximum total position size
STOP_LOSS_POINTS = 0  # Overall stop loss in points
POLL_INTERVAL = 5  # Seconds between checks

# -------------------------

class GridTradingBot:
    def __init__(self):
        self.ig = None
        self.epic = EPIC
        self.current_price = None
        self.open_positions = []
        self.pending_orders = []
        self.grid_levels = []
        
    def create_ig_session(self):
        """Create and authenticate IG session"""
        self.ig = IGService(
            username=USERNAME,
            password=PASSWORD, 
            api_key=API_KEY
        )
        self.ig.create_session()
        logging.info("Successfully logged in to IG (%s)", ACC_TYPE)
        return self.ig

    def find_epic(self, search_text):
        """Find epic by symbol search"""
        logging.info("Searching for epic matching '%s'", search_text)
        markets = self.ig.search_markets(search_text)
        
        if markets is None:
            logging.error("No markets returned")
            return None
            
        # Handle different response formats
        if hasattr(markets, "empty"):
            if markets.empty:
                logging.error("No markets found for: %s", search_text)
                return None
            markets = markets.to_dict("records")
            
        for market in markets:
            if isinstance(market, dict):
                name = market.get("instrumentName", "").upper()
                epic = market.get("epic")
                if search_text.upper() in name:
                    logging.info("Found epic: %s (%s)", epic, name)
                    return epic
                    
        logging.error("Could not find epic for: %s", search_text)
        return None

    def get_current_price(self, epic):
        """Get current market price - fixed for different response types"""
        try:
            market_info = self.ig.fetch_market_by_epic(epic)
            
            # Handle different response types
            if hasattr(market_info, 'iloc'):  # DataFrame
                bid = float(market_info.iloc[0]['bid'])
                offer = float(market_info.iloc[0]['offer'])
                current_price = (bid + offer) / 2
                logging.info("Current price (DataFrame): Bid=%s, Offer=%s, Mid=%s", bid, offer, current_price)
                return current_price
            elif hasattr(market_info, 'get'):  # Dict-like object (Munch, dict, etc.)
                # Try to access bid/offer directly
                if 'bid' in market_info and 'offer' in market_info:
                    bid = float(market_info.get('bid'))
                    offer = float(market_info.get('offer'))
                    current_price = (bid + offer) / 2
                    logging.info("Current price (Direct): Bid=%s, Offer=%s, Mid=%s", bid, offer, current_price)
                    return current_price
                # Try snapshot data
                elif 'snapshot' in market_info:
                    snapshot = market_info.get('snapshot', {})
                    bid = float(snapshot.get('bid'))
                    offer = float(snapshot.get('offer'))
                    current_price = (bid + offer) / 2
                    logging.info("Current price (Snapshot): Bid=%s, Offer=%s, Mid=%s", bid, offer, current_price)
                    return current_price
                else:
                    # Log the structure to understand what we're getting
                    logging.info("Market info keys: %s", list(market_info.keys()))
                    # Try to find bid/offer in any nested structure
                    for key, value in market_info.items():
                        if hasattr(value, 'get'):
                            if 'bid' in value and 'offer' in value:
                                bid = float(value.get('bid'))
                                offer = float(value.get('offer'))
                                current_price = (bid + offer) / 2
                                logging.info("Current price (Nested): Bid=%s, Offer=%s, Mid=%s", bid, offer, current_price)
                                return current_price
            else:
                logging.error("Unexpected market info type: %s - %s", type(market_info), market_info)
                return None
                
        except Exception as e:
            logging.error("Error getting current price: %s", e)
            return None

    def get_current_price_alternative(self, epic):
        """Alternative method to get current price using different API endpoint"""
        try:
            # Try using a different method to get price
            prices = self.ig.fetch_historical_prices_by_epic_and_num(
                epic, resolution='MINUTE', numpoints=1
            )
            
            if hasattr(prices, 'prices'):
                # Get the latest price
                latest = prices['prices'][-1]
                if hasattr(latest, 'get'):
                    close_price = float(latest.get('closePrice'))
                    logging.info("Current price (Historical): %s", close_price)
                    return close_price
            else:
                logging.error("Unexpected prices format: %s", type(prices))
                
        except Exception as e:
            logging.error("Error with alternative price method: %s", e)
            
        return None

    def calculate_grid_levels(self, current_price, num_orders, distance_points):
        """Calculate buy and sell grid levels"""
        if current_price is None:
            logging.error("Cannot calculate grid levels: current price is None")
            return []
            
        # Convert points to price (adjust multiplier based on your instrument)
        # For Gold, 1 point might be 0.01 or 1.0 depending on the instrument
        distance_price = distance_points * 0.01  # Adjust this multiplier as needed
        
        buy_levels = []
        sell_levels = []
        
        # Calculate buy levels (below current price)
        for i in range(1, num_orders + 1):
            price = current_price - (i * distance_price)
            buy_levels.append({
                'price': round(price, 4),
                'level': i,
                'type': 'BUY'
            })
            
        # Calculate sell levels (above current price)  
        for i in range(1, num_orders + 1):
            price = current_price + (i * distance_price)
            sell_levels.append({
                'price': round(price, 4),
                'level': i,
                'type': 'SELL'
            })
            
        logging.info("Grid levels calculated:")
        for level in buy_levels + sell_levels:
            logging.info("  %s Level %d: %s", level['type'], level['level'], level['price'])
            
        return buy_levels + sell_levels

    def place_limit_order(self, epic, direction, size, level, limit_price, 
                         take_profit=None, stop_loss=None):
        """Place a limit order at specified price level"""
        try:
            logging.info("Placing %s limit order at %s: size=%s", 
                        direction, limit_price, size)
            
            # Calculate take profit and stop loss prices
            tp_price = None
            sl_price = None
            
            if take_profit:
                tp_offset = take_profit * 0.01  # Adjust multiplier as needed
                if direction.upper() == "BUY":
                    tp_price = limit_price + tp_offset
                else:
                    tp_price = limit_price - tp_offset
                    
            if stop_loss:
                sl_offset = stop_loss * 0.01  # Adjust multiplier as needed
                if direction.upper() == "BUY":
                    sl_price = limit_price - sl_offset
                else:
                    sl_price = limit_price + sl_offset
            
            # Place the working order
            resp = self.ig.create_working_order(
                epic=epic,
                direction=direction.upper(),
                size=size,
                level=limit_price,
                order_type="LIMIT",
                time_in_force="GOOD_TILL_CANCELLED",
                currency_code=CURRENCY,
                force_open=False,
                guaranteed_stop=False,
                limit_level=tp_price,
                stop_level=sl_price,
                expiry="-"  # No expiry for GTC orders
            )
            
            logging.info("Limit order placed successfully: %s", resp.get('dealReference', 'Unknown'))
            return resp
            
        except Exception as e:
            logging.error("Error placing limit order: %s", e)
            return None

    def calculate_position_size(self, level, base_size, num_orders):
        """Calculate position size for each grid level"""
        # Simple: all positions same size
        # You can customize this for progressive sizing
        return base_size

    def setup_grid_orders(self):
        """Setup all grid orders with better error handling"""
        # Try primary price method
        self.current_price = self.get_current_price(self.epic)
        
        # If primary fails, try alternative method
        if self.current_price is None:
            logging.warning("Primary price method failed, trying alternative...")
            self.current_price = self.get_current_price_alternative(self.epic)
            
        if self.current_price is None:
            logging.error("All price retrieval methods failed")
            return False
            
        logging.info("Current price established: %s", self.current_price)
        
        # Calculate grid levels
        self.grid_levels = self.calculate_grid_levels(
            self.current_price, NUM_ORDERS, ORDER_DISTANCE
        )
        
        if not self.grid_levels:
            logging.error("No grid levels calculated")
            return False
        
        # Place limit orders for each grid level
        successful_orders = 0
        for grid_level in self.grid_levels:
            size = self.calculate_position_size(
                grid_level['level'], BASE_SIZE, NUM_ORDERS
            )
            
            # Individual stop loss for each order
            individual_stop_loss = STOP_LOSS_POINTS * 2
            
            order_resp = self.place_limit_order(
                epic=self.epic,
                direction=grid_level['type'],
                size=size,
                level=grid_level['level'],
                limit_price=grid_level['price'],
                take_profit=AVERAGE_PROFIT,
                stop_loss=individual_stop_loss
            )
            
            if order_resp:
                self.pending_orders.append({
                    'grid_level': grid_level,
                    'order_ref': order_resp.get('dealReference'),
                    'size': size
                })
                successful_orders += 1
            
            time.sleep(1)  # Rate limiting
        
        logging.info("Grid setup complete: %d/%d orders placed successfully", 
                    successful_orders, len(self.grid_levels))
        return successful_orders > 0

    def get_open_positions(self):
        """Get current open positions with better error handling"""
        try:
            positions = self.ig.fetch_open_positions()
            open_positions = []
            
            if positions is None:
                return []
                
            if hasattr(positions, 'empty'):
                if positions.empty:
                    return []
                positions = positions.to_dict('records')
            
            for pos in positions:
                if isinstance(pos, dict):
                    open_positions.append({
                        'deal_id': pos.get('dealId'),
                        'direction': pos.get('direction'),
                        'size': float(pos.get('size', 0)),
                        'level': float(pos.get('level', 0)),
                        'epic': pos.get('epic')
                    })
            
            return open_positions
            
        except Exception as e:
            logging.error("Error fetching open positions: %s", e)
            return []

    def get_pending_orders(self):
        """Get current pending orders with better error handling"""
        try:
            orders = self.ig.fetch_working_orders()
            pending_orders = []
            
            if orders is None:
                return []
                
            if hasattr(orders, 'empty'):
                if orders.empty:
                    return []
                orders = orders.to_dict('records')
            
            for order in orders:
                if isinstance(order, dict):
                    pending_orders.append({
                        'order_id': order.get('dealId'),
                        'direction': order.get('direction'),
                        'size': float(order.get('size', 0)),
                        'level': float(order.get('level', 0)),
                        'epic': order.get('epic')
                    })
            
            return pending_orders
            
        except Exception as e:
            logging.error("Error fetching pending orders: %s", e)
            return []

    def monitor_grid(self):
        """Monitor and maintain the grid"""
        try:
            # Refresh current positions and orders
            self.open_positions = self.get_open_positions()
            current_pending = self.get_pending_orders()
            
            logging.info("Grid Status - Open Positions: %d, Pending Orders: %d", 
                        len(self.open_positions), len(current_pending))
            
            # Check if we need to replace filled orders
            if len(self.open_positions) > 0:
                self.manage_opened_positions()
                
            # Check if any pending orders were filled and need replacement
            self.manage_pending_orders(current_pending)
            
            # Check overall exposure
            self.check_risk_management()
            
        except Exception as e:
            logging.error("Error in grid monitoring: %s", e)

    def manage_opened_positions(self):
        """Manage opened positions from grid"""
        total_size = sum(pos['size'] for pos in self.open_positions)
        
        if total_size >= MAX_POSITION_SIZE:
            logging.warning("Maximum position size reached: %s", total_size)
            # Consider closing some positions or stopping new orders

    def manage_pending_orders(self, current_pending):
        """Manage pending orders - replace filled ones"""
        current_order_refs = [order.get('order_id') for order in current_pending]
        
        for tracked_order in self.pending_orders[:]:
            if tracked_order['order_ref'] not in current_order_refs:
                logging.info("Order filled/cancelled: %s", tracked_order['order_ref'])
                self.pending_orders.remove(tracked_order)

    def check_risk_management(self):
        """Check overall risk management"""
        total_exposure = sum(pos['size'] for pos in self.open_positions)
        
        if total_exposure > MAX_POSITION_SIZE:
            logging.error("Exceeded maximum position size: %s > %s", 
                         total_exposure, MAX_POSITION_SIZE)

    def close_all_positions(self):
        """Close all open positions"""
        logging.info("Closing all open positions")
        for position in self.open_positions:
            try:
                # Determine close direction (opposite of open)
                close_direction = 'SELL' if position['direction'] == 'BUY' else 'BUY'
                
                self.ig.close_open_position(
                    deal_id=position['deal_id'],
                    direction=close_direction,
                    epic=position['epic'],
                    size=position['size'],
                    order_type='MARKET'
                )
                logging.info("Closed position: %s", position['deal_id'])
            except Exception as e:
                logging.error("Error closing position %s: %s", 
                             position['deal_id'], e)

    def cancel_all_orders(self):
        """Cancel all pending orders"""
        logging.info("Cancelling all pending orders")
        pending_orders = self.get_pending_orders()
        for order in pending_orders:
            try:
                self.ig.delete_working_order(order['order_id'])
                logging.info("Cancelled order: %s", order['order_id'])
            except Exception as e:
                logging.error("Error cancelling order %s: %s", 
                             order['order_id'], e)

    def run(self):
        """Main bot execution"""
        try:
            # Setup IG connection
            self.create_ig_session()
            
            # Find epic if not provided
            if not self.epic:
                self.epic = self.find_epic(EPIC_SEARCH)
                if not self.epic:
                    logging.error("Could not find epic for %s", EPIC_SEARCH)
                    return
            
            logging.info("Using epic: %s", self.epic)
            
            # Test price retrieval first
            test_price = self.get_current_price(self.epic)
            if test_price is None:
                logging.error("Price retrieval test failed. Cannot continue.")
                return
                
            logging.info("Price retrieval test successful: %s", test_price)
            
            # Setup initial grid
            if not self.setup_grid_orders():
                logging.error("Failed to setup grid orders")
                return
            
            # Main monitoring loop
            logging.info("Starting grid monitoring...")
            while True:
                self.monitor_grid()
                time.sleep(POLL_INTERVAL)
                
        except KeyboardInterrupt:
            logging.info("Bot stopped by user")
        except Exception as e:
            logging.exception("Unhandled exception: %s", e)
        finally:
            # Cleanup
            logging.info("Shutting down bot...")
            try:
                self.cancel_all_orders()
                # self.close_all_positions()  # Uncomment if you want to close positions on exit
                self.ig.logout()
                logging.info("Cleanup complete")
            except Exception as e:
                logging.error("Error during cleanup: %s", e)


def validate_config():
    """Validate configuration before starting"""
    errors = []
    
    if not all([USERNAME, PASSWORD, API_KEY]):
        errors.append("Missing IG credentials")
    
    if NUM_ORDERS <= 0:
        errors.append("NUM_ORDERS must be positive")
    
    if ORDER_DISTANCE <= 0:
        errors.append("ORDER_DISTANCE must be positive")
    
    if AVERAGE_PROFIT <= 0:
        errors.append("AVERAGE_PROFIT must be positive")
    
    if BASE_SIZE <= 0:
        errors.append("BASE_SIZE must be positive")
    
    if errors:
        for error in errors:
            logging.error("Configuration error: %s", error)
        return False
    
    return True


if __name__ == "__main__":
    if validate_config():
        bot = GridTradingBot()
        bot.run()
    else:
        logging.error("Configuration validation failed. Please check your settings.")