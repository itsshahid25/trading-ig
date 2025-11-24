import time
import logging
from typing import List, Dict, Optional
from trading_ig import IGService
from trading_ig import config
#from trading_ig import IGStreamService
import json

class IGGridTrader:



    
    def __init__(self, api_key: str, username: str, password: str, acc_type: str):
        """
        Initialize IG Grid Trader
        
        Args:
            api_key: IG API key
            username: IG username
            password: IG password
            account_type: IG account type
            demo: Use demo account (default: True)
        """
        self.api_key = api_key
        self.username = username
        self.password = password
        self.acc_type = acc_type
        #self.demo = demo
        
    
        # Initialize IG Service
        self.ig_service = IGService(
            username, password, api_key, acc_type
        )
        
        # Helper: create IG session
    

        
        # Create session
        self.ig_service.create_session()
        

        # Trading parameters (to be set by user)
        self.epic = None
        self.direction = None
        self.num_orders = 0
        self.grid_distance = 0
        self.lot_size = 0
        self.target_profit = 0
        
        # Track orders and positions
        self.grid_orders = []
        self.open_positions = []
        
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def set_trading_parameters(self, epic: str, direction: str, num_orders: int, 
                             grid_distance: float, lot_size: float, target_profit: float):
        """
        Set trading parameters for grid strategy
        
        Args:
            epic: Instrument EPIC code
            direction: 'BUY' or 'SELL'
            num_orders: Number of grid orders
            grid_distance: Distance between grid levels in points
            lot_size: Lot size per order
            target_profit: Target average profit to close all orders
        """
        self.epic = epic
        self.direction = direction.upper()
        self.num_orders = num_orders
        self.grid_distance = grid_distance
        self.lot_size = lot_size
        self.target_profit = target_profit
        
        self.logger.info(f"Trading parameters set: {epic}, {direction}, {num_orders} orders")

    def get_market_price(self) -> float:
        """Get current market price for the epic"""
        try:
            market_info = self.ig_service.fetch_market_by_epic(self.epic)
            test = market_info['snapshot']['bid']
            print(f"Market info bid: {test}")   
            return float(market_info['snapshot']['bid'])
        except Exception as e:
            market_info = self.ig_service.fetch_market_by_epic(self.epic)
            test = market_info['snapshot']['bid']
            print(f"Market info bid: {test}")  
            self.logger.error(f"Error getting market price: {e}")
            raise

    def calculate_grid_levels(self, current_price: float) -> List[float]:
        """
        Calculate grid levels based on current price and direction
        
        Args:
            current_price: Current market price
            
        Returns:
            List of grid levels
        """
        grid_levels = []
        
        if self.direction == 'BUY':
            # For BUY grid, place orders below current price
            for i in range(self.num_orders):
                level = current_price - (i * self.grid_distance)
                grid_levels.append(level)
        else:
            # For SELL grid, place orders above current price
            for i in range(self.num_orders):
                level = current_price + (i * self.grid_distance)
                grid_levels.append(level)
                
        return grid_levels

    def place_limit_order(self, level: float, order_ref: str) -> Optional[Dict]:
        """
        Place a limit order at specified grid level
        
        Args:
            level: Price level for the order
            order_ref: Order reference
            
        Returns:
            Order response or None if failed
        """
        try:
            if self.direction == 'BUY':
                response = self.ig_service.create_open_position(
                    epic=self.epic,
                    direction='BUY',
                    size=self.lot_size,
                    order_type='LIMIT',
                    level=level,
                    limit_level=level + (self.grid_distance * 2),  # Take profit
                    stop_level=level - (self.grid_distance * 2),   # Stop loss
                    deal_reference=order_ref
                )
            else:
                response = self.ig_service.create_open_position(
                    epic=self.epic,
                    direction='SELL',
                    size=self.lot_size,
                    order_type='LIMIT',
                    level=level,
                    limit_level=level - (self.grid_distance * 2),  # Take profit
                    stop_level=level + (self.grid_distance * 2),   # Stop loss
                    deal_reference=order_ref
                )
            
            self.logger.info(f"Order placed at {level}: {order_ref}")
            return response
            
        except Exception as e:
            self.logger.error(f"Failed to place order at {level}: {e}")
            return None

    def create_grid(self):
        """Create the grid of orders"""
        try:
            current_price = self.get_market_price()
            grid_levels = self.calculate_grid_levels(current_price)
            
            self.logger.info(f"Current price: {current_price}")
            self.logger.info(f"Grid levels: {grid_levels}")
            
            # Place orders at each grid level
            for i, level in enumerate(grid_levels):
                order_ref = f"GRID_{self.direction}_{i+1}_{time.strftime('%Y%m%d_%H%M%S')}"
                
                order_response = self.place_limit_order(level, order_ref)
                
                if order_response:
                    self.grid_orders.append({
                        'level': level,
                        'order_ref': order_ref,
                        'response': order_response
                    })
                
                # Small delay to avoid rate limiting
                time.sleep(0.5)
            
            self.logger.info(f"Grid creation completed. {len(self.grid_orders)} orders placed.")
            
        except Exception as e:
            self.logger.error(f"Error creating grid: {e}")
            raise

    def get_open_positions(self):
        """Get all open positions for the epic"""
        try:
            positions = self.ig_service.fetch_open_positions()
            epic_positions = [
                pos for pos in positions if pos['market']['epic'] == self.epic
            ]
            self.open_positions = epic_positions
            return epic_positions
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}")
            return []

    def calculate_average_profit(self) -> float:
        """
        Calculate average profit across all open positions
        
        Returns:
            Average profit in points
        """
        positions = self.get_open_positions()
        
        if not positions:
            return 0.0
        
        total_profit = 0.0
        for position in positions:
            try:
                # Get position profit/loss
                profit_loss = float(position['position']['profitLoss'])
                total_profit += profit_loss
            except (KeyError, ValueError) as e:
                self.logger.warning(f"Could not read profit for position: {e}")
                continue
        
        average_profit = total_profit / len(positions) if positions else 0.0
        return average_profit

    def close_all_positions(self):
        """Close all open positions for the epic"""
        try:
            positions = self.get_open_positions()
            closed_count = 0
            
            for position in positions:
                deal_id = position['position']['dealId']
                direction = position['position']['direction']
                
                # Reverse direction to close position
                close_direction = 'SELL' if direction == 'BUY' else 'BUY'
                size = position['position']['size']
                
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
            epic_orders = [
                order for order in working_orders 
                if order['marketData']['epic'] == self.epic
            ]
            
            cancelled_count = 0
            for order in epic_orders:
                deal_id = order['workingOrderData']['dealId']
                response = self.ig_service.delete_working_order(deal_id)
                
                if response:
                    cancelled_count += 1
                    self.logger.info(f"Cancelled order: {deal_id}")
                
                time.sleep(0.5)
            
            self.logger.info(f"Cancelled {cancelled_count} pending orders")
            return cancelled_count
            
        except Exception as e:
            self.logger.error(f"Error cancelling orders: {e}")
            return 0

    def monitor_and_close(self, check_interval=30):
        """
        Monitor positions and close when target profit is reached
        
        Args:
            check_interval: Time between checks in seconds
        """
        self.logger.info("Starting profit monitoring...")
        
        try:
            while True:
                average_profit = self.calculate_average_profit()
                self.logger.info(f"Current average profit: {average_profit:.2f}")
                
                if average_profit >= self.target_profit:
                    self.logger.info(f"Target profit reached! Closing all positions.")
                    
                    # Close positions and cancel pending orders
                    self.close_all_positions()
                    self.cancel_pending_orders()
                    
                    self.logger.info("Grid trading completed successfully!")
                    break
                
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            self.logger.info("Monitoring interrupted by user")
        except Exception as e:
            self.logger.error(f"Error during monitoring: {e}")

    def get_account_balance(self):
        """Get current account balance"""
        try:
            
            account_info =  self.ig_service.fetch_accounts()
            return float(account_info.balance[0])
        except Exception as e:
            self.logger.error(f"Error getting account balance: {e}")
            return 0.0

    def run_grid_strategy(self, epic: str, direction: str, num_orders: int, 
                         grid_distance: float, lot_size: float, target_profit: float):
        """
        Complete grid strategy execution
        
        Args:
            epic: Instrument EPIC code
            direction: 'BUY' or 'SELL'
            num_orders: Number of grid orders
            grid_distance: Distance between grid levels
            lot_size: Lot size per order
            target_profit: Target average profit
        """
        # Set parameters
        self.set_trading_parameters(epic, direction, num_orders, grid_distance, lot_size, target_profit)
        
        # Check account balance
        balance = self.get_account_balance()
        required_margin = num_orders * lot_size * 100  # Simplified margin calculation
        self.logger.info(f"Account balance: {balance}, Required margin: ~{required_margin}")
        
        if balance < required_margin:
            self.logger.warning("Insufficient balance for the grid strategy!")
            return
        
        # Create grid
        self.create_grid()
        
        # Start monitoring
        self.monitor_and_close()
