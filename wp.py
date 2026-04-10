"""
WhatsApp Cloud Automation Engine
Multi-user | Per-account DB | History-sync safe | Logout alerts
"""

import asyncio
import time
import os
import threading
from pyrogram import Client, filters, types, idle
from pyrogram.errors import MessageNotModified
from pymongo import MongoClient
from neonize.client import NewClient
from neonize.events import MessageEv, LoggedOutEv

# ═══════════════════════════ CONFIG ══════════════════════════════════

API_ID    = 21705136
API_HASH  = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8644651500:AAFFAPlgYfnVFetvQpmpcvf9p459Vgpq5xM"

MONGO_URI = "mongodb+srv://Krishna:pss968048@cluster0.4rfuzro.mongodb.net/?retryWrites=true&w=majority"

# After login, wait this many seconds before processing ANY message
# (lets history sync finish completely before we start responding)
SYNC_WAIT_SECONDS = 1

# ═══════════════════════════ DATABASE ════════════════════════════════

mongo     = MongoClient(MONGO_URI)
db        = mongo["wp"]
msg_col   = db["msg"]    # per-user message sequences
sess_col  = db["session"]    # WA session info
stats_col = db["stats"]       # daily message counts
seen_col  = db["contacts"]    # numbers already messaged per session
cfg_col   = db["settings"]    # per-user bot on/off

# ═══════════════════════════ PYROGRAM ════════════════════════════════

app = Client("wa_bot_session", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# Runtime stores
user_states    = {}   # tg_user_id → state dict
active_clients = {}   # session_id → {"client", "ready_after", "tg_user_id"}
processing_users = set()  # NAYA LINE: Concurrency lock ke liye

# ═══════════════════════ PER-USER BOOTSTRAP ══════════════════════════

def bootstrap_user(uid: int):
    """Ensure every user has default messages + settings on first use."""
    if msg_col.count_documents({"tg_user_id": uid}) == 0:
        msg_col.insert_many([
            {"tg_user_id": uid, "step": 1, "type": "text", "text": "👋 Hello! Thank you for reaching out."},
            {"tg_user_id": uid, "step": 2, "type": "text", "text": "⏳ Please hold on while we get things ready for you."},
            {"tg_user_id": uid, "step": 3, "type": "text", "text": "😊 How can we help you today?"},
        ])
    if not cfg_col.find_one({"tg_user_id": uid, "key": "bot_status"}):
        cfg_col.insert_one({"tg_user_id": uid, "key": "bot_status", "value": "on"})


def bot_is_on(uid: int) -> bool:
    doc = cfg_col.find_one({"tg_user_id": uid, "key": "bot_status"})
    return (doc.get("value", "on") == "on") if doc else True


def toggle_bot(uid: int) -> str:
    current = bot_is_on(uid)
    new_val = "off" if current else "on"
    cfg_col.update_one({"tg_user_id": uid, "key": "bot_status"}, {"$set": {"value": new_val}}, upsert=True)
    return new_val


def free_slot(uid: int):
    for i in range(1, 100):
        sid = f"{uid}_{i}"
        if not sess_col.find_one({"session_id": sid}):
            return sid
    return None


def user_sessions(uid: int):
    return list(sess_col.find({"tg_user_id": uid}).sort("session_id", 1))


def user_stats(uid: int) -> dict:
    today = time.strftime('%Y-%m-%d')
    stat  = stats_col.find_one({"tg_user_id": uid, "date": today}) or {}
    live  = sum(1 for info in active_clients.values() if info["tg_user_id"] == uid)
    return {
        "Messages Sent Today" : stat.get("count", 0),
        "Live Connections"    : live,
        "Total WP Accounts"   : sess_col.count_documents({"tg_user_id": uid}),
        "Total Users Reached" : seen_col.count_documents({"tg_user_id": uid}),
    }

# ═══════════════════════════ KEYBOARDS ═══════════════════════════════

def kb_main(uid: int):
    status  = bot_is_on(uid)
    tog_lbl = "Pause Auto-Reply" if status else "▶️  Resume Auto-Reply"
    tog_ico = "🟢 ON" if status else "🔴 OFF"
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(f"➕  Add WhatsApp Account", callback_data="add_wa")],
        [
            types.InlineKeyboardButton("📋  My Accounts",     callback_data="manage_accs"),
            types.InlineKeyboardButton("📊  Statistics",      callback_data="live_stats"),
        ],
        [
            types.InlineKeyboardButton("✏️  Edit Messages",   callback_data="config_msgs"),
            types.InlineKeyboardButton("Preview Set msg", callback_data="view_msgs"),
        ],
        [types.InlineKeyboardButton(f"{tog_lbl}  ({tog_ico})", callback_data="toggle_bot")],
    ])


def kb_cancel():
    return types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("Cancel", callback_data="back_main")]]
    )


def kb_back():
    return types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("Back to Menu", callback_data="back_main")]]
    )


def kb_add_wa():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Login via Pairing Code", callback_data="add_wa_pair")],
        [types.InlineKeyboardButton("Cancel",                 callback_data="back_main")],
    ])


def kb_config():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("Msg 1", callback_data="set_msg_1"),
            types.InlineKeyboardButton("Msg 2", callback_data="set_msg_2"),
            types.InlineKeyboardButton("Msg 3", callback_data="set_msg_3"),
        ],
        [types.InlineKeyboardButton("Back to Menu", callback_data="back_main")],
    ])


def kb_accounts(sessions: list):
    rows = []
    for i, s in enumerate(sessions, 1):
        ico  = "🟢" if s.get("is_active") else "🔴"
        num  = s.get("number", "Unknown")
        sid  = s["session_id"]
        rows.append([
            types.InlineKeyboardButton(f"#{i}  {ico}  {num}", callback_data=f"ignore_x"),
            types.InlineKeyboardButton("Logout",           callback_data=f"del_acc_{sid}"),
        ])
    rows.append([types.InlineKeyboardButton("Back to Menu", callback_data="back_main")])
    return types.InlineKeyboardMarkup(rows)


def kb_stats(stats: dict):
    rows = [[types.InlineKeyboardButton(f"{k}:  {v}", callback_data="ignore_x")] for k, v in stats.items()]
    rows.append([types.InlineKeyboardButton("Back to Menu", callback_data="back_main")])
    return types.InlineKeyboardMarkup(rows)

# ═══════════════════════ WA CORE — AUTO REPLY ════════════════════════
def wa_send(client, jid, msg: dict) -> bool:
    """Text aur Media send karne ka fixed logic."""
    mtype = msg.get("type", "text")
    text = msg.get("text", "")
    file_path = msg.get("file_path", "")

    # --- Text Message ---
    if mtype == "text":
        try:
            from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message, ExtendedTextMessage
            client.send_message(jid, Message(extendedTextMessage=ExtendedTextMessage(text=text)))
            return True
        except Exception:
            try: client.send_message(jid, text); return True
            except: pass
        return False

    # --- Media Message (Photo, Video, Document/APK) ---
    if mtype in ["photo", "video", "document"]:
        import os, mimetypes
        if not os.path.exists(file_path):
            print(f"[wa_send] Error: File missing at {file_path}")
            return False

        try:
            filename = os.path.basename(file_path)
            with open(file_path, "rb") as f:
                media_data = f.read()

            # APK MIME Fix (Taaki BIN na dikhe)
            if filename.lower().endswith(".apk"):
                mime = "application/vnd.android.package-archive"
            else:
                mime, _ = mimetypes.guess_type(file_path)
                if not mime:
                    mime = "application/octet-stream"

            # Video + Caption Fix
            if mtype == "video":
                # Force MIME to video/mp4 if unsure
                v_mime = mime if "video" in str(mime) else "video/mp4"
                media_msg = client.build_video_message(
                    media_data, 
                    caption=text, 
                    mime_type=v_mime
                )
            
            elif mtype == "photo":
                media_msg = client.build_image_message(media_data, caption=text, mime_type=mime)
            
            else: # Document/APK
                media_msg = client.build_document_message(media_data, title=filename, mimetype=mime)

            # Sending Media
            client.send_message(jid, message=media_msg)
            return True

        except Exception as e:
            print(f"[wa_send] Media Send Failed: {e}")
            return False

    return False
    
    

async def auto_reply(session_id: str, jid, client, tg_uid: int):
    """Send configured sequence to a first-time sender."""
    try:
        user_num = getattr(jid, "User",   "")
        server   = getattr(jid, "Server", "")
        print(f"[AutoReply:{session_id}] from={user_num}@{server}")

        # Skip only real groups (g.us) and broadcast lists
        if server == "g.us" or server == "broadcast":
            return

        # user_num must not be empty
        if not user_num:
            return

        # SYNC GUARD
        info = active_clients.get(session_id)
        if not info:
            return
        remaining = info["ready_after"] - time.time()
        if remaining > 0:
            return

        if not bot_is_on(tg_uid):
            return

        # 🟢 LOCK SYSTEM START: Ek time pe ek number par ek hi process chalega
        process_key = f"{session_id}_{user_num}"
        if process_key in processing_users:
            return
        processing_users.add(process_key)

        try:
            # Already contacted from this session?
            if seen_col.find_one({"session_id": session_id, "number": user_num}):
                print(f"[AutoReply] {user_num} already seen, skipping")
                return

            # NAYA CHANGE: User ko turant database me save karo
            seen_col.insert_one({
                "session_id": session_id,
                "number":     user_num,
                "tg_user_id": tg_uid,
                "ts":         time.time(),
            })

            msgs = list(msg_col.find({"tg_user_id": tg_uid}).sort("step", 1))
            if not msgs:
                return

            print(f"[AutoReply] Sending {len(msgs)} messages to {user_num}@{server}")
            sent_count = 0

            for msg in msgs:
                await asyncio.sleep(2)
                try:
                    client.send_presence("composing" if msg["type"] == "text" else "recording", jid)
                except Exception:
                    pass
                await asyncio.sleep(1.5)

                ok = wa_send(client, jid, msg)
                
                if ok:
                    sent_count += 1
                    today = time.strftime('%Y-%m-%d')
                    stats_col.update_one(
                        {"tg_user_id": tg_uid, "date": today},
                        {"$inc": {"count": 1}},
                        upsert=True
                    )
                try:
                    client.send_presence("paused", jid)
                except Exception:
                    pass

            print(f"[AutoReply] Done — {sent_count}/{len(msgs)} sent to {user_num}")

        finally:
            # Kaam khatam hone ke baad number ko processing list se hata do
            processing_users.discard(process_key)

    except Exception as e:
        print(f"[AutoReply] Exception: {e}")
        

# ═══════════════════════ WA CLIENT FACTORY ═══════════════════════════

def make_msg_handler(sid: str, uid: int, loop):
    def handler(client_obj, ev: MessageEv):
        try:
            # Step 1: get MessageSource
            try:
                src = ev.Info.MessageSource
            except Exception as e:
                print(f"[{sid}] MessageSource error: {e}")
                return

            # Step 2: skip own messages
            try:
                if src.IsFromMe:
                    return
            except Exception:
                pass  # if unknown, proceed

            # Step 3: get Chat JID
            try:
                jid = src.Chat
            except Exception as e:
                print(f"[{sid}] JID error: {e}")
                return

            user_num = getattr(jid, "User",   "")
            server   = getattr(jid, "Server", "")
            print(f"[{sid}] MSG from {user_num}@{server}")
            asyncio.run_coroutine_threadsafe(auto_reply(sid, jid, client_obj, uid), loop)
        except Exception as e:
            print(f"[{sid}] Handler error: {e}")
    return handler


def make_logout_handler(sid: str, loop):
    def handler(client_obj, ev: LoggedOutEv):
        try:
            doc  = sess_col.find_one({"session_id": sid}) or {}
            uid  = doc.get("tg_user_id")
            num  = doc.get("number", "Unknown")

            sess_col.delete_one({"session_id": sid})
            seen_col.delete_many({"session_id": sid})
            active_clients.pop(sid, None)

            db_f = f"session_{sid}.db"
            if os.path.exists(db_f):
                try: os.remove(db_f)
                except Exception: pass

            if uid:
                txt = (
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⚠️  **WhatsApp Logged Out!**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Account Number  : `{num}`\n"
                    f"Session : `{sid}`\n\n"
                    "Account removed from WhatsApp.\n"
                    "Tap **Reconnect** to add it again."
                )
                kb = types.InlineKeyboardMarkup(
                    [[types.InlineKeyboardButton("Reconnect Account", callback_data="add_wa")]]
                )
                asyncio.run_coroutine_threadsafe(
                    app.send_message(chat_id=uid, text=txt, reply_markup=kb), loop
                )
        except Exception:
            pass
    return handler


def _run(client):
    try: client.connect()
    except Exception: pass


async def start_wa_client(sid: str, phone: str, status_msg, uid: int):
    loop = asyncio.get_running_loop()

    db_path = f"session{sid}.db"
    client  = NewClient(db_path)
    client.event(MessageEv)(make_msg_handler(sid, uid, loop))
    client.event(LoggedOutEv)(make_logout_handler(sid, loop))

    # ready_after = far future until login is confirmed
    active_clients[sid] = {"client": client, "ready_after": time.time() + 99999, "tg_user_id": uid}

    threading.Thread(target=_run, args=(client,), daemon=True).start()
    await asyncio.sleep(4)

    cancel_kb = kb_cancel()
    try:
        try:    code = client.PairPhone(phone, True)
        except Exception:
            try: code = client.pair_phone(phone)
            except Exception: code = None

        if not code:
            sent = await status_msg.edit_text(
                "**Pairing Failed**\n\nCould not fetch code. Check the number and try again.",
                reply_markup=cancel_kb
            )
            user_states[uid]["active_msg_id"] = sent.id
            return

        user_states[uid]["state"] = "awaiting_login_confirmation"
        txt = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ **Slot `{sid}` Ready**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**Pairing Code:**\n`{code}`\n\n"
            "Open WhatsApp  →  Linked Devices\n"
            "→  Link a Device  →  Link with phone number\n"
            "→  Enter the code above\n\n"
            "Waiting for confirmation…"
        )
        sent = await status_msg.edit_text(txt, reply_markup=cancel_kb)
        user_states[uid]["active_msg_id"] = sent.id
        asyncio.create_task(verify_login(client, sid, phone, uid, sent.id))

    except Exception:
        try:
            sent = await status_msg.edit_text("Pairing error. Please try again.", reply_markup=cancel_kb)
            user_states[uid]["active_msg_id"] = sent.id
        except Exception:
            pass


async def verify_login(client, sid: str, phone: str, uid: int, msg_id: int):
    """Poll until login confirmed, then mark session ready."""
    for _ in range(120):
        await asyncio.sleep(2)
        try:
            authed = False
            if hasattr(client, "is_logged_in"):
                authed = client.is_logged_in
            else:
                try:
                    me     = client.get_me()
                    authed = me is not None and hasattr(me, "JID")
                except Exception:
                    pass

            if authed:
                number = phone
                try:
                    me     = client.get_me()
                    number = str(me.JID).split("@")[0]
                except Exception:
                    pass

                if not sess_col.find_one({"session_id": sid}):
                    sess_col.insert_one({
                        "session_id": sid,
                        "number":     number,
                        "is_active":  True,
                        "tg_user_id": uid,
                    })

                # ── KEY FIX: set ready_after = now + SYNC_WAIT_SECONDS ──
                # All history-sync MessageEv events will be ignored during this window
                if sid in active_clients:
                    active_clients[sid]["ready_after"] = time.time() + SYNC_WAIT_SECONDS

                try: await app.delete_messages(uid, msg_id)
                except Exception: pass

                try:
                    await app.send_message(
                        chat_id=uid,
                        text=(
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            "✅  **Login Successful!**\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Account Number  : `{number}`\n"
                            f"Session : `{sid}`\n\n"
                            f"Syncing… Auto-reply activates in ~{SYNC_WAIT_SECONDS}s\n"
                            "Any new message received after sync will get your auto-reply sequence."
                        ),
                        reply_markup=kb_main(uid),
                    )
                except Exception:
                    pass
                break
        except Exception:
            pass


async def remove_session(sid: str):
    """Manual logout from Telegram panel."""
    info = active_clients.pop(sid, None)
    if info:
        try: info["client"].logout()
        except Exception: pass

    f = f"session{sid}.db"
    if os.path.exists(f):
        try: os.remove(f)
        except Exception: pass

    sess_col.delete_one({"session_id": sid})
    seen_col.delete_many({"session_id": sid})

# ════════════════════ STARTUP — RECONNECT SESSIONS ═══════════════════

async def reconnect_sessions():
    loop = asyncio.get_running_loop()
    for sess in list(sess_col.find({})):
        sid = sess.get("session_id")
        uid = sess.get("tg_user_id")
        if not sid or not uid:
            continue
        db_path = f"session{sid}.db"
        if not os.path.exists(db_path):
            sess_col.delete_one({"session_id": sid})
            continue
        try:
            c = NewClient(db_path)
            c.event(MessageEv)(make_msg_handler(sid, uid, loop))
            c.event(LoggedOutEv)(make_logout_handler(sid, loop))
            # On restart, sync can happen again — wait before processing
            active_clients[sid] = {
                "client":      c,
                "ready_after": time.time() + SYNC_WAIT_SECONDS,
                "tg_user_id":  uid,
            }
            threading.Thread(target=_run, args=(c,), daemon=True).start()
            await asyncio.sleep(1)
        except Exception:
            pass

# ═══════════════════════ TELEGRAM HANDLERS ═══════════════════════════

def get_state(uid: int) -> dict:
    if uid not in user_states:
        user_states[uid] = {}
    return user_states[uid]


WELCOME = (
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "**WA Auto-Reply Bot**\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Welcome! Each user gets their own isolated WA accounts,\n\n"
    "message sequences, and contact database.\n\n"
    "Use the buttons below to get started. 👇"
)


@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    uid = message.from_user.id
    bootstrap_user(uid)
    get_state(uid)
    try:
        await message.reply(WELCOME, reply_markup=kb_main(uid))
    except Exception:
        pass


@app.on_callback_query()
async def on_callback(client, cq):
    data = cq.data
    uid  = cq.from_user.id
    st   = get_state(uid)
    bootstrap_user(uid)

    async def edit(text, kb=None):
        try:
            if kb:
                await cq.message.edit_text(text, reply_markup=kb)
            else:
                await cq.message.edit_text(text)
        except MessageNotModified:
            pass

    # ── BACK ──────────────────────────────────────────────────────────
    if data == "back_main":
        if st.get("active_msg_id"):
            try: await client.delete_messages(uid, st["active_msg_id"])
            except Exception: pass
        user_states[uid] = {}
        await edit(WELCOME, kb_main(uid))

    # ── ADD WA ────────────────────────────────────────────────────────
    elif data == "add_wa":
        sid = free_slot(uid)
        if not sid:
            await cq.answer("⚠️ Maximum accounts reached!", show_alert=True)
            return
        st["session_id"] = sid
        await edit(
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Add WhatsApp Account**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Slot assigned: `{sid}`\n\n"
            "Choose login method below:",
            kb_add_wa()
        )

    # ── PAIR ──────────────────────────────────────────────────────────
    elif data == "add_wa_pair":
        if st.get("session_id"):
            st["state"] = "awaiting_pair_number"
            await edit(
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "📱  **Enter Phone Number**\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Enter the WhatsApp number with country code.\n\n"
                "Example: `919876543210`\n"
                "No `+`, no spaces, no dashes.",
                kb_cancel()
            )

    # ── MANAGE ────────────────────────────────────────────────────────
    elif data == "manage_accs":
        sessions = user_sessions(uid)
        head = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋  **My WA Accounts**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        if not sessions:
            head += "\n_No accounts linked yet._\n\nTap ➕ to add one."
        await edit(head, kb_accounts(sessions))

    # ── DELETE ACCOUNT ────────────────────────────────────────────────
    elif data.startswith("del_acc_"):
        sid  = data[len("del_acc_"):]
        sess = sess_col.find_one({"session_id": sid, "tg_user_id": uid})
        if not sess:
            await cq.answer("Session not found.", show_alert=True)
            return
        await remove_session(sid)
        await cq.answer("✅ Account removed.", show_alert=True)
        sessions = user_sessions(uid)
        await edit("📋  **My WA Accounts**\n\nDirectory updated.", kb_accounts(sessions))

    # ── STATS ─────────────────────────────────────────────────────────
    elif data == "live_stats":
        s = user_stats(uid)
        status = "🟢 Running" if bot_is_on(uid) else "🔴 Paused"
        head = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📊  **Statistics**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🤖  Bot Status  :  {status}\n\n"
        )
        await edit(head, kb_stats(s))

    # ── CONFIG MESSAGES ───────────────────────────────────────────────
    elif data == "config_msgs":
        msgs = list(msg_col.find({"tg_user_id": uid}).sort("step", 1))
        preview = ""
        for m in msgs:
            preview += f"**Msg {m['step']}:** `{m.get('text','(empty)')[:60]}`\n"
        await edit(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "✏️  **Edit Auto-Reply Sequence**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{preview}\n"
            "Select a message slot to update it:",
            kb_config()
        )

    # ── VIEW MESSAGES ─────────────────────────────────────────────────
    elif data == "view_msgs":
        msgs = list(msg_col.find({"tg_user_id": uid}).sort("step", 1))
        out = "━━━━━━━━━━━━━━━━━━━━━━\n👁  **Message Set Preview**\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for m in msgs:
            t = "Text" if m.get("type") == "text" else "Apk or documents"
            out += f"**Step {m['step']}**  [{t}]\n`{m.get('text','(empty)')}`\n\n"
        await edit(out, kb_back())

    # ── TOGGLE BOT ────────────────────────────────────────────────────
    elif data == "toggle_bot":
        new = toggle_bot(uid)
        lbl = "ACTIVE 🟢" if new == "on" else "PAUSED 🔴"
        await cq.answer(f"Auto-Reply is now {lbl}", show_alert=True)
        try:
            await cq.message.edit_reply_markup(reply_markup=kb_main(uid))
        except MessageNotModified:
            pass

    # ── SET MESSAGE ───────────────────────────────────────────────────
    elif data.startswith("set_msg_"):
        step = int(data.split("_")[2])
        st["state"] = f"awaiting_msg_{step}"
        st["step"]  = step
        await edit(
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✏️  **Edit Message {step}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Send the new message content below.\n"
            "You can send **Text**, **Photo**, **Video**, **apk**, or **Document**."
        )

    elif data.startswith("ignore"):
        pass


@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def on_text(client, message):
    uid = message.from_user.id
    st  = get_state(uid)
    s   = st.get("state", "")

    if s == "awaiting_pair_number":
        sid    = st.get("session_id")
        number = "".join(filter(str.isdigit, message.text))
        try:
            loading = await message.reply(
                f"⏳  Generating pairing code for slot `{sid}`…\n\nThis may take a few seconds."
            )
        except Exception:
            return
        await start_wa_client(sid, number, loading, uid)

    elif s.startswith("awaiting_msg_"):
        step = st.get("step")
        msg_col.update_one(
            {"tg_user_id": uid, "step": step},
            {"$set": {"text": message.text, "type": "text"}},
            upsert=True
        )
        try:
            await message.reply(f"✅  Message {step} updated successfully!")
        except Exception:
            pass
        user_states[uid] = {}
        await cmd_start(client, message)


@app.on_message(filters.media & filters.private)
async def on_media(client, message):
    uid = message.from_user.id
    st  = get_state(uid)
    s   = st.get("state", "")

    if s.startswith("awaiting_msg_"):
        step = st.get("step")
        try:
            notif = await message.reply("⏳ Processing.....")
        except Exception:
            return

        mtype   = "photo" if message.photo else "video" if message.video else "document"
        caption = message.caption or ""

        # Telegram se file server par download karo
        file_path = await client.download_media(message)

        msg_col.update_one(
            {"tg_user_id": uid, "step": step},
            {"$set": {"text": caption, "type": mtype, "file_path": file_path}},
            upsert=True
        )
        try:
            await notif.edit_text(f"✅  Message {step} updated with {mtype}!")
        except MessageNotModified:
            pass
        user_states[uid] = {}
        await cmd_start(client, message)


# ════════════════════════════ ENTRY POINT ════════════════════════════

async def main():
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  WA Auto-Reply Bot  —  Starting")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    await app.start()
    print("✅  Telegram bot connected.")
    await reconnect_sessions()
    print(f"✅  WA sessions loaded. Sync guard: {SYNC_WAIT_SECONDS}s")
    print("⏳  Listening for messages…")
    await idle()
    await app.stop()


app.run(main())
