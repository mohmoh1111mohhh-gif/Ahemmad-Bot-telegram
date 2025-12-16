# main.py - النسخة المعدلة مع رسالة حذف الروابط المخصصة

import os
import re
import time
import random
import logging
from collections import defaultdict
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from config import BOT_TOKEN, SUPER_ADMIN_IDS
from database import init_db, get_db, Group, GroupSetting, Session

# إعدادات التسجيل
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r'(https?://[^\s]+|t\.me/[^\s]+|@\w+|telegram\.me/[^\s]+)', re.IGNORECASE)
FLOOD_TRACKER = defaultdict(lambda: defaultdict(list))
XO_GAMES = defaultdict(dict)
EMOJIS = {'X': '❌', 'O': '⭕', ' ': '⬜'}
BOT_O_ID, BOARD_SIZE = -1, 3

def get_or_create_group(chat_id: int, db: Session) -> Group:
    group = db.query(Group).filter(Group.id == chat_id).first()
    if not group:
        group = Group(id=chat_id)
        db.add(group); db.commit(); db.refresh(group)
    return group

# --- دالة الحماية المعدلة لرسالة حذف الروابط ---
async def check_for_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user.id in SUPER_ADMIN_IDS: return False
    
    message = update.message
    text = message.text_html or message.caption_html or ""
    
    db_gen = get_db(); db = next(db_gen)
    try:
        group = get_or_create_group(update.effective_chat.id, db)
        if not group.link_filtering_enabled: return False
    finally: db.close()
    
    # فحص وجود روابط أو يوزرات
    if URL_REGEX.search(text) or (message.entities and any(e.type in ['url', 'text_link', 'mention'] for e in message.entities)):
        try:
            await message.delete()
            # اسم المستخدم مع رابط البروفايل
            user_mention = f"[{user.first_name}](tg://user?id={user.id})"
            response_text = (
                f"• عذراً عزيزي ↤︎「 {user_mention} 」\n"
                f"• ممنوع إرسال الروابط هنا ."
            )
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=response_text, 
                parse_mode='Markdown'
            )
            return True
        except Exception as e:
            logger.error(f"Error in link deletion: {e}")
            return True
    return False

async def check_for_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user.id in SUPER_ADMIN_IDS: return False
    uid, cid = update.effective_user.id, update.effective_chat.id
    now = time.time()
    FLOOD_TRACKER[cid][uid] = [t for t in FLOOD_TRACKER[cid][uid] if t >= now - 3]
    FLOOD_TRACKER[cid][uid].append(now)
    if len(FLOOD_TRACKER[cid][uid]) > 5:
        try:
            await context.bot.restrict_chat_member(cid, uid, can_send_messages=False, until_date=int(now)+600)
            await update.message.reply_text("🚫 تم كتمك 10 دقائق بسبب التكرار.")
            return True
        except: return False
    return False

# --- دالة الردود التلقائية ---
async def handle_greetings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text: return
    text = update.message.text.strip().lower()
    
    if any(word in text for word in ["سلام", "السلام عليكم", "سلام عليكم"]):
        await update.message.reply_text("وعليكم السلام ورحمة الله وبركاته")
    elif "ألاء" in text or "الاء" in text:
        await update.message.reply_text("أترك حبيبتي 😍💖")
    elif text == "باي":
        await update.message.reply_text("مانك مطول؟")

# --- منطق XO والتشغيل (نفس الأكواد السابقة مع الترتيب الصحيح) ---
async def start_xo_by_text(update, context):
    kbd = [[InlineKeyboardButton("🧑‍🤝‍🧑 إنسان", callback_data="XO_MODE_PVP"), InlineKeyboardButton("🤖 بوت", callback_data="XO_MODE_PVB")]]
    await update.message.reply_text("🎮 اختر وضع اللعب:", reply_markup=InlineKeyboardMarkup(kbd))

async def xo_mode_select_handler(update, context):
    query = update.callback_query; await query.answer()
    cid, uid = query.message.chat_id, query.from_user.id
    XO_GAMES[cid] = {'board': [[' ']*3 for _ in range(3)], 'player_x': uid, 'player_o': BOT_O_ID if "PVB" in query.data else None, 'turn': 'X', 'message_id': query.message.message_id}
    txt = "بدأت اللعبة! دورك X" if "PVB" in query.data else "بانتظار انضمام لاعب O..."
    await query.edit_message_text(txt, reply_markup=get_board_markup(cid))

def check_win(b):
    for i in range(3):
        if b[i][0]==b[i][1]==b[i][2]!=' ': return b[i][0]
        if b[0][i]==b[1][i]==b[2][i]!=' ': return b[0][i]
    if b[0][0]==b[1][1]==b[2][2]!=' ': return b[0][0]
    if b[0][2]==b[1][1]==b[2][0]!=' ': return b[0][2]
    return None

def get_board_markup(cid):
    game = XO_GAMES[cid]
    return InlineKeyboardMarkup([[InlineKeyboardButton(EMOJIS[game['board'][r][c]], callback_data=f"XO_{r}_{c}") for c in range(3)] for r in range(3)])

async def xo_button_handler(update, context):
    query = update.callback_query; await query.answer(); cid, uid = query.message.chat_id, query.from_user.id
    if cid not in XO_GAMES: return
    game = XO_GAMES[cid]
    _, r, c = query.data.split('_'); r, c = int(r), int(c)
    if game['player_o'] is None and uid != game['player_x']:
        game['player_o'] = uid; await query.edit_message_text(f"انضم اللاعب O. الدور الآن لـ {EMOJIS['X']}", reply_markup=get_board_markup(cid)); return
    if (game['turn'] == 'X' and uid != game['player_x']) or (game['turn'] == 'O' and uid != game['player_o'] and game['player_o'] != BOT_O_ID): return
    if game['board'][r][c] != ' ': return
    game['board'][r][c] = game['turn']
    win = check_win(game['board'])
    if win or not any(' ' in row for row in game['board']):
        res = f"🏆 الفائز: {EMOJIS[win]}" if win else "🤝 تعادل!"
        await query.edit_message_text(res, reply_markup=get_board_markup(cid))
        del XO_GAMES[cid]; return
    game['turn'] = 'O' if game['turn'] == 'X' else 'X'
    if game['turn'] == 'O' and game['player_o'] == BOT_O_ID:
        empty = [(ri, ci) for ri in range(3) for ci in range(3) if game['board'][ri][ci] == ' ']
        if empty:
            rx, cx = random.choice(empty); game['board'][rx][cx] = 'O'
            win = check_win(game['board'])
            if win or not any(' ' in row for row in game['board']):
                res = f"🏆 الفائز: {EMOJIS[win]}" if win else "🤝 تعادل!"
                await query.edit_message_text(res, reply_markup=get_board_markup(cid))
                del XO_GAMES[cid]; return
            game['turn'] = 'X'
    await query.edit_message_text(f"الدور لـ: {EMOJIS[game['turn']]}", reply_markup=get_board_markup(cid))

async def protection_main_handler(update, context):
    if await check_for_flood(update, context): return
    if await check_for_links(update, context): return

def main():
    token = os.environ.get("TOKEN") or BOT_TOKEN
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("🛡️ البوت يعمل.")))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r'^xo$', re.I)), start_xo_by_text))
    app.add_handler(CallbackQueryHandler(xo_mode_select_handler, pattern="^XO_MODE_"))
    app.add_handler(CallbackQueryHandler(xo_button_handler, pattern="^XO_"))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND, protection_main_handler), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_greetings), group=2)
    app.run_polling()

if __name__ == "__main__": main()
