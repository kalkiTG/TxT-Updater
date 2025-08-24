import os
from flask import Flask
from telethon import TelegramClient, events, Button
from dotenv import load_dotenv
import asyncio
from datetime import datetime

# Load environment variables
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL"))

# Flask app for keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# Telegram Bot
bot = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Memory for storing user files and state
user_files = {}
updated_files = {}

# Function to show main menu
async def show_main_menu(event):
    buttons = [
        [Button.text("📂 Upload Old File", resize=True), Button.text("📂 Upload New File", resize=True)],
        [Button.text("✅ Convert", resize=True)]
    ]
    await event.respond(
        "<b>👋 Welcome to the Link Cleaner Bot!</b>\n\n"
        "✅ Upload your <b>Old File</b> and <b>New File</b>, then click <b>Convert</b>.\n\n"
        "<i>This bot removes duplicate links from the new file based on the old file.</i>",
        buttons=buttons,
        parse_mode='html'
    )

# Start Command
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_files[event.chat_id] = {'old': None, 'new': None}
    await show_main_menu(event)

# Cancel Command
@bot.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    chat_id = event.chat_id
    if chat_id in user_files:
        user_files[chat_id] = {'old': None, 'new': None}
    await event.respond("<b>❌ Process canceled! Start fresh by uploading files again.</b>", parse_mode='html')
    await show_main_menu(event)

# File Upload Handler
@bot.on(events.NewMessage)
async def file_handler(event):
    if event.file and event.file.name.endswith('.txt'):
        chat_id = event.chat_id
        if chat_id not in user_files:
            user_files[chat_id] = {'old': None, 'new': None}

        # Determine file type
        if user_files[chat_id]['old'] is None:
            file_type = 'old'
        elif user_files[chat_id]['new'] is None:
            file_type = 'new'
        else:
            await event.respond("<b>You already uploaded both files!</b>\nUse /cancel to start over.", parse_mode='html')
            return

        if not os.path.exists('downloads'):
            os.makedirs('downloads')

        file_path = f"downloads/{file_type}_{event.file.name}"
        await event.download_media(file_path)
        user_files[chat_id][file_type] = file_path

        await event.respond(f"<b>✅ {file_type.capitalize()} file uploaded successfully!</b>", parse_mode='html')

# Convert Button Handler
@bot.on(events.NewMessage(pattern='✅ Convert'))
async def convert_handler(event):
    chat_id = event.chat_id
    if chat_id not in user_files or not user_files[chat_id]['old'] or not user_files[chat_id]['new']:
        await event.respond("<b>Please upload both Old and New files first!</b>", parse_mode='html')
        return

    old_file_path = user_files[chat_id]['old']
    new_file_path = user_files[chat_id]['new']

    # Process files
    with open(old_file_path, 'r', encoding='utf-8') as f:
        old_links = set(line.strip() for line in f if line.strip())
    with open(new_file_path, 'r', encoding='utf-8') as f:
        new_links = [line.strip() for line in f if line.strip()]

    updated_links = [link for link in new_links if link not in old_links]
    removed_count = len(new_links) - len(updated_links)

    base, ext = os.path.splitext(new_file_path)
    updated_file_path = f"{base}_updated{ext}"
    with open(updated_file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(updated_links))

    updated_files[chat_id] = updated_file_path

    # Count media
    video_count = sum(1 for link in updated_links if link.lower().endswith(('.mp4', '.mkv')))
    pdf_count = sum(1 for link in updated_links if link.lower().endswith('.pdf'))

    # Stylish caption for user
    caption = f"""
<b>✅ Update Completed!</b>

<b>📂 Old Links:</b> {len(old_links)}
<b>🆕 New Links:</b> {len(new_links)}
<b>🔍 Updated Links:</b> {len(updated_links)}
<b>❌ Removed:</b> {removed_count}

<b>🎬 Videos:</b> {video_count}
<b>📄 PDFs:</b> {pdf_count}

<i>✨ Powered by Stylish Bot ✨</i>
"""

    # Log caption with user info
    user = await bot.get_entity(chat_id)
    username = f"@{user.username}" if user.username else "N/A"
    log_caption = f"""
<b>📢 New Conversion Completed</b>

<b>👤 User:</b> {username}
<b>🆔 User ID:</b> {chat_id}
<b>⏰ Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>📂 Old Links:</b> {len(old_links)}
<b>🆕 New Links:</b> {len(new_links)}
<b>✅ Updated Links:</b> {len(updated_links)}
<b>❌ Removed:</b> {removed_count}

<b>🎬 Videos:</b> {video_count}
<b>📄 PDFs:</b> {pdf_count}
"""

    # Send files to log channel
    await bot.send_file(LOG_CHANNEL, old_file_path, caption="<b>📂 Old File</b>", parse_mode='html')
    await bot.send_file(LOG_CHANNEL, new_file_path, caption="<b>🆕 New File</b>", parse_mode='html')
    await bot.send_file(LOG_CHANNEL, updated_file_path, caption=log_caption, parse_mode='html')

    # Send updated file to user with extra buttons
    buttons = [
        [Button.text("🔄 Start Over", resize=True), Button.text("📥 Download Updated Again", resize=True)],
        [Button.text("❓ Help", resize=True)]
    ]
    await event.respond(file=updated_file_path, caption="<b>✅ Here is your updated file!</b>", buttons=buttons, parse_mode='html')

    # Clear uploaded old/new for fresh start (keep updated path for re-download)
    user_files[chat_id] = {'old': None, 'new': None}

# Handle post-conversion buttons
@bot.on(events.NewMessage(pattern='🔄 Start Over'))
async def start_over(event):
    chat_id = event.chat_id
    user_files[chat_id] = {'old': None, 'new': None}
    await show_main_menu(event)

@bot.on(events.NewMessage(pattern='📥 Download Updated Again'))
async def download_again(event):
    chat_id = event.chat_id
    if chat_id in updated_files and os.path.exists(updated_files[chat_id]):
        await event.respond(file=updated_files[chat_id], caption="<b>📥 Your updated file again!</b>", parse_mode='html')
    else:
        await event.respond("<b>No updated file found! Please upload files and convert first.</b>", parse_mode='html')

@bot.on(events.NewMessage(pattern='❓ Help'))
async def help_handler(event):
    help_text = """
<b>📘 Help Menu</b>

1️⃣ /start → Start the bot  
2️⃣ /cancel → Cancel current process  
3️⃣ Upload Old File and New File  
4️⃣ Tap ✅ Convert to clean links  
5️⃣ Download your updated file  
"""
    await event.respond(help_text, parse_mode='html')

# Flask + Bot
def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

loop = asyncio.get_event_loop()
loop.create_task(bot.run_until_disconnected())

if __name__ == "__main__":
    from threading import Thread
    Thread(target=run_flask).start()
    loop.run_forever()
