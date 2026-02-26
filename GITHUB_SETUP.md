# GitHub Repository Setup Guide

## Step 1: Create GitHub Repository

1. Go to https://github.com and sign in
2. Click the "+" icon → "New repository"
3. Repository name: `apex-mnq-strategy` or your preferred name
4. Description: "APEX MNQ Futures Trading Strategy with Webull API Integration"
5. Choose "Private" or "Public" (Private recommended for trading strategies)
6. **DO NOT** initialize with README, .gitignore, or license (we already have these)
7. Click "Create repository"

## Step 2: Connect Local Repository to GitHub

After creating the repository, GitHub will show you commands. Run these in your terminal:

```bash
# Add the remote repository (replace with your actual repository URL)
git remote add origin https://github.com/YOUR_USERNAME/apex-mnq-strategy.git

# Push your code to GitHub
git push -u origin main
```

## Step 3: Verify Upload

1. Refresh your GitHub repository page
2. You should see all your files uploaded
3. The repository should have:
   - backtest_nq.py (main trading script)
   - webull_api.py (API integration)
   - README.md (documentation)
   - requirements.txt (dependencies)
   - .env.example (environment template)
   - setup.sh (installation script)

## Step 4: Set Up GitHub Actions (Optional)

For automated testing, you can add GitHub Actions:

1. In your repository, go to "Actions" tab
2. Click "set up a workflow yourself"
3. Use this basic Python test workflow:

```yaml
name: Python Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.9'
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
    - name: Test compilation
      run: |
        python -m py_compile backtest_nq.py
        python -m py_compile webull_api.py
```

## Step 5: Clone to iTerm/Terminal

To work with the repository in iTerm or any terminal:

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/apex-mnq-strategy.git
cd apex-mnq-strategy

# Run setup
./setup.sh

# Run backtest
python3 backtest_nq.py

# Run live trading (after setting up .env)
python3 backtest_nq.py --live --demo
```

## Security Notes

- **Never commit your .env file** (it's in .gitignore)
- Keep your API credentials secure
- Consider using GitHub secrets for any CI/CD
- Use private repositories for trading strategies

## Branching Strategy

- `main`: Production-ready code
- `develop`: Development branch
- Feature branches: `feature/webull-integration`, `feature/risk-management`, etc.

## Contributing

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make changes and test
3. Commit: `git commit -m "Add your feature"`
4. Push: `git push origin feature/your-feature`
5. Create Pull Request on GitHub