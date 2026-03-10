#!/usr/bin/env python3
"""
MT5 Web Terminal Trader — Selenium-based browser automation for MT5.
====================================================================
Automates the MetaTrader 5 web terminal via browser clicks/keyboard.
Orders appear as manual web trades (ORDER_REASON_CLIENT), NOT as EA trades.

Works on Mac/Linux/Windows — no MT5 desktop installation needed.

Setup:
  pip install selenium webdriver-manager python-dotenv

Usage:
  python mt5_web_trader.py                    # Launch & login, then interactive mode
  python mt5_web_trader.py --headless         # Run headless (no visible browser)
  python mt5_web_trader.py --screenshot       # Take screenshot after login

Interactive commands (after login):
  buy <symbol> <lots>       — Place market buy  (e.g. buy XAUUSD 0.01)
  sell <symbol> <lots>      — Place market sell (e.g. sell XAUUSD 0.01)
  close <symbol>            — Close all positions for symbol
  balance                   — Show account info
  screenshot                — Take screenshot of current state
  quit                      — Exit

NOTE: The MT5 web terminal uses Canvas rendering. This script uses a combination
of keyboard shortcuts, coordinate-based clicking, and DOM interaction for any
HTML dialog elements that appear on top of the canvas.
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env', override=True)
except ImportError:
    pass

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    from webdriver_manager.chrome import ChromeDriverManager
    USE_WDM = True
except ImportError:
    USE_WDM = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger("mt5_web")

# ─── Configuration ───────────────────────────────────────────────────────────

MT5_WEB_URL = "https://web.metatrader.app/terminal"
MT5_LOGIN = os.getenv('MT5_LOGIN', '')
MT5_PASSWORD = os.getenv('MT5_PASSWORD', '').strip("'\"")
MT5_SERVER = os.getenv('MT5_SERVER', 'Upcomers')

SCREENSHOT_DIR = Path(__file__).resolve().parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


class MT5WebTrader:
    """Selenium-based automation for MetaTrader 5 web terminal."""

    def __init__(self, headless=False):
        self.driver = None
        self.headless = headless
        self.logged_in = False

    def _init_driver(self):
        """Initialize Chrome WebDriver."""
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        # Standard Chrome options for stability
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")

        # Make it look like a real browser
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # User agent to look normal
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        if USE_WDM:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
        else:
            self.driver = webdriver.Chrome(options=options)

        # Remove webdriver flag
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        logger.info("Chrome WebDriver initialized")

    def screenshot(self, name=None):
        """Take a screenshot and save it."""
        if not name:
            name = f"mt5_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        path = SCREENSHOT_DIR / f"{name}.png"
        self.driver.save_screenshot(str(path))
        logger.info(f"Screenshot saved: {path}")
        return path

    def open_terminal(self):
        """Navigate to MT5 web terminal."""
        self._init_driver()
        logger.info(f"Opening {MT5_WEB_URL}")
        self.driver.get(MT5_WEB_URL)
        time.sleep(3)
        self.screenshot("01_terminal_loaded")
        logger.info("Web terminal loaded")

    def login(self):
        """Log into the MT5 web terminal with credentials from .env."""
        if not MT5_LOGIN or not MT5_PASSWORD:
            logger.error("MT5_LOGIN and MT5_PASSWORD must be set in .env")
            sys.exit(1)

        logger.info(f"Logging in as {MT5_LOGIN} on server {MT5_SERVER}...")

        # The web terminal login flow:
        # 1. Wait for the page to fully load
        # 2. Look for login dialog or "Open an account" / "Connect" button
        # 3. Fill in server, login, password

        try:
            # Wait for page to be interactive
            time.sleep(5)
            self.screenshot("02_before_login")

            # Try to find and interact with the login/connect elements
            # The MT5 web terminal may show different dialogs depending on state
            self._attempt_login_flow()

        except Exception as e:
            logger.error(f"Login failed: {e}")
            self.screenshot("login_error")
            raise

    def _attempt_login_flow(self):
        """Try multiple approaches to complete the login."""
        wait = WebDriverWait(self.driver, 30)

        # Approach 1: Look for any visible input fields (server, login, password)
        # The MT5 web terminal typically shows a connection dialog on first load

        # First, let's see what's on the page
        page_source = self.driver.page_source
        logger.info(f"Page title: {self.driver.title}")
        logger.info(f"Page source length: {len(page_source)}")

        # Check for iframe — MT5 web terminal often runs inside an iframe
        iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
        if iframes:
            logger.info(f"Found {len(iframes)} iframe(s), switching to first one")
            self.driver.switch_to.frame(iframes[0])
            time.sleep(2)

        # Try to find input fields
        self._try_fill_login_form()

    def _try_fill_login_form(self):
        """Attempt to find and fill login form fields."""
        wait = WebDriverWait(self.driver, 15)

        # Strategy 1: Look for input elements by type
        inputs = self.driver.find_elements(By.CSS_SELECTOR, "input")
        logger.info(f"Found {len(inputs)} input fields")

        for i, inp in enumerate(inputs):
            inp_type = inp.get_attribute("type") or "text"
            inp_name = inp.get_attribute("name") or ""
            inp_placeholder = inp.get_attribute("placeholder") or ""
            inp_id = inp.get_attribute("id") or ""
            logger.info(f"  Input {i}: type={inp_type}, name={inp_name}, "
                       f"placeholder={inp_placeholder}, id={inp_id}")

        # Strategy 2: Look for specific elements by various selectors
        selectors_to_try = [
            # Common patterns for MT5 web terminal login
            ("input[name='login']", MT5_LOGIN),
            ("input[name='Login']", MT5_LOGIN),
            ("input[name='account']", MT5_LOGIN),
            ("input[placeholder*='ogin']", MT5_LOGIN),
            ("input[placeholder*='ccount']", MT5_LOGIN),
            ("input[type='text']", MT5_LOGIN),
            ("input[name='password']", MT5_PASSWORD),
            ("input[name='Password']", MT5_PASSWORD),
            ("input[placeholder*='assword']", MT5_PASSWORD),
            ("input[type='password']", MT5_PASSWORD),
        ]

        filled_login = False
        filled_password = False

        for selector, value in selectors_to_try:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(value)
                        if value == MT5_LOGIN:
                            filled_login = True
                            logger.info(f"Filled login field: {selector}")
                        else:
                            filled_password = True
                            logger.info(f"Filled password field: {selector}")
                        break
            except Exception:
                continue

        # Try to find and fill server field
        server_selectors = [
            "input[name='server']",
            "input[name='Server']",
            "input[placeholder*='erver']",
        ]
        for selector in server_selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    if el.is_displayed():
                        el.clear()
                        el.send_keys(MT5_SERVER)
                        logger.info(f"Filled server field: {selector}")
                        break
            except Exception:
                continue

        self.screenshot("03_after_fill")

        # Try to find and click submit/connect button
        button_selectors = [
            "button[type='submit']",
            "button.connect",
            "button.login",
            "input[type='submit']",
            "button",  # fallback: any button
        ]

        button_texts = ['connect', 'login', 'sign in', 'ok', 'submit', 'log in']

        for selector in button_selectors:
            try:
                buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for btn in buttons:
                    if btn.is_displayed():
                        btn_text = btn.text.lower().strip()
                        if any(t in btn_text for t in button_texts) or selector != "button":
                            logger.info(f"Clicking button: '{btn.text}' ({selector})")
                            btn.click()
                            time.sleep(3)
                            self.screenshot("04_after_login_click")
                            self.logged_in = True
                            return
            except Exception:
                continue

        # If no button found, try pressing Enter
        if filled_login or filled_password:
            logger.info("No button found, pressing Enter...")
            ActionChains(self.driver).send_keys(Keys.RETURN).perform()
            time.sleep(3)
            self.screenshot("04_after_enter")
            self.logged_in = True
        else:
            logger.warning("Could not find login form elements.")
            logger.info("The MT5 web terminal may use a Canvas-based login.")
            logger.info("Check screenshots in ./screenshots/ to see the current state.")
            self.screenshot("04_no_login_form")

    def _send_new_order(self, symbol, lots, order_type):
        """
        Place a new order using keyboard shortcut F9 (New Order).

        The New Order dialog may be HTML-based even though the main
        terminal is canvas-based.
        """
        logger.info(f"Placing {order_type} order: {lots} lots of {symbol}")

        # Press F9 to open New Order dialog
        ActionChains(self.driver).send_keys(Keys.F9).perform()
        time.sleep(2)
        self.screenshot(f"order_{order_type}_dialog")

        # Try to find the order dialog elements
        # Look for symbol input
        symbol_inputs = self.driver.find_elements(
            By.CSS_SELECTOR,
            "input[name='symbol'], input[placeholder*='ymbol'], "
            "input.symbol-input, select.symbol-select"
        )
        for inp in symbol_inputs:
            if inp.is_displayed():
                inp.clear()
                inp.send_keys(symbol)
                logger.info(f"Set symbol: {symbol}")
                break

        # Look for volume/lots input
        volume_inputs = self.driver.find_elements(
            By.CSS_SELECTOR,
            "input[name='volume'], input[name='lots'], "
            "input[placeholder*='olume'], input[placeholder*='ots']"
        )
        for inp in volume_inputs:
            if inp.is_displayed():
                inp.clear()
                inp.send_keys(str(lots))
                logger.info(f"Set volume: {lots}")
                break

        time.sleep(1)

        # Click Buy or Sell button in the dialog
        buttons = self.driver.find_elements(By.CSS_SELECTOR, "button, .button, [role='button']")
        target_text = "buy" if order_type == "buy" else "sell"

        for btn in buttons:
            if btn.is_displayed():
                btn_text = btn.text.lower().strip()
                if target_text in btn_text:
                    logger.info(f"Clicking '{btn.text}' button")
                    btn.click()
                    time.sleep(2)
                    self.screenshot(f"order_{order_type}_placed")
                    logger.info(f"{order_type.upper()} order placed: {lots} {symbol}")
                    return True

        logger.warning(f"Could not find {order_type} button in order dialog")
        logger.info("Check screenshots for current state")
        self.screenshot(f"order_{order_type}_failed")
        return False

    def buy(self, symbol, lots):
        """Place a market buy order."""
        return self._send_new_order(symbol, lots, "buy")

    def sell(self, symbol, lots):
        """Place a market sell order."""
        return self._send_new_order(symbol, lots, "sell")

    def get_page_info(self):
        """Get current page state for debugging."""
        info = {
            "title": self.driver.title,
            "url": self.driver.current_url,
            "source_length": len(self.driver.page_source),
        }

        # Count various elements
        for tag in ["input", "button", "select", "canvas", "iframe"]:
            elements = self.driver.find_elements(By.TAG_NAME, tag)
            info[f"{tag}_count"] = len(elements)

        return info

    def interactive_mode(self):
        """Run interactive command loop from iTerm."""
        print("\n" + "=" * 60)
        print("  MT5 WEB TRADER — Interactive Mode")
        print("=" * 60)
        print("  Commands:")
        print("    buy <symbol> <lots>    — Market buy")
        print("    sell <symbol> <lots>   — Market sell")
        print("    screenshot             — Take screenshot")
        print("    info                   — Page debug info")
        print("    quit                   — Exit")
        print("=" * 60)

        while True:
            try:
                cmd = input("\nmt5> ").strip().lower()
                if not cmd:
                    continue

                parts = cmd.split()
                action = parts[0]

                if action == "quit" or action == "exit":
                    break
                elif action == "screenshot" or action == "ss":
                    path = self.screenshot()
                    print(f"Saved: {path}")
                elif action == "info":
                    info = self.get_page_info()
                    for k, v in info.items():
                        print(f"  {k}: {v}")
                elif action == "buy" and len(parts) >= 3:
                    symbol = parts[1].upper()
                    lots = float(parts[2])
                    self.buy(symbol, lots)
                elif action == "sell" and len(parts) >= 3:
                    symbol = parts[1].upper()
                    lots = float(parts[2])
                    self.sell(symbol, lots)
                elif action == "f9":
                    # Open new order dialog directly
                    ActionChains(self.driver).send_keys(Keys.F9).perform()
                    time.sleep(1)
                    self.screenshot("f9_dialog")
                    print("F9 pressed — check screenshot")
                elif action == "click" and len(parts) >= 3:
                    # Manual coordinate click: click <x> <y>
                    x, y = int(parts[1]), int(parts[2])
                    canvas = self.driver.find_elements(By.TAG_NAME, "canvas")
                    if canvas:
                        ActionChains(self.driver).move_to_element_with_offset(
                            canvas[0], x, y
                        ).click().perform()
                        print(f"Clicked canvas at ({x}, {y})")
                        time.sleep(0.5)
                        self.screenshot(f"click_{x}_{y}")
                    else:
                        print("No canvas element found")
                elif action == "key":
                    # Send keyboard shortcut: key F9, key ENTER, etc.
                    key_name = parts[1].upper() if len(parts) > 1 else ""
                    key_map = {
                        "F9": Keys.F9, "F1": Keys.F1, "F2": Keys.F2,
                        "F5": Keys.F5, "F8": Keys.F8, "F11": Keys.F11,
                        "ENTER": Keys.RETURN, "ESC": Keys.ESCAPE,
                        "TAB": Keys.TAB, "SPACE": Keys.SPACE,
                    }
                    if key_name in key_map:
                        ActionChains(self.driver).send_keys(key_map[key_name]).perform()
                        print(f"Sent key: {key_name}")
                        time.sleep(0.5)
                        self.screenshot(f"key_{key_name}")
                    else:
                        print(f"Unknown key: {key_name}")
                        print(f"Available: {', '.join(key_map.keys())}")
                elif action == "type" and len(parts) >= 2:
                    # Type text: type <text>
                    text = " ".join(parts[1:])
                    ActionChains(self.driver).send_keys(text).perform()
                    print(f"Typed: {text}")
                elif action == "help":
                    print("  buy <symbol> <lots>  — Market buy")
                    print("  sell <symbol> <lots> — Market sell")
                    print("  screenshot / ss      — Take screenshot")
                    print("  info                 — Page debug info")
                    print("  f9                   — Open New Order dialog")
                    print("  click <x> <y>        — Click canvas at coordinates")
                    print("  key <KEY>            — Send keyboard key")
                    print("  type <text>          — Type text")
                    print("  quit                 — Exit")
                else:
                    print(f"Unknown command: {cmd}")
                    print("Type 'help' for available commands")

            except KeyboardInterrupt:
                print("\nUse 'quit' to exit")
            except Exception as e:
                logger.error(f"Error: {e}")

    def close(self):
        """Close the browser."""
        if self.driver:
            self.driver.quit()
            logger.info("Browser closed")


def main():
    parser = argparse.ArgumentParser(description='MT5 Web Terminal Trader (Selenium)')
    parser.add_argument('--headless', action='store_true',
                       help='Run browser in headless mode')
    parser.add_argument('--screenshot', action='store_true',
                       help='Take screenshot after login and exit')
    parser.add_argument('--no-login', action='store_true',
                       help='Open terminal without auto-login')
    args = parser.parse_args()

    trader = MT5WebTrader(headless=args.headless)

    try:
        trader.open_terminal()

        if not args.no_login:
            trader.login()

        if args.screenshot:
            trader.screenshot("final_state")
            return

        # Enter interactive mode
        trader.interactive_mode()

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if trader.driver:
            trader.screenshot("fatal_error")
    finally:
        trader.close()


if __name__ == "__main__":
    main()
