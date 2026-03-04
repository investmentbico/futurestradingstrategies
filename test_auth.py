import os
from tastytrade_api import TastytradeAPI

# Load credentials from .env
with open('.env', 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            parts = line.strip().split('=', 1)
            if len(parts) == 2:
                key, value = parts
                os.environ[key] = value

# Initialize API
api = TastytradeAPI()

# Get all accounts
accounts = api.get_accounts()
if not accounts:
    print("❌ No accounts found or failed to retrieve accounts.")
else:
    print('=== AVAILABLE ACCOUNTS & BALANCES ===')
    for account in accounts:
        account_number = account.get("account-number")
        if account_number:
            print(f'Account ID: {account_number}')
            print(f'Account Type: {account.get("account-type", "Unknown")}')
            
            # Get balance for each account
            balance = api.get_account_balance(account_number)
            if balance:
                print(f'  Cash Balance: ${balance.get("cash-balance", 0):,.2f}')
                print(f'  Buying Power: ${balance.get("buying-power", 0):,.2f}')
                print(f'  Equity: ${balance.get("net-liquidation", 0):,.2f}')
            else:
                print(f"  Could not retrieve balance for account {account_number}")
            print('---')
