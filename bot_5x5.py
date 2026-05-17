import sqlite3
import random
import json
import base64  # 🔥 አዲሱ ማስተካከያ (ለሊንኩ ደህንነት)
from telegram import Update, ReplyKeyboardMarkup, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- 1. የዳታቤዝ መዋቅር መፍጠር ---
def init_db():
    conn = sqlite3.connect('cartela.db')
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
            numbers TEXT
        )''')
    conn.commit()
    conn.close()

# --- 2. የ 5x5 የቢንጎ ቁጥሮች ማመንጫ ---
def generate_bingo_numbers():
    b = random.sample(range(1, 16), 5)
    i = random.sample(range(16, 31), 5)
    n = random.sample(range(31, 46), 5)
    n[2] = "FREE"  # መካከለኛው ነፃ ቦታ
    g = random.sample(range(46, 61), 5)
    o = random.sample(range(61, 76), 5)
    
    matrix = []
    for r in range(5):
        matrix.append([b[r], i[r], n[r], g[r], o[r]])
    return matrix

# --- 3. ቢንጎ ማረጋገጫ ---
def check_bingo_win(matrix, marked_list):
    def is_ok(val):
        return val == "FREE" or int(val) in marked_list

    for row in matrix:
        if all(is_ok(x) for x in row): return True
    for col in range(5):
        if all(is_ok(matrix[row][col]) for row in range(5)): return True
    if all(is_ok(matrix[idx][idx]) for idx in range(5)): return True
    if all(is_ok(matrix[idx][4-idx]) for idx in range(5)): return True
    return False

# --- 4. የጨዋታው ሁኔታ ---
GAME_STATE = {
    "is_active": False,
    "admin_id": 7196518682,  # 🔴 የአድሚን IDህን እዚህ አስገባ
    "called_numbers": []
}

# --- 5. የቦት መጀመሪያ /start ትዕዛዝ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect('cartela.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, 100.0)", (user.id, user.username))
    conn.commit()
    conn.close()
    
    if user.id == GAME_STATE["admin_id"]:
        keyboard = [['📢 ጨዋታ ጀምር', '🛑 ጨዋታ ጨርስ'], ['🎲 ቁጥር ጥራ', '🎰 ካርቴላ ቁረጥ']]
    else:
        keyboard = [['🎰 ካርቴላ ቁረጥ', '💰 ሂሳብ እይ']]
        
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"ሰላም {user.first_name}👋! ወደ WebApp ቢንጎ ቦት በደህና መጣህ።",
        reply_markup=reply_markup
    )

# --- 6. የጽሑፍ መልዕክቶችን ማስተናገጃ ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    # === ሀ. የአድሚን ክፍሎች ===
    if text == '📢 ጨዋታ ጀምር' and user_id == GAME_STATE["admin_id"]:
        GAME_STATE["is_active"] = True
        GAME_STATE["called_numbers"] = []
        
        conn = sqlite3.connect('cartela.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cards")
        conn.commit()
        conn.close()
        await update.message.reply_text("📢 አዲስ ጨዋታ ተጀምሯል። ተጫዋቾች ካርቴላ መቁረጥ ይችላሉ!")
        return
        
    elif text == '🛑 ጨዋታ ጨርስ' and user_id == GAME_STATE["admin_id"]:
        GAME_STATE["is_active"] = False
        await update.message.reply_text("🛑 ጨዋታው በይፋ ተጠናቋል።")
        return
        
    elif text == '🎲 ቁጥር ጥራ' and user_id == GAME_STATE["admin_id"]:
        if not GAME_STATE["is_active"]:
            await update.message.reply_text("❌ መጀመሪያ ጨዋታውን ይጀምሩ!")
            return
        available = [n for n in range(1, 76) if n not in GAME_STATE["called_numbers"]]
        if not available:
            await update.message.reply_text("🔢 ሁሉም ቁጥሮች ተጠርተው አልቀዋል!")
            return
        num = random.choice(available)
        GAME_STATE["called_numbers"].append(num)
        await update.message.reply_text(f"🎲 የተጠራ ቁጥር: <b>[ {num} ]</b>", parse_mode="HTML")
        return

    # === ለ. የተጫዋች ክፍል (የሊንክ ስህተቱ የተስተካከለበት 🚀) ===
    elif 'ካርቴላ' in text:
        matrix = generate_bingo_numbers()
        
        conn = sqlite3.connect('cartela.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO cards (user_id, numbers) VALUES (?, ?)", (user_id, json.dumps(matrix)))
        card_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # ዳታውን ወደ አጭር ጽሁፍ እንቀይረዋለን (በቴሌግራም እንዳይናቅ)
        matrix_json = json.dumps(matrix)
        called_str = ",".join(map(str, GAME_STATE["called_numbers"]))
        
        # በሊንክ ላይ ስፔስና ቅንፍ እንዳይኖር በ Base64 እናጭቀዋለን
        matrix_encoded = base64.b64encode(matrix_json.encode()).decode()
        
        # 🔗 እጅግ አስተማማኝ እና አጭር URL
        webapp_url = f"https://amharicbingo.github.io/bingo-cards/?id={card_id}&m={matrix_encoded}&c={called_str}"
        
        keyboard = [[InlineKeyboardButton(text="🎮 ካርቴላውን ክፈት (WebApp)", web_app=WebAppInfo(url=webapp_url))]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"🎫 <b>ካርቴላ ቁጥር #{card_id} ተዘጋጅቷል!</b>\nከታች ያለውን ሰማያዊ ቁልፍ ተጭነህ በስልክህ መጫወት ትችላለህ።",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return
        
    elif text == '💰 ሂሳብ እይ':
        conn = sqlite3.connect('cartela.db')
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        await update.message.reply_text(f"💰 ያሎት ወቅታዊ ሂሳብ: {row[0] if row else 0.0} ብር")
        return

# --- 7. ከ WebApp ዳታ መቀበያ ---
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    webapp_data = update.effective_message.web_app_data.data
    data = json.loads(webapp_data)
    
    if data.get("action") == "claim_bingo":
        card_id = int(data.get("card_id"))
        marked_list = list(map(int, data.get("marked", [])))
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('cartela.db')
        cursor = conn.cursor()
        cursor.execute("SELECT numbers FROM cards WHERE card_id = ?", (card_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            await update.message.reply_text("❌ የካርቴላ መረጃ አልተገኘም!")
            return
            
        matrix = json.loads(row[0])
        
        if check_bingo_win(matrix, marked_list):
            await update.message.reply_text(f"🏆 <b>ቢንጎ!</b> ካርቴላ #{card_id} አሸንፏል! 🎉", parse_mode="HTML")
            await context.bot.send_message(
                chat_id=GAME_STATE["admin_id"], 
                text=f"🔔 ተጫዋች @{update.effective_user.username} በካርቴላ #{card_id} ቢንጎ ብሏል!"
            )
        else:
            await update.message.reply_text("❌ ማረጋገጫ፡ ካርቴላዎ ገና ሙሉ መስመር አልሰራም።")

# --- 8. ቦቱን የማስነሻ ዋና ፈንክሽን ---
def main():
    init_db()
    TOKEN = "8675884335:AAFNSXGp7unpPMHjqoLbQZsmVdoXFwpBITk"  # 🔴 የቦት ቶክንህን እዚህ አስገባ
    
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ እባክህ መጀመሪያ የቦት ቶክንህን አስገባ!")
        return

    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    
    print("🚀 የ WebApp ቢንጎ ቦት በተሳካ ሁኔታ ተነስቷል...")
    application.run_polling()

if __name__ == '__main__':
    main()
