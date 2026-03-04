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

# Get balance
print('=== DEMO ACCOUNT BALANCE ===')
balance = api.get_account_balance()
if balance:
    print(f'Cash Balance: ${balance.get("cash-balance", 0):,.2f}')
    print(f'Buying Power: ${balance.get("buying-power", 0):,.2f}')
    print(f'Equity: ${balance.get("net-liquidation", 0):,.2f}')
    print(f'Account Number: {api.account_number}')
else:
    print('❌ Failed to get account balance')
