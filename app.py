import os
import re
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

def normalize_line(line: str) -> str:
    # Keep exact line semantics but trim whitespace
    return line.strip()

def diff_new_minus_old(old_file: str, new_file: str) -> list:
    with open(old_file, "r", encoding="utf-8", errors="ignore") as f:
        old_lines = {normalize_line(l) for l in f if l.strip()}

    with open(new_file, "r", encoding="utf-8", errors="ignore") as f:
        new_lines = [normalize_line(l) for l in f if l.strip()]

    updated = []
    seen = set()
    for line in new_lines:
        if line not in old_lines and line not in seen:
            updated.append(line)
            seen.add(line)
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
            "Send two <b>.txt</b> files (each line = one link row like <code>Title: link</code>), then press <b>Convert</b>.\n\n"
            f"🕒 <b>IST</b>: {ist_now_str()}\n"
            f"— <b>{BRAND}</b>"
        )
        await event.respond(welcome_text, buttons=buttons, parse_mode="html")

    @c.on(events.NewMessage(pattern=r'^/cancel$'))
    async def cancel_handler(event):
        chat_id = event.chat_id
        SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
        await event.respond(f"✅ Cancelled. Use /start to begin again.\n— {BRAND}")

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

        # Count "links" as non-empty lines (consistent with line-per-link format)
        count = count_nonempty_lines(str(path))

        # Minimal, clean user caption (no filenames)
        caption_user = (
            f"📦 <b>{which.capitalize()} file saved</b>\n"
            f"🔗 <b>Total lines:</b> <code>{count}</code>\n"
            f"🕒 <b>IST:</b> {ist_now_str()}\n"
            f"— <b>{BRAND}</b>"
        )
        await event.respond(caption_user, parse_mode="html")

        # Verbose log caption with all details
        user = await c.get_entity(chat_id)
        fancy_user = stylish_user(user)
        caption_log = (
            f"📂 <b>{which.upper()} FILE RECEIVED</b>\n"
            f"📝 Name: <code>{event.file.name}</code>\n"
            f"🔗 Total lines: <code>{count}</code>\n"
            f"👤 User: {fancy_user}\n"
            f"🆔 User ID: <code>{chat_id}</code>\n"
            f"🕒 IST: {ist_now_str()}\n"
            f"— <b>{BRAND}</b>"
        )
        try:
            await c.send_file(LOG_CHANNEL, str(path), caption=caption_log, parse_mode="html")
        except Exception as e:
            log.error(f"Failed to log uploaded {which} file: {e}")

async def convert_now(c: TelegramClient, chat_id: int, event=None):
    sess = SESSIONS.get(chat_id) or {}
    old_file = sess.get("old")
    new_file = sess.get("new")

    if not old_file or not new_file:
        await c.send_message(chat_id, "❌ Please upload both OLD and NEW .txt files first.\n— " + BRAND)
        return

    if event:
        try:
            await event.edit("⏳ Processing your files...")
        except Exception:
            pass

    # Counts for reporting
    old_count = count_nonempty_lines(old_file)
    new_count = count_nonempty_lines(new_file)

    # Diff (only new lines, order preserved, no duplicates)
    updated_lines = diff_new_minus_old(old_file, new_file)
    added_count = len(updated_lines)
    no_updates = (added_count == 0)

    # Build updated file name (based on original NEW file)
    original_new_name = os.path.basename(new_file).replace(f"{chat_id}_new_", "")
    base_no_ext, _ = os.path.splitext(original_new_name)
    updated_file_name = f"{base_no_ext}_updated.txt"
    updated_file_path = DATA_DIR / updated_file_name

    # Always create an updated file for consistency and logging
    if no_updates:
        # Small informative file (not empty) for traceability
        payload = [
            "# No new lines found (new - old = 0)",
            f"# Generated at (IST): {ist_now_str()}",
            f"# Old lines: {old_count}",
            f"# New lines: {new_count}",
            "# — " + BRAND,
        ]
        with open(updated_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(payload) + "\n")
    else:
        with open(updated_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(updated_lines) + "\n")

    # User-friendly minimal caption (NO filenames)
    user = await c.get_entity(chat_id)
    fancy_user = stylish_user(user)
    if no_updates:
        final_caption_user = (
            f"✅ <b>No updates found</b>\n"
            f"🔗 Old: <code>{old_count}</code> • New: <code>{new_count}</code> • Added: <code>0</code>\n"
            f"👤 {fancy_user}\n"
            f"— <b>{BRAND}</b>"
        )
    else:
        final_caption_user = (
            f"✨ <b>Updated file ready</b>\n"
            f"🔗 Old: <code>{old_count}</code> • New: <code>{new_count}</code> • Added: <code>{added_count}</code>\n"
            f"👤 {fancy_user}\n"
            f"— <b>{BRAND}</b>"
        )

    # Send updated file to user (always)
    await c.send_file(chat_id, str(updated_file_path), caption=final_caption_user, parse_mode="html")

    # Log channel: send all three with full details
    summary_caption = (
        "📊 <b>CONVERSION SUMMARY</b>\n"
        f"🕒 IST: {ist_now_str()}\n"
        f"👤 User: {fancy_user}\n"
        f"🆔 User ID: <code>{chat_id}</code>\n"
        "━━━━━━━━━━━━━━━\n"
        f"OLD lines: <code>{old_count}</code>\n"
        f"NEW lines: <code>{new_count}</code>\n"
        f"ADDED lines (new-old): <code>{added_count}</code>\n"
        "━━━━━━━━━━━━━━━\n"
        f"Updated file is {'NOT EMPTY' if not no_updates else 'INFORMATIVE (no new lines)'}\n"
        f"— <b>{BRAND}</b>"
    )

    try:
        # Old file with details
        await c.send_file(
            LOG_CHANNEL,
            old_file,
            caption=(
                "📂 <b>OLD FILE</b>\n"
                f"📝 Name: <code>{os.path.basename(old_file)}</code>\n"
                f"🔗 Total lines: <code>{old_count}</code>\n"
                f"👤 {fancy_user}\n"
                f"🆔 <code>{chat_id}</code>\n"
                f"🕒 IST: {ist_now_str()}\n"
                f"— <b>{BRAND}</b>"
            ),
            parse_mode="html"
        )
        # New file with details
        await c.send_file(
            LOG_CHANNEL,
            new_file,
            caption=(
                "📂 <b>NEW FILE</b>\n"
                f"📝 Name: <code>{os.path.basename(new_file)}</code>\n"
                f"🔗 Total lines: <code>{new_count}</code>\n"
                f"👤 {fancy_user}\n"
                f"🆔 <code>{chat_id}</code>\n"
                f"🕒 IST: {ist_now_str()}\n"
                f"— <b>{BRAND}</b>"
            ),
            parse_mode="html"
        )
        # Updated file with compact summary
        await c.send_file(LOG_CHANNEL, str(updated_file_path), caption=summary_caption, parse_mode="html")

        log.info(f"Logged old, new, and updated files to {LOG_CHANNEL}")
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
    return f"TxT-Updater alive. IST: {ist_now_str()} — {BRAND}"

# ============== ENTRYPOINT ==============
if __name__ == "__main__":
    Thread(target=start_bot, daemon=True).start()
    flask_app.run(host="0.0.0.0", port=PORT)
