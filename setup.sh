#!/bin/bash
# Setup script for APEX MNQ Strategy Project

echo "🚀 Setting up APEX MNQ Strategy Project"
echo "======================================"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8+ first."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Create virtual environment
echo "📦 Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚙️  Creating .env file from template..."
    cp .env.example .env
    echo "⚠️  Please edit .env file with your Webull API credentials!"
else
    echo "✅ .env file already exists"
fi

# Test backtest script
echo "🧪 Testing backtest script..."
python3 -m py_compile backtest_nq.py
if [ $? -eq 0 ]; then
    echo "✅ Backtest script compiled successfully"
else
    echo "❌ Backtest script has syntax errors"
    exit 1
fi

# Test Webull API script
echo "🧪 Testing Webull API script..."
python3 -m py_compile webull_api.py
if [ $? -eq 0 ]; then
    echo "✅ Webull API script compiled successfully"
else
    echo "❌ Webull API script has syntax errors"
    exit 1
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your Webull API credentials"
echo "2. Run backtest: python3 backtest_nq.py"
echo "3. For live trading: python3 backtest_nq.py --live --demo"
echo ""
echo "For more information, see README.md"