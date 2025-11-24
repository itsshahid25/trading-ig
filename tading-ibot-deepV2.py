import time
import logging
import math
from trading_ig import IGService
from trading_ig import config
from typing import List, Dict, Optional
from datetime import datetime

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
ORDER_DISTANCE = 1000  # Distance between orders in points
AVERAGE_PROFIT = 5  # Target profit in points

# Risk Management
MAX_POSITION_SIZE = 2.0  # Maximum total position size
STOP_LOSS_POINTS = 50  # Overall stop loss in points
POLL_INTERVAL = 5  # Seconds between checks
MAX_DAILY_LOSS = 100  # Maximum daily loss in account currency

# -------------------------

class GridTradingBot:
    def __init__(self):
        self.ig = None
        self.epic = EPIC
        self.current_price = None
        self.open_positions = []
        self.pending_orders = []
        self.grid_levels = []
        self.daily_pnl = 0.0
        self.start_time = datetime.now()
        self.orders_placed = 0
        self.trades_executed = 0
        
    def create_ig_session(self):
        """Create and authenticate IG session"""
        self.ig = IGService(
            username=config.IG_USERNAME,
            password=config.IG_PASSWORD, 
            api_key=config.IG_API_KEY)
        
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
        """Get current market price"""
        try:
            market_info = self.ig.fetch_market_by_epic(epic)
            
            # Handle different response types
            if hasattr(market_info, 'iloc'):  # DataFrame
                bid = float(market_info.iloc[0]['bid'])
                offer = float(market_info.iloc[0]['offer'])
                current_price = (bid + offer) / 2
                return current_price
            elif hasattr(market_info, 'get'):  # Dict-like object
                if 'bid' in market_info and 'offer' in market_info:
                    bid = float(market_info.get('bid'))
                    offer = float(market_info.get('offer'))
                    current_price = (bid + offer) / 2
                    return current_price
                elif 'snapshot' in market_info:
                    snapshot = market_info.get('snapshot', {})
                    bid = float(snapshot.get('bid'))
                    offer = float(snapshot.get('offer'))
                    current_price = (bid + offer) / 2
                    return current_price
            else:
                logging.error("Unexpected market info type: %s", type(market_info))
                return None
                
        except Exception as e:
            logging.error("Error getting current price: %s", e)
            return None

    def calculate_grid_levels(self, current_price, num_orders, distance_points):
        """Calculate buy and sell grid levels"""
        if current_price is None:
            logging.error("Cannot calculate grid levels: current price is None")
            return []
            
        # Convert points to price
        distance_price = distance_points * 0.01
        
        buy_levels = []
        sell_levels = []
        
        # Calculate buy levels (below current price)
        for i in range(1, num_orders + 1):
            price = current_price - (i * distance_price)
            buy_levels.append({
                'price': round(price, 4),
                'level': i,
                'type': 'BUY',
                'order_ref': None
            })
            
        # Calculate sell levels (above current price)  
        for i in range(1, num_orders + 1):
            price = current_price + (i * distance_price)
            sell_levels.append({
                'price': round(price, 4),
                'level': i,
                'type': 'SELL',
                'order_ref': None
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
                tp_offset = take_profit * 0.01
                if direction.upper() == "BUY":
                    tp_price = limit_price + tp_offset
                else:
                    tp_price = limit_price - tp_offset
                    
            if stop_loss:
                sl_offset = stop_loss * 0.01
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
                expiry="-",
            )
            
            deal_ref = resp.get('dealReference', 'Unknown')
            logging.info("Limit order placed successfully: %s", deal_ref)
            self.orders_placed += 1
            return resp
            
        except Exception as e:
            logging.error("Error placing limit order: %s", e)
            return None

    def calculate_position_size(self, level, base_size, num_orders):
        """Calculate position size for each grid level"""
        return base_size

    def setup_grid_orders(self):
        """Setup all grid orders"""
        self.current_price = self.get_current_price(self.epic)
            
        if self.current_price is None:
            logging.error("Price retrieval failed")
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
                grid_level['order_ref'] = order_resp.get('dealReference')
                self.pending_orders.append({
                    'grid_level': grid_level,
                    'order_ref': order_resp.get('dealReference'),
                    'size': size,
                    'placed_time': datetime.now()
                })
                successful_orders += 1
            
            time.sleep(1)  # Rate limiting
        
        logging.info("Grid setup complete: %d/%d orders placed successfully", 
                    successful_orders, len(self.grid_levels))
        return successful_orders > 0

    def get_open_positions(self):
        """Get current open positions"""
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
                        'open_level': float(pos.get('openLevel', 0)),
                        'epic': pos.get('epic'),
                        'profit_loss': float(pos.get('profitLoss', 0))
                    })
            
            return open_positions
            
        except Exception as e:
            logging.error("Error fetching open positions: %s", e)
            return []

    def get_pending_orders(self):
        """Get current pending orders"""
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
                        'deal_reference': order.get('dealReference'),
                        'direction': order.get('direction'),
                        'size': float(order.get('size', 0)),
                        'level': float(order.get('orderLevel', 0)),
                        'epic': order.get('epic'),
                        'order_type': order.get('orderType', 'UNKNOWN')
                    })
            
            return pending_orders
            
        except Exception as e:
            logging.error("Error fetching pending orders: %s", e)
            return []

    def get_account_balance(self):
        """Get current account balance"""
        try:
            accounts = self.ig.fetch_accounts()
            if hasattr(accounts, 'iloc'):
                return float(accounts.iloc[0]['balance'])
            elif isinstance(accounts, list) and accounts:
                return float(accounts[0].get('balance', 0))
            else:
                logging.error("Unexpected accounts format")
                return 0
        except Exception as e:
            logging.error("Error fetching account balance: %s", e)
            return 0

    def monitor_grid(self):
        """Monitor and maintain the grid"""
        try:
            # Refresh current positions and orders
            current_open_positions = self.get_open_positions()
            current_pending = self.get_pending_orders()
            current_balance = self.get_account_balance()
            
            # Check for new positions (orders that got filled)
            self.check_new_positions(current_open_positions)
            
            # Update pending orders tracking
            self.update_pending_orders(current_pending)
            
            # Check risk limits
            self.check_risk_management(current_balance)
            
            # Log status
            total_exposure = sum(pos['size'] for pos in current_open_positions)
            logging.info("Grid Status - Positions: %d, Pending: %d, Exposure: %.2f, PnL: %.2f", 
                        len(current_open_positions), len(current_pending), 
                        total_exposure, self.daily_pnl)
            
        except Exception as e:
            logging.error("Error in grid monitoring: %s", e)

    def check_new_positions(self, current_open_positions):
        """Check for new positions that were filled from our pending orders"""
        current_deal_refs = [pos.get('deal_id') for pos in current_open_positions]
        
        # Check if we have new positions that weren't tracked before
        for pos in current_open_positions:
            if pos['deal_id'] not in [p.get('deal_id') for p in self.open_positions]:
                logging.info("New position opened: %s %s at %s", 
                            pos['direction'], pos['size'], pos['open_level'])
                self.trades_executed += 1
        
        self.open_positions = current_open_positions

    def update_pending_orders(self, current_pending):
        """Update pending orders tracking"""
        current_order_refs = [order.get('deal_reference') for order in current_pending]
        
        # Remove orders that are no longer pending
        for tracked_order in self.pending_orders[:]:
            if tracked_order['order_ref'] not in current_order_refs:
                logging.info("Order filled/cancelled: %s", tracked_order['order_ref'])
                self.pending_orders.remove(tracked_order)
                
                # Option: Auto-replace filled orders to maintain grid
                # self.replace_grid_order(tracked_order['grid_level'])

    def replace_grid_order(self, grid_level):
        """Replace a filled grid order to maintain the grid"""
        logging.info("Auto-replacing %s order at level %d", 
                    grid_level['type'], grid_level['level'])
        
        size = self.calculate_position_size(
            grid_level['level'], BASE_SIZE, NUM_ORDERS
        )
        
        order_resp = self.place_limit_order(
            epic=self.epic,
            direction=grid_level['type'],
            size=size,
            level=grid_level['level'],
            limit_price=grid_level['price'],
            take_profit=AVERAGE_PROFIT,
            stop_loss=STOP_LOSS_POINTS * 2
        )
        
        if order_resp:
            grid_level['order_ref'] = order_resp.get('dealReference')
            self.pending_orders.append({
                'grid_level': grid_level,
                'order_ref': order_resp.get('dealReference'),
                'size': size,
                'placed_time': datetime.now()
            })

    def check_risk_management(self, current_balance):
        """Check overall risk management"""
        total_exposure = sum(pos['size'] for pos in self.open_positions)
        
        # Check position size limits
        if total_exposure > MAX_POSITION_SIZE:
            logging.error("Exceeded maximum position size: %s > %s", 
                         total_exposure, MAX_POSITION_SIZE)
            # Could auto-close some positions here
        
        # Check daily loss limit
        if self.daily_pnl < -MAX_DAILY_LOSS:
            logging.error("Daily loss limit reached: %.2f", self.daily_pnl)
            self.emergency_stop()
            
        # Check if we need to rebalance grid (price moved significantly)
        self.check_grid_rebalance()

    def check_grid_rebalance(self):
        """Check if grid needs rebalancing due to significant price movement"""
        current_price = self.get_current_price(self.epic)
        if current_price and self.current_price:
            price_change_pct = abs((current_price - self.current_price) / self.current_price) * 100
            
            # If price moved more than X%, consider rebalancing grid
            if price_change_pct > 2.0:  # 2% threshold
                logging.warning("Significant price movement detected: %.2f%%. Consider manual rebalancing.", 
                               price_change_pct)

    def emergency_stop(self):
        """Emergency stop - close all positions and cancel all orders"""
        logging.error("EMERGENCY STOP ACTIVATED!")
        self.close_all_positions()
        self.cancel_all_orders()
        logging.info("Emergency stop completed")

    def close_all_positions(self):
        """Close all open positions"""
        logging.info("Closing all open positions")
        for position in self.open_positions:
            try:
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

    def print_stats(self):
        """Print trading statistics"""
        runtime = datetime.now() - self.start_time
        hours = runtime.total_seconds() / 3600
        
        logging.info("=" * 50)
        logging.info("TRADING STATISTICS")
        logging.info("=" * 50)
        logging.info("Runtime: %.2f hours", hours)
        logging.info("Orders Placed: %d", self.orders_placed)
        logging.info("Trades Executed: %d", self.trades_executed)
        logging.info("Current Positions: %d", len(self.open_positions))
        logging.info("Pending Orders: %d", len(self.pending_orders))
        logging.info("Daily PnL: %.2f %s", self.daily_pnl, CURRENCY)
        logging.info("=" * 50)

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
            
            # Test price retrieval
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
            iteration = 0
            while True:
                self.monitor_grid()
                
                # Print stats every 12 iterations (1 minute with 5s interval)
                iteration += 1
                if iteration % 12 == 0:
                    self.print_stats()
                
                time.sleep(POLL_INTERVAL)
                
        except KeyboardInterrupt:
            logging.info("Bot stopped by user")
        except Exception as e:
            logging.exception("Unhandled exception: %s", e)
        finally:
            # Cleanup and print final stats
            self.print_stats()
            logging.info("Shutting down bot...")
            try:
                self.cancel_all_orders()
                # Uncomment next line if you want to close positions on exit
                # self.close_all_positions()
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