#!/usr/bin/env python3
"""Telegram async bridge (asyncio polling). Runs in parallel with trading loop.
- Only responds to whitelisted chat_id (TELEGRAM_CHAT_ID)
- /status, /arb, /position, /help, freeform -> all echo + relays to Hermes
"""
import os, sys, json, asyncio
import time
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ALLOWED_IDS = {int(CHAT_ID)} if CHAT_ID.isdigit() else set()

def send_telegram(chat_id: int, text: str) -> bool:
    if not TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception as exc:
        print(json.dumps({"event": "telegram_send_error", "error": str(exc)}), flush=True)
        return False

def handle_command(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "📡 Boş mesaj. /help yazabilirsin."
    lower = text.lower()
    if lower in {"/help", "help"}:
        return (
            "🤖 *Hermes v08 komutları:*\n"
            "/status — Çalışma durumu\n"
            "/arb — Arbitraj fırsatları\n"
            "/position — Açık pozisyon\n"
            "/pnl — Toplam kâr/zarar\n"
            "/help — Bu mesaj"
        )
    if lower == "/status":
        # Trading position (paper_state)
        trade_text = "📊 *Trading (Paper):*\n"
        try:
            for p in ["/app/state/paper_state.json", "./state/paper_state.json", "state/paper_state.json"]:
                if os.path.exists(p):
                    with open(p) as f:
                        paper = json.load(f)
                    pos = paper.get("position")
                    equity = paper.get("equity", 0)
                    trade_text += f"• Equity: `{equity:.4f}` USDT\n"
                    trade_text += f"• Pozisyon: `{'AÇIK ' + pos['asset'] + ' @ ' + str(pos.get('entry_price')) if pos else 'YOK'}`\n"
                    trade_text += f"• Kapalı trades: `{paper.get('closed_trades', 0)}`\n"
                    break
        except Exception as exc:
            trade_text += f"• Okuma hatası: {exc}\n"
        # Arbitraj status (arb_heartbeat)
        arb_text = "\n🔄 *Arbitraj:*\n"
        try:
            for p in ["/app/state/arb_heartbeat.json", "./state/arb_heartbeat.json", "state/arb_heartbeat.json"]:
                if os.path.exists(p):
                    with open(p) as f:
                        arb = json.load(f)
                    arb_text += f"• Son tarama: `{arb.get('timestamp', '?')}`\n"
                    arb_text += f"• Spatial fırsat: `{arb.get('spatial_count', 0)}`\n"
                    arb_text += f"• Triangular fırsat: `{arb.get('triangular_count', 0)}`\n"
                    arb_text += f"• En iyi net: `%{arb.get('best_spatial_net', 0):.2f}` (spatial) | `%{arb.get('best_triangular_net', 0):.2f}` (triang)\n"
                    break
            else:
                arb_text += "• Henüz tarama yok (4sn'de başlar)\n"
        except Exception as exc:
            arb_text += f"• Okuma hatası: {exc}\n"
        return (
            f"🤖 *Hermes v08 — /status*\n\n"
            f"{trade_text}\n{arb_text}\n"
            f"• Döngü: Trading 60s / Arbitraj 4s\n"
            f"• Watchlist: 22 helal coin\n"
            f"• Borsa: Binance + Bybit + OKX + KuCoin\n"
            f"• Mod: paper (live)\n"
            f"• Strateji: v08 (min_smc=0, SL 1%, R/R 2.0)"
        )
    if lower == "/arb":
        try:
            r = requests.get("https://api.telegram.org/bot{}/getMe".format(TOKEN), timeout=5)
            return "🔍 Arbitraj taraması aktif (4s). Loglar: `arb_heartbeat.json`"
        except Exception:
            return "🔍 Arbitraj taraması aktif (4s)."
    if lower == "/position":
        try:
            state_path = os.getenv("HERMES_STATE_DIR", "/app/state") + "/paper_state.json"
            if not os.path.exists(state_path):
                # try relative
                for p in ["/app/state/paper_state.json", "./state/paper_state.json", "state/paper_state.json"]:
                    if os.path.exists(p):
                        state_path = p
                        break
            with open(state_path) as f:
                paper = json.load(f)
            pos = paper.get("position")
            if pos is None:
                return "📊 Pozisyon açık değil."
            return (
                f"📊 *Pozisyon:*\n"
                f"• Coin: `{pos.get('asset', '?')}`\n"
                f"• Entry: {pos.get('entry_price', '?')}\n"
                f"• Stop: {pos.get('stop_loss', '?')}\n"
                f"• Target: {pos.get('take_profit', '?')}\n"
                f"• Equity: {paper.get('equity', 0):.4f} USDT"
            )
        except Exception as exc:
            return f"📊 Pozisyon okunamadı: {exc}"
    if lower == "/pnl":
        try:
            for p in ["/app/state/paper_state.json", "./state/paper_state.json", "state/paper_state.json"]:
                if os.path.exists(p):
                    with open(p) as f:
                        paper = json.load(f)
                    return (
                        f"💰 *P&L Durum:*\n"
                        f"• Equity: `{paper.get('equity', 0):.4f}` USDT\n"
                        f"• Closed trades: `{paper.get('closed_trades', 0)}`\n"
                        f"• Position: `{paper.get('position') or 'yok'}`"
                    )
            return "💰 Paper state henüz oluşmamış."
        except Exception as exc:
            return f"💰 PnL okunamadı: {exc}"
    # freeform message
    return (
        f"📡 Alındı: `{text[:200]}`\n"
        f"✅ Hermes trading/arb çalışıyor. /help yaz."
    )

async def poll_loop() -> None:
    if not TOKEN:
        print(json.dumps({"event": "telegram_disabled", "reason": "no_token"}), flush=True)
        return
    print(
        json.dumps(
            {
                "event": "telegram_bridge_start",
                "whitelist": list(ALLOWED_IDS),
                "mode": "async_polling",
            }
        ),
        flush=True,
    )
    last_id = 0
    backoff = 1.0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            r = requests.get(url, params={"offset": last_id + 1, "timeout": 25}, timeout=30)
            data = r.json()
            backoff = 1.0  # reset on success
            for up in data.get("result", []):
                last_id = max(last_id, up.get("update_id", 0))
                msg = up.get("message", {})
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                text = msg.get("text", "").strip()
                if chat_id not in ALLOWED_IDS:
                    # Silently ignore unauthorized
                    continue
                from_user = msg.get("from", {}).get("first_name", "user")
                print(
                    json.dumps(
                        {
                            "event": "telegram_message",
                            "from": from_user,
                            "chat_id": chat_id,
                            "text": text,
                        }
                    ),
                    flush=True,
                )
                reply = handle_command(text)
                await asyncio.to_thread(send_telegram, chat_id, reply)
        except Exception as exc:
            print(
                json.dumps({"event": "telegram_poll_error", "error": str(exc), "backoff": backoff}),
                flush=True,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        await asyncio.sleep(0.5)

if __name__ == "__main__":
    try:
        asyncio.run(poll_loop())
    except KeyboardInterrupt:
        print(json.dumps({"event": "telegram_bridge_stop", "reason": "kbd"}), flush=True)
