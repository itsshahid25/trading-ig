from trading_ig import IGService
from trading_ig import config


def get_ig_account_info():
    """
    Helper function to retrieve your IG account details
    You only need to run this once to get your account information
    """
    
    
    # Your main IG credentials (from the IG dashboard)
    API_KEY = "fa51b09392954a22298b67d2dfd765cfeac8fe26"
    USERNAME = "itsshahid25"
    PASSWORD = "S621541@74i"
    ACC_type = "Demo"
    acc_number = "Z63VU2"
    
    try:
        # Create IG service without account ID first
        ig_service = IGService(USERNAME, PASSWORD, API_KEY, ACC_type)
        ig_service.create_session()
        
        # Get account information
        accounts = ig_service.fetch_accounts()
        print("\n=== Your Account Details ===")
        print(f"Available accounts: {len(accounts)}")
        
        for i, account in enumerate(accounts):
            print(f"\nAccount {i+1}:")
            print(f"  Account ID: {account['accountId']}")
            print(f"  Account Name: {account['accountName']}")
            print(f"  Account Type: {account['accountType']}")
            print(f"  Currency: {account['currency']}")
            print(f"  Balance: {account['balance']['balance']}")
            print(f"  Available: {account['balance']['available']}")
            print(f"  Deposit: {account['balance']['deposit']}")
        
        # Get client details
        client_details = ig_service.fetch_client_account_details()
        print(f"\n=== Client Details ===")
        print(f"Client ID: {client_details['clientId']}")
        print(f"Timezone: {client_details['timezone']}")
        print(f"Locale: {client_details['locale']}")
        print(f"Currency: {client_details['currency']}")
        
        return accounts
        
    except Exception as e:
        print(f"Error getting account info: {e}")
        return None

# Run this first to get your account details
# get_ig_account_info()