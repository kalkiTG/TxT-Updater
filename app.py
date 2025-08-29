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
PORT = int(os.getenv("PORT", "10000"))  # For Render

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
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist).strftime("%d-%m-%Y %I:%M:%S %p")

def stylish_user(u):
    uname = f"@{u.username}" if getattr(u, "username", None) else "N/A"
    return f"{u.first_name or 'User'} ({uname})"

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
            "👋 <b>Welcome to the Ultimate Link Cleaner Bot!</b>\n\n"
            "✨ <i>Clean your links with style and precision.</i> ✨\n\n"
            "📄 <b>Instructions:</b>\n"
            "• Each line format: <code>Title: link</code>\n"
            "• Upload two <b>.txt</b> files:\n"
            "   1️⃣ <b>Old file</b> (unique links)\n"
            "   2️⃣ <b>New file</b>\n"
            "• Tap <b>Convert</b> to remove duplicates from NEW based on OLD.\n\n"
            f"🕒 <b>Current Time (IST)</b>: {ist_now_str()}\n"
            f"— <b>{BRAND}</b>"
        )
        await event.respond(welcome_text, buttons=buttons, parse_mode="html")

    @c.on(events.NewMessage(pattern=r'^/cancel$'))
    async def cancel_handler(event):
        chat_id = event.chat_id
        SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
        await event.respond(f"✅ Process cancelled. You can start again with /start.\n— {BRAND}")

    @c.on(events.CallbackQuery)
    async def callbacks(event):
        chat_id = event.chat_id
        data = (event.data or b"").decode("utf-8", errors="ignore")

        if chat_id not in SESSIONS:
            SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}

        if data == "upload_old":
            SESSIONS[chat_id]["awaiting"] = "old"
            await event.answer("Send the OLD .txt file now.", alert=True)
            await event.respond("📥 Please send the <b>OLD</b> .txt file.", parse_mode="html")

        elif data == "upload_new":
            SESSIONS[chat_id]["awaiting"] = "new"
            await event.answer("Send the NEW .txt file now.", alert=True)
            await event.respond("📥 Please send the <b>NEW</b> .txt file.", parse_mode="html")

        elif data == "cancel":
            SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
            await event.answer("Process cancelled.", alert=True)
            await event.respond(f"✅ Cancelled. Use /start to begin again.\n— {BRAND}")

        elif data == "convert":
            await event.answer("Processing…", alert=False)
            await convert_now(c, chat_id, event)

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
        safe_name = (event.file.name or f"{which}.txt").replace("/", "_").replace("\\", "_")
        path = DATA_DIR / f"{chat_id}_{which}_{safe_name}"
        await event.download_media(file=str(path))
        sess[which] = str(path)
        sess["awaiting"] = None

        # Count links in uploaded file
        count = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip():
                    count += 1

        caption = (
            f"📂 <b>{which.capitalize()} File Uploaded</b>\n\n"
            f"📝 <b>File Name:</b> <code>{event.file.name}</code>\n"
            f"🔗 <b>Total Links:</b> <code>{count}</code>\n"
            f"🕒 <b>Uploaded At (IST):</b> {ist_now_str()}\n\n"
            f"— {BRAND}"
        )

        await event.respond(caption, parse_mode="html")

async def convert_now(c: TelegramClient, chat_id: int, event=None):
    sess = SESSIONS.get(chat_id) or {}
    old_file = sess.get("old")
    new_file = sess.get("new")

    if not old_file or not new_file:
        await c.send_message(chat_id, "❌ Please upload both OLD and NEW .txt files first.")
        return

    if event:
        await event.edit("⏳ Processing your files...")

    # Read files fully (compare whole lines)
    with open(old_file, "r", encoding="utf-8", errors="ignore") as f:
        old_lines = {line.strip() for line in f if line.strip()}

    with open(new_file, "r", encoding="utf-8", errors="ignore") as f:
        new_lines = [line.strip() for line in f if line.strip()]

    # Keep only truly new lines
    kept_lines = []
    seen = set()
    for line in new_lines:
        if line in old_lines or line in seen:
            continue
        kept_lines.append(line)
        seen.add(line)

    kept_count = len(kept_lines)
    removed = max(0, len(new_lines) - kept_count)

    # Save updated file
    original_new_name = os.path.basename(new_file).replace(f"{chat_id}_new_", "")
    updated_file_name = f"{os.path.splitext(original_new_name)[0]} ×͜×.txt"
    updated_file_path = DATA_DIR / updated_file_name

    final_lines = kept_lines + [
        "",
        f"# Total Updated Lines: {kept_count}",
        f"# Removed Duplicates: {removed}",
    ]

    with open(updated_file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(final_lines))

    user = await c.get_entity(chat_id)
    fancy_user = stylish_user(user)

    # Cool final caption
    final_caption = (
        f"✨ <b>Link Cleaning Complete!</b> ✨\n\n"
        f"📊 <b>Summary Report</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📂 <b>Old File:</b> <code>{os.path.basename(old_file)}</code>\n"
        f"   └ Total Links: <code>{len(old_lines)}</code>\n\n"
        f"📂 <b>New File:</b> <code>{os.path.basename(new_file)}</code>\n"
        f"   └ Total Links: <code>{len(new_lines)}</code>\n\n"
        f"📂 <b>Updated File:</b> <code>{updated_file_name}</code>\n"
        f"   └ Links After Cleaning: <code>{kept_count}</code>\n"
        f"   └ Removed Duplicates: <code>{removed}</code>\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>User:</b> {fancy_user}\n"
        f"🆔 <b>User ID:</b> <code>{chat_id}</code>\n"
        f"🕒 <b>Time (IST):</b> {ist_now_str()}\n\n"
        f"— {BRAND}"
    )

    # Send updated file to user
    await c.send_file(chat_id, updated_file_path, caption=final_caption, parse_mode="html")

    # Send ALL 3 files to log channel
    try:
        # Old file
        await c.send_file(
            LOG_CHANNEL,
            old_file,
            caption=(
                f"📂 <b>Old File</b>\n"
                f"📝 Name: <code>{os.path.basename(old_file)}</code>\n"
                f"🔗 Total Links: <code>{len(old_lines)}</code>\n\n"
                f"👤 {fancy_user} | 🆔 <code>{chat_id}</code>\n"
                f"🕒 {ist_now_str()}\n— {BRAND}"
            ),
            parse_mode="html"
        )
        # New file
        await c.send_file(
            LOG_CHANNEL,
            new_file,
            caption=(
                f"📂 <b>New File</b>\n"
                f"📝 Name: <code>{os.path.basename(new_file)}</code>\n"
                f"🔗 Total Links: <code>{len(new_lines)}</code>\n\n"
                f"👤 {fancy_user} | 🆔 <code>{chat_id}</code>\n"
                f"🕒 {ist_now_str()}\n— {BRAND}"
            ),
            parse_mode="html"
        )
        # Updated file with full summary
        await c.send_file(LOG_CHANNEL, updated_file_path, caption=final_caption, parse_mode="html")

        log.info(f"Sent old, new, and updated files to log channel {LOG_CHANNEL}")
    except Exception as e:
        log.error(f"Failed to send files to log channel: {e}")

    sess["updated"] = str(updated_file_path)
    sess["old"] = None
    sess["new"] = None

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

# ============== START BOT THREAD ==============
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

# ============== FLASK (Render health) ==============
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return f"TxT-Updater is alive. Time (IST): {ist_now_str()} — {BRAND}"

# ============== ENTRYPOINT ==============
if __name__ == "__main__":
    Thread(target=start_bot, daemon=True).start()
    flask_app.run(host="0.0.0.0", port=PORT)
