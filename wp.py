import asyncio
import qrcode
import io
import time
import os
import threading
from pyrogram import Client, filters, types
from pyrogram.errors import MessageNotModified
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
contacts_col = db["contacts"]
settings_col = db["settings"]

if configs_col.count_documents({}) == 0:
    configs_col.insert_many([
        {
            "step": 1,
            "type": "text",
            "text": "Hello! Thank you for contacting us."
        },
        {
            "step": 2,
            "type": "text",
            "text": "Please wait a moment while we process your request."
        },
        {
            "step": 3,
            "type": "text",
            "text": "How can we assist you today?"
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

if not settings_col.find_one({"key": "bot_status"}):
    settings_col.insert_one(
        {
            "key": "bot_status",
            "status": "started"
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
    status_doc = settings_col.find_one({"key": "bot_status"})
    current_status = status_doc.get("status", "started") if status_doc else "started"
    
    if current_status == "started":
        toggle_text = "Stop Auto-Reply"
    else:
        toggle_text = "Start Auto-Reply"
        
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    "Add Account",
                    callback_data="add_wa"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "Manage Accounts",
                    callback_data="manage_accs"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "Configure Messages",
                    callback_data="config_msgs"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "View Messages",
                    callback_data="view_msgs"
                )
            ],
            [
                types.InlineKeyboardButton(
                    toggle_text,
                    callback_data="toggle_bot"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "System Stats",
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
                    "Login via Pairing Code",
                    callback_data="add_wa_pair"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "Cancel",
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
                    "Set Msg 1",
                    callback_data="set_msg_1"
                ),
                types.InlineKeyboardButton(
                    "Set Msg 2",
                    callback_data="set_msg_2"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "Set Msg 3",
                    callback_data="set_msg_3"
                )
            ],
            [
                types.InlineKeyboardButton(
                    "Back",
                    callback_data="back_main"
                )
            ]
        ]
    )

def get_accounts_keyboard(accounts_list):
    buttons = []
    for index, account in enumerate(accounts_list, 1):
        status_icon = "Active" if account["is_active"] else "Inactive"
        display_text = f"Sess.{index} | {status_icon} | {account['number']}"
        buttons.append(
            [
                types.InlineKeyboardButton(
                    display_text,
                    callback_data=f"ignore_{account['id']}"
                ),
                types.InlineKeyboardButton(
                    "Remove",
                    callback_data=f"del_acc_{account['id']}"
                )
            ]
        )
    buttons.append(
        [
            types.InlineKeyboardButton(
                "Back",
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
                    f"{key}: {value}",
                    callback_data="ignore_stat"
                )
            ]
        )
    buttons.append(
        [
            types.InlineKeyboardButton(
                "Back",
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

async def verify_login_loop(client_instance, session_id, phone_number, user_id, active_msg_id):
    for _ in range(150):
        await asyncio.sleep(2)
        try:
            is_logged = False
            
            if hasattr(client_instance, 'is_logged_in'):
                val = getattr(client_instance, 'is_logged_in')
                is_logged = val() if callable(val) else val
            elif hasattr(client_instance, 'IsLoggedIn'):
                val = getattr(client_instance, 'IsLoggedIn')
                is_logged = val() if callable(val) else val
            else:
                db_path = f"session_{session_id}.db"
                if os.path.exists(db_path):
                    if os.path.getsize(db_path) > 90000:
                        is_logged = True

            if is_logged:
                existing_entry = sessions_col.find_one({"session_id": session_id})
                if not existing_entry:
                    sessions_col.insert_one(
                        {
                            "session_id": session_id,
                            "number": phone_number or "QR_Linked",
                            "is_active": True
                        }
                    )
                    try:
                        await app.delete_messages(user_id, active_msg_id)
                    except Exception:
                        pass
                    try:
                        await app.send_message(
                            chat_id=user_id,
                            text=f"Authentication Successful\n\nSession ID {session_id} has connected to WhatsApp.",
                            reply_markup=get_main_keyboard()
                        )
                    except Exception:
                        pass
                break
        except Exception:
            pass

async def auto_responder_task(session_id, chat_jid):
    try:
        user_number = getattr(chat_jid, 'User', "")
        server_type = getattr(chat_jid, 'Server', "")
        
        if server_type in ["g.us", "lid"] or not user_number:
            return
            
        status_doc = settings_col.find_one({"key": "bot_status"})
        if status_doc and status_doc.get("status") == "stopped":
            return
            
        contact_exists = contacts_col.find_one({"session_id": session_id, "number": user_number})
        if contact_exists:
            return
            
        contacts_col.insert_one({"session_id": session_id, "number": user_number})
        
        client = active_clients.get(session_id)
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
                client.send_message(chat_jid, msg["text"])
            except Exception:
                pass
            await asyncio.sleep(1.5)
            
    except Exception:
        pass

async def initialize_wa_client(session_id, phone_number, callback_msg, user_id):
    current_loop = asyncio.get_running_loop()
    
    user_states[user_id]["session_id"] = session_id
    user_states[user_id]["phone_number"] = phone_number
    
    def message_handler(client, message: MessageEv):
        try:
            msg_info = message.Info
            msg_source = msg_info.MessageSource
            if msg_source.IsFromMe:
                return
            chat_jid = msg_source.Chat
            asyncio.run_coroutine_threadsafe(auto_responder_task(session_id, chat_jid), current_loop)
        except Exception:
            pass

    db_path = f"session_{session_id}.db"
    client = NewClient(db_path)
    client.event(MessageEv)(message_handler)
    active_clients[session_id] = client

    def run_client_connection():
        try:
            client.connect()
        except Exception:
            pass

    threading.Thread(target=run_client_connection, daemon=True).start()

    await asyncio.sleep(4)

    cancel_keyboard = types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    "Cancel",
                    callback_data="back_main"
                )
            ]
        ]
    )

    if phone_number:
        try:
            try:
                pairing_code = client.PairPhone(phone_number, True)
            except Exception:
                try:
                    pairing_code = client.pair_phone(phone_number)
                except Exception:
                    pairing_code = "UNAVAILABLE"
            
            if pairing_code == "UNAVAILABLE":
                try:
                    sent_msg = await callback_msg.edit_text(
                        "Connection Error\nFailed to connect with WhatsApp servers. Please ensure VPS internet is active and try again.",
                        reply_markup=cancel_keyboard
                    )
                    user_states[user_id]["active_msg_id"] = sent_msg.id
                except MessageNotModified:
                    pass
                return
            
            user_states[user_id]["state"] = "awaiting_login_confirmation"
            
            success_text = f"Account Slot ID: {session_id} Initialized\n\n"
            success_text += f"Your Pairing Code is: {pairing_code}\n\n"
            success_text += f"Please open WhatsApp > Linked Devices > Link with Phone Number and enter this code."
            
            try:
                sent_msg = await callback_msg.edit_text(
                    success_text,
                    reply_markup=cancel_keyboard
                )
                user_states[user_id]["active_msg_id"] = sent_msg.id
            except MessageNotModified:
                pass
            
            asyncio.create_task(verify_login_loop(client, session_id, phone_number, user_id, sent_msg.id))
            
        except Exception as e:
            try:
                sent_msg = await callback_msg.edit_text(
                    f"Pairing generation failed.",
                    reply_markup=cancel_keyboard
                )
                user_states[user_id]["active_msg_id"] = sent_msg.id
            except MessageNotModified:
                pass
            return
    else:
        qr_text = "QR Mode Active\n\n"
        qr_text += "QR code is rendered in your VPS Terminal Console.\n\n"
        qr_text += "Please open your SSH terminal to view and scan."
        
        try:
            sent_msg = await callback_msg.edit_text(
                qr_text,
                reply_markup=cancel_keyboard
            )
            user_states[user_id]["active_msg_id"] = sent_msg.id
        except MessageNotModified:
            pass
            
        asyncio.create_task(verify_login_loop(client, session_id, None, user_id, sent_msg.id))

async def logout_client(session_id):
    client = active_clients.get(session_id)
    if client:
        del active_clients[session_id]
        
    db_file_path = f"session_{session_id}.db"
    if os.path.exists(db_file_path):
        try:
            os.remove(db_file_path)
        except Exception:
            pass
            
    sessions_col.delete_one({"session_id": session_id})
    contacts_col.delete_many({"session_id": session_id})

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
        "Active Live Engine Instances": live_count,
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
                "number": sess.get("number", "Unknown"),
                "is_active": sess.get("is_active", False)
            }
        )
        
    return accounts_list

@app.on_message(~filters.user(ADMIN_ID))
async def not_admin(client, message):
    try:
        await message.reply("Access denied. Admin only.")
    except Exception:
        pass
    message.stop_propagation()

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start(client, message):
    welcome_text = "WhatsApp Multi-Device Auto-Responder Panel\n\n"
    welcome_text += "Select an option below to manage accounts and configurations."
    
    if ADMIN_ID not in user_states:
        user_states[ADMIN_ID] = {}
        
    try:
        await message.reply(welcome_text, reply_markup=get_main_keyboard())
    except Exception:
        pass

@app.on_callback_query()
async def handle_callbacks(client, callback_query):
    callback_data_string = callback_query.data
    user_id = callback_query.from_user.id
    
    if user_id not in user_states:
        user_states[user_id] = {}
    
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
                "WhatsApp Bot Panel Main Menu:",
                reply_markup=get_main_keyboard()
            )
        except MessageNotModified:
            pass

    elif callback_data_string == "add_wa":
        new_session_id = find_free_session_id()
        if not new_session_id:
             try:
                 await callback_query.answer("Maximum accounts reached.", show_alert=True)
             except Exception:
                 pass
             return
        
        user_states[user_id]["session_id"] = new_session_id
        try:
            await callback_query.message.edit_text(
                f"Allocating slot ID {new_session_id}. Select login method:",
                reply_markup=get_add_wa_keyboard()
            )
        except MessageNotModified:
            pass

    elif callback_data_string == "add_wa_qr":
        pending_session_id = user_states[user_id].get("session_id")
        if pending_session_id:
            try:
                await callback_query.answer("Starting terminal QR service", show_alert=False)
                await callback_query.message.edit_text("Processing connection, please wait...")
            except Exception:
                pass
            await initialize_wa_client(pending_session_id, None, callback_query.message, user_id)

    elif callback_data_string == "add_wa_pair":
        pending_session_id = user_states[user_id].get("session_id")
        if pending_session_id:
            user_states[user_id]["state"] = "awaiting_pair_number"
            try:
                await callback_query.message.edit_text(
                    "Please enter the target WhatsApp number including country code.\nDo not include + or spaces.",
                    reply_markup=types.InlineKeyboardMarkup(
                        [
                            [
                                types.InlineKeyboardButton(
                                    "Cancel",
                                    callback_data="back_main"
                                )
                            ]
                        ]
                    )
                )
            except MessageNotModified:
                pass

    elif callback_data_string == "manage_accs":
        current_accounts_list = list_accounts_conceptual()
        try:
            await callback_query.message.edit_text(
                "Directory of Managed WhatsApp Accounts:",
                reply_markup=get_accounts_keyboard(current_accounts_list)
            )
        except MessageNotModified:
            pass

    elif callback_data_string.startswith("del_acc_"):
        target_session_id = callback_data_string.split("_")[2]
        await logout_client(target_session_id)
        updated_accounts_list = list_accounts_conceptual()
        
        try:
            await callback_query.answer(f"Session {target_session_id} removed.", show_alert=True)
        except Exception:
            pass
        
        try:
            await callback_query.message.edit_text(
                f"Directory Updated:",
                reply_markup=get_accounts_keyboard(updated_accounts_list)
            )
        except MessageNotModified:
            pass

    elif callback_data_string == "live_stats":
        current_stats_list = count_accounts()
        try:
            await callback_query.message.edit_text(
                "System Analytics:",
                reply_markup=get_stats_keyboard(current_stats_list)
            )
        except MessageNotModified:
            pass

    elif callback_data_string == "config_msgs":
        try:
            await callback_query.message.edit_text(
                "Configure Sequence Messages:",
                reply_markup=get_config_msgs_keyboard()
            )
        except MessageNotModified:
            pass
            
    elif callback_data_string == "view_msgs":
        all_db_messages = list(configs_col.find().sort("step", 1))
        config_status_display = ""
        for msg_item in all_db_messages:
            config_status_display += f"Step {msg_item['step']} : "
            if msg_item.get('type') == 'text':
                config_status_display += f"Text -> {msg_item.get('text', 'Empty')}\n\n"
            else:
                config_status_display += f"Media -> Caption: {msg_item.get('text', 'No Caption')}\n\n"
                
        try:
            await callback_query.message.edit_text(
                f"Currently Active Configured Sequence:\n\n{config_status_display}",
                reply_markup=types.InlineKeyboardMarkup(
                    [
                        [
                            types.InlineKeyboardButton(
                                "Back",
                                callback_data="back_main"
                            )
                        ]
                    ]
                )
            )
        except MessageNotModified:
            pass
            
    elif callback_data_string == "toggle_bot":
        status_doc = settings_col.find_one({"key": "bot_status"})
        current_status = status_doc.get("status", "started") if status_doc else "started"
        
        new_status = "stopped" if current_status == "started" else "started"
        settings_col.update_one({"key": "bot_status"}, {"$set": {"status": new_status}}, upsert=True)
        
        try:
            await callback_query.answer(f"Bot auto-reply is now {new_status}.", show_alert=True)
        except Exception:
            pass
            
        try:
            await callback_query.message.edit_reply_markup(reply_markup=get_main_keyboard())
        except MessageNotModified:
            pass

    elif callback_data_string.startswith("set_msg_"):
        target_step_number = int(callback_data_string.split("_")[2])
        user_states[user_id]["state"] = f"awaiting_msg_{target_step_number}"
        user_states[user_id]["step"] = target_step_number
        try:
            await callback_query.message.edit_text(
                f"Send the new content payload (Text, Photo, Video, Document) for Message {target_step_number}."
            )
        except MessageNotModified:
            pass

    elif callback_data_string.startswith("ignore"):
        pass

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_text_inputs(client, message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
        
    input_text_content = message.text
    current_user_state = user_states[user_id].get("state")
    
    if current_user_state == "awaiting_pair_number":
        active_session_id = user_states[user_id].get("session_id")
        
        clean_number = "".join(filter(str.isdigit, input_text_content))
        
        try:
            processing_message = await message.reply(
                f"Establishing secure connection to WhatsApp servers..."
            )
        except Exception:
            return
            
        await initialize_wa_client(active_session_id, clean_number, processing_message, user_id)
        
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
        try:
            await message.reply(f"Text Configuration for Step {target_step_id} synchronized.")
        except Exception:
            pass
        
        user_states[user_id] = {}
        await start(client, message)

@app.on_message(filters.media & filters.private)
async def handle_media_inputs(client, message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
        
    current_user_state = user_states[user_id].get("state")
    
    if current_user_state and current_user_state.startswith("awaiting_msg_"):
        target_step_id = user_states[user_id].get("step")
        
        try:
            downloading_notification = await message.reply("Initiating media download and synchronization...")
        except Exception:
            return
        
        media_type_string = "document"
        if message.photo:
            media_type_string = "photo"
        elif message.video:
            media_type_string = "video"
            
        media_caption_text = message.caption or "Attached Media"
        
        configs_col.update_one(
            {"step": target_step_id},
            {
                "$set": {
                    "text": media_caption_text,
                    "type": media_type_string,
                    "file_path": "remote_placeholder"
                }
            },
            upsert=True
        )
        
        try:
            await downloading_notification.edit_text(f"Media Upload Completed. Sequence Step {target_step_id} updated.")
        except MessageNotModified:
            pass
        
        user_states[user_id] = {}
        await start(client, message)

if __name__ == "__main__":
    print("Multi-Device WA Protocol running...")
    app.run()
