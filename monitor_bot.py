#!/usr/bin/env python3
"""
FREE Multi-Asset Price Monitor Bot
Tracks crypto, stocks, and metals with intelligent alerts.
Zero cost - uses only free APIs and notification channels.
"""

import json
import time
import logging
import schedule
import requests
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

# ====================== CONFIGURATION ======================
# Paste your config between the triple quotes OR load from file
CONFIG_JSON = """
{
  "assets": [
    {
      "type": "crypto",
      "name": "Bitcoin",
      "symbol": "BTC",
      "targets": [50000.0, 52000.0],
      "threshold_percent": 0.05,
      "allow_repeat_alerts": false
    },
    {
      "type": "stock",
      "name": "Apple Inc.",
      "symbol": "AAPL",
      "targets": [190.50],
      "threshold_percent": 0.1,
      "allow_repeat_alerts": false
    },
    {
      "type": "metal",
      "name": "Gold",
      "symbol": "GC=F",
      "metal_type": "gold",
      "targets": [2650.0],
      "threshold_percent": 0.03,
      "allow_repeat_alerts": false
    }
  ],
  "polling_intervals_sec": {
    "crypto": 30,
    "stock": 60,
    "metal": 60
  },
  "notification_channels": [
    {
      "type": "telegram",
      "bot_token": "YOUR_TELEGRAM_BOT_TOKEN_HERE",
      "chat_id": "YOUR_TELEGRAM_CHAT_ID_HERE"
    }
  ],
  "alert_cooldown_sec": 300
}
"""

# ====================== CORE ALERT ENGINE ======================
@dataclass
class AlertState:
    """Tracks price zone transitions to prevent spam alerts"""
    last_in_zone: bool = False
    last_alert_time: Optional[datetime] = None

class AlertEngine:
    def __init__(self, assets: list, cooldown_sec: int = 300):
        self.assets = assets
        self.cooldown_sec = cooldown_sec
        self.states: Dict[Tuple[str, float], AlertState] = {}
        
        # Initialize state for all asset/target combinations
        for asset in assets:
            for target in asset['targets']:
                key = (asset['symbol'], target)
                self.states[key] = AlertState(last_in_zone=False)
    
    def should_alert(self, symbol: str, target: float, 
                    current_price: float, threshold_percent: float) -> bool:
        """Alert ONLY when price ENTERS threshold zone (not while inside)"""
        key = (symbol, target)
        if key not in self.states:
            self.states[key] = AlertState(last_in_zone=False)
        
        state = self.states[key]
        diff_percent = abs(current_price - target) / target * 100
        currently_in_zone = diff_percent <= threshold_percent
        
        # Critical logic: Alert only on zone ENTRY
        should_trigger = (
            not state.last_in_zone and  # Was outside zone
            currently_in_zone and       # Now inside zone
            self._cooldown_expired(state)  # Respects global cooldown
        )
        
        # Update state AFTER decision
        state.last_in_zone = currently_in_zone
        if should_trigger:
            state.last_alert_time = datetime.now()
            
        return should_trigger
    
    def _cooldown_expired(self, state: AlertState) -> bool:
        if state.last_alert_time is None:
            return True
        elapsed = (datetime.now() - state.last_alert_time).total_seconds()
        return elapsed >= self.cooldown_sec

# ====================== PRICE FETCHERS ======================
class CryptoFetcher:
    """Fetches crypto prices via CoinGecko (free, no API key required)"""
    BASE_URL = "https://api.coingecko.com/api/v3/coins/markets"
    
    def fetch(self, symbols: List[str]) -> Dict[str, float]:
        symbols_lower = [s.lower() for s in symbols]
        params = {
            'vs_currency': 'usd',
            'symbols': ','.join(symbols_lower),
            'price_change_percentage': '1h'
        }
        
        try:
            resp = requests.get(self.BASE_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            return {
                coin['symbol'].upper(): coin['current_price']
                for coin in data if coin['symbol'].upper() in symbols
            }
        except Exception as e:
            logging.warning(f"Crypto fetch error: {e}")
            return {}

class StockMetalFetcher:
    """Unified fetcher for stocks/metals using Yahoo Finance via yfinance"""
    def __init__(self):
        # Lazy import to avoid dependency if not needed
        try:
            global yf
            import yfinance as yf
        except ImportError:
            raise ImportError(
                "yfinance not installed. Install with: pip install yfinance"
            )
    
    def fetch(self, symbols: List[str]) -> Dict[str, float]:
        prices = {}
        if not symbols:
            return prices
        
        try:
            tickers = yf.Tickers(symbols)
            for symbol in symbols:
                try:
                    ticker = tickers.tickers.get(symbol)
                    if not ticker:
                        continue
                    
                    # Try multiple price sources for reliability
                    price = (
                        getattr(ticker, 'fast_info', {}).get('last_price') or
                        ticker.info.get('regularMarketPrice') or
                        ticker.history(period='1d', interval='1m')['Close'].iloc[-1]
                    )
                    if price:
                        prices[symbol] = float(price)
                except Exception as e:
                    logging.debug(f"Error fetching {symbol}: {e}")
                    continue
        except Exception as e:
            logging.warning(f"Batch fetch error: {e}")
        return prices

# ====================== NOTIFICATION CHANNELS ======================
class TelegramChannel:
    def __init__(self, bot_token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
    
    def send(self, message: str) -> bool:
        if "YOUR_TELEGRAM" in self.url or "YOUR_TELEGRAM" in self.chat_id:
            logging.error("❌ Telegram not configured! Replace placeholders in config.")
            return False
        
        try:
            resp = requests.post(self.url, json={
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'Markdown'
            }, timeout=5)
            success = resp.status_code == 200
            if not success:
                logging.warning(f"Telegram error: {resp.text}")
            return success
        except Exception as e:
            logging.error(f"Telegram send failed: {e}")
            return False

class DiscordChannel:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send(self, message: str) -> bool:
        if "YOUR_DISCORD" in self.webhook_url or "..." in self.webhook_url:
            logging.error("❌ Discord not configured! Replace placeholder in config.")
            return False
        
        try:
            resp = requests.post(self.webhook_url, json={
                'content': message,
                'username': 'Price Monitor Bot'
            }, timeout=5)
            return resp.status_code in (200, 204)
        except Exception as e:
            logging.error(f"Discord send failed: {e}")
            return False

class NotificationDispatcher:
    def __init__(self, channel_configs: List[dict]):
        self.channels = []
        for cfg in channel_configs:
            if cfg['type'] == 'telegram':
                self.channels.append(TelegramChannel(cfg['bot_token'], cfg['chat_id']))
            elif cfg['type'] == 'discord':
                self.channels.append(DiscordChannel(cfg['webhook_url']))
            else:
                logging.warning(f"Unsupported channel type: {cfg['type']}")
    
    def broadcast(self, message: str):
        if not self.channels:
            logging.warning("⚠️ No notification channels configured!")
            print("\n" + "="*50)
            print("ALERT (console only):")
            print(message)
            print("="*50 + "\n")
            return
        
        for channel in self.channels:
            try:
                if channel.send(message):
                    logging.info(f"Notification sent via {channel.__class__.__name__}")
            except Exception as e:
                logging.error(f"Channel broadcast error: {e}")

# ====================== MAIN MONITOR ======================
class PriceMonitor:
    def __init__(self, config: dict):
        self.config = config
        self.assets = config['assets']
        self.alert_engine = AlertEngine(
            self.assets, 
            cooldown_sec=config.get('alert_cooldown_sec', 300)
        )
        self.dispatcher = NotificationDispatcher(config['notification_channels'])
        
        # Initialize fetchers
        self.crypto_fetcher = CryptoFetcher()
        self.stock_metal_fetcher = StockMetalFetcher()
        
        # Group assets by type for efficient batching
        self.asset_groups = {
            'crypto': [a for a in self.assets if a['type'] == 'crypto'],
            'stock': [a for a in self.assets if a['type'] == 'stock'],
            'metal': [a for a in self.assets if a['type'] == 'metal']
        }
        
        self._validate_config()
    
    def _validate_config(self):
        """Validate critical configuration values"""
        for asset in self.assets:
            if not (0.01 <= asset['threshold_percent'] <= 0.1):
                raise ValueError(
                    f"Invalid threshold for {asset['symbol']}: "
                    f"{asset['threshold_percent']}% (must be 0.01-0.1%)"
                )
            if not asset['targets']:
                raise ValueError(f"No target prices defined for {asset['symbol']}")
    
    def _format_alert(self, asset: dict, target: float, 
                     current_price: float, diff_percent: float) -> str:
        """Generate human-readable alert message"""
        direction = "↑" if current_price > target else "↓"
        asset_type = asset['type'].upper()
        if asset['type'] == 'metal' and 'metal_type' in asset:
            asset_type = asset['metal_type'].upper()
        
        return (
            f"🚨 *{asset_type} PRICE ALERT* 🚨\n"
            f"Asset: {asset['name']} ({asset['symbol']})\n"
            f"Current: ${current_price:,.2f} {direction}\n"
            f"Target: ${target:,.2f}\n"
            f"Diff: {diff_percent:.4f}%\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    
    def _check_assets(self, asset_type: str):
        """Core monitoring logic for one asset type"""
        assets = self.asset_groups[asset_type]
        if not assets:
            return
        
        # Batch fetch prices
        symbols = list({a['symbol'] for a in assets})
        fetcher = (self.crypto_fetcher if asset_type == 'crypto' 
                  else self.stock_metal_fetcher)
        prices = fetcher.fetch(symbols)
        
        if not prices:
            logging.warning(f"No prices received for {asset_type} assets")
            return
        
        # Check each asset against targets
        for asset in assets:
            symbol = asset['symbol']
            if symbol not in prices:
                logging.warning(f"No price data for {symbol} ({asset_type})")
                continue
                
            current_price = prices[symbol]
            for target in asset['targets']:
                diff_percent = abs(current_price - target) / target * 100
                
                if self.alert_engine.should_alert(
                    symbol, target, current_price, asset['threshold_percent']
                ):
                    message = self._format_alert(
                        asset, target, current_price, diff_percent
                    )
                    self.dispatcher.broadcast(message)
                    logging.info(
                        f"ALERT: {symbol} ${current_price:.2f} near ${target:.2f} "
                        f"({diff_percent:.4f}%)"
                    )
                else:
                    logging.debug(
                        f"{symbol}: ${current_price:.2f} | Target ${target:.2f} | "
                        f"Diff {diff_percent:.4f}%"
                    )
    
    def start(self):
        """Start scheduled monitoring"""
        intervals = self.config['polling_intervals_sec']
        
        # Schedule asset-type-specific jobs
        schedule.every(intervals['crypto']).seconds.do(
            lambda: self._check_assets('crypto')
        )
        schedule.every(intervals['stock']).seconds.do(
            lambda: self._check_assets('stock')
        )
        schedule.every(intervals['metal']).seconds.do(
            lambda: self._check_assets('metal')
        )
        
        # Run first check immediately
        logging.info("🚀 Starting price monitor...")
        for asset_type in ['crypto', 'stock', 'metal']:
            if self.asset_groups[asset_type]:
                logging.info(f"Monitoring {asset_type.upper()} assets every "
                           f"{intervals[asset_type]} seconds")
                self._check_assets(asset_type)
        
        logging.info("✅ Monitor active! Press Ctrl+C to stop.\n")
        
        while True:
            schedule.run_pending()
            time.sleep(1)

# ====================== ENTRY POINT ======================
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )

def load_config():
    """Load config from embedded JSON or external file"""
    try:
        # Try loading from external file first
        with open('config.json', 'r') as f:
            logging.info("Loaded configuration from config.json")
            return json.load(f)
    except FileNotFoundError:
        # Fall back to embedded config
        logging.info("Using embedded configuration (create config.json to customize)")
        config = json.loads(CONFIG_JSON)
        
        # Safety check: warn if using default tokens
        for channel in config['notification_channels']:
            if 'YOUR_' in json.dumps(channel):
                logging.warning(
                    "\n⚠️  DEFAULT CONFIGURATION DETECTED ⚠️\n"
                    "You MUST configure notification channels before alerts will work!\n"
                    "1. Create a Telegram bot via @BotFather\n"
                    "2. Get your chat ID via @userinfobot\n"
                    "3. Update config.json with your tokens\n"
                )
        return config
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config: {e}")

def show_setup_instructions():
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║  💰 FREE PRICE MONITOR BOT - SETUP GUIDE                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

STEP 1: Install requirements (one-time)
  pip install requests schedule yfinance

STEP 2: Configure notifications (REQUIRED)
  Option A - Telegram (recommended):
    1. Start chat with @BotFather on Telegram
    2. Create new bot → get API token
    3. Start chat with @userinfobot → get your chat ID
    4. Create config.json with your token/ID (see example below)

  Option B - Discord:
    1. Server Settings → Integrations → Webhooks → New Webhook
    2. Copy webhook URL into config.json

STEP 3: Create config.json (example)
{
  "assets": [
    {
      "type": "crypto",
      "name": "Ethereum",
      "symbol": "ETH",
      "targets": [3000.0],
      "threshold_percent": 0.05,
      "allow_repeat_alerts": false
    }
  ],
  "polling_intervals_sec": {
    "crypto": 30,
    "stock": 60,
    "metal": 60
  },
  "notification_channels": [
    {
      "type": "telegram",
      "bot_token": "123456789:AAH_ABC123...",   <-- YOUR BOT TOKEN
      "chat_id": "987654321"                    <-- YOUR CHAT ID
    }
  ],
  "alert_cooldown_sec": 300
}

STEP 4: Run the bot
  python price_monitor.py

💡 Pro Tips:
  • Metal symbols: Gold=GC=F, Silver=SI=F, Platinum=PL=F
  • Stock symbols: Use standard tickers (AAPL, TSLA, etc.)
  • Crypto symbols: Use standard tickers (BTC, ETH, etc.)
  • Thresholds: 0.01-0.1% recommended for precision alerts
  • Keep running 24/7 on free services: PythonAnywhere, Replit, or Raspberry Pi

⚠️  WARNING: This bot uses free APIs with rate limits. Do not reduce polling
    intervals below 30 seconds or you may get blocked!
""")

def main():
    setup_logging()
    
    # Show setup instructions on first run
    try:
        with open('.first_run', 'r'):
            pass
    except FileNotFoundError:
        show_setup_instructions()
        with open('.first_run', 'w') as f:
            f.write('1')
        time.sleep(3)  # Give user time to read
    
    try:
        config = load_config()
        monitor = PriceMonitor(config)
        monitor.start()
    except KeyboardInterrupt:
        logging.info("\n👋 Monitor stopped by user. Goodbye!")
    except Exception as e:
        logging.exception(f"Fatal error: {e}")
        print("\n💡 Need help? Check the setup instructions at the top of this script.")

if __name__ == "__main__":
    main()
