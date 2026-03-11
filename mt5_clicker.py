#!/usr/bin/env python3
"""
MT5 Desktop Clicker — PyAutoGUI screen automation for MetaTrader 5.
====================================================================
Clicks directly on the MT5 desktop app window. No browser, no Selenium.
Just screen coordinates — like your mouse clicking the buttons.

All trades register as ORDER_REASON_CLIENT (manual clicks).

Setup:
  pip install pyautogui pillow

Usage:
  python mt5_clicker.py                   # Interactive mode
  python mt5_clicker.py calibrate         # Calibrate button positions
  python mt5_clicker.py buy               # Quick buy at saved coordinates
  python mt5_clicker.py sell              # Quick sell at saved coordinates
  python mt5_clicker.py close             # Quick close position

Interactive commands:
  buy                — Click saved BUY button coordinate
  sell               — Click saved SELL button coordinate
  close              — Click saved CLOSE button coordinate
  set buy <x> <y>   — Save BUY button coordinate
  set sell <x> <y>  — Save SELL button coordinate
  set close <x> <y> — Save CLOSE button coordinate
  click <x> <y>     — Click any screen coordinate
  pos                — Show current mouse position (5 sec to move mouse)
  find               — Find mouse position in real-time (move mouse, press Ctrl+C)
  screenshot         — Take screenshot of full screen
  calibrate          — Guided setup of all button positions
  show               — Show saved coordinates
  quit               — Exit
"""

import os
import sys
import time
import json
import logging
from pathlib import Path
from datetime import datetime

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # Move mouse to corner to abort
    pyautogui.PAUSE = 0.1      # Small pause between actions
except ImportError:
    print("ERROR: pyautogui not installed. Run:")
    print("  pip install pyautogui pillow")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger("mt5_click")

# ─── Paths ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "mt5_coordinates.json"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ─── Default coordinate config ───────────────────────────────────────────────

DEFAULT_CONFIG = {
    "buy": {"x": 0, "y": 0, "label": "BUY button (One-Click Trading panel)"},
    "sell": {"x": 0, "y": 0, "label": "SELL button (One-Click Trading panel)"},
    "close": {"x": 0, "y": 0, "label": "CLOSE position button"},
    "volume_up": {"x": 0, "y": 0, "label": "Volume UP arrow"},
    "volume_down": {"x": 0, "y": 0, "label": "Volume DOWN arrow"},
    "volume_field": {"x": 0, "y": 0, "label": "Volume input field"},
    "new_order": {"x": 0, "y": 0, "label": "New Order button (toolbar)"},
    "close_dialog_confirm": {"x": 0, "y": 0, "label": "Close confirmation OK button"},
}


class MT5Clicker:
    """Screen coordinate clicker for MetaTrader 5 desktop app."""

    def __init__(self):
        self.config = self._load_config()
        screen = pyautogui.size()
        log.info(f"Screen size: {screen.width}x{screen.height}")

    def _load_config(self):
        """Load saved coordinates from JSON file."""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            # Merge with defaults for any new keys
            merged = {**DEFAULT_CONFIG, **saved}
            return merged
        return DEFAULT_CONFIG.copy()

    def _save_config(self):
        """Save coordinates to JSON file."""
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2)
        log.info(f"Coordinates saved to {CONFIG_FILE}")

    def set_coord(self, name, x, y):
        """Set a named coordinate."""
        if name in self.config:
            self.config[name]["x"] = x
            self.config[name]["y"] = y
        else:
            self.config[name] = {"x": x, "y": y, "label": name}
        self._save_config()
        log.info(f"Set {name} = ({x}, {y})")

    def click(self, x, y, clicks=1):
        """Click at screen coordinates."""
        log.info(f"Clicking ({x}, {y})")
        pyautogui.click(x, y, clicks=clicks)

    def click_named(self, name):
        """Click a saved named coordinate."""
        if name not in self.config:
            log.error(f"Unknown button: {name}")
            return False
        coord = self.config[name]
        if coord["x"] == 0 and coord["y"] == 0:
            log.error(f"Button '{name}' not calibrated yet! Use: set {name} <x> <y>")
            return False
        log.info(f"Clicking {name} at ({coord['x']}, {coord['y']})")
        pyautogui.click(coord["x"], coord["y"])
        return True

    def buy(self):
        """Click the BUY button."""
        if self.click_named("buy"):
            log.info("BUY clicked")
            return True
        return False

    def sell(self):
        """Click the SELL button."""
        if self.click_named("sell"):
            log.info("SELL clicked")
            return True
        return False

    def close_position(self):
        """Click the CLOSE button (and confirm if needed)."""
        if self.click_named("close"):
            log.info("CLOSE clicked")
            # If there's a confirmation dialog, click OK after a short delay
            confirm = self.config.get("close_dialog_confirm", {})
            if confirm.get("x", 0) != 0:
                time.sleep(0.5)
                self.click_named("close_dialog_confirm")
                log.info("Close confirmation clicked")
            return True
        return False

    def set_volume(self, volume):
        """Click the volume field and type the volume."""
        coord = self.config.get("volume_field", {})
        if coord.get("x", 0) == 0:
            log.error("Volume field not calibrated! Use: set volume_field <x> <y>")
            return False
        # Triple-click to select all text in field, then type new value
        pyautogui.click(coord["x"], coord["y"], clicks=3)
        time.sleep(0.1)
        pyautogui.typewrite(str(volume), interval=0.05)
        log.info(f"Volume set to {volume}")
        return True

    def screenshot(self, name=None):
        """Take a full screen screenshot."""
        if not name:
            name = f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        path = SCREENSHOT_DIR / f"{name}.png"
        img = pyautogui.screenshot()
        img.save(str(path))
        log.info(f"Screenshot saved: {path}")
        return path

    def get_mouse_pos(self):
        """Get current mouse position."""
        pos = pyautogui.position()
        return pos.x, pos.y

    def find_position(self):
        """Track mouse position in real-time. Press Ctrl+C to stop."""
        print("\n  Move your mouse to the target button.")
        print("  Press Ctrl+C when the cursor is on the button.\n")
        try:
            while True:
                x, y = pyautogui.position()
                print(f"\r  Mouse: ({x}, {y})    ", end="", flush=True)
                time.sleep(0.1)
        except KeyboardInterrupt:
            x, y = pyautogui.position()
            print(f"\n  Captured: ({x}, {y})")
            return x, y

    def calibrate(self):
        """Guided calibration of all button positions."""
        print("\n" + "=" * 60)
        print("  MT5 BUTTON CALIBRATION")
        print("=" * 60)
        print("\n  Make sure MT5 is open and visible on screen.")
        print("  For each button, move your mouse to it and press Enter.")
        print("  Press 's' to skip a button.\n")

        buttons = ["buy", "sell", "close", "volume_field",
                   "new_order", "close_dialog_confirm"]

        for name in buttons:
            label = self.config.get(name, {}).get("label", name)
            current = self.config.get(name, {})
            cx, cy = current.get("x", 0), current.get("y", 0)

            if cx != 0:
                print(f"  {label} — current: ({cx}, {cy})")
            else:
                print(f"  {label} — not set")

            resp = input(f"  Hover mouse over [{name}] and press Enter (or 's' to skip): ").strip()
            if resp.lower() == 's':
                print(f"  Skipped {name}")
                continue

            x, y = pyautogui.position()
            self.set_coord(name, x, y)
            print(f"  Saved {name} = ({x}, {y})\n")

        print("\n  Calibration complete!")
        self.show_config()

    def show_config(self):
        """Display all saved coordinates."""
        print("\n  Saved coordinates:")
        print("  " + "-" * 50)
        for name, coord in self.config.items():
            label = coord.get("label", name)
            x, y = coord.get("x", 0), coord.get("y", 0)
            status = f"({x}, {y})" if x != 0 or y != 0 else "(not set)"
            print(f"  {name:25s} {status:15s}  {label}")
        print()

    def interactive(self):
        """Interactive command loop from iTerm."""
        print("\n" + "=" * 60)
        print("  MT5 DESKTOP CLICKER — Interactive Mode")
        print("=" * 60)
        print("  Commands:")
        print("    buy / sell / close      — Click saved button")
        print("    set <name> <x> <y>      — Save button coordinate")
        print("    click <x> <y>           — Click any coordinate")
        print("    pos                     — Show mouse position (5s)")
        print("    find                    — Track mouse (Ctrl+C to capture)")
        print("    calibrate               — Guided button setup")
        print("    vol <amount>            — Set volume (e.g. vol 0.01)")
        print("    show                    — Show saved coordinates")
        print("    screenshot / ss         — Take screenshot")
        print("    quit                    — Exit")
        print("=" * 60)
        print("  TIP: Move mouse to corner to emergency-stop (failsafe)")
        print()

        while True:
            try:
                cmd = input("mt5> ").strip()
                if not cmd:
                    continue

                parts = cmd.split()
                action = parts[0].lower()

                if action in ("quit", "exit", "q"):
                    break

                elif action == "buy":
                    self.buy()

                elif action == "sell":
                    self.sell()

                elif action == "close":
                    self.close_position()

                elif action == "set" and len(parts) >= 4:
                    name = parts[1].lower()
                    x, y = int(parts[2]), int(parts[3])
                    self.set_coord(name, x, y)

                elif action == "click" and len(parts) >= 3:
                    x, y = int(parts[1]), int(parts[2])
                    self.click(x, y)
                    print(f"  Clicked ({x}, {y})")

                elif action == "dclick" and len(parts) >= 3:
                    x, y = int(parts[1]), int(parts[2])
                    self.click(x, y, clicks=2)
                    print(f"  Double-clicked ({x}, {y})")

                elif action == "pos":
                    print("  Move mouse to target... (5 seconds)")
                    time.sleep(5)
                    x, y = self.get_mouse_pos()
                    print(f"  Mouse position: ({x}, {y})")

                elif action == "find":
                    self.find_position()

                elif action == "calibrate":
                    self.calibrate()

                elif action == "show":
                    self.show_config()

                elif action == "vol" and len(parts) >= 2:
                    self.set_volume(parts[1])

                elif action in ("screenshot", "ss"):
                    path = self.screenshot()
                    print(f"  Saved: {path}")

                elif action == "type" and len(parts) >= 2:
                    text = " ".join(parts[1:])
                    pyautogui.typewrite(text, interval=0.05)
                    print(f"  Typed: {text}")

                elif action == "hotkey" and len(parts) >= 2:
                    # e.g. hotkey ctrl c, hotkey f9
                    keys = [k.lower() for k in parts[1:]]
                    pyautogui.hotkey(*keys)
                    print(f"  Hotkey: {'+'.join(keys)}")

                elif action == "wait" and len(parts) >= 2:
                    secs = float(parts[1])
                    print(f"  Waiting {secs}s...")
                    time.sleep(secs)

                elif action == "help":
                    print("  buy                     — Click BUY button")
                    print("  sell                    — Click SELL button")
                    print("  close                   — Click CLOSE button")
                    print("  set <name> <x> <y>      — Save coordinate")
                    print("  click <x> <y>           — Click coordinate")
                    print("  dclick <x> <y>          — Double-click coordinate")
                    print("  pos                     — Mouse position (5s delay)")
                    print("  find                    — Track mouse live")
                    print("  calibrate               — Guided button setup")
                    print("  vol <amount>            — Set lot size")
                    print("  show                    — Show all coordinates")
                    print("  screenshot / ss         — Screenshot")
                    print("  type <text>             — Type text")
                    print("  hotkey <key1> <key2>    — Send hotkey (e.g. hotkey f9)")
                    print("  wait <seconds>          — Pause")
                    print("  quit                    — Exit")

                else:
                    print(f"  Unknown: {cmd}  (type 'help')")

            except KeyboardInterrupt:
                print("\n  Use 'quit' to exit")
            except pyautogui.FailSafeException:
                print("\n  FAILSAFE triggered! (mouse moved to corner)")
                print("  This is a safety feature. Resuming...")
            except Exception as e:
                log.error(f"Error: {e}")


def main():
    clicker = MT5Clicker()

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "calibrate":
            clicker.calibrate()
        elif cmd == "buy":
            clicker.buy()
        elif cmd == "sell":
            clicker.sell()
        elif cmd == "close":
            clicker.close_position()
        elif cmd == "screenshot":
            clicker.screenshot()
        elif cmd == "show":
            clicker.show_config()
        elif cmd == "pos":
            x, y = pyautogui.position()
            print(f"({x}, {y})")
        else:
            print(f"Unknown command: {cmd}")
            print("Commands: calibrate, buy, sell, close, screenshot, show, pos")
    else:
        clicker.interactive()


if __name__ == "__main__":
    main()
