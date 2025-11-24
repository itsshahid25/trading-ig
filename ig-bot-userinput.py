from trading_ig_grid_trader import IGGridTrader

def get_user_input():
    """Get trading parameters from user"""
    print("=== IG Grid Trading System ===")
    
    # Trading parameters
    epic = input("Enter EPIC code (e.g., CS.D.EURUSD.MINI.IP): ").strip()
    direction = input("Enter direction (BUY/SELL): ").strip().upper()
    num_orders = int(input("Number of grid orders: "))
    grid_distance = float(input("Grid distance in points: "))
    lot_size = float(input("Lot size per order: "))
    target_profit = float(input("Target average profit: "))
    
    return epic, direction, num_orders, grid_distance, lot_size, target_profit

def main():
    """Main execution function"""
    
    # IG API credentials (store these securely!)
    API_KEY = "fa51b09392954a22298b67d2dfd765cfeac8fe26"
    USERNAME = "itsshahid25"
    PASSWORD = "S621541@74i"
    ACC_TYPE = "Demo"
    
    try:
        # Initialize grid trader
        trader = IGGridTrader(
            api_key=API_KEY,
            username=USERNAME,
            password=PASSWORD,
            acc_type = ACC_TYPE
            #demo=True  # Set to False for live trading
        )
        
        # Get user parameters
        epic, direction, num_orders, grid_distance, lot_size, target_profit = get_user_input()
        
        # Confirm parameters
        print(f"\n=== Strategy Summary ===")
        print(f"EPIC: {epic}")
        print(f"Direction: {direction}")
        print(f"Number of orders: {num_orders}")
        print(f"Grid distance: {grid_distance}")
        print(f"Lot size: {lot_size}")
        print(f"Target profit: {target_profit}")
        
        confirm = input("\nProceed with strategy? (y/n): ").strip().lower()
        
        if confirm == 'y':
            # Run the grid strategy
            trader.run_grid_strategy(
                epic=epic,
                direction=direction,
                num_orders=num_orders,
                grid_distance=grid_distance,
                lot_size=lot_size,
                target_profit=target_profit
            )
        else:
            print("Strategy cancelled.")
            
    except Exception as e:
        print(f"Error: {e}")

# Example usage with predefined parameters
def example_usage():
    """Example of how to use the grid trader"""
    
    API_KEY = "your_api_key"
    USERNAME = "your_username" 
    PASSWORD = "your_password"
    ACCOUNT_TYPE = "your_account_id"
    
    trader = IGGridTrader(API_KEY, USERNAME, PASSWORD, ACCOUNT_TYPE)
    
    # Run EUR/USD buy grid strategy
    trader.run_grid_strategy(
        epic="CS.D.EURUSD.MINI.IP",
        direction="BUY",
        num_orders=5,
        grid_distance=10.0,  # 10 points
        lot_size=0.5,       # 0.5 lots per order
        target_profit=25.0  # 25 points average profit
    )

if __name__ == "__main__":
    main()