import asyncio
import qrcode
import io
import time
import os
import json
from pyrogram import Client, filters, types
from pymongo import MongoClient
from neonize.client import NewClient
from neonize.events import OnMessage
from neonize.types import JID, Message

# ==========================================
# ⚙️ CONFIGURATION (Replace with YOUR REAL values)
# ==========================================
API_ID = 21705136   # Replace with your API_ID
API_HASH = "78730e89d196e160b0f1992018c6cb19" # Replace with your API_HASH
BOT_TOKEN = "8644651500:AAGx-3CxjaWP8r6CA9vB7073VwFHQc9U3nI"  # Replace with your Bot Token
ADMIN_ID = 8176628365             # Replace with your Telegram User ID

MONGO_DB_URI = "mongodb+srv://Krishna:pss968048@cluster0.4rfuzro.mongodb.net/?retryWrites=true&w=majority"

# ==========================================
# 🗄️ MONGODB CLOUD SETUP
# ==========================================
db_client = MongoClient(MONGO_DB_URI)
db = db_client["whatsapp_bot_pro"]
configs_col = db["messages"]
sessions_col = db["sessions"]
stats_col = db["stats"]

# Ensure default messages exist in MongoDB Atlas
if configs_col.count_documents({}) == 0:
    configs_col.insert_many([
        {"step": 1, "type": "text", "text": "Hi there!"},
        {"step": 2, "type": "text", "text": "Please wait..."},
        {"step": 3, "type": "text", "text": "How can I help you?"}
    ])
    print("✅ Default messages MongoDB me set ho gaye hain.")

# Initialize stats if needed
today_str = time.strftime('%Y-%m-%d')
if not stats_col.find_one({"date": today_str}):
    stats_col.insert_one({"date": today_str, "total_msgs_today": 0})

# ==========================================
# 🤖 TELEGRAM BOT INITIALIZATION
# ==========================================
app = Client("wa_bot_session", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# To store pending user inputs like number for pairing or messages
user_states = {}

# ==========================================
# 📱 TELEGRAM UI - KEYBOARDS (All Inline Buttons)
# ==========================================

# Main Menu Keyboard
def get_main_keyboard():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("➕ Add WhatsApp", callback_data="add_wa")],
        [types.InlineKeyboardButton("🗑️ Manage Accounts", callback_data="manage_accs")],
        [types.InlineKeyboardButton("⚙️ Config Messages", callback_data="config_msgs")],
        [types.InlineKeyboardButton("📊 Live Stats", callback_data="live_stats")]
    ])

# "Add WhatsApp" Options Keyboard
def get_add_wa_keyboard():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📲 Scan QR", callback_data="add_wa_qr")],
        [types.InlineKeyboardButton("🔢 Pairing Code", callback_data="add_wa_pair")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ])

# "Config Messages" Options Keyboard
def get_config_msgs_keyboard():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("1️⃣ Set 1st Msg", callback_data="set_msg_1")],
        [types.InlineKeyboardButton("2️⃣ Set 2nd Msg", callback_data="set_msg_2")],
        [types.InlineKeyboardButton("3️⃣ Set 3rd Msg", callback_data="set_msg_3")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ])

# Function to generate an Inline Keyboard from a list of accounts
def get_accounts_keyboard(accounts_list):
    buttons = []
    for index, account in enumerate(accounts_list, 1):
        status = "✅" if account["is_active"] else "❌"
        # Combine number and deletion icon in one row for better usability
        buttons.append([
            types.InlineKeyboardButton(f".{index} {status} {account['number']}", callback_data=f"ignore_{account['id']}"),
            types.InlineKeyboardButton("🗑️", callback_data=f"del_acc_{account['id']}")
        ])
    buttons.append([types.InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return types.InlineKeyboardMarkup(buttons)

# Function to generate Inline Keyboard from a list of statuses
def get_stats_keyboard(stats_list):
    buttons = []
    for key, value in stats_list.items():
        # Make these buttons unclickable but informational
        buttons.append([types.InlineKeyboardButton(f"📊 {key}: {value}", callback_data="ignore_stat")])
    buttons.append([types.InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return types.InlineKeyboardMarkup(buttons)

# ==========================================
# 🟢 WHATSAPP ENGINE LOGIC & SESSION MANAGEMENT
# ==========================================

# Active Neonize clients storage (by session_id)
active_clients = {}

# Helper to find a free session slot
def find_free_session_id():
    for i in range(1, 100):  # Limiting to 99 accounts for safety
        if not sessions_col.find_one({"session_id": str(i)}):
            return str(i)
    return None

# ==========================================
# 📡 THE AUTO-RESPONDER TASK
# ==========================================
async def auto_responder_task(client_id, chat_jid):
    """
    Handles the 3-message sequence for a single chat.
    Implemented as a separate task to avoid blocking the main message handler.
    """
    client = active_clients.get(client_id)
    if not client:
        return

    # Check MongoDB cloud for current messages
    db_messages = list(configs_col.find().sort("step", 1))
    
    # Check if this chat is a "new" chat (stateless implementation: check DB for last interaction)
    # Since this is a simple demo, we skip this check and always respond to every message.
    
    today_str = time.strftime('%Y-%m-%d')
    stats_col.update_one({"date": today_str}, {"$inc": {"total_msgs_today": 3}}, upsert=True)

    for msg in db_messages:
        # Strict 3-second delay between each message
        await asyncio.sleep(3)

        # Set status to Typing/Composing (for text messages) or Recording/Uploading (for media)
        try:
            presence_type = "composing" if msg["type"] == "text" else "recording"
            # Conceptual presence command (actual command varies by neonize version)
            # await client.SendPresence(presence_type, chat_jid) 
        except Exception as presence_err:
            print(f"Failed to set presence: {presence_err}")

        # Add additional 1.5s random delay to simulate human typing
        await asyncio.sleep(1.5)

        # Send actual message based on its type from DB Atlas
        try:
            if msg["type"] == "text":
                # Conceptual sendMessage command (actual command varies by neonize version)
                # await client.SendMessage(chat_jid, msg["text"])
                print(f"CONCEPTUAL: Sent step {msg['step']} to {chat_jid}: {msg['text']}")
            elif msg["type"] == "photo":
                # Conceptually send photo from path/url
                # await client.SendImage(chat_jid, msg["file_path"], msg["text"])
                 print(f"CONCEPTUAL: Sent image for step {msg['step']} to {chat_jid}")
            elif msg["type"] == "video":
                # Conceptually send video
                # await client.SendVideo(chat_jid, msg["file_path"], msg["text"])
                print(f"CONCEPTUAL: Sent video for step {msg['step']} to {chat_jid}")
            elif msg["type"] == "document":
                # Conceptually send document
                # await client.SendDocument(chat_jid, msg["file_path"], msg["text"], "app.apk")
                print(f"CONCEPTUAL: Sent document for step {msg['step']} to {chat_jid}")

        except Exception as send_err:
            print(f"Failed to send message: {send_err}")
        finally:
            # Clear presence status after message is sent
            try:
                # await client.SendPresence("paused", chat_jid)
                pass
            except Exception:
                pass

# Function to create and initialize a new Neonize client
async def initialize_wa_client(session_id, phone_number, callback_msg):
    """
    Core function for real WA multi-device handling. 
    Connects to MongoDB Atlas and handles QR/Pairing.
    """
    
    # Store the ongoing login callback to update it
    user_states[session_id] = {"callback_msg": callback_msg, "phone_number": phone_number}
    
    # Define OnMessage event handler inline for specific client instance
    def message_handler(client, message):
        chat_jid = message.Info JID
        # Ignore groups
        if "g.us" in chat_jid:
            return
        
        # Statelessly trigger the auto-responder task in the background
        # We pass client_id instead of client object for safer async reference
        asyncio.create_task(auto_responder_task(session_id, chat_jid))

    # Initialize Neonize Client
    client = NewClient(
        f"session_{session_id}.db",  # Local database name, but login state goes to Atlas
        None,  # Use default crypto device
        options={"use_qr_code_for_login": phone_number is None} # Determine QR vs Pairing flow
    )
    
    client.AddEventHandler(OnMessage(message_handler))

    # Handle pairing vs QR flow based on the input
    if phone_number:
        # Paring Code logic
        try:
            # Conceptually get pair code (Actual neonize command needed)
            # pairing_code = await client.GetPairingCode(phone_number)
            pairing_code = f"DEMO-{session_id}" # CONCEPTUAL for demo
            
            # Update user state to await number input, now that pair code is shown
            user_states[session_id]["state"] = "awaiting_login_confirmation"
            
            await callback_msg.edit_text(
                f"✅ **Client ID: {session_id} Initialized.**\n\n"
                f"🔢 Your Pairing Code is: **`{pairing_code}`**\n\n"
                f"Please enter this code in your linked WhatsApp device now.",
                reply_markup=get_accounts_keyboard([{"id": session_id, "number": phone_number, "is_active": False}])
            )
        except Exception as e:
            await callback_msg.edit_text(f"❌ Error getting pairing code: {e}")
            return
    else:
        # QR Code logic (Conceptual as direct QR generation requires specific neonize version)
        await callback_msg.edit_text(f"✅ Client ID: {session_id} initialized with QR code.")
        
    active_clients[session_id] = client
    
    # Event handler for connection status change (Not shown, but crucial to update Atlas)
    # and to register the number once login is confirmed. For this demo, we assume manual entry later.
    
    await client.Connect()

# conceptual logout function
async def logout_client(session_id):
    client = active_clients.get(session_id)
    if client:
        # conceptual logout
        # await client.Logout()
        del active_clients[session_id]
        
    # delete local file
    os.remove(f"session_{session_id}.db")
    # Delete from Atlas cloud DB
    sessions_col.delete_one({"session_id": session_id})
    print(f"Logged out and deleted session {session_id}")

# conceptual account count
def count_accounts():
    live = 0
    disconnected = 0
    today = time.strftime('%Y-%m-%d')
    total_msgs_sent = stats_col.find_one({"date": today})["total_msgs_today"]

    for client in active_clients.values():
        if client: # conceptual live check
            live += 1
        else:
            disconnected += 1
            
    active_in_atlas = sessions_col.count_documents({"is_active": True})
    
    stats_list = {
        "Total Msgs Sent Today": total_msgs_sent,
        "Active Neonize Clients (Conceptual)": f"{live} Live, {disconnected} Discon.",
        "Active Accounts (in MongoDB)": active_in_atlas,
        "Live Users Currently Connected": active_in_atlas # Simplified
    }
    return stats_list

# conceptual account list
def list_accounts_conceptual():
    # we simulate fetching from Atlas sessions table
    atlas_sessions = sessions_col.find().sort("session_id", 1)
    
    accounts_list = []
    for sess in atlas_sessions:
        # use conceptual live check or Atlas status
        accounts_list.append({
            "id": sess["session_id"],
            "number": sess.get("number", "Unknown"),
            "is_active": sess["is_active"]
        })
        
    return accounts_list

# ==========================================
# 📱 TELEGRAM BOT COMMANDS
# ==========================================

# Middleware: Allow ONLY the Admin
@app.on_message(~filters.user(ADMIN_ID))
async def not_admin(client, message):
    await message.reply("⛔ Aap admin nahi hain. Access denied.")
    message.stop_propagation()

# Command /start (All inline buttons, pro start)
@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start(client, message):
    text = (
        "🤖 **Cloud WA Bot Panel - PRO Edition**\n"
        "*(All data stored safely on MongoDB Atlas Cloud)*\n\n"
        "Use the buttons below to manage your WhatsApp bot and configure messages."
    )
    # Clear any previous user state
    user_states[ADMIN_ID] = {}
    await message.reply(text, reply_markup=get_main_keyboard())

# ==========================================
# 📱 TELEGRAM CALLBACK HANDLERS (for Inline Buttons)
# ==========================================
@app.on_callback_query()
async def handle_callbacks(client, callback_query):
    data = callback_query.data
    user_id = callback_query.from_user.id
    
    if data == "back_main":
        # Clear any input states when going back to main
        user_states[user_id] = {}
        await callback_query.message.edit_text("WhatsApp Bot Pro Panel - Main Menu", reply_markup=get_main_keyboard())

    elif data == "add_wa":
        # New account slot selection logic needed for production, hardcoding a conceptual free slot
        session_id = find_free_session_id()
        if not session_id:
             await callback_query.answer("Maximum accounts reached.", show_alert=True)
             return
        
        user_states[user_id] = {"session_id": session_id} # concept session id allocation
        await callback_query.message.edit_text(f"Initializing a new slot (ID: {session_id})...", reply_markup=get_add_wa_keyboard())

    elif data == "add_wa_qr":
        # Conceptual QR code generation logic
        session_id = user_states.get(user_id, {}).get("session_id")
        # Initialize client conceptually (qr: phone number is None)
        await initialize_wa_client(session_id, None, callback_query.message)

    elif data == "add_wa_pair":
        # Prompt user to send number
        session_id = user_states.get(user_id, {}).get("session_id")
        user_states[user_id]["state"] = "awaiting_pair_number"
        await callback_query.message.edit_text("Please enter your WhatsApp number (with country code) to receive a pairing code:")

    elif data == "manage_accs":
        # conceptual account list
        accounts_list = list_accounts_conceptual()
        await callback_query.message.edit_text("Managing your WhatsApp Accounts (data from Atlas Cloud):", reply_markup=get_accounts_keyboard(accounts_list))

    elif data.startswith("del_acc_"):
        # conceptual account deletion
        session_id = data.split("_")[2]
        await logout_client(session_id)
        # Update view
        accounts_list = list_accounts_conceptual()
        await callback_query.message.edit_text(f"Logged out and deleted session {session_id}.", reply_markup=get_accounts_keyboard(accounts_list))

    elif data == "live_stats":
        # conceptual account stats
        stats_list = count_accounts()
        await callback_query.message.edit_text("📊 Live Stats (data from Atlas Cloud):", reply_markup=get_stats_keyboard(stats_list))

    elif data == "config_msgs":
        # conceptual message config
        # we simulation showing currently configured text messages from Atlas for better user feedback
        db_messages = list(configs_col.find().sort("step", 1))
        config_status = ""
        for msg in db_messages:
            config_status += f"{msg['step']}️⃣ {msg['text']}\n"
        await callback_query.message.edit_text(f"Currently configured messages (data from Atlas Cloud):\n\n{config_status}", reply_markup=get_config_msgs_keyboard())

    elif data.startswith("set_msg_"):
        # Prompt user to send new message content
        step = int(data.split("_")[2])
        user_states[user_id]["state"] = f"awaiting_msg_{step}"
        user_states[user_id]["step"] = step
        await callback_query.message.edit_text(f"Please send the new content (Text, Photo, Video, or Document/APK) for Message {step}. Send /cancel to cancel:")

    elif data.startswith("ignore"):
         # conceptual ignore for non-clickable buttons
        pass
    else:
        # If callback data isn't recognized, simply ignore
        pass

# ==========================================
# 📱 TELEGRAM USER INPUT HANDLER (for Pairing Code Number)
# ==========================================
@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_text_inputs(client, message):
    user_id = message.from_user.id
    input_text = message.text
    
    # State check
    if user_id in user_states:
        state = user_states[user_id].get("state")
        
        if state == "awaiting_pair_number":
            # Process phone number for pairing
            session_id = user_states[user_id].get("session_id")
            
            # concept login initialization with pair code
            callback_msg = await message.reply(f"Initializing a conceptual pairing code slot (ID: {session_id})...")
            await initialize_wa_client(session_id, input_text, callback_msg)
            
            # conceptually add to atlas as disconnected initially
            sessions_col.insert_one({"session_id": session_id, "number": input_text, "is_active": False})
            
            # Clear input state
            del user_states[user_id]
            
        elif state.startswith("awaiting_msg_"):
            # If user sends just text for configuration, process it
            step = user_states[user_id].get("step")
            # Update Atlas Cloud DB
            configs_col.update_one({"step": step}, {"$set": {"text": input_text, "type": "text"}}, upsert=True)
            # Send updated view
            await start(client, message)
            del user_states[user_id]
            
    else:
        # If not awaiting input, treat as normal message and ignore
        pass
        
# ==========================================
# 📱 TELEGRAM MEDIA INPUT HANDLER (for Message Configuration)
# ==========================================
@app.on_message(filters.media & filters.private)
async def handle_media_inputs(client, message):
    user_id = message.from_user.id
    
    if user_id in user_states:
        state = user_states[user_id].get("state")
        
        if state.startswith("awaiting_msg_"):
            # Download media and configure it in Atlas cloud
            step = user_states[user_id].get("step")
            
            await message.reply("Conceptual message configuration: media downloaded, caption saved conceptually in Atlas cloud.")
            
            # conceptual Atlas update
            # media path would need actual download path, conceptual update:
            configs_col.update_one({"step": step}, {"$set": {"text": message.caption or "Demo media conceptual text", "type": "photo", "file_path": "Demo path"}}, upsert=True)
            
            # Send updated view
            await start(client, message)
            del user_states[user_id]

# ==========================================
# 🚀 START SCRIPT
# ==========================================
if __name__ == "__main__":
    print("Starting pure Python Telegram + WA Pro Engine connected to MongoDB Atlas...")
    # asyncio.run(main()) # Cannot use run because of pyrogram conflicting loop, 
    # instead we define main logic inside try block as done usually
    app.run()
        
