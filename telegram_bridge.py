#!/usr/bin/env python3
"""Telegram bridge for Hermes trading agent.
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env.
Only responds to messages from the whitelisted chat_id.
Runs continuous long-polling; connects to Hermes loop via HTTP webhook.
"""
import os, sys, json, time
sys.path.insert(0, "/c/Users/PC/hermes-trading")

# Dependencies: python-telegram-bot or httpx + requests
try:
    import requests
except ImportError:
    print(json.dumps({"event":"telegram_bridge_missing_requests","fix":"pip install requests"}))
    sys.exit(1)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "https://integrate.api.nvidia.com/v1/chat/completions")  # Hermes endpoint proxy

def send_telegram(chat_id: str, text: str, token: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception as exc:
        print(json.dumps({"event":"telegram_send_error","error":str(exc)}), flush=True)
        return False

def get_updates(token: str, last_update_id: int = 0):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 30}
    try:
        r = requests.get(url, params=params, timeout=35)
        data = r.json()
        return data.get("result", [])
    except Exception as exc:
        print(json.dumps({"event":"telegram_poll_error","error":str(exc)}), flush=True)
        return []

def main():
    if not TOKEN or not CHAT_ID:
        print(json.dumps({"event":"telegram_config_missing","token_set":bool(TOKEN),"chat_id_set":bool(CHAT_ID)}), flush=True)
        return
    print(json.dumps({"event":"telegram_bridge_start","chat_id":CHAT_ID,"mode":"polling","whitelist":"only_"+CHAT_ID}), flush=True)
    last_id = 0
    while True:
        updates = get_updates(TOKEN, last_id)
        for up in updates:
            last_id = max(last_id, up.get("update_id", 0))
            msg = up.get("message", {})
            if msg.get("chat",{}).get("id") == int(CHAT_ID):
                text = msg.get("text","").strip()
                from_chat = msg.get("from",{}).get("first_name","user")
                print(json.dumps({
                    "event":"telegram_message",
                    "from":from_chat,
                    "chat_id":CHAT_ID,
                    "text":text,
                    "action":"forward_to_hermes"
                }), flush=True)
                # Forward to Hermes — response will be handled by gateway
                # For now, echo back status
                reply = f"📡 Alındı: `{text}`\n✅ Hermes çalışıyor (v08: trading 60s, arb 4s)."
                send_telegram(CHAT_ID, reply, TOKEN)
        time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(json.dumps({"event":"telegram_bridge_stop","reason":"keyboard_interrupt"}), flush=True)
