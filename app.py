import os
import re
import asyncio
import logging
from datetime import datetime
from threading import Thread
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, unquote

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
    """Return current time in Indian Standard Time."""
    ist = pytz.timezone("Asia/Kolkata")
    return datetime.now(ist).strftime("%d-%m-%Y %I:%M:%S %p")

def stylish_user(u):
    uname = f"@{u.username}" if getattr(u, "username", None) else "N/A"
    return f"{u.first_name or 'User'} ({uname})"

def normalize_link(url: str) -> str:
    """
    Normalize URLs for comparison:
    - decode %xx
    - drop scheme, lowercase host, strip leading 'www.'
    - remove trailing slash on path
    - remove tracking query params (utm_*, fbclid, gclid, gclsrc, ref, ref_src, mc_cid, mc_eid, igshid, spm)
    - keep other query params (sorted)
    - drop fragments
    Returns a canonical string like: example.com/path?key=value
    """
    if not url:
        return ""
    url = unquote(url.strip())
    p = urlparse(url)

    # If somehow scheme absent but looks like a URL, keep as-is best we can
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = unquote(p.path or "")
    if path.endswith("/") and path != "/":
        path = path[:-1]

    # filter tracking params
    tracking_keys = {"fbclid", "gclid", "gclsrc", "ref", "ref_src", "mc_cid", "mc_eid", "igshid", "spm"}
    params = []
    for k, v in parse_qsl(p.query or "", keep_blank_values=True):
        lk = k.lower()
        if lk.startswith("utm_") or lk in tracking_keys:
            continue
        params.append((k, v))
    params.sort()
    query = urlencode(params, doseq=True)

    norm = f"{netloc}{path}"
    if query:
        norm += f"?{query}"
    return norm

def extract_link_from_line(line: str) -> str:
    """
    Extract the URL from a 'Title: link' line.
    Prefer an explicit http(s)://... in the line (anywhere).
    Fallback: take substring after the last ':' or last whitespace.
    """
    if not line:
        return ""
    m = re.search(r'(https?://\S+)', line)
    if m:
        return normalize_link(m.group(1))
    # Fallbacks if URL not matched explicitly
    if ":" in line:
        return normalize_link(line.rsplit(":", 1)[-1].strip())
    parts = line.split()
    return normalize_link(parts[-1] if parts else "")

# ============== SESSIONS ==============
SESSIONS = {}  # { chat_id: {"awaiting": "old"/"new"/None, "old": path, "new": path, "updated": path} }

# ============== TELEGRAM BOT ==============
client = None

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
            "• Each line should be: <b>Title: link</b>\n"
            "• Send two .txt files:\n"
            "   1) <b>Old file</b>\n"
            "   2) <b>New file</b>\n"
            "• Tap <b>Convert</b> → removes lines from NEW if their link exists in OLD.\n\n"
            f"🕒 <b>Time (IST)</b>: {ist_now_str()}\n"
            f"— {BRAND}",
            buttons=buttons, parse_mode="html"
        )

    # /cancel
    @c.on(events.NewMessage(pattern=r'^/cancel$'))
    async def cancel_handler(event):
        chat_id = event.chat_id
        SESSIONS[chat_id] = {"awaiting": None, "old": None, "new": None, "updated": None}
        await event.respond(f"✅ Process cancelled. You can start again with /start.\n— {BRAND}")

    # Inline buttons
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
            await convert_now(c, chat_id)

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
        safe_name = (event.file.name or f"{which}.txt").replace("/", "_").replace("\\", "_")
        # Avoid overwriting when both files share same filename:
        path = DATA_DIR / (f"{which}_{safe_name}")  # no user id in filename
        await event.download_media(file=str(path))
        sess[which] = str(path)
        sess["awaiting"] = None

        await event.respond(
            f"✅ <b>{which.capitalize()} file</b> saved.\nNow tap <b>Convert</b> when both files are uploaded.",
            parse_mode="html"
        )

# ============== CONVERSION ==============
async def convert_now(c: TelegramClient, chat_id: int):
    sess = SESSIONS.get(chat_id) or {}
    old_file = sess.get("old")
    new_file = sess.get("new")

    if not old_file or not new_file:
        await c.send_message(chat_id, "❌ Please upload both OLD and NEW .txt files first.")
        return

    # Build set of normalized links from OLD file
    old_links = set()
    with open(old_file, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            norm = extract_link_from_line(line)
            if norm:
                old_links.add(norm)

    # Process NEW file: keep only lines whose (normalized) link not in old_links; de-dupe within new
    kept_lines = []
    seen_links = set()
    kept_videos = 0
    kept_pdfs = 0
    total_new_lines = 0

    with open(new_file, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            total_new_lines += 1
            # Extract + normalize link
            m = re.search(r'(https?://\S+)', line)
            link_for_count = m.group(1).lower() if m else ""
            norm = extract_link_from_line(line)
            if not norm:
                # If a line has no detectable URL, keep it (optional: you can choose to drop)
                kept_lines.append(line)
                continue
            if norm in old_links or norm in seen_links:
                continue  # remove this line
            kept_lines.append(line)
            seen_links.add(norm)
            # Counts by extension
            if link_for_count.endswith((".mp4", ".mkv", ".mov", ".avi")):
                kept_videos += 1
            if link_for_count.endswith(".pdf"):
                kept_pdfs += 1

    kept_count = len(kept_lines)
    removed = max(0, total_new_lines - kept_count)

    # Compose final file content (keep lines + summary footer)
    final_lines = list(kept_lines) + [
        "",
        f"# Total Updated Lines: {kept_count}",
        f"# Videos: {kept_videos}",
        f"# PDFs: {kept_pdfs}",
    ]

    # Save updated file alongside NEW (same base + _updated.txt; no user id)
    base = os.path.splitext(new_file)[0]
    updated_file = f"{base}_updated.txt"
    with open(updated_file, "w", encoding="utf-8") as f:
        f.write("\n".join(final_lines))

    # Build captions
    user = await c.get_entity(chat_id)
    fancy_user = stylish_user(user)
    user_caption = (
        "✨ <b>Link Cleaning Complete</b> ✨\n\n"
        f"👤 <b>User</b>: {fancy_user}\n"
        f"🆔 <b>User ID</b>: <code>{chat_id}</code>\n"
        f"🕒 <b>Time (IST)</b>: {ist_now_str()}\n\n"
        f"📂 <b>Old Links</b>: <code>{len(old_links)}</code>\n"
        f"🆕 <b>New Lines</b>: <code>{total_new_lines}</code>\n"
        f"✅ <b>Updated Lines</b>: <code>{kept_count}</code>\n"
        f"❌ <b>Removed</b>: <code>{removed}</code>\n"
        f"🎬 <b>Videos</b>: <code>{kept_videos}</code> • 📄 <b>PDFs</b>: <code>{kept_pdfs}</code>\n\n"
        f"— {BRAND}"
    )

    buttons = [
        [Button.text("🔄 Start Over", resize=True), Button.text("📥 Download Updated Again", resize=True)],
        [Button.text("❌ Cancel", resize=True)]
    ]
    await c.send_file(chat_id, updated_file, caption=user_caption, parse_mode="html", buttons=buttons)

    # Log channel (updated file only to reduce noise)
    await c.send_file(LOG_CHANNEL, updated_file, caption=f"✅ Cleaned File by {fancy_user}", parse_mode="html")

    # Update session
    sess["updated"] = updated_file
    sess["old"] = None
    sess["new"] = None

# ============== EXTRA BUTTONS ==============
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
