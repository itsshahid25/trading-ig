import time
import logging
import requests
from typing import List, Dict, Optional
from ig import IGService

class IGGridTrader:
    def __init__(self, api_key: str, username: str, password: str, account_id: str, demo=True):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.account_id = account_id
        self.demo = demo
        self.ig_service = None
        self.connected = False
        
        # Trading parameters
        self.epic = None
        self.direction = None
        self.num_orders = 0
        self.grid_distance = 0
        self.lot_size = 0
        self.target_profit = 0
        self.grid_orders = []
        
        # Setup logging to match your working format
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger()

    def connect(self) -> bool:
        """Connect to IG API using your working configuration"""
        try:
            self.logger.info("Connecting to IG API...")
            
            self.ig_service = IGService(
                self.username,
                self.password, 
                self.api_key,
                self.account_id,
                self.demo
            )
            
            # Create session
            self.ig_service.create_session()
            self.connected = True
            self.logger.info("Successfully connected to IG API")
            
            # Verify connection
            balance = self.get_account_balance()
            self.logger.info(f"Account balance: {balance}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            return False

    def get_account_balance(self) -> float:
        """Get account balance"""
        try:
            accounts = self.ig_service.fetch_accounts()
            for account in accounts:
                if account['accountId'] == self.account_id:
                    return float(account['balance']['balance'])
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting balance: {e}")
            return 0.0

    def get_market_price(self) -> float:
        """Get current market price - using your working EPIC format"""
        try:
            self.logger.info(f"GET '/markets/{self.epic}'")
            market_info = self.ig_service.fetch_market_by_epic(self.epic)
            
            bid = float(market_info['snapshot']['bid'])
            ask = float(market_info['snapshot']['ask'])
            
            # Return appropriate price based on direction
            if self.direction == 'SELL':
                price = bid
            else:
                price = ask
                
            self.logger.info(f"Market Price - Bid: {bid}, Ask: {ask}")
            return price
            
        except Exception as e:
            self.logger.error(f"Error getting market price: {e}")
            raise

    def set_trading_parameters(self, epic: str, direction: str, num_orders: int, 
                             grid_distance: float, lot_size: float, target_profit: float):
        """Set grid trading parameters"""
        self.epic = epic
        self.direction = direction.upper()
        self.num_orders = num_orders
        self.grid_distance = grid_distance
        self.lot_size = lot_size
        self.target_profit = target_profit
        
        self.logger.info(f"Grid parameters set: {epic}, {direction}, {num_orders} orders")
        return True

    def calculate_grid_levels(self, current_price: float) -> List[float]:
        """Calculate grid levels based on current price"""
        grid_levels = []
        
        if self.direction == 'BUY':
            # BUY grid - orders below current price
            for i in range(self.num_orders):
                level = round(current_price - ((i + 1) * self.grid_distance), 2)
                grid_levels.append(level)
        else:
            # SELL grid - orders above current price  
            for i in range(self.num_orders):
                level = round(current_price + ((i + 1) * self.grid_distance), 2)
                grid_levels.append(level)
                
        self.logger.info(f"Grid levels: {grid_levels}")
        return grid_levels

    def place_limit_order(self, level: float, order_ref: str) -> bool:
        """Place limit order at grid level"""
        try:
            # Calculate stop levels (2x grid distance)
            stop_distance = self.grid_distance * 2
            
            if self.direction == 'BUY':
                response = self.ig_service.create_open_position(
                    epic=self.epic,
                    direction='BUY',
                    size=self.lot_size,
                    order_type='LIMIT',
                    level=level,
                    limit_level=level + stop_distance,  # Take profit
                    stop_level=level - stop_distance,   # Stop loss
                    deal_reference=order_ref
                )
            else:
                response = self.ig_service.create_open_position(
                    epic=self.epic,
                    direction='SELL', 
                    size=self.lot_size,
                    order_type='LIMIT',
                    level=level,
                    limit_level=level - stop_distance,  # Take profit
                    stop_level=level + stop_distance,   # Stop loss
                    deal_reference=order_ref
                )
            
            if response and 'dealReference' in response:
                deal_ref = response['dealReference']
                self.logger.info(f"POST '/positions/otc' - Order placed at {level}: {deal_ref}")
                return True
            else:
                self.logger.error(f"Order failed at {level}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to place order at {level}: {e}")
            return False

    def create_grid_orders(self):
        """Create the complete grid of orders"""
        try:
            self.logger.info("Creating grid orders...")
            
            # Get current price
            current_price = self.get_market_price()
            grid_levels = self.calculate_grid_levels(current_price)
            
            # Place grid orders
            successful_orders = 0
            for i, level in enumerate(grid_levels):
                order_ref = f"GRID_{self.direction}_{i+1}_{int(time.time())}"
                
                self.logger.info(f"Placing order {i+1} at {level}")
                if self.place_limit_order(level, order_ref):
                    successful_orders += 1
                    self.grid_orders.append({
                        'level': level,
                        'order_ref': order_ref,
                        'timestamp': time.time()
                    })
                
                time.sleep(1)  # Rate limiting
            
            self.logger.info(f"Grid creation complete: {successful_orders}/{self.num_orders} orders placed")
            return successful_orders > 0
            
        except Exception as e:
            self.logger.error(f"Error creating grid: {e}")
            return False

    def get_open_positions(self):
        """Get all open positions for the epic"""
        try:
            positions = self.ig_service.fetch_open_positions()
            epic_positions = [pos for pos in positions if pos['market']['epic'] == self.epic]
            return epic_positions
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}")
            return []

    def calculate_average_profit(self) -> float:
        """Calculate average profit across all positions"""
        positions = self.get_open_positions()
        
        if not positions:
            return 0.0
        
        total_profit = 0.0
        for position in positions:
            try:
                profit_loss = float(position['position']['profitLoss'])
                total_profit += profit_loss
            except (KeyError, ValueError):
                continue
        
        average_profit = total_profit / len(positions)
        self.logger.info(f"Current average profit: {average_profit:.2f}")
        return average_profit

    def close_all_positions(self):
        """Close all open positions"""
        try:
            positions = self.get_open_positions()
            closed_count = 0
            
            for position in positions:
                deal_id = position['position']['dealId']
                direction = position['position']['direction']
                size = position['position']['size']
                
                # Reverse direction to close
                close_direction = 'SELL' if direction == 'BUY' else 'BUY'
                
                response = self.ig_service.close_open_position(
                    deal_id=deal_id,
                    direction=close_direction,
                    size=size,
                    order_type='MARKET'
                )
                
                if response:
                    closed_count += 1
                    self.logger.info(f"Closed position: {deal_id}")
                
                time.sleep(0.5)
            
            self.logger.info(f"Closed {closed_count} positions")
            return closed_count
            
        except Exception as e:
            self.logger.error(f"Error closing positions: {e}")
            return 0

    def cancel_pending_orders(self):
        """Cancel all pending working orders"""
        try:
            working_orders = self.ig_service.fetch_working_orders()
            epic_orders = [order for order in working_orders if order['marketData']['epic'] == self.epic]
            
            cancelled_count = 0
            for order in epic_orders:
                deal_id = order['workingOrderData']['dealId']
                self.ig_service.delete_working_order(deal_id)
                cancelled_count += 1
                self.logger.info(f"Cancelled order: {deal_id}")
                time.sleep(0.5)
            
            self.logger.info(f"Cancelled {cancelled_count} pending orders")
            return cancelled_count
            
        except Exception as e:
            self.logger.error(f"Error cancelling orders: {e}")
            return 0

    def monitor_and_close(self, check_interval=30):
        """Monitor profit and close when target reached"""
        self.logger.info("Starting profit monitoring...")
        
        try:
            while True:
                avg_profit = self.calculate_average_profit()
                
                if avg_profit >= self.target_profit:
                    self.logger.info(f"Target profit reached! Closing all positions.")
                    self.close_all_positions()
                    self.cancel_pending_orders()
                    self.logger.info("Grid trading completed!")
                    break
                
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            self.logger.info("Monitoring interrupted by user")
        except Exception as e:
            self.logger.error(f"Error during monitoring: {e}")

    def run_grid_strategy(self, epic: str, direction: str, num_orders: int, 
                         grid_distance: float, lot_size: float, target_profit: float):
        """Run complete grid strategy"""
        
        # Connect first
        if not self.connect():
            return False
        
        # Set parameters
        self.set_trading_parameters(epic, direction, num_orders, grid_distance, lot_size, target_profit)
        
        # Create grid
        if self.create_grid_orders():
            # Start monitoring
            self.monitor_and_close()
            return True
        return False