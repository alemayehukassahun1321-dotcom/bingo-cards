import sqlite3
import random
import json
import base64
import asyncio
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

DB_PATH = 'cartela.db'
API_PORT = 8080  # ዌብ አፑ መረጃ የሚወስድበት ፖርት

# 📢 የባንክ እና የመክፈያ መረጃ መልዕክት
BANK_INFO_MSG = (
    "💰 <b>የመክፈያ መንገዶች (Deposit Options)፦</b>\n\n"
    "👉 <b>በቴሌ ብር (telebirr)</b>\n"
    "📱 አካውንት፦ <code>0914672973</code>\n"
    "👤 ስም፦ ALEMAYEHU KASSAHUN\n\n"
    "👉 <b>በCBE Birr / የኢትዮጵያ ንግድ ባንክ</b>\n"
    "📱 አካውንት፦ <code>0906791001</code>\n"
    "👤 ስም፦ ALEMAYEHU KASSAHUN\n\n"
    "⚠️ <b>ማሳሰቢያ፦</b> ክፍያ ከፈጸሙ በኋላ ያረጋገጡበትን <b>የፎቶ ደረሰኝ (Screenshot)</b> ከታች ያለውን በተን ተክተው ለአድሚኑ ይላኩ።"
)

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    return conn

# --- 1. የዳታቤዝ መዋቅር መፍጠር ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 100.0
        )''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            card_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            numbers TEXT
        )''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
    conn.commit()
    conn.close()

def save_game_state_to_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO game_state (key, value) VALUES ('is_active', ?)", (str(GAME_STATE["is_active"]),))
    cursor.execute("INSERT OR REPLACE INTO game_state (key, value) VALUES ('called_numbers', ?)", (json.dumps(GAME_STATE["called_numbers"]),))
    cursor.execute("INSERT OR REPLACE INTO game_state (key, value) VALUES ('current_chat_id', ?)", (str(GAME_STATE["current_chat_id"]),))
    cursor.execute("INSERT OR REPLACE INTO game_state (key, value) VALUES ('winner_card_id', ?)", (str(GAME_STATE["winner_card_id"]),))
    conn.commit()
    conn.close()

def load_game_state_from_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM game_state WHERE key = 'is_active'")
        row_active = cursor.fetchone()
        cursor.execute("SELECT value FROM game_state WHERE key = 'called_numbers'")
        row_numbers = cursor.fetchone()
        cursor.execute("SELECT value FROM game_state WHERE key = 'current_chat_id'")
        row_chat = cursor.fetchone()
        cursor.execute("SELECT value FROM game_state WHERE key = 'winner_card_id'")
        row_winner = cursor.fetchone()
        
        if row_active and row_active[0] == 'True':
            GAME_STATE["is_active"] = True
            GAME_STATE["called_numbers"] = json.loads(row_numbers[0]) if row_numbers else []
            GAME_STATE["current_chat_id"] = int(row_chat[0]) if row_chat and row_chat[0] != 'None' else None
            GAME_STATE["winner_card_id"] = int(row_winner[0]) if row_winner and row_winner[0] != 'None' else None
        else:
            GAME_STATE["is_active"] = False
            GAME_STATE["called_numbers"] = []
            GAME_STATE["current_chat_id"] = None
            GAME_STATE["winner_card_id"] = None
    except sqlite3.OperationalError:
        GAME_STATE["is_active"] = False
        GAME_STATE["called_numbers"] = []
        GAME_STATE["current_chat_id"] = None
        GAME_STATE["winner_card_id"] = None
    finally:
        conn.close()

# --- 2. የ 5x5 የቢንጎ ቁጥሮች ማመንጫ ---
def generate_bingo_numbers():
    b = random.sample(range(1, 16), 5)
    i = random.sample(range(16, 31), 5)
    n = random.sample(range(31, 46), 5)
    n[2] = "FREE"
    g = random.sample(range(46, 61), 5)
    o = random.sample(range(61, 76), 5)
    
    matrix = []
    for r in range(5):
        matrix.append([b[r], i[r], n[r], g[r], o[r]])
    return matrix

# --- 3. 🎯 አውቶማቲክ የአሸናፊነት ማረጋገጫ ህጎች ---
def server_check_bingo(matrix, called_list):
    def is_called(val):
        return val == "FREE" or int(val) in called_list

    for row in matrix:
        if all(is_called(x) for x in row): return True
        
    for col in range(5):
        if all(is_called(matrix[row][col]) for row in range(5)): return True
        
    if all(is_called(matrix[idx][idx]) for idx in range(5)): return True
    if all(is_called(matrix[idx][4-idx]) for idx in range(5)): return True
    
    corners = [matrix[0][0], matrix[0][4], matrix[4][0], matrix[4][4]]
    if all(is_called(x) for x in corners): return True

    return False

# --- 4. የጨዋታው ሁኔታ ---
GAME_STATE = {
    "is_active": False,
    "admin_id": 7196518682, # የአድሚን ID
    "called_numbers": [],
    "current_chat_id": None,
    "winner_card_id": None,
    "bot_instance": None
}

# --- 🎲 [አዲሱ ማስተካከያ] በተን በተጫነ ቁጥር አንድ ቁጥር ብቻ በእጅ መጥሪያ ሎጂክ ---
async def call_single_next_number(bot):
    if not GAME_STATE["is_active"]:
        return None

    available = [n for n in range(1, 76) if n not in GAME_STATE["called_numbers"]]
    if not available:
        GAME_STATE["is_active"] = False
        save_game_state_to_db()
        if GAME_STATE["current_chat_id"]:
            await bot.send_message(chat_id=GAME_STATE["current_chat_id"], text="🏁 ሁሉም ቁጥሮች ተጠርተው ጨዋታው አብቅቷል!")
        return None

    num = random.choice(available)
    GAME_STATE["called_numbers"].append(num)
    save_game_state_to_db()

    # አሸናፊ በራስ-ሰር መኖሩን ይፈትሻል
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT card_id, username, numbers FROM cards")
    all_cards = cursor.fetchall()
    conn.close()

    for c_id, u_name, c_numbers in all_cards:
        card_matrix = json.loads(c_numbers)
        if server_check_bingo(card_matrix, GAME_STATE["called_numbers"]):
            GAME_STATE["is_active"] = False
            GAME_STATE["winner_card_id"] = c_id
            save_game_state_to_db()
            
            win_text = (
                f"🏆 <b>ቢንጎ! ጨዋታው በራስ-ሰር ተዘግቷል!</b> 🎉\n\n"
                f"🥇 አሸናፊ ተጫዋች፦ <b>{u_name}</b>\n"
                f"🎫 የካርቴላ ቁጥር፦ <b>#{c_id}</b>\n\n"
                f"🔢 በመጨረሻ የተጠራው ቁጥር፦ <b>[{num}]</b>\n"
                f"💡 <i>አድሚኑ ለአዲስ ጨዋታ 'ጀምር' ሲል በድጋሚ መጫወት ትችላላችሁ።</i>"
            )
            if GAME_STATE["current_chat_id"]:
                await bot.send_message(chat_id=GAME_STATE["current_chat_id"], text=win_text, parse_mode="HTML")
            return num

    # የቅርብ ጊዜ የወጡ 5 ቁጥሮችን ያዘጋጃል
    last_few = GAME_STATE["called_numbers"][-5:]
    last_few_str = " ➡️ ".join(f"<b>[{n}]</b>" for n in last_few)
    
    # በፊደላት ተለይቶ እንዲወጣ (B-1, I-19 ...)
    letter = ""
    if num >= 1 and num <= 15: letter = "B"
    elif num >= 16 and num <= 30: letter = "I"
    elif num >= 31 and num <= 45: letter = "N"
    elif num >= 46 and num <= 60: letter = "G"
    elif num >= 61 and num <= 75: letter = "O"

    text = (
        f"🎯 <b>ALEM BINGO (አለም ቢንጎ)</b> 🎯\n\n"
        f"📢 <b>የቢንጎ ቁጥር ጥሪ፦</b> 🎉 <b>[ {letter}-{num} ]</b> 🎉\n\n"
        f"🕒 የቅርብ ጊዜ የወጡት፦\n{last_few_str}\n\n"
        f"🔢 ጠቅላላ የተጠሩ ቁጥሮች ብዛት፦ <b>{len(GAME_STATE['called_numbers'])}/75</b>\n"
        f"💡 <i>ካርቴላ የቆረጣችሁ ተጫዋቾች ዌብ አፑ ላይ ቀጥታ ማየት ትችላላችሁ!</i>"
    )
    
    if GAME_STATE["current_chat_id"]:
        try:
            await bot.send_message(chat_id=GAME_STATE["current_chat_id"], text=text, parse_mode="HTML")
        except Exception as e:
            pass
            
    return num

# --- 5. የትዕዛዝ ፈንክሽኖች (Command Handlers) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username_to_save = f"@{user.username}" if user.username else user.first_name
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, username, balance) VALUES (?, ?, 100.0)", (user.id, username_to_save))
    conn.commit()
    conn.close()
    
    keyboard = [
        ['🎰 ካርቴላ ቁረጥ'],
        ['💰 Deposit', '🏧 Withdraw'],
        ['📊 ሂሳብ እይ', '👥 Invite'],
        ['📖 How To Play', '📞 Contact Us'],
        ['🚀 Join Us']
    ]
    
    if user.id == GAME_STATE["admin_id"]:
        keyboard.insert(0, ['📢 ጨዋታ ጀምር'])
        
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"<b>Welcome to ALEM BINGO (አለም ቢንጎ)!🎯</b>\nለሙከራ 100 ብር አካውንትዎ ላይ ተጨምሯል። ከታች ያሉትን ቁልፎች ተጠቅመው መጫወት ይችላሉ።",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def start_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != GAME_STATE["admin_id"]:
        await update.message.reply_text("❌ ይህ ትዕዛዝ ለጋሜ አድሚን ብቻ የተፈቀደ ነው!")
        return
    await handle_message_logic('📢 ጨዋታ ጀምር', update, context)

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(text="📬 ለአድሚኑ ደረሰኝ ላክ", url="https://t.me/Alemayehu_K")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(BANK_INFO_MSG, parse_mode="HTML", reply_markup=reply_markup)

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(text="💰 ማውጫ መረጃ ላክ", url="https://t.me/Alemayehu_K")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🏧 <b>ገንዘብ ለማውጣት (Withdraw)</b>\n\nማውጣት የሚፈልጉትን የገንዘብ መጠን፣ የባንክ ስም እና አካውንት ቁጥር ለአድሚኑ ይላኩ።",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_message_logic('📊 ሂሳብ እይ', update, context)

async def transfer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != GAME_STATE["admin_id"]: return
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        if cursor.rowcount > 0:
            conn.commit()
            await update.message.reply_text(f"✅ ለተጠቃሚ {target_id} {amount} ብር ተሞልቷል!")
        conn.close()
    except:
        await update.message.reply_text("📊 አጠቃቀም: `/transfer [ID] [መጠን]`")

async def bonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != GAME_STATE["admin_id"]: return
    try:
        amount = float(context.args[0])
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance + ?", (amount,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🎁 ለሁሉም ተጫዋቾች {amount} ብር ቦነስ ታድሏል!")
    except:
        await update.message.reply_text("📊 አጠቃቀም: `/bonus [መጠን]`")

async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != GAME_STATE["admin_id"]: return
    message_text = " ".join(context.args)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    for row in users:
        try:
            await context.bot.send_message(chat_id=row[0], text=f"📣 <b>ማስታወቂያ፦</b>\n\n{message_text}", parse_mode="HTML")
        except: continue
    await update.message.reply_text("✅ ማስታወቂያው ለሁሉም ተልኳል!")

async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    invite_link = f"https://t.me/{context.bot.username}?start=ref_{update.effective_user.id}"
    await update.message.reply_text(f"👥 <b>የግብዣ ሊንክዎ (Referral Link)፦</b>\n\n{invite_link}\n\nወዳጅ ዘመድዎን በመጋበዝ ተጨማሪ ቦነስ ያግኙ!", parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 <b>እንዴት መጫወት ይቻላል?</b>\n\n1. አድሚኑ ጨዋታ እስኪጀምር ይጠብቁ።\n2. '🎰 ካርቴላ ቁረጥ' የሚለውን በተን ይጫኑ።\n3. የቆረጡት ካርቴላ መስመር ሲሞላ ሲስተሙ በራሱ ይፈትሽና ያሸንፍዎታል!")

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📞 <b>ማንኛውንም ጥያቄ አድሚኑን ለማግኘት፦</b>\n\n👉 @Alemayehu_K ን ያነጋግሩ።")

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 <b>የቀጥታ ጨዋታዎችን እና እጣዎችን ለመከታተል የቴሌግራም ቻናላችንን ይቀላቀሉ!</b>")

# --- 6. የጽሑፍ መልዕክቶችን ማስተናገጃ ---
async def handle_message_logic(text, update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_name = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
    
    if text == '📢 ጨዋታ ጀምር' and user_id == GAME_STATE["admin_id"]:
        if GAME_STATE["is_active"]:
            await update.message.reply_text("❌ በአሁኑ ሰዓት ንቁ ጨዋታ እየተካሄደ ነው!")
            return
            
        GAME_STATE["is_active"] = True
        GAME_STATE["called_numbers"] = []
        GAME_STATE["current_chat_id"] = chat_id
        GAME_STATE["winner_card_id"] = None
        save_game_state_to_db()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cards")
        conn.commit()
        conn.close()
        
        await update.message.reply_text("📢 አዲስ የ ALEM BINGO ጨዋታ ተጀምሯል። አሁን ካርቴላ በመቁረጥ የዌብ አፑ ላይ 'ቀጣይ ጥራ' እያላችሁ መጫወት ትችላላችሁ!")
        return

    elif 'ካርቴላ' in text:
        if not GAME_STATE["is_active"]:
            await update.message.reply_text("❌ በአሁኑ ሰዓት ምንም ንቁ ጨዋታ የለም። እባክህ ጨዋታ እስኪጀምር ጠብቅ!")
            return

        matrix = generate_bingo_numbers()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO cards (user_id, username, numbers) VALUES (?, ?, ?)", (user_id, user_name, json.dumps(matrix)))
        card_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        matrix_json = json.dumps(matrix)
        called_str = ",".join(map(str, GAME_STATE["called_numbers"]))
        matrix_encoded = base64.b64encode(matrix_json.encode()).decode()
        
        webapp_url = f"https://alemayehukassahun1321-dotcom.github.io/amharic-bingo/index.html?id={card_id}&game_id=1&m={matrix_encoded}&c={called_str}"
        keyboard = [[InlineKeyboardButton(text="🎮 ካርቴላውን ክፈት (WebApp)", web_app=WebAppInfo(url=webapp_url))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"🎫 <b>ካርቴላ ቁጥር #{card_id} ተዘጋጅቷል!</b>\nከታች ያለውን ሰማያዊ ቁልፍ ተጭነህ መጫወት ትችላለህ።",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return
        
    elif text == '📊 ሂሳብ እይ':
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        await update.message.reply_text(f"💰 ያሎት ወቅታዊ ሂሳብ: {row[0] if row else 0.0} ብር\n🆔 የእርስዎ ID: <code>{user_id}</code>", parse_mode="HTML")
        return
        
    elif text == '💰 Deposit':
        await deposit_command(update, context)
    elif text == '🏧 Withdraw':
        await withdraw_command(update, context)
    elif text == '👥 Invite':
        await invite_command(update, context)
    elif text == '📖 How To Play':
        await help_command(update, context)
    elif text == '📞 Contact Us':
        await contact_command(update, context)
    elif text == '🚀 Join Us':
        await join_command(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        await handle_message_logic(update.message.text, update, context)

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

# --- 7. ለዌብ አፑ መረጃ መስጫ እና መቀበያ API ---
async def http_get_live_numbers(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if GAME_STATE["winner_card_id"] is not None:
        return web.json_response({"status": "game_over", "winner": GAME_STATE["winner_card_id"]}, headers=headers)
    return web.json_response({"status": "active", "numbers": GAME_STATE["called_numbers"]}, headers=headers)

# 🎲 [የተስተካከለው ዋና ኤፒአይ] የዌብ አፑ በተን ሲነካ ቀጣይ ቁጥር የሚጠራበት
async def http_trigger_next_call(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if not GAME_STATE["is_active"]:
        return web.json_response({"status": "error", "reason": "No active game"}, headers=headers)
        
    loop = asyncio.get_running_loop()
    # ቁጥሩን በፓይተን ቦት በኩል እንዲጠራ ያደርጋል
    asyncio.run_coroutine_threadsafe(call_single_next_number(GAME_STATE["bot_instance"]), loop)
    
    return web.json_response({"status": "success"}, headers=headers)

async def http_submit_bingo(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    try:
        data = await request.json()
        card_id = int(data.get("card_id"))
        player_name = data.get("player_name", "ተጫዋች")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT numbers FROM cards WHERE card_id = ?", (card_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row: return web.json_response({"valid": False, "error": "ካርቴላው አልተገኘም"}, headers=headers)
        matrix = json.loads(row[0])
        
        if server_check_bingo(matrix, GAME_STATE["called_numbers"]):
            GAME_STATE["is_active"] = False
            GAME_STATE["winner_card_id"] = card_id
            save_game_state_to_db()
            
            if GAME_STATE["current_chat_id"] and GAME_STATE["bot_instance"]:
                asyncio.create_task(GAME_STATE["bot_instance"].send_message(
                    chat_id=GAME_STATE["current_chat_id"],
                    text=f"🏆 <b>ቢንጎ ተብሏል! ጨዋታው ተጠናቋል!</b> 🎉\n\nተጫዋች <b>{player_name}</b> በካርቴላ <b>#{card_id}</b> አሸንፏል! 🥇",
                    parse_mode="HTML"
                ))
            return web.json_response({"valid": True}, headers=headers)
        return web.json_response({"valid": False, "reason": "Not a winning combination"}, headers=headers)
    except Exception as e:
        return web.json_response({"valid": False, "error": str(e)}, headers=headers)

async def http_options_handler(request):
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    })

async def start_api_server():
    app = web.Application()
    app.router.add_get('/get_live_numbers', http_get_live_numbers)
    app.router.add_post('/submit_bingo', http_submit_bingo)
    app.router.add_post('/trigger_next_call', http_trigger_next_call)
    app.router.add_options('/get_live_numbers', http_options_handler)
    app.router.add_options('/submit_bingo', http_options_handler)
    app.router.add_options('/trigger_next_call', http_options_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', API_PORT)
    await site.start()
    print(f"📡 የዌብ አፕ ኤፒአይ ሰርቨር በፖርት {API_PORT} ላይ ተነስቷል...")

# --- 8. ቦቱን የማስነሻ ዋና ፈንክሽን ---
def main():
    init_db()
    load_game_state_from_db()
    
    TOKEN = "8675884335:AAFRhl4-ZEjSTqNupbg5iHD_7bt8iez1LNg" 
    
    application = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(60.0)  
        .read_timeout(60.0)     
        .build()
    )
    
    GAME_STATE["bot_instance"] = application.bot
    
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def set_commands(app):
        commands = [
            BotCommand("start", "ቦቱን ለመጀመር"),
            BotCommand("game", "ጨዋታ ለመጀመር (Admin)"),
            BotCommand("deposit", "ገንዘብ ለማስገባት"),
            BotCommand("withdraw", "ገንዘብ ለማውጣት"),
            BotCommand("balance", "ሂሳብ ለመፈተሽ"),
            BotCommand("transfer", "ገንዘብ ለመሙላት (Admin)"),
            BotCommand("bonus", "ቦነስ ለማደል (Admin)"),
            BotCommand("post", "ማስታወቂያ ለመልቀቅ (Admin)"),
            BotCommand("invite", "ጓደኞችን ለመጋበዝ"),
            BotCommand("help", "እንዴት እንደሚጫወቱ ለማየት"),
            BotCommand("contact", "እኛን ለማግኘት"),
            BotCommand("join", "ቻናላችንን ለመቀላቀል")
        ]
        await app.bot.set_my_commands(commands)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("game", start_game_command))
    application.add_handler(CommandHandler("deposit", deposit_command))
    application.add_handler(CommandHandler("withdraw", withdraw_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("transfer", transfer_command))
    application.add_handler(CommandHandler("bonus", bonus_command))
    application.add_handler(CommandHandler("post", post_command))
    application.add_handler(CommandHandler("invite", invite_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("contact", contact_command))
    application.add_handler(CommandHandler("join", join_command))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    
    loop.run_until_complete(set_commands(application))
    loop.run_until_complete(start_api_server())

    print("🚀 የ ALEM BINGO ቦት በተሳካ ሁኔታ ተነስቷል...")
    application.run_polling()

if __name__ == '__main__':
    main()
