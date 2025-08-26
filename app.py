import os
import time
import logging
from telethon import TelegramClient, events, Button
from datetime import datetime
from pytz import timezone
from flask import Flask
import threading

# =====================
# CONFIG
# =====================
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))
BRAND = "🔗 Link Updater Bot"
PORT = int(os.getenv("PORT", 8080))  # For Render health check
SESSIONS = {}

# =====================
# LOGGER
# =====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================
# BOT CLIENT
# =====================
bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# =====================
# FLASK HEALTH SERVER
# =====================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running fine!", 200

# Run Flask in background
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()

# =====================
# UTILITIES
# =====================
def ist_now_str():
    return datetime.now(timezone("Asia/Kolkata")).strftime("%d-%m-%Y %I:%M %p")

def stylish_user(user):
    name = user.first_name or "User"
    if user.last_name:
        name += f" {user.last_name}"
    return f"{name}"

def extract_link(line: str) -> str:
    """Extract link from 'title : link' or plain link format"""
    if ":" in line:
        return line.split(":", 1)[-1].strip().lower()
    parts = line.split()
    return parts[-1].lower() if parts else ""

# =====================
# START COMMAND
# =====================
@bot.on(events.NewMessage(pattern="/start"))
async def start(event):
    await event.respond(
        "👋 Welcome!\n\nUpload your **OLD file** first, then your **NEW file**.\n\n"
        "I will create an updated file with only new links that are not in the old file.",
        buttons=[
            [Button.text("📂 Upload OLD File")],
            [Button.text("📂 Upload NEW File")],
            [Button.text("ℹ Help")]
        ]
    )

# =====================
# HELP COMMAND
# =====================
@bot.on(events.NewMessage(pattern="/help"))
async def help_cmd(event):
    await event.respond(
        "ℹ **How to use this bot:**\n\n"
        "1️⃣ Send OLD file (.txt)\n"
        "2️⃣ Send NEW file (.txt)\n"
        "✅ Bot will give you an updated file with only unique new lines.\n\n"
        f"{BRAND}",
        parse_mode="md"
    )

# =====================
# FILE HANDLER
# =====================
@bot.on(events.NewMessage(func=lambda e: e.file and e.file.name.endswith(".txt")))
async def handle_file(event):
    chat_id = event.chat_id
    sess = SESSIONS.get(chat_id, {})
    file_name = event.file.name

    # Download file
    file_path = await event.download_media()
    logger.info(f"Received file {file_name} from {chat_id}")

    if not sess.get("old"):
        sess["old"] = file_path
        await event.reply("✅ Old file saved.\n\nNow send the **NEW file**.")
    elif not sess.get("new"):
        sess["new"] = file_path
        await event.reply("✅ New file saved.\n\nProcessing your files...")
        await convert_now(bot, chat_id)
    else:
        await event.reply("❌ You already uploaded both files.\nUse /start to reset.")

    SESSIONS[chat_id] = sess

# =====================
# MAIN CONVERSION LOGIC
# =====================
async def convert_now(c: TelegramClient, chat_id: int):
    sess = SESSIONS.get(chat_id) or {}
    old_file = sess.get("old")
    new_file = sess.get("new")

    if not old_file or not new_file:
        await c.send_message(chat_id, "❌ Please upload both OLD and NEW .txt files first.")
        return

    # Read old links
    old_links = set()
    with open(old_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.strip():
                old_links.add(extract_link(line))

    updated_lines = []
    seen_links = set()
    total_new_lines = 0
    video_count = 0
    pdf_count = 0

    # Process new file lines
    with open(new_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_new_lines += 1
            link = extract_link(line)
            if link not in old_links and link not in seen_links:
                updated_lines.append(line)
                seen_links.add(link)
                if link.endswith((".mp4", ".mkv", ".mov", ".avi")):
                    video_count += 1
                if link.endswith(".pdf"):
                    pdf_count += 1

    # If no new links
    if len(updated_lines) == 0:
        await c.send_message(chat_id, "✅ No new links found! Everything is already up to date.")
        await c.send_message(LOG_CHANNEL, f"ℹ User {chat_id} uploaded files but no new links were found.")
        sess.clear()
        return

    # Save updated file
    updated_file = os.path.splitext(new_file)[0] + "_updated.txt"
    with open(updated_file, "w", encoding="utf-8") as f:
        f.write("\n".join(updated_lines))

    removed = total_new_lines - len(updated_lines)

    # Caption for user
    user = await c.get_entity(chat_id)
    fancy_user = stylish_user(user)
    caption_user = (
        f"✅ <b>Update Complete</b>\n\n"
        f"👤 <b>User</b>: {fancy_user}\n"
        f"🆔 <b>User ID</b>: <code>{chat_id}</code>\n"
        f"🕒 <b>Time (IST)</b>: {ist_now_str()}\n\n"
        f"📂 Old Links: <code>{len(old_links)}</code>\n"
        f"📂 New Lines: <code>{total_new_lines}</code>\n"
        f"✅ Added: <code>{len(updated_lines)}</code>\n"
        f"❌ Ignored: <code>{removed}</code>\n"
        f"🎬 Videos: <code>{video_count}</code> • 📄 PDFs: <code>{pdf_count}</code>\n"
        f"— {BRAND}"
    )

    # Send updated file to user with buttons
    buttons = [
        [Button.text("🔄 Start Over"), Button.text("📥 Download Again")],
        [Button.text("❌ Cancel")]
    ]
    await c.send_file(chat_id, updated_file, caption=caption_user, parse_mode="html", buttons=buttons)

    # Send logs
    caption_log = (
        f"📢 <b>Conversion Done</b>\n"
        f"👤 User: {fancy_user}\n"
        f"🆔 ID: <code>{chat_id}</code>\n"
        f"⏰ Time: {ist_now_str()}\n"
        f"Old: {len(old_links)} | New: {total_new_lines} | Added: {len(updated_lines)}\n"
        f"Videos: {video_count} | PDFs: {pdf_count}"
    )
    await c.send_message(LOG_CHANNEL, caption_log, parse_mode="html")
    await c.send_file(LOG_CHANNEL, old_file, caption="📂 Old File")
    await c.send_file(LOG_CHANNEL, new_file, caption="📂 New File")
    await c.send_file(LOG_CHANNEL, updated_file, caption="📂 Updated File")

    # Reset session
    sess.clear()

# =====================
# RUN BOT
# =====================
print("✅ Bot is running...")
bot.run_until_disconnected()
