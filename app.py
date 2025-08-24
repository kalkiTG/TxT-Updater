import os
import asyncio
import logging
from datetime import datetime
from threading import Thread
from pathlib import Path

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
PORT = int(os.getenv("PORT", "10000"))  # used by Render

assert API_ID and API_HASH and BOT_TOKEN and LOG_CHANNEL, "Please set API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL env vars."

# ============== LOGGING ==============
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("txt-updater")

# ============== CONSTANTS ==============
BRAND = "ᴍᴀʀꜱʜᴍᴀʟʟᴏᴡ×͜×"
DATA_DIR = Path("downloads")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ============== HELPERS ==============
def ist_now_str() -> str:
    """Return current time in Indian Standard Time."""
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist).strftime("%d-%m-%Y %I:%M:%S %p")

def stylish_user(u):
    uname = f"@{u.username}" if getattr(u, "username", None) else "N/A"
    return f"{u.first_name or 'User'} ({uname})"

# ============== SESSIONS ==============
# Store user state: awaiting ('old' or 'new'), file paths and last updated path
SESSIONS = {}  # { chat_id: {"awaiting": "old"/"new"/None, "old": path, "new": path, "updated": path} }

# ============== BUILD TELETHON CLIENT INSIDE ITS OWN THREAD/LOOP ==============
# We construct the client in the thread to avoid cross-loop issues.
client = None  # will be set in start_bot()

def register_handlers(c: TelegramClient):
    # /start
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
        await event.respond(
            f"👋 Hello {event.sender.first_name}!\n\n"
            "<b>Welcome to the Link Cleaner Bot</b>\n"
            "• Send two .txt files (one per line links):\n"
            "   1) <b>Old file</b>\n"
            "   2) <b>New file</b>\n"
            "• Tap <b>Convert</b> to remove links from the new file that exist in the old file.\n\n"
            f"🕒 <b>Time (IST)</b>: {ist_now_str()}\n"
            f"— {BRAND}",
            buttons=buttons, parse_mode="html"
        )

    # /cancel
    @c.on(events.NewMessage(pattern=r'^/cancel$'))
    async def cancel_handler(event):
        chat_id = event.chat_id
        SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
        await event.respond(
            f"✅ Process cancelled. You can start again with /start.\n— {BRAND}"
        )

    # Inline buttons
    @c.on(events.CallbackQuery)
    async def callbacks(event):
        chat_id = event.chat_id
        data = event.data or b""
        data = data.decode("utf-8", errors="ignore")

        if chat_id not in SESSIONS:
            SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}

        if data == "upload_old":
            SESSIONS[chat_id]["awaiting"] = "old"
            await event.answer("Send the OLD .txt file now.", alert=True)
            await event.edit(event.original_update.message, buttons=None)
            await event.respond("📥 Please send the <b>OLD</b> .txt file.", parse_mode="html")

        elif data == "upload_new":
            SESSIONS[chat_id]["awaiting"] = "new"
            await event.answer("Send the NEW .txt file now.", alert=True)
            await event.edit(event.original_update.message, buttons=None)
            await event.respond("📥 Please send the <b>NEW</b> .txt file.", parse_mode="html")

        elif data == "cancel":
            SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
            await event.answer("Process cancelled.", alert=True)
            await event.respond(f"✅ Cancelled. Use /start to begin again.\n— {BRAND}")

        elif data == "convert":
            await event.answer("Processing…", alert=False)
            await convert_now(c, chat_id, event)

    # File receiver (.txt)
    @c.on(events.NewMessage(func=lambda e: bool(e.file)))
    async def file_handler(event):
        chat_id = event.chat_id
        sess = SESSIONS.setdefault(chat_id, {"awaiting": None, "old": None, "new": None, "updated": None})

        if not event.file or not (event.file.name or "").lower().endswith(".txt"):
            await event.respond("❌ Please send a .txt file.")
            return

        if sess.get("awaiting") not in ("old", "new"):
            await event.respond("ℹ️ Choose what to upload first: use /start and the buttons.")
            return

        which = sess["awaiting"]
        safe_name = (event.file.name or f"{which}.txt").replace("/", "_")
        path = DATA_DIR / f"{chat_id}_{which}_{safe_name}"
        await event.download_media(file=str(path))
        sess[which] = str(path)
        sess["awaiting"] = None

        await event.respond(f"✅ <b>{which.capitalize()} file</b> saved.\nNow tap <b>Convert</b> when both files are uploaded.",
                            parse_mode="html")

async def convert_now(c: TelegramClient, chat_id: int, trigger_event):
    sess = SESSIONS.get(chat_id) or {}
    old_file = sess.get("old")
    new_file = sess.get("new")

    if not old_file or not new_file:
        await c.send_message(chat_id, "❌ Please upload both OLD and NEW .txt files first.")
        return

    # Read files
    def read_links(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return [ln.strip() for ln in f if ln.strip()]

    old_links = set(read_links(old_file))
    new_links = read_links(new_file)
    updated_links = [ln for ln in new_links if ln not in old_links]

    # Save updated file: same base name + _updated.txt
    base = os.path.splitext(new_file)[0]
    updated_file = f"{base}_updated.txt"
    with open(updated_file, "w", encoding="utf-8") as f:
        f.write("\n".join(updated_links))

    # Counts
    removed = len(new_links) - len(updated_links)
    video_count = sum(1 for ln in updated_links if ln.lower().endswith((".mp4", ".mkv", ".mov", ".avi")))
    pdf_count = sum(1 for ln in updated_links if ln.lower().endswith(".pdf"))

    # Captions
    user = await c.get_entity(chat_id)
    fancy_user = stylish_user(user)

    user_caption = (
        "✨ <b>Link Cleaning Complete</b> ✨\n\n"
        f"👤 <b>User</b>: {fancy_user}\n"
        f"🆔 <b>User ID</b>: <code>{chat_id}</code>\n"
        f"🕒 <b>Time (IST)</b>: {ist_now_str()}\n\n"
        f"📂 <b>Old Links</b>: <code>{len(old_links)}</code>\n"
        f"🆕 <b>New Links</b>: <code>{len(new_links)}</code>\n"
        f"✅ <b>Updated Links</b>: <code>{len(updated_links)}</code>\n"
        f"❌ <b>Removed</b>: <code>{removed}</code>\n"
        f"🎬 <b>Videos</b>: <code>{video_count}</code> • 📄 <b>PDFs</b>: <code>{pdf_count}</code>\n\n"
        f"— {BRAND}"
    )

    log_caption = (
        "📢 <b>New Conversion Completed</b>\n\n"
        f"👤 <b>User</b>: {fancy_user}\n"
        f"🆔 <b>User ID</b>: <code>{chat_id}</code>\n"
        f"🕒 <b>Time (IST)</b>: {ist_now_str()}\n\n"
        f"📂 <b>Old Links</b>: <code>{len(old_links)}</code>\n"
        f"🆕 <b>New Links</b>: <code>{len(new_links)}</code>\n"
        f"✅ <b>Updated Links</b>: <code>{len(updated_links)}</code>\n"
        f"❌ <b>Removed</b>: <code>{removed}</code>\n"
        f"🎬 <b>Videos</b>: <code>{video_count}</code> • 📄 <b>PDFs</b>: <code>{pdf_count}</code>\n"
        f"— {BRAND}"
    )

    # Send to user
    buttons = [
        [Button.text("🔄 Start Over", resize=True), Button.text("📥 Download Updated Again", resize=True)],
        [Button.text("❌ Cancel", resize=True)]
    ]
    await c.send_file(chat_id, updated_file, caption=user_caption, parse_mode="html", buttons=buttons)

    # Send to log channel
    await c.send_file(LOG_CHANNEL, old_file, caption="<b>📂 Old File</b>", parse_mode="html")
    await c.send_file(LOG_CHANNEL, new_file, caption="<b>🆕 New File</b>", parse_mode="html")
    await c.send_file(LOG_CHANNEL, updated_file, caption=log_caption, parse_mode="html")

    # Save for re-download and clear uploads
    sess["updated"] = updated_file
    sess["old"] = None
    sess["new"] = None

# Post-conversion text buttons
def register_text_buttons(c: TelegramClient):
    @c.on(events.NewMessage(pattern=r'^🔄 Start Over$'))
    async def start_over(event):
        chat_id = event.chat_id
        SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
        await c.send_message(chat_id, "🔄 Fresh start! Use /start to upload files again.\n— " + BRAND)

    @c.on(events.NewMessage(pattern=r'^📥 Download Updated Again$'))
    async def download_again(event):
        chat_id = event.chat_id
        sess = SESSIONS.get(chat_id) or {}
        upd = sess.get("updated")
        if upd and os.path.exists(upd):
            await c.send_file(chat_id, upd, caption="📥 Your updated file again!\n— " + BRAND)
        else:
            await c.send_message(chat_id, "⚠️ No updated file found. Please /start and process again.\n— " + BRAND)

    @c.on(events.NewMessage(pattern=r'^❌ Cancel$'))
    async def cancel_btn(event):
        chat_id = event.chat_id
        SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
        await c.send_message(chat_id, "✅ Cancelled. Use /start to begin again.\n— " + BRAND)

# Thread target to run Telethon
def start_bot():
    global client
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient("bot", API_ID, API_HASH, loop=loop)
    register_handlers(client)
    register_text_buttons(client)

    async def runner():
        await client.start(bot_token=BOT_TOKEN)
        log.info("Telethon bot started.")
        await client.run_until_disconnected()

    try:
        loop.run_until_complete(runner())
    finally:
        loop.close()

# ============== FLASK APP (Render health) ==============
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return f"TxT-Updater is alive. Time (IST): {ist_now_str()} — {BRAND}"

# ============== ENTRYPOINT ==============
if __name__ == "__main__":
    # Start the bot in a background thread
    Thread(target=start_bot, daemon=True).start()

    # Run Flask server (must bind to PORT for Render)
    flask_app.run(host="0.0.0.0", port=PORT)
