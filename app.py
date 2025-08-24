import os
import asyncio
import logging
import threading
from flask import Flask
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pathlib import Path

# Load .env
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))

# Configure logging
logging.basicConfig(level=logging.INFO)

# Create folders for uploads
os.makedirs("downloads", exist_ok=True)

# Initialize Flask app for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running fine! ✅"

# Initialize Telegram bot
bot = Client("link_cleaner_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Session dictionary to manage user state
sessions = {}

# Start command
@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    sessions[message.from_user.id] = {}
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Upload Old File", callback_data="upload_old")],
        [InlineKeyboardButton("📂 Upload New File", callback_data="upload_new")],
        [InlineKeyboardButton("✅ Convert", callback_data="convert")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])
    await message.reply_text(
        f"👋 Hello {message.from_user.first_name}!\n\n"
        "**Welcome to Link Cleaner Bot**\n"
        "Upload two files:\n"
        "- Old links file\n"
        "- New links file\n\n"
        "Then click Convert.\n\n"
        "ᴍᴀʀꜱʜᴍᴀʟʟᴏᴡ×͜×",
        reply_markup=keyboard
    )

# Callback handler
@bot.on_callback_query()
async def button_handler(client, query):
    user_id = query.from_user.id
    if query.data == "upload_old":
        await query.message.reply_text("Please send me the **old file**.")
        sessions[user_id]["awaiting"] = "old"
    elif query.data == "upload_new":
        await query.message.reply_text("Please send me the **new file**.")
        sessions[user_id]["awaiting"] = "new"
    elif query.data == "convert":
        await convert_files(query.message, user_id)
    elif query.data == "cancel":
        sessions[user_id] = {}
        await query.message.reply_text("✅ Process canceled. Start again with /start.")

# File upload
@bot.on_message(filters.document)
async def handle_files(client, message):
    user_id = message.from_user.id
    if user_id not in sessions or "awaiting" not in sessions[user_id]:
        await message.reply_text("Please use /start and select what file to upload.")
        return

    file_type = sessions[user_id]["awaiting"]
    file_path = f"downloads/{user_id}_{file_type}.txt"
    await message.download(file_path)
    sessions[user_id][file_type] = file_path
    sessions[user_id].pop("awaiting", None)
    await message.reply_text(f"✅ {file_type.capitalize()} file saved!")

# Conversion logic
async def convert_files(message, user_id):
    session = sessions.get(user_id, {})
    old_file = session.get("old")
    new_file = session.get("new")

    if not old_file or not new_file:
        await message.reply_text("❌ Both files are required. Upload them first.")
        return

    # Read files
    with open(old_file, 'r', encoding='utf-8') as f:
        old_links = set(line.strip() for line in f if line.strip())
    with open(new_file, 'r', encoding='utf-8') as f:
        new_links = [line.strip() for line in f if line.strip()]

    # Filter
    updated_links = [link for link in new_links if link not in old_links]
    updated_file = new_file.replace(".txt", "_updated.txt")
    with open(updated_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(updated_links))

    # Stylish caption
    caption = (
        f"✨ **Link Cleaning Complete** ✨\n\n"
        f"👤 **User:** {message.from_user.first_name} (@{message.from_user.username})\n"
        f"🆔 **User ID:** `{user_id}`\n\n"
        f"📜 Old File: `{len(old_links)}` links\n"
        f"📜 New File: `{len(new_links)}` links\n"
        f"✅ Updated File: `{len(updated_links)}` links\n\n"
        f"ᴍᴀʀꜱʜᴍᴀʟʟᴏᴡ×͜×"
    )

    # Send files back to user
    await message.reply_document(updated_file, caption=caption)

    # Log channel
    await bot.send_message(LOG_CHANNEL, f"🔔 New Conversion by **{message.from_user.first_name}** (@{message.from_user.username})")
    await bot.send_document(LOG_CHANNEL, old_file, caption="📂 Old File")
    await bot.send_document(LOG_CHANNEL, new_file, caption="📂 New File")
    await bot.send_document(LOG_CHANNEL, updated_file, caption="📂 Updated File")

# Run Flask + Bot together
def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run()
