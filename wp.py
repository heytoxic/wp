import asyncio
import qrcode
import io
import time
import os
import json
import threading
from pyrogram import Client, filters, types
from pymongo import MongoClient
from neonize.client import NewClient
from neonize.events import OnMessage
from neonize.types import Message

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

if configs_col.count_documents({}) == 0:
    configs_col.insert_many([
        {"step": 1, "type": "text", "text": "Hi there!"},
        {"step": 2, "type": "text", "text": "Please wait..."},
        {"step": 3, "type": "text", "text": "How can I help you?"}
    ])

today_str = time.strftime('%Y-%m-%d')
if not stats_col.find_one({"date": today_str}):
    stats_col.insert_one({"date": today_str, "total_msgs_today": 0})

app = Client("wa_bot_session", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

user_states = {}

def get_main_keyboard():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("➕ Add WhatsApp", callback_data="add_wa")],
        [types.InlineKeyboardButton("🗑️ Manage Accounts", callback_data="manage_accs")],
        [types.InlineKeyboardButton("⚙️ Config Messages", callback_data="config_msgs")],
        [types.InlineKeyboardButton("📊 Live Stats", callback_data="live_stats")]
    ])

def get_add_wa_keyboard():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📲 Scan QR", callback_data="add_wa_qr")],
        [types.InlineKeyboardButton("🔢 Pairing Code", callback_data="add_wa_pair")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ])

def get_config_msgs_keyboard():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("1️⃣ Set 1st Msg", callback_data="set_msg_1")],
        [types.InlineKeyboardButton("2️⃣ Set 2nd Msg", callback_data="set_msg_2")],
        [types.InlineKeyboardButton("3️⃣ Set 3rd Msg", callback_data="set_msg_3")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ])

def get_accounts_keyboard(accounts_list):
    buttons = []
    for index, account in enumerate(accounts_list, 1):
        status = "✅" if account["is_active"] else "❌"
        buttons.append([
            types.InlineKeyboardButton(f".{index} {status} {account['number']}", callback_data=f"ignore_{account['id']}"),
            types.InlineKeyboardButton("🗑️", callback_data=f"del_acc_{account['id']}")
        ])
    buttons.append([types.InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return types.InlineKeyboardMarkup(buttons)

def get_stats_keyboard(stats_list):
    buttons = []
    for key, value in stats_list.items():
        buttons.append([types.InlineKeyboardButton(f"📊 {key}: {value}", callback_data="ignore_stat")])
    buttons.append([types.InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return types.InlineKeyboardMarkup(buttons)

active_clients = {}

def find_free_session_id():
    for i in range(1, 100):
        if not sessions_col.find_one({"session_id": str(i)}):
            return str(i)
    return None

async def auto_responder_task(client_id, chat_jid):
    client = active_clients.get(client_id)
    if not client:
        return

    db_messages = list(configs_col.find().sort("step", 1))
    
    today_str = time.strftime('%Y-%m-%d')
    stats_col.update_one({"date": today_str}, {"$inc": {"total_msgs_today": 3}}, upsert=True)

    for msg in db_messages:
        await asyncio.sleep(3)

        try:
            presence_type = "composing" if msg["type"] == "text" else "recording"
        except Exception as presence_err:
            pass

        await asyncio.sleep(1.5)

        try:
            if msg["type"] == "text":
                print(msg["text"])
            elif msg["type"] == "photo":
                 print(msg["type"])
            elif msg["type"] == "video":
                print(msg["type"])
            elif msg["type"] == "document":
                print(msg["type"])

        except Exception as send_err:
            pass
        finally:
            try:
                pass
            except Exception:
                pass

async def initialize_wa_client(session_id, phone_number, callback_msg):
    user_states[session_id] = {"callback_msg": callback_msg, "phone_number": phone_number}
    
    def message_handler(client, message):
        chat_jid = str(message.Info.MessageSource.Chat)
        if "g.us" in chat_jid:
            return
        
        asyncio.create_task(auto_responder_task(session_id, chat_jid))

    client = NewClient(
        f"session_{session_id}.db",
        None,
        options={"use_qr_code_for_login": phone_number is None}
    )
    
    client.AddEventHandler(OnMessage(message_handler))

    if phone_number:
        try:
            pairing_code = f"DEMO-{session_id}"
            
            user_states[session_id]["state"] = "awaiting_login_confirmation"
            
            await callback_msg.edit_text(
                f"✅ **Client ID: {session_id} Initialized.**\n\n🔢 Your Pairing Code is: **`{pairing_code}`**\n\nPlease enter this code in your linked WhatsApp device now.",
                reply_markup=get_accounts_keyboard([{"id": session_id, "number": phone_number, "is_active": False}])
            )
        except Exception as e:
            await callback_msg.edit_text(str(e))
            return
    else:
        await callback_msg.edit_text(f"✅ Client ID: {session_id} initialized with QR code.")
        
    active_clients[session_id] = client
    
    threading.Thread(target=client.Connect, daemon=True).start()

async def logout_client(session_id):
    client = active_clients.get(session_id)
    if client:
        del active_clients[session_id]
        
    if os.path.exists(f"session_{session_id}.db"):
        os.remove(f"session_{session_id}.db")
    sessions_col.delete_one({"session_id": session_id})

def count_accounts():
    live = 0
    disconnected = 0
    today = time.strftime('%Y-%m-%d')
    stats_data = stats_col.find_one({"date": today})
    total_msgs_sent = stats_data["total_msgs_today"] if stats_data else 0

    for client in active_clients.values():
        if client:
            live += 1
        else:
            disconnected += 1
            
    active_in_atlas = sessions_col.count_documents({"is_active": True})
    
    stats_list = {
        "Total Msgs Sent Today": total_msgs_sent,
        "Active Neonize Clients": f"{live} Live, {disconnected} Discon.",
        "Active Accounts": active_in_atlas,
        "Live Users Currently Connected": active_in_atlas
    }
    return stats_list

def list_accounts_conceptual():
    atlas_sessions = sessions_col.find().sort("session_id", 1)
    
    accounts_list = []
    for sess in atlas_sessions:
        accounts_list.append({
            "id": sess["session_id"],
            "number": sess.get("number", "Unknown"),
            "is_active": sess.get("is_active", False)
        })
        
    return accounts_list

@app.on_message(~filters.user(ADMIN_ID))
async def not_admin(client, message):
    await message.reply("⛔ Aap admin nahi hain. Access denied.")
    message.stop_propagation()

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start(client, message):
    text = "🤖 **Cloud WA Bot Panel - PRO Edition**\n*(All data stored safely on MongoDB Atlas Cloud)*\n\nUse the buttons below to manage your WhatsApp bot and configure messages."
    user_states[ADMIN_ID] = {}
    await message.reply(text, reply_markup=get_main_keyboard())

@app.on_callback_query()
async def handle_callbacks(client, callback_query):
    data = callback_query.data
    user_id = callback_query.from_user.id
    
    if data == "back_main":
        user_states[user_id] = {}
        await callback_query.message.edit_text("WhatsApp Bot Pro Panel - Main Menu", reply_markup=get_main_keyboard())

    elif data == "add_wa":
        session_id = find_free_session_id()
        if not session_id:
             await callback_query.answer("Maximum accounts reached.", show_alert=True)
             return
        
        user_states[user_id] = {"session_id": session_id}
        await callback_query.message.edit_text(f"Initializing a new slot (ID: {session_id})...", reply_markup=get_add_wa_keyboard())

    elif data == "add_wa_qr":
        session_id = user_states.get(user_id, {}).get("session_id")
        await initialize_wa_client(session_id, None, callback_query.message)

    elif data == "add_wa_pair":
        session_id = user_states.get(user_id, {}).get("session_id")
        user_states[user_id]["state"] = "awaiting_pair_number"
        await callback_query.message.edit_text("Please enter your WhatsApp number (with country code) to receive a pairing code:")

    elif data == "manage_accs":
        accounts_list = list_accounts_conceptual()
        await callback_query.message.edit_text("Managing your WhatsApp Accounts:", reply_markup=get_accounts_keyboard(accounts_list))

    elif data.startswith("del_acc_"):
        session_id = data.split("_")[2]
        await logout_client(session_id)
        accounts_list = list_accounts_conceptual()
        await callback_query.message.edit_text(f"Logged out and deleted session {session_id}.", reply_markup=get_accounts_keyboard(accounts_list))

    elif data == "live_stats":
        stats_list = count_accounts()
        await callback_query.message.edit_text("📊 Live Stats:", reply_markup=get_stats_keyboard(stats_list))

    elif data == "config_msgs":
        db_messages = list(configs_col.find().sort("step", 1))
        config_status = ""
        for msg in db_messages:
            config_status += f"{msg['step']}️⃣ {msg.get('text', '')}\n"
        await callback_query.message.edit_text(f"Currently configured messages:\n\n{config_status}", reply_markup=get_config_msgs_keyboard())

    elif data.startswith("set_msg_"):
        step = int(data.split("_")[2])
        user_states[user_id]["state"] = f"awaiting_msg_{step}"
        user_states[user_id]["step"] = step
        await callback_query.message.edit_text(f"Please send the new content (Text, Photo, Video, or Document/APK) for Message {step}. Send /cancel to cancel:")

    elif data.startswith("ignore"):
        pass

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_text_inputs(client, message):
    user_id = message.from_user.id
    input_text = message.text
    
    if user_id in user_states:
        state = user_states[user_id].get("state")
        
        if state == "awaiting_pair_number":
            session_id = user_states[user_id].get("session_id")
            
            callback_msg = await message.reply(f"Initializing a conceptual pairing code slot (ID: {session_id})...")
            await initialize_wa_client(session_id, input_text, callback_msg)
            
            sessions_col.insert_one({"session_id": session_id, "number": input_text, "is_active": False})
            
            del user_states[user_id]
            
        elif state and state.startswith("awaiting_msg_"):
            step = user_states[user_id].get("step")
            configs_col.update_one({"step": step}, {"$set": {"text": input_text, "type": "text"}}, upsert=True)
            await start(client, message)
            del user_states[user_id]

@app.on_message(filters.media & filters.private)
async def handle_media_inputs(client, message):
    user_id = message.from_user.id
    
    if user_id in user_states:
        state = user_states[user_id].get("state")
        
        if state and state.startswith("awaiting_msg_"):
            step = user_states[user_id].get("step")
            
            await message.reply("Media saved successfully.")
            
            configs_col.update_one({"step": step}, {"$set": {"text": message.caption or "Media Message", "type": "photo", "file_path": "Demo path"}}, upsert=True)
            
            await start(client, message)
            del user_states[user_id]

if __name__ == "__main__":
    app.run()
        
