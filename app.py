import os
import asyncio
from telethon import TelegramClient, events, Button
from datetime import datetime
import pytz
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))

client = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Flask app for Render keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running successfully!"

# Globals for file handling
user_sessions = {}

# Get IST Time
def get_ist_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime("%d-%m-%Y %I:%M:%S %p")

# Start Command
@client.on(events.NewMessage(pattern="/start"))
async def start(event):
    await event.respond(
        f"👋 Hello {event.sender.first_name}!\n\n"
        "Upload your **Old File** first.\n\n"
        f"Made with ❤️ by ᴍᴀʀꜱʜᴍᴀʟʟᴏᴡ×͜×",
        buttons=[
            [Button.text("❌ Cancel", resize=True)]
        ]
    )
    user_sessions[event.sender_id] = {"status": "waiting_old"}

# Cancel Command
@client.on(events.NewMessage(pattern="/cancel"))
async def cancel(event):
    user_sessions.pop(event.sender_id, None)
    await event.respond("✅ Process cancelled. Start again with /start.\n\nᴍᴀʀꜱʜᴍᴀʟʟᴏᴡ×͜×")

# File Upload Handler
@client.on(events.NewMessage(func=lambda e: e.file))
async def handle_file(event):
    user_id = event.sender_id
    if user_id not in user_sessions:
        await event.respond("Please start with /start first.")
        return

    status = user_sessions[user_id].get("status")

    if status == "waiting_old":
        old_file_path = f"old_{user_id}.txt"
        await event.download_media(file=old_file_path)
        user_sessions[user_id]["old_file"] = old_file_path
        user_sessions[user_id]["status"] = "waiting_new"
        await event.respond("✅ Old file received. Now upload **New File**.")
    elif status == "waiting_new":
        new_file_path = f"new_{user_id}.txt"
        await event.download_media(file=new_file_path)
        user_sessions[user_id]["new_file"] = new_file_path
        await process_files(event, user_id)

async def process_files(event, user_id):
    session = user_sessions[user_id]
    old_file = session["old_file"]
    new_file = session["new_file"]

    # Read old links
    with open(old_file, 'r', encoding='utf-8') as f:
        old_links = set(line.strip() for line in f if line.strip())

    # Read new links
    with open(new_file, 'r', encoding='utf-8') as f:
        new_links = [line.strip() for line in f if line.strip()]

    # Filter new links
    updated_links = [link for link in new_links if link not in old_links]

    # Save updated file
    updated_file = f"{os.path.splitext(new_file)[0]}_updated.txt"
    with open(updated_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(updated_links))

    total_old = len(old_links)
    total_new = len(new_links)
    total_updated = len(updated_links)

    caption = (
        f"✨ **Links Updated Successfully** ✨\n\n"
        f"🧾 **Old File Links**: `{total_old}`\n"
        f"🆕 **New File Links**: `{total_new}`\n"
        f"✅ **Remaining Links**: `{total_updated}`\n\n"
        f"🕒 **Time (IST)**: {get_ist_time()}\n\n"
        f"Made with ❤️ by ᴍᴀʀꜱʜᴍᴀʟʟᴏᴡ×͜×"
    )

    await event.respond(file=updated_file, message=caption)

    # Send logs to channel
    sender = await event.get_sender()
    log_text = (
        f"📢 **Process Completed**\n\n"
        f"👤 User: {sender.first_name} (@{sender.username})\n"
        f"🆔 User ID: `{sender.id}`\n\n"
        f"🧾 Old Links: {total_old}\n"
        f"🆕 New Links: {total_new}\n"
        f"✅ Updated: {total_updated}\n"
        f"🕒 Time: {get_ist_time()}\n"
    )
    await client.send_message(LOG_CHANNEL, log_text, file=[old_file, new_file, updated_file])

    # Cleanup session
    user_sessions.pop(user_id, None)

# Run both Flask and Bot
async def main():
    await client.start()
    print("Bot is running...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    import threading
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    asyncio.run(main())
