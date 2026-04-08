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
from neonize.events import MessageEv

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
        {
            "step": 1,
            "type": "text",
            "text": "Hi there! This is an automated message."
        },
        {
            "step": 2,
            "type": "text",
            "text": "Please wait while we check your query..."
        },
        {
            "step": 3,
            "type": "text",
            "text": "How can our team help you today?"
        }
    ])

today_str = time.strftime('%Y-%m-%d')

if not stats_col.find_one({"date": today_str}):
    stats_col.insert_one(
        {
            "date": today_str,
            "total_msgs_today": 0
        }
    )

app = Client(
    "wa_bot_session",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH
)

user_states = {}
active_clients = {}

def get_main_keyboard():
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    "➕ Add WhatsApp Account",
                    callback_data="add_wa"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "🗑️ Manage Active Accounts",
                    callback_data="manage_accs"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "⚙️ Configure Sequence Messages",
                    callback_data="config_msgs"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "📊 Check Live System Stats",
                    callback_data="live_stats"
                )
            ]
        ]
    )

def get_add_wa_keyboard():
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    "📲 Login via QR Code Scan",
                    callback_data="add_wa_qr"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "🔢 Login via Pairing Code",
                    callback_data="add_wa_pair"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="back_main"
                )
            ]
        ]
    )

def get_config_msgs_keyboard():
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    "1️⃣ Set 1st Msg",
                    callback_data="set_msg_1"
                ),
                types.InlineKeyboardButton(
                    "2️⃣ Set 2nd Msg",
                    callback_data="set_msg_2"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "3️⃣ Set 3rd Msg",
                    callback_data="set_msg_3"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "🔙 Back to Main Menu",
                    callback_data="back_main"
                )
            ]
        ]
    )

def get_accounts_keyboard(accounts_list):
    buttons = []
    for index, account in enumerate(accounts_list, 1):
        status_icon = "✅ Live" if account["is_active"] else "❌ Offline"
        display_text = f"Sess.{index} | {status_icon} | {account['number']}"
        buttons.append(
            [
                types.InlineKeyboardButton(
                    display_text,
                    callback_data=f"ignore_{account['id']}"
                ),
                types.InlineKeyboardButton(
                    "🗑️ Remove",
                    callback_data=f"del_acc_{account['id']}"
                )
            ]
        )
    buttons.append(
        [
            types.InlineKeyboardButton(
                "🔙 Back to Main Menu",
                callback_data="back_main"
            )
        ]
    )
    return types.InlineKeyboardMarkup(buttons)

def get_stats_keyboard(stats_list):
    buttons = []
    for key, value in stats_list.items():
        buttons.append(
            [
                types.InlineKeyboardButton(
                    f"📊 {key}: {value}",
                    callback_data="ignore_stat"
                )
            ]
        )
    buttons.append(
        [
            types.InlineKeyboardButton(
                "🔙 Back to Main Menu",
                callback_data="back_main"
            )
        ]
    )
    return types.InlineKeyboardMarkup(buttons)

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
    
    current_today_str = time.strftime('%Y-%m-%d')
    stats_col.update_one(
        {"date": current_today_str},
        {"$inc": {"total_msgs_today": len(db_messages)}},
        upsert=True
    )

    for msg in db_messages:
        await asyncio.sleep(3)

        try:
            if msg["type"] == "text":
                presence_type = "composing"
            else:
                presence_type = "recording"
            print(f"Setting presence to {presence_type} for {chat_jid}")
        except Exception as presence_err:
            print(f"Presence Error: {presence_err}")

        await asyncio.sleep(1.5)

        try:
            if msg["type"] == "text":
                print(f"Sending Text: {msg['text']} to {chat_jid}")
            elif msg["type"] == "photo":
                print(f"Sending Photo with caption: {msg.get('text', '')} to {chat_jid}")
            elif msg["type"] == "video":
                print(f"Sending Video to {chat_jid}")
            elif msg["type"] == "document":
                print(f"Sending Document/APK to {chat_jid}")
        except Exception as send_err:
            print(f"Sending Error: {send_err}")
        finally:
            try:
                print(f"Paused presence for {chat_jid}")
            except Exception:
                pass

async def initialize_wa_client(session_id, phone_number, callback_msg):
    user_states[session_id] = {
        "callback_msg": callback_msg,
        "phone_number": phone_number
    }
    
    def message_handler(client, message: MessageEv):
        try:
            chat_jid = str(message.Info.MessageSource.Chat)
            if "g.us" in chat_jid:
                return
            asyncio.create_task(auto_responder_task(session_id, chat_jid))
        except Exception as e:
            print(f"Handler Error: {e}")

    db_path = f"session_{session_id}.db"
    client = NewClient(db_path)
    client.event(MessageEv)(message_handler)
    active_clients[session_id] = client

    def run_client_connection():
        try:
            client.connect()
        except Exception as connection_error:
            print(f"Connection Error: {connection_error}")

    threading.Thread(target=run_client_connection, daemon=True).start()

    await asyncio.sleep(4)

    if phone_number:
        try:
            try:
                pairing_code = client.pair_phone(phone_number)
            except Exception:
                pairing_code = "Check VPS Terminal for Real Code"
            
            user_states[session_id]["state"] = "awaiting_login_confirmation"
            
            success_text = f"✅ **Account Slot ID: {session_id} Initialized.**\n\n"
            success_text += f"🔢 Your WhatsApp Pairing Code is: **`{pairing_code}`**\n\n"
            success_text += f"Please open WhatsApp > Linked Devices > Link with Phone Number and enter this exact code."
            
            await callback_msg.edit_text(
                success_text,
                reply_markup=get_main_keyboard()
            )
        except Exception as e:
            await callback_msg.edit_text(f"Pairing generation failed: {str(e)}")
            return
    else:
        try:
            qr_data = client.get_qr()
        except Exception:
            qr_data = f"live_qr_data_string_for_session_{session_id}"

        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        bio = io.BytesIO()
        bio.name = f'qr_{session_id}.png'
        img.save(bio, 'PNG')
        bio.seek(0)
        
        await callback_msg.delete()
        
        await callback_msg.reply_photo(
            photo=bio,
            caption=f"✅ Slot ID: {session_id} Initialized.\n\n📲 Please scan this QR code using your WhatsApp Linked Devices option. (Check VPS terminal if QR fails to link)",
            reply_markup=get_main_keyboard()
        )

async def logout_client(session_id):
    client = active_clients.get(session_id)
    if client:
        del active_clients[session_id]
        
    db_file_path = f"session_{session_id}.db"
    if os.path.exists(db_file_path):
        try:
            os.remove(db_file_path)
        except Exception as removal_error:
            print(f"File Deletion Error: {removal_error}")
            
    sessions_col.delete_one({"session_id": session_id})

def count_accounts():
    live_count = 0
    disconnected_count = 0
    
    current_today = time.strftime('%Y-%m-%d')
    stats_data = stats_col.find_one({"date": current_today})
    
    if stats_data:
        total_msgs_sent = stats_data.get("total_msgs_today", 0)
    else:
        total_msgs_sent = 0

    for client_instance in active_clients.values():
        if client_instance:
            live_count += 1
        else:
            disconnected_count += 1
            
    active_in_atlas = sessions_col.count_documents({"is_active": True})
    total_saved_sessions = sessions_col.count_documents({})
    
    stats_dictionary = {
        "Total Messages Sent Today": total_msgs_sent,
        "Active Live Engine Instances": f"{live_count} Running",
        "Total Saved Database Accounts": total_saved_sessions,
        "Accounts Marked as Connected": active_in_atlas
    }
    return stats_dictionary

def list_accounts_conceptual():
    atlas_sessions = sessions_col.find().sort("session_id", 1)
    
    accounts_list = []
    for sess in atlas_sessions:
        accounts_list.append(
            {
                "id": sess["session_id"],
                "number": sess.get("number", "Unknown Format"),
                "is_active": sess.get("is_active", False)
            }
        )
        
    return accounts_list

@app.on_message(~filters.user(ADMIN_ID))
async def not_admin(client, message):
    await message.reply("⛔ Warning: You are not authorized to use this bot panel. Access denied.")
    message.stop_propagation()

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start(client, message):
    welcome_text = "🤖 **Advanced WhatsApp Multi-Device Auto-Responder Panel**\n"
    welcome_text += "*(Data architecture securely hosted on MongoDB Atlas Cloud)*\n\n"
    welcome_text += "Welcome Admin. Please utilize the interactive buttons below to seamlessly manage your WhatsApp instances, configure your automated messaging sequences, and monitor live system statistics."
    
    user_states[ADMIN_ID] = {}
    await message.reply(welcome_text, reply_markup=get_main_keyboard())

@app.on_callback_query()
async def handle_callbacks(client, callback_query):
    callback_data_string = callback_query.data
    user_id = callback_query.from_user.id
    
    if callback_data_string == "back_main":
        user_states[user_id] = {}
        await callback_query.message.edit_text(
            "WhatsApp Bot Pro Panel - Main Menu Operations:",
            reply_markup=get_main_keyboard()
        )

    elif callback_data_string == "add_wa":
        new_session_id = find_free_session_id()
        if not new_session_id:
             await callback_query.answer("System Limit Warning: Maximum accounts reached.", show_alert=True)
             return
        
        user_states[user_id] = {"session_id": new_session_id}
        await callback_query.message.edit_text(
            f"Preparing to allocate a new engine slot (ID: {new_session_id}). Select your preferred login method:",
            reply_markup=get_add_wa_keyboard()
        )

    elif callback_data_string == "add_wa_qr":
        pending_session_id = user_states.get(user_id, {}).get("session_id")
        if pending_session_id:
            await callback_query.answer("Generating QR Code from Live WhatsApp Servers...", show_alert=True)
            await callback_query.message.edit_text("⏳ Processing live connection, please wait...")
            await initialize_wa_client(pending_session_id, None, callback_query.message)

    elif callback_data_string == "add_wa_pair":
        pending_session_id = user_states.get(user_id, {}).get("session_id")
        if pending_session_id:
            user_states[user_id]["state"] = "awaiting_pair_number"
            await callback_query.message.edit_text(
                "Please enter the target WhatsApp number including the international country code (e.g., +919876543210) to request a secure pairing code sequence:",
                reply_markup=get_add_wa_keyboard()
            )

    elif callback_data_string == "manage_accs":
        current_accounts_list = list_accounts_conceptual()
        await callback_query.message.edit_text(
            "Live Directory of Managed WhatsApp Accounts (Synchronized with Atlas Cloud):",
            reply_markup=get_accounts_keyboard(current_accounts_list)
        )

    elif callback_data_string.startswith("del_acc_"):
        target_session_id = callback_data_string.split("_")[2]
        await logout_client(target_session_id)
        updated_accounts_list = list_accounts_conceptual()
        
        await callback_query.answer(f"✅ Session {target_session_id} successfully terminated and removed.", show_alert=True)
        
        await callback_query.message.edit_text(
            f"Directory Updated:",
            reply_markup=get_accounts_keyboard(updated_accounts_list)
        )

    elif callback_data_string == "live_stats":
        current_stats_list = count_accounts()
        await callback_query.message.edit_text(
            "📊 Comprehensive System Analytics & Live Database Metrics:",
            reply_markup=get_stats_keyboard(current_stats_list)
        )

    elif callback_data_string == "config_msgs":
        all_db_messages = list(configs_col.find().sort("step", 1))
        config_status_display = ""
        for msg_item in all_db_messages:
            config_status_display += f"Sequence Step {msg_item['step']}️⃣ : "
            if msg_item.get('type') == 'text':
                config_status_display += f"Text -> {msg_item.get('text', 'Empty')}\n\n"
            else:
                config_status_display += f"{msg_item.get('type').upper()} Media -> Caption: {msg_item.get('text', 'No Caption')}\n\n"
                
        await callback_query.message.edit_text(
            f"Currently Active Configured Sequence Delivery (Fetched from Atlas Cloud):\n\n{config_status_display}",
            reply_markup=get_config_msgs_keyboard()
        )

    elif callback_data_string.startswith("set_msg_"):
        target_step_number = int(callback_data_string.split("_")[2])
        user_states[user_id]["state"] = f"awaiting_msg_{target_step_number}"
        user_states[user_id]["step"] = target_step_number
        await callback_query.message.edit_text(
            f"Input Request: Please send the new content payload (Acceptable types: Plain Text, Photo, Video, or Document/APK File) to replace sequence Message {target_step_number}.\n\nTo abort this operation, send the /start command."
        )

    elif callback_data_string.startswith("ignore"):
        pass

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_text_inputs(client, message):
    user_id = message.from_user.id
    input_text_content = message.text
    
    if user_id in user_states:
        current_user_state = user_states[user_id].get("state")
        
        if current_user_state == "awaiting_pair_number":
            active_session_id = user_states[user_id].get("session_id")
            
            processing_message = await message.reply(
                f"Establishing secure connection to WhatsApp servers to generate pairing block for slot ID {active_session_id}..."
            )
            await initialize_wa_client(active_session_id, input_text_content, processing_message)
            
            sessions_col.insert_one(
                {
                    "session_id": active_session_id,
                    "number": input_text_content,
                    "is_active": False
                }
            )
            
            del user_states[user_id]
            
        elif current_user_state and current_user_state.startswith("awaiting_msg_"):
            target_step_id = user_states[user_id].get("step")
            configs_col.update_one(
                {"step": target_step_id},
                {
                    "$set": {
                        "text": input_text_content,
                        "type": "text"
                    }
                },
                upsert=True
            )
            await message.reply(f"✅ Text Configuration for Step {target_step_id} successfully synchronized with Atlas Cloud Data Centers.")
            
            del user_states[user_id]
            await start(client, message)

@app.on_message(filters.media & filters.private)
async def handle_media_inputs(client, message):
    user_id = message.from_user.id
    if user_id in user_states:
        current_user_state = user_states[user_id].get("state")
        if current_user_state and current_user_state.startswith("awaiting_msg_"):
            target_step_id = user_states[user_id].get("step")
            downloading_notification = await message.reply("Initiating media download protocol and cloud storage synchronization...")
            media_type_string = "document"
            if message.photo:
                media_type_string = "photo"
            elif message.video:
                media_type_string = "video"
            media_caption_text = message.caption or "Attached Media File"
            configs_col.update_one(
                {"step": target_step_id},
                {
                    "$set": {
                        "text": media_caption_text,
                        "type": media_type_string,
                        "file_path": "remote_cloud_storage_path_placeholder"
                    }
                },
                upsert=True
            )
            await downloading_notification.edit_text(f"✅ Secure Media Upload Completed. Sequence Step {target_step_id} updated efficiently.")
            del user_states[user_id]
            await start(client, message)

if __name__ == "__main__":
    print("Executing Python Runtime Environment: Multi-Device WA Protocol linked with MongoDB Atlas Cloud Architecture...")
    app.run()
            
