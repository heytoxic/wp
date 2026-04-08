import asyncio
import qrcode
import io
from pyrogram import Client, filters
from pymongo import MongoClient

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
API_ID = 21705136
API_HASH = "78730e89d196e160b0f1992018c6cb19"
BOT_TOKEN = "8644651500:AAGx-3CxjaWP8r6CA9vB7073VwFHQc9U3nI"  # Apna Bot Token yahan daalein
ADMIN_ID = 8176628365             # Apna Telegram User ID yahan daalein

MONGO_DB_URI = "mongodb+srv://Krishna:pss968048@cluster0.4rfuzro.mongodb.net/?retryWrites=true&w=majority"

# ==========================================
# 🗄️ MONGODB CLOUD SETUP
# ==========================================
try:
    print("MongoDB Atlas se connect ho raha hai...")
    db_client = MongoClient(MONGO_DB_URI)
    db = db_client["whatsapp_bot_db"]
    configs_col = db["messages"]
    sessions_col = db["sessions"]
    print("✅ MongoDB Atlas connected successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")

# Default messages agar DB poori tarah khali ho
if configs_col.count_documents({}) == 0:
    configs_col.insert_many([
        {"step": 1, "text": "Hi there!"},
        {"step": 2, "text": "Please wait..."},
        {"step": 3, "text": "How can I help you?"}
    ])
    print("✅ Default messages MongoDB me set ho gaye hain.")

# ==========================================
# 🤖 TELEGRAM BOT INITIALIZATION
# ==========================================
app = Client("wa_bot_session", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# ==========================================
# 🟢 WHATSAPP ENGINE LOGIC
# ==========================================
wa_client = None

async def wa_auto_responder(sender_id):
    """3-message sequence bhejne ka async function jo MongoDB se messages uthayega"""
    # MongoDB se latest messages uthana
    messages = configs_col.find().sort("step", 1)
    
    for msg in messages:
        await asyncio.sleep(3) # Strict 3-second delay (Anti-Ban)
        
        # Typing status simulate karein (Pseudo-code for WA Library)
        # await wa_client.send_presence("composing", sender_id)
        # await asyncio.sleep(1.5)
        
        # Message Send karein
        # await wa_client.send_message(sender_id, msg["text"])
        # await wa_client.send_presence("paused", sender_id)
        
        print(f"Sent Step {msg['step']} to {sender_id}: {msg['text']}")

async def on_wa_message_received(message):
    sender = message.sender_id
    if "@g.us" in sender:  # Ignore groups
        return
        
    # Sequence statelessly trigger karein
    asyncio.create_task(wa_auto_responder(sender))

# ==========================================
# 📱 TELEGRAM COMMANDS
# ==========================================

# Middleware: Sirf admin ko allow karein
@app.on_message(~filters.user(ADMIN_ID))
async def not_admin(client, message):
    await message.reply("⛔ Aap admin nahi hain. Access denied.")
    message.stop_propagation()

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start(client, message):
    text = (
        "🤖 **Cloud WA Bot Panel**\n"
        "*(Data stored safely on MongoDB Atlas)*\n\n"
        "Commands:\n"
        "🔑 `/login` - Naya WA scan karein\n"
        "📝 `/set1 <text>` - Pehla message set karein\n"
        "📝 `/set2 <text>` - Doosra message set karein\n"
        "📝 `/set3 <text>` - Teesra message set karein\n"
        "📊 `/status` - DB aur System status dekhein"
    )
    await message.reply(text)

@app.on_message(filters.command("login") & filters.user(ADMIN_ID))
async def login_wa(client, message):
    await message.reply("⌛ QR Code generate kar raha hu, please wait...")
    
    # Generate QR Code (Is part ko actual WA library ke QR se replace karna hoga)
    qr_data = "mock_qr_data_string_from_wa_library"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    bio = io.BytesIO()
    bio.name = 'qr.png'
    img.save(bio, 'PNG')
    bio.seek(0)
    
    await message.reply_photo(photo=bio, caption="📲 Ise apne WhatsApp se scan karein!")

@app.on_message(filters.command(["set1", "set2", "set3"]) & filters.user(ADMIN_ID))
async def set_messages(client, message):
    cmd = message.command[0]  # "set1", "set2" etc.
    step = int(cmd[-1])       # Extract number 1, 2, or 3
    
    text = message.text.split(' ', 1)
    if len(text) < 2:
        await message.reply(f"⚠️ Please naya message bhi likhein. Ex: `/{cmd} Hello from Cloud!`")
        return
        
    new_text = text[1]
    
    # Cloud MongoDB update karna
    configs_col.update_one({"step": step}, {"$set": {"text": new_text}}, upsert=True)
    
    await message.reply(f"✅ Cloud DB Updated!\nMessage {step} ab yeh set ho gaya hai: **'{new_text}'**")

@app.on_message(filters.command("status") & filters.user(ADMIN_ID))
async def check_status(client, message):
    # Cloud MongoDB se data fetch karna
    msgs = list(configs_col.find().sort("step", 1))
    
    status_text = "📊 **MongoDB Atlas Live Status:**\n\n"
    for msg in msgs:
        status_text += f"{msg['step']}️⃣ {msg['text']}\n"
        
    await message.reply(status_text)

# ==========================================
# 🚀 START SCRIPT
# ==========================================
if __name__ == "__main__":
    print("Starting pure Python Telegram + WA Engine connected to MongoDB Atlas...")
    # asyncio.run(wa_client.connect()) # Start WA async loop
    app.run() # Start Telegram bot

