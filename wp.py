import asyncio
import time
import os
import threading
from pyrogram import Client, filters, types
from pyrogram.errors import MessageNotModified
from pymongo import MongoClient
from neonize.client import NewClient
from neonize.events import MessageEv, ConnectedEv, LoggedOutEv

API_ID = 21705136
API_HASH = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8644651500:AAGx-3CxjaWP8r6CA9vB7073VwFHQc9U3nI"
ADMIN_ID = 8176628365

MONGO_DB_URI = "mongodb+srv://Krishna:pss968048@cluster0.4rfuzro.mongodb.net/?retryWrites=true&w=majority"

db_client = MongoClient(MONGO_DB_URI)
db = db_client["whatsapp_bot_pro"]
configs_col = db["messages"]
sessions_col = db["sessions"]
stats_col = db["stats"]
contacts_col = db["contacts"]
settings_col = db["settings"]

app = Client(
    "wa_bot_session",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH
)

# {session_id: {"client": obj, "connect_time": float, "tg_user_id": int}}
user_states = {}
active_clients = {}


# ─────────────────────────── PER-USER INIT ────────────────────────────

def ensure_user_messages(tg_user_id):
    """Create default message sequence for a new user."""
    if configs_col.count_documents({"tg_user_id": tg_user_id}) == 0:
        configs_col.insert_many([
            {"tg_user_id": tg_user_id, "step": 1, "type": "text",
             "text": "Hello! Thank you for contacting us."},
            {"tg_user_id": tg_user_id, "step": 2, "type": "text",
             "text": "Please wait a moment while we process your request."},
            {"tg_user_id": tg_user_id, "step": 3, "type": "text",
             "text": "How can we assist you today?"}
        ])


def ensure_user_settings(tg_user_id):
    """Create default settings for a new user."""
    if not settings_col.find_one({"tg_user_id": tg_user_id, "key": "bot_status"}):
        settings_col.insert_one({"tg_user_id": tg_user_id, "key": "bot_status", "status": "started"})


# ─────────────────────────── KEYBOARDS ───────────────────────────────

def get_main_keyboard(tg_user_id):
    status_doc = settings_col.find_one({"tg_user_id": tg_user_id, "key": "bot_status"})
    current_status = status_doc.get("status", "started") if status_doc else "started"
    toggle_text = "⏸️ Stop Auto-Reply" if current_status == "started" else "▶️ Start Auto-Reply"
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📱 Add WhatsApp Account", callback_data="add_wa")],
        [
            types.InlineKeyboardButton("⚙️ Manage Accounts", callback_data="manage_accs"),
            types.InlineKeyboardButton("📊 System Stats", callback_data="live_stats")
        ],
        [
            types.InlineKeyboardButton("📝 Configure Sequence", callback_data="config_msgs"),
            types.InlineKeyboardButton("👁️ View Messages", callback_data="view_msgs")
        ],
        [types.InlineKeyboardButton(toggle_text, callback_data="toggle_bot")]
    ])


def get_add_wa_keyboard():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🔑 Login via Pairing Code", callback_data="add_wa_pair")],
        [types.InlineKeyboardButton("❌ Cancel", callback_data="back_main")]
    ])


def get_config_msgs_keyboard():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("Set Message 1", callback_data="set_msg_1"),
            types.InlineKeyboardButton("Set Message 2", callback_data="set_msg_2")
        ],
        [types.InlineKeyboardButton("Set Message 3", callback_data="set_msg_3")],
        [types.InlineKeyboardButton("🔙 Return to Menu", callback_data="back_main")]
    ])


def get_accounts_keyboard(accounts_list):
    buttons = []
    for index, account in enumerate(accounts_list, 1):
        status_icon = "🟢" if account["is_active"] else "🔴"
        display_text = f"Slot {index} | {status_icon} | {account['number']}"
        buttons.append([
            types.InlineKeyboardButton(display_text, callback_data=f"ignore_{account['id']}"),
            types.InlineKeyboardButton("🗑️ Logout", callback_data=f"del_acc_{account['id']}")
        ])
    buttons.append([types.InlineKeyboardButton("🔙 Return to Menu", callback_data="back_main")])
    return types.InlineKeyboardMarkup(buttons)


def get_stats_keyboard(stats_list):
    buttons = [[types.InlineKeyboardButton(f"{k}: {v}", callback_data="ignore_stat")]
               for k, v in stats_list.items()]
    buttons.append([types.InlineKeyboardButton("🔙 Return to Menu", callback_data="back_main")])
    return types.InlineKeyboardMarkup(buttons)


# ─────────────────────────── HELPERS ────────────────────────────────

def find_free_session_id(tg_user_id):
    """Each user gets their own numbered slots: {tg_user_id}_{n}"""
    for i in range(1, 100):
        sid = f"{tg_user_id}_{i}"
        if not sessions_col.find_one({"session_id": sid}):
            return sid
    return None


def list_accounts_conceptual(tg_user_id):
    result = []
    for sess in sessions_col.find({"tg_user_id": tg_user_id}).sort("session_id", 1):
        result.append({
            "id": sess["session_id"],
            "number": sess.get("number", "Unknown"),
            "is_active": sess.get("is_active", False)
        })
    return result


def count_accounts(tg_user_id):
    live_count = sum(
        1 for sid, info in active_clients.items()
        if info.get("tg_user_id") == tg_user_id
    )
    current_today = time.strftime('%Y-%m-%d')
    stats_data = stats_col.find_one({"tg_user_id": tg_user_id, "date": current_today})
    total_msgs = stats_data.get("total_msgs_today", 0) if stats_data else 0
    total_sessions = sessions_col.count_documents({"tg_user_id": tg_user_id})
    total_contacts = contacts_col.count_documents({"tg_user_id": tg_user_id})
    return {
        "Total Messages Sent Today": total_msgs,
        "Active WA Connections": live_count,
        "Total WA Accounts": total_sessions,
        "Unique Users Reached": total_contacts
    }


# ───────────────────── WA AUTO-RESPONDER CORE ────────────────────────

async def auto_responder_task(session_id, chat_jid_obj, client, tg_user_id, msg_timestamp):
    """Send sequence messages to a new first-time contact."""
    try:
        user_number = getattr(chat_jid_obj, 'User', "")
        server_type = getattr(chat_jid_obj, 'Server', "")

        # Skip groups and non-numeric JIDs
        if server_type in ["g.us", "lid"] or not user_number.isdigit():
            return

        # ── FIX: Ignore history/old messages synced on login ──────────
        client_info = active_clients.get(session_id, {})
        connect_time = client_info.get("connect_time", time.time())
        # Messages older than 60 seconds before connect time = history sync, skip them
        if msg_timestamp < (connect_time - 60):
            return
        # ─────────────────────────────────────────────────────────────

        # Check if bot is active for this user
        status_doc = settings_col.find_one({"tg_user_id": tg_user_id, "key": "bot_status"})
        if status_doc and status_doc.get("status") == "stopped":
            return

        # Check if this number has already been messaged FROM THIS WA account
        if contacts_col.find_one({"session_id": session_id, "number": user_number}):
            return

        # Load this user's configured messages
        db_messages = list(configs_col.find({"tg_user_id": tg_user_id}).sort("step", 1))
        if not db_messages:
            return

        messages_sent = 0

        for msg in db_messages:
            await asyncio.sleep(2)

            try:
                presence = "composing" if msg["type"] == "text" else "recording"
                client.send_presence(presence, chat_jid_obj)
            except Exception:
                pass

            await asyncio.sleep(1.5)

            sent_ok = False
            try:
                client.send_message(chat_jid_obj, msg.get("text", ""))
                sent_ok = True
            except Exception:
                pass

            if sent_ok:
                messages_sent += 1
                today = time.strftime('%Y-%m-%d')
                stats_col.update_one(
                    {"tg_user_id": tg_user_id, "date": today},
                    {"$inc": {"total_msgs_today": 1}},
                    upsert=True
                )

            try:
                client.send_presence("paused", chat_jid_obj)
            except Exception:
                pass

        # Mark this number as contacted for this WA session
        if messages_sent > 0:
            contacts_col.insert_one({
                "session_id": session_id,
                "number": user_number,
                "tg_user_id": tg_user_id,
                "timestamp": time.time()
            })

    except Exception:
        pass


# ─────────────────── WA CLIENT INIT / LOGOUT ─────────────────────────

def _make_message_handler(session_id, tg_user_id, loop):
    """Factory to avoid closure bugs in loops."""
    def handler(client_obj, message: MessageEv):
        try:
            msg_info = message.Info
            msg_source = msg_info.MessageSource
            if msg_source.IsFromMe:
                return
            chat_jid_obj = msg_source.Chat
            try:
                ts = msg_info.Timestamp
                msg_timestamp = ts.timestamp() if hasattr(ts, 'timestamp') else float(ts)
            except Exception:
                msg_timestamp = time.time()

            asyncio.run_coroutine_threadsafe(
                auto_responder_task(session_id, chat_jid_obj, client_obj, tg_user_id, msg_timestamp),
                loop
            )
        except Exception:
            pass
    return handler


def _make_logout_handler(session_id):
    def handler(client_obj, event: LoggedOutEv):
        try:
            sessions_col.delete_one({"session_id": session_id})
            contacts_col.delete_many({"session_id": session_id})
            if session_id in active_clients:
                del active_clients[session_id]
        except Exception:
            pass
    return handler


async def initialize_wa_client(session_id, phone_number, callback_msg, user_id):
    current_loop = asyncio.get_running_loop()
    user_states[user_id]["session_id"] = session_id
    user_states[user_id]["phone_number"] = phone_number

    db_path = f"session_{session_id}.db"
    client = NewClient(db_path)
    client.event(MessageEv)(_make_message_handler(session_id, user_id, current_loop))
    client.event(LoggedOutEv)(_make_logout_handler(session_id))

    # connect_time is set NOW; verified timestamp after login success
    active_clients[session_id] = {
        "client": client,
        "connect_time": time.time(),
        "tg_user_id": user_id
    }

    threading.Thread(target=lambda: _run_client(client), daemon=True).start()
    await asyncio.sleep(4)

    cancel_kb = types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("❌ Cancel Process", callback_data="back_main")]]
    )

    try:
        try:
            pairing_code = client.PairPhone(phone_number, True)
        except Exception:
            try:
                pairing_code = client.pair_phone(phone_number)
            except Exception:
                pairing_code = None

        if not pairing_code:
            try:
                sent = await callback_msg.edit_text(
                    "❌ **Connection Error**\nFailed to get pairing code. Check the number and try again.",
                    reply_markup=cancel_kb
                )
                user_states[user_id]["active_msg_id"] = sent.id
            except MessageNotModified:
                pass
            return

        user_states[user_id]["state"] = "awaiting_login_confirmation"
        success_text = (
            f"✅ **Account Slot ID: {session_id} Initialized.**\n\n"
            f"🔢 Your Pairing Code is: `{pairing_code}`\n\n"
            f"Open WhatsApp ➜ Linked Devices ➜ Link with Phone Number and enter this code."
        )
        try:
            sent = await callback_msg.edit_text(success_text, reply_markup=cancel_kb)
            user_states[user_id]["active_msg_id"] = sent.id
        except MessageNotModified:
            sent = callback_msg

        asyncio.create_task(verify_login_loop(client, session_id, phone_number, user_id, sent.id))

    except Exception:
        try:
            sent = await callback_msg.edit_text("Pairing failed. Please try again.", reply_markup=cancel_kb)
            user_states[user_id]["active_msg_id"] = sent.id
        except Exception:
            pass


def _run_client(client):
    try:
        client.connect()
    except Exception:
        pass


async def logout_client(session_id):
    info = active_clients.get(session_id)
    if info:
        try:
            info["client"].logout()
        except Exception:
            pass
        del active_clients[session_id]

    db_file = f"session_{session_id}.db"
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except Exception:
            pass

    sessions_col.delete_one({"session_id": session_id})
    contacts_col.delete_many({"session_id": session_id})


async def verify_login_loop(client_instance, session_id, phone_number, user_id, active_msg_id):
    for _ in range(120):
        await asyncio.sleep(2)
        try:
            is_auth = False
            if hasattr(client_instance, 'is_logged_in'):
                is_auth = client_instance.is_logged_in
            else:
                try:
                    me = client_instance.get_me()
                    is_auth = me is not None and hasattr(me, 'JID')
                except Exception:
                    pass

            if is_auth:
                actual_number = phone_number
                try:
                    me = client_instance.get_me()
                    actual_number = str(me.JID).split('@')[0]
                except Exception:
                    pass

                if not sessions_col.find_one({"session_id": session_id}):
                    sessions_col.insert_one({
                        "session_id": session_id,
                        "number": actual_number,
                        "is_active": True,
                        "tg_user_id": user_id
                    })

                    # ── FIX: Reset connect_time AFTER successful login so history
                    #    messages (which arrive during sync) are all ignored ──
                    if session_id in active_clients:
                        active_clients[session_id]["connect_time"] = time.time()

                    try:
                        await app.delete_messages(user_id, active_msg_id)
                    except Exception:
                        pass

                    try:
                        await app.send_message(
                            chat_id=user_id,
                            text=(
                                f"✅ **Authentication Successful!**\n\n"
                                f"Session ID: `{session_id}`\n"
                                f"Connected Number: `{actual_number}`\n\n"
                                "Your account is now live. New users who message this number will receive your auto-reply sequence."
                            ),
                            reply_markup=get_main_keyboard(user_id)
                        )
                    except Exception:
                        pass
                break
        except Exception:
            pass


# ──────────────── STARTUP: RECONNECT SAVED SESSIONS ─────────────────

async def reconnect_all_sessions():
    """On bot restart, reconnect every saved WA session."""
    loop = asyncio.get_running_loop()
    all_sessions = list(sessions_col.find({}))

    for sess in all_sessions:
        session_id = sess.get("session_id")
        tg_user_id = sess.get("tg_user_id")
        if not session_id or not tg_user_id:
            continue

        db_path = f"session_{session_id}.db"
        if not os.path.exists(db_path):
            # Session file lost — clean up DB entry
            sessions_col.delete_one({"session_id": session_id})
            continue

        try:
            client = NewClient(db_path)
            client.event(MessageEv)(_make_message_handler(session_id, tg_user_id, loop))
            client.event(LoggedOutEv)(_make_logout_handler(session_id))

            active_clients[session_id] = {
                "client": client,
                "connect_time": time.time(),
                "tg_user_id": tg_user_id
            }

            threading.Thread(target=lambda c=client: _run_client(c), daemon=True).start()
            await asyncio.sleep(1)
        except Exception:
            pass


# ─────────────────── TELEGRAM BOT HANDLERS ───────────────────────────

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    ensure_user_messages(user_id)
    ensure_user_settings(user_id)

    if user_id not in user_states:
        user_states[user_id] = {}

    welcome_text = (
        "🤖 **WhatsApp Cloud Automation Engine**\n\n"
        "Welcome to your personal Multi-Device control panel.\n"
        "Each user has their own isolated WA accounts, messages, and database."
    )
    try:
        await message.reply(welcome_text, reply_markup=get_main_keyboard(user_id))
    except Exception:
        pass


@app.on_callback_query()
async def handle_callbacks(client, callback_query):
    callback_data_string = callback_query.data
    user_id = callback_query.from_user.id

    if user_id not in user_states:
        user_states[user_id] = {}

    ensure_user_messages(user_id)
    ensure_user_settings(user_id)

    # ── BACK TO MAIN ───────────────────────────────────────────────
    if callback_data_string == "back_main":
        if "active_msg_id" in user_states[user_id]:
            try:
                await client.delete_messages(user_id, user_states[user_id]["active_msg_id"])
            except Exception:
                pass
            del user_states[user_id]["active_msg_id"]

        user_states[user_id] = {}
        try:
            await callback_query.message.edit_text(
                "🤖 **WhatsApp Cloud Automation Engine**\n\nSelect an operation below:",
                reply_markup=get_main_keyboard(user_id)
            )
        except MessageNotModified:
            pass

    # ── ADD WA ─────────────────────────────────────────────────────
    elif callback_data_string == "add_wa":
        new_session_id = find_free_session_id(user_id)
        if not new_session_id:
            try:
                await callback_query.answer("Maximum accounts reached.", show_alert=True)
            except Exception:
                pass
            return

        user_states[user_id]["session_id"] = new_session_id
        try:
            await callback_query.message.edit_text(
                f"**Allocating Instance Slot: {new_session_id}**\n\nSelect authentication method:",
                reply_markup=get_add_wa_keyboard()
            )
        except MessageNotModified:
            pass

    # ── PAIR LOGIN ─────────────────────────────────────────────────
    elif callback_data_string == "add_wa_pair":
        pending_session_id = user_states[user_id].get("session_id")
        if pending_session_id:
            user_states[user_id]["state"] = "awaiting_pair_number"
            try:
                await callback_query.message.edit_text(
                    "📱 **Enter Target WhatsApp Number**\n\n"
                    "Include country code (e.g., 919876543210).\n"
                    "⚠️ Do NOT include '+' or spaces.",
                    reply_markup=types.InlineKeyboardMarkup(
                        [[types.InlineKeyboardButton("❌ Cancel Process", callback_data="back_main")]]
                    )
                )
            except MessageNotModified:
                pass

    # ── MANAGE ACCOUNTS ────────────────────────────────────────────
    elif callback_data_string == "manage_accs":
        accounts_list = list_accounts_conceptual(user_id)
        try:
            await callback_query.message.edit_text(
                "📋 **Active Sessions Directory**",
                reply_markup=get_accounts_keyboard(accounts_list)
            )
        except MessageNotModified:
            pass

    # ── DELETE ACCOUNT ─────────────────────────────────────────────
    elif callback_data_string.startswith("del_acc_"):
        # Use split with maxsplit to handle session IDs containing underscores
        target_session_id = callback_data_string[len("del_acc_"):]

        # Security: verify this session belongs to this Telegram user
        sess = sessions_col.find_one({"session_id": target_session_id, "tg_user_id": user_id})
        if not sess:
            try:
                await callback_query.answer("Session not found or not yours.", show_alert=True)
            except Exception:
                pass
            return

        await logout_client(target_session_id)
        updated_list = list_accounts_conceptual(user_id)

        try:
            await callback_query.answer("Session purged successfully.", show_alert=True)
        except Exception:
            pass
        try:
            await callback_query.message.edit_text(
                "📋 **Active Sessions Directory**\nDirectory updated.",
                reply_markup=get_accounts_keyboard(updated_list)
            )
        except MessageNotModified:
            pass

    # ── LIVE STATS ─────────────────────────────────────────────────
    elif callback_data_string == "live_stats":
        stats = count_accounts(user_id)
        try:
            await callback_query.message.edit_text(
                "📊 **System Diagnostics & Metrics**",
                reply_markup=get_stats_keyboard(stats)
            )
        except MessageNotModified:
            pass

    # ── CONFIG MESSAGES ────────────────────────────────────────────
    elif callback_data_string == "config_msgs":
        try:
            await callback_query.message.edit_text(
                "⚙️ **Sequence Configuration Protocol**\nChoose a step to modify:",
                reply_markup=get_config_msgs_keyboard()
            )
        except MessageNotModified:
            pass

    # ── VIEW MESSAGES ──────────────────────────────────────────────
    elif callback_data_string == "view_msgs":
        all_msgs = list(configs_col.find({"tg_user_id": user_id}).sort("step", 1))
        display = ""
        for m in all_msgs:
            display += f"**Step {m['step']}** ➞ "
            if m.get('type') == 'text':
                display += f"Text: `{m.get('text', 'Empty')}`\n\n"
            else:
                display += f"Media ➞ Caption: `{m.get('text', 'No Caption')}`\n\n"
        try:
            await callback_query.message.edit_text(
                f"👁️ **Currently Active Sequence:**\n\n{display}",
                reply_markup=types.InlineKeyboardMarkup(
                    [[types.InlineKeyboardButton("🔙 Return to Menu", callback_data="back_main")]]
                )
            )
        except MessageNotModified:
            pass

    # ── TOGGLE BOT ─────────────────────────────────────────────────
    elif callback_data_string == "toggle_bot":
        status_doc = settings_col.find_one({"tg_user_id": user_id, "key": "bot_status"})
        current_status = status_doc.get("status", "started") if status_doc else "started"
        new_status = "stopped" if current_status == "started" else "started"

        settings_col.update_one(
            {"tg_user_id": user_id, "key": "bot_status"},
            {"$set": {"status": new_status}},
            upsert=True
        )

        label = "ACTIVATED" if new_status == "started" else "PAUSED"
        try:
            await callback_query.answer(f"Auto-Responder is now {label}.", show_alert=True)
        except Exception:
            pass
        try:
            await callback_query.message.edit_reply_markup(reply_markup=get_main_keyboard(user_id))
        except MessageNotModified:
            pass

    # ── SET MESSAGE STEP ───────────────────────────────────────────
    elif callback_data_string.startswith("set_msg_"):
        target_step = int(callback_data_string.split("_")[2])
        user_states[user_id]["state"] = f"awaiting_msg_{target_step}"
        user_states[user_id]["step"] = target_step
        try:
            await callback_query.message.edit_text(
                f"📥 **Data Intake Module**\n\n"
                f"Send the new content (Text, Photo, Video, or Document) for **Step {target_step}**."
            )
        except MessageNotModified:
            pass

    elif callback_data_string.startswith("ignore"):
        pass


@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_text_inputs(client, message):
    user_id = message.from_user.id

    if user_id not in user_states:
        user_states[user_id] = {}

    current_state = user_states[user_id].get("state")

    if current_state == "awaiting_pair_number":
        active_session_id = user_states[user_id].get("session_id")
        clean_number = "".join(filter(str.isdigit, message.text))
        try:
            processing_msg = await message.reply(
                f"⏳ Initializing cryptographic handshake for Slot {active_session_id}..."
            )
        except Exception:
            return
        await initialize_wa_client(active_session_id, clean_number, processing_msg, user_id)

    elif current_state and current_state.startswith("awaiting_msg_"):
        target_step = user_states[user_id].get("step")
        configs_col.update_one(
            {"tg_user_id": user_id, "step": target_step},
            {"$set": {"text": message.text, "type": "text"}},
            upsert=True
        )
        try:
            await message.reply(f"✅ Text payload integrated into Step {target_step}.")
        except Exception:
            pass
        user_states[user_id] = {}
        await start(client, message)


@app.on_message(filters.media & filters.private)
async def handle_media_inputs(client, message):
    user_id = message.from_user.id

    if user_id not in user_states:
        user_states[user_id] = {}

    current_state = user_states[user_id].get("state")

    if current_state and current_state.startswith("awaiting_msg_"):
        target_step = user_states[user_id].get("step")

        try:
            notif = await message.reply("⏳ Syncing media payload...")
        except Exception:
            return

        media_type = "document"
        if message.photo:
            media_type = "photo"
        elif message.video:
            media_type = "video"

        caption = message.caption or "Attached Media"

        configs_col.update_one(
            {"tg_user_id": user_id, "step": target_step},
            {"$set": {"text": caption, "type": media_type, "file_path": "remote_placeholder"}},
            upsert=True
        )
        try:
            await notif.edit_text(f"✅ Media payload integrated into Step {target_step}.")
        except MessageNotModified:
            pass

        user_states[user_id] = {}
        await start(client, message)


# ─────────────────────────── ENTRY POINT ─────────────────────────────

async def main():
    print("Initiating Multi-Device WA Server Node...")
    await reconnect_all_sessions()
    await app.start()
    print("Telegram bot is live. Listening for updates...")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
