import os
import re
import asyncio
import logging
from datetime import datetime
from threading import Thread
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask
from telethon import TelegramClient, events, Button
import pytz

# ============== ENV SETUP ==============
load_dotenv()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", "0"))
PORT = int(os.getenv("PORT", "10000"))  # For Render / Flask

assert API_ID and API_HASH and BOT_TOKEN and LOG_CHANNEL, "Please set API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL in .env"

# ============== LOGGING ==============
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("txt-updater")

# ============== CONSTANTS ==============
BRAND = "ᴍᴀʀꜱʜᴍᴀʟʟᴏᴡ×͜×"
DATA_DIR = Path("downloads")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ============== HELPERS ==============
def ist_now_str() -> str:
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist).strftime("%d-%m-%Y %I:%M:%S %p")

def stylish_user(u):
    uname = f"@{u.username}" if getattr(u, "username", None) else "N/A"
    fname = (u.first_name or "User")
    return f"{fname} ({uname})"

def count_nonempty_lines(file_path: str) -> int:
    cnt = 0
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.strip():
                cnt += 1
    return cnt

# === Normalization for stable comparison ===
def normalize_link(line: str) -> str:
    """Extract stable identifier from a link, ignoring expiring tokens."""
    match = re.search(r'(https?://\S+)', line)
    if not match:
        return line.strip()
    url = match.group(1)
    path = urlparse(url).path
    parts = path.strip("/").split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])  # e.g., "720/main.m3u8" or "file.pdf"
    else:
        return parts[-1]

def diff_new_minus_old(old_file: str, new_file: str) -> list:
    with open(old_file, "r", encoding="utf-8", errors="ignore") as f:
        old_norm = {normalize_link(l) for l in f if l.strip()}

    with open(new_file, "r", encoding="utf-8", errors="ignore") as f:
        new_lines = [l.strip() for l in f if l.strip()]
        new_norm = [normalize_link(l) for l in new_lines]

    updated = []
    seen = set()
    for line, norm in zip(new_lines, new_norm):
        if norm not in old_norm and norm not in seen:
            updated.append(line)
            seen.add(norm)
    return updated

# ============== SESSIONS ==============
SESSIONS = {}

# ============== TELEGRAM BOT ==============
client = None

def register_handlers(c: TelegramClient):
    @c.on(events.NewMessage(pattern=r'^/start$'))
    async def start_handler(event):
        chat_id = event.chat_id
        SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}

        buttons = [
            [Button.inline("📂 Upload Old File", data=b"upload_old")],
            [Button.inline("📂 Upload New File", data=b"upload_new")],
            [Button.inline("✅ Convert", data=b"convert")],
            [Button.inline("❌ Cancel", data=b"cancel")]
        ]
        welcome_text = (
            "👋 <b>Welcome!</b>\n"
            "Upload two .txt files (each line = one link row like <code>Title: link</code>), then tap <b>Convert</b>.\n\n"
            f"🕒 <b>IST</b>: {ist_now_str()}\n"
            f"— <b>{BRAND}</b>"
        )
        await event.respond(welcome_text, buttons=buttons, parse_mode="html")

    @c.on(events.CallbackQuery)
    async def callbacks(event):
        chat_id = event.chat_id
        data = (event.data or b"").decode("utf-8", errors="ignore")

        if chat_id not in SESSIONS:
            SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}

        if data == "upload_old":
            SESSIONS[chat_id]["awaiting"] = "old"
            await event.respond("📥 Please send the <b>OLD</b> .txt file.", parse_mode="html")

        elif data == "upload_new":
            SESSIONS[chat_id]["awaiting"] = "new"
            await event.respond("📥 Please send the <b>NEW</b> .txt file.", parse_mode="html")

        elif data == "cancel":
            SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
            await event.respond(f"✅ Cancelled. Use /start to begin again.\n— {BRAND}")

        elif data == "convert":
            await convert_now(c, chat_id, event)

    @c.on(events.NewMessage(func=lambda e: bool(e.file)))
    async def file_handler(event):
        chat_id = event.chat_id
        sess = SESSIONS.setdefault(chat_id, {"awaiting": None, "old": None, "new": None, "updated": None})

        if not event.file or not (event.file.name or "").lower().endswith(".txt"):
            await event.respond("❌ Please send a .txt file.")
            return

        if sess.get("awaiting") not in ("old", "new"):
            await event.respond("ℹ️ Use /start and select what to upload.")
            return

        which = sess["awaiting"]
        safe_name = (event.file.name or f"{which}.txt").replace("/", "_").replace("\\", "_")
        path = DATA_DIR / f"{chat_id}_{which}_{safe_name}"
        await event.download_media(file=str(path))
        sess[which] = str(path)
        sess["awaiting"] = None

        count = count_nonempty_lines(str(path))
        caption_user = (
            f"📦 <b>{which.capitalize()} file saved</b>\n"
            f"🔗 Total lines: <code>{count}</code>\n"
            f"🕒 {ist_now_str()}\n"
            f"— <b>{BRAND}</b>"
        )
        await event.respond(caption_user, parse_mode="html")

async def convert_now(c: TelegramClient, chat_id: int, event=None):
    sess = SESSIONS.get(chat_id) or {}
    old_file = sess.get("old")
    new_file = sess.get("new")

    if not old_file or not new_file:
        await c.send_message(chat_id, "❌ Upload both OLD and NEW .txt files first.\n— " + BRAND)
        return

    old_count = count_nonempty_lines(old_file)
    new_count = count_nonempty_lines(new_file)
    updated_lines = diff_new_minus_old(old_file, new_file)
    added_count = len(updated_lines)

    original_new_name = os.path.basename(new_file).replace(f"{chat_id}_new_", "")
    base_no_ext, _ = os.path.splitext(original_new_name)
    updated_file_name = f"{base_no_ext}_updated.txt"
    updated_file_path = DATA_DIR / updated_file_name

    if added_count == 0:
        payload = [
            "# No new lines found",
            f"# Old lines: {old_count}",
            f"# New lines: {new_count}",
            f"# Generated {ist_now_str()}",
            f"# — {BRAND}"
        ]
        with open(updated_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(payload) + "\n")
    else:
        with open(updated_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(updated_lines) + "\n")

    user = await c.get_entity(chat_id)
    fancy_user = stylish_user(user)

    final_caption = (
        f"✨ <b>Update Complete</b>\n"
        f"🔗 Old: <code>{old_count}</code> • New: <code>{new_count}</code> • Added: <code>{added_count}</code>\n"
        f"👤 {fancy_user}\n"
        f"— <b>{BRAND}</b>"
    )
    await c.send_file(chat_id, str(updated_file_path), caption=final_caption, parse_mode="html")

    # Log all files
    try:
        await c.send_file(LOG_CHANNEL, old_file, caption=f"📂 OLD FILE\n🔗 {old_count}\n👤 {fancy_user}", parse_mode="html")
        await c.send_file(LOG_CHANNEL, new_file, caption=f"📂 NEW FILE\n🔗 {new_count}\n👤 {fancy_user}", parse_mode="html")
        await c.send_file(LOG_CHANNEL, str(updated_file_path), caption=final_caption, parse_mode="html")
    except Exception as e:
        log.error(f"Failed to send files to log channel: {e}")

    sess["updated"] = str(updated_file_path)
    sess["old"] = None
    sess["new"] = None

# ============== START BOT THREAD ==============
def start_bot():
    global client
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient("bot", API_ID, API_HASH, loop=loop)
    register_handlers(client)

    async def runner():
        await client.start(bot_token=BOT_TOKEN)
        log.info("Telethon bot started.")
        await client.run_until_disconnected()

    try:
        loop.run_until_complete(runner())
    finally:
        loop.close()

# ============== FLASK (Render health) ==============
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return f"TxT-Updater alive. IST: {ist_now_str()} — {BRAND}"

# ============== ENTRYPOINT ==============
if __name__ == "__main__":
    Thread(target=start_bot, daemon=True).start()
    flask_app.run(host="0.0.0.0", port=PORT)
