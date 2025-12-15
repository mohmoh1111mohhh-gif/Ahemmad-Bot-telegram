# main.py - منظومة Ahemmad 

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
# تأكد أن هذه الملفات موجودة في مشروعك:
from config import BOT_TOKEN, SUPER_ADMIN_IDS
from database import init_db, get_db, Group, GroupSetting, Session 
import logging
import time
from collections import defaultdict
import re
from telegram.constants import ChatType
import random
import os

# تهيئة التسجيل
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- متغيرات ووظائف مساعدة ---
URL_REGEX = re.compile(r'(https?://[^\s]+|t\.me/[^\s]+|@\w+|telegram\.me/[^\s]+)', re.IGNORECASE)
FLOOD_TRACKER = defaultdict(lambda: defaultdict(list))
FLOOD_LIMIT = 5
FLOOD_WINDOW = 3

# --- متغيرات XO (تيك تاك تو) ---
XO_GAMES = defaultdict(dict)
EMOJIS = {'X': '❌', 'O': '⭕', ' ': '⬜'}
BOT_O_ID = -1 # معرف وهمي للبوت كلاعب O
BOARD_SIZE = 3

def get_or_create_group(chat_id: int, db: Session) -> Group:
    """استرجاع إعدادات المجموعة أو إنشائها"""
    group = db.query(Group).filter(Group.id == chat_id).first()
    if not group:
        group = Group(id=chat_id)
        db.add(group)
        db.commit()
        db.refresh(group)
    return group

async def check_admin_permission(update: Update, context: ContextTypes.DEFAULT_TYPE, required_permission: str) -> bool:
    """التحقق من صلاحيات المشرفين"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if user_id in SUPER_ADMIN_IDS: return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if required_permission == 'can_restrict_members':
            return member.status in ['creator', 'administrator'] and member.can_restrict_members
        elif required_permission == 'can_delete_messages':
            return member.status in ['creator', 'administrator'] and member.can_delete_messages
        return False
    except Exception as e:
        logger.error(f"خطأ في التحقق من الصلاحيات: {e}")
        return False

# --- دوال الحماية (Modules) ---

async def check_for_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """فلترة الروابط بناءً على إعدادات المجموعة"""
    message, user, chat_id = update.message, update.effective_user, update.effective_chat.id
    text = message.text_html or message.caption_html or ""
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user.id)
        if chat_member.status in ['creator', 'administrator']: return False
    except Exception: pass

    db_generator = get_db(); db: Session = next(db_generator)
    try:
        group = get_or_create_group(chat_id, db)
        if not group.link_filtering_enabled: return False
    finally:
        db.close()

    has_urls = URL_REGEX.search(text) or message.entities and any(e.type in ['url', 'text_link'] for e in message.entities)
    if has_urls:
        try:
            await message.delete()
            five_minutes = int(time.time()) + 300 
            await context.bot.restrict_chat_member(chat_id, user.id, can_send_messages=False, until_date=five_minutes)
            await context.bot.send_message(chat_id, f"🚨 **تنبيه حماية:** تم حذف رسالة الروابط للمستخدم **@{user.username or user.first_name}** وتم كتمه 5 دقائق.", parse_mode='Markdown')
            return True
        except Exception as e:
            logger.error(f"فشل تنفيذ عقوبة الرابط: {e}")
            return True 
    return False

async def check_for_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """منع إغراق المجموعة بالرسائل (Anti-Flood)"""
    message, chat_id, user_id = update.message, update.effective_chat.id, update.message.from_user.id
    current_time = time.time()
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user.id)
        if chat_member.status in ['creator', 'administrator']: return False
    except Exception: pass

    FLOOD_TRACKER[chat_id][user_id] = [t for t in FLOOD_TRACKER[chat_id][user_id] if t >= current_time - FLOOD_WINDOW]
    FLOOD_TRACKER[chat_id][user_id].append(current_time)
    
    if len(FLOOD_TRACKER[chat_id][user_id]) > FLOOD_LIMIT:
        mute_duration = 600
        until_date = int(current_time) + mute_duration
        try:
            await context.bot.restrict_chat_member(chat_id, user.id, can_send_messages=False, until_date=until_date)
            FLOOD_TRACKER[chat_id][user_id] = [] 
            await context.bot.send_message(chat_id, f"🚫 **Ahemmad:** تم كتم المستخدم **@{message.from_user.username or message.from_user.first_name}** لمدة 10 دقائق لتجاوزه حد الفيضانات.", parse_mode='Markdown')
            return True
        except Exception as e:
            logger.error(f"فشل كتم المستخدم (Flood): {e}")
            return False
    return False

async def check_for_blacklisted_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """فلترة الكلمات المسيئة من إعدادات DB"""
    message, user, chat_id = update.message, update.effective_user, update.effective_chat.id
    text = message.text.lower() if message.text else ""
    if not text: return False
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user.id)
        if chat_member.status in ['creator', 'administrator']: return False
    except Exception: pass

    db_generator = get_db(); db: Session = next(db_generator)
    try:
        blacklisted_settings = db.query(GroupSetting).filter(GroupSetting.group_id == chat_id, GroupSetting.setting_key == 'blacklisted_words').all()
        banned_words = [s.setting_value for s in blacklisted_settings]
        
        for word in banned_words:
            if word in text:
                await message.delete()
                one_hour = int(time.time()) + 3600
                await context.bot.restrict_chat_member(chat_id, user.id, can_send_messages=False, until_date=one_hour)
                await context.bot.send_message(chat_id, f"🛑 **Ahemmad:** تم حذف الرسالة وكتم المستخدم لساعة لاستخدامه كلمة محظورة (**{word}**).", parse_mode='Markdown')
                return True
    except Exception as e:
        logger.error(f"خطأ في فلترة الكلمات: {e}")
    finally:
        db.close()
    return False

# --- دالة الردود التلقائية ---

async def handle_greetings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """الرد على التحية والكلمات المخصصة."""
    if not update.message or update.message.text is None: return
    if update.message.text.startswith('/'): return
    
    if update.message and update.message.text:
        text = update.message.text.lower().strip()
        
        greetings = ["سلام", "السلام عليكم", "سلام عليكم"]
        
        if any(word in text for word in greetings):
            await update.message.reply_text("وعليكم السلام ورحمة الله وبركاته")
        elif text == "باي":
            await update.message.reply_text("مانك مطول؟")
        elif text == "ألاء":
            await update.message.reply_text("أترك حبيبتي 😍💖")
        elif "صباح الخير" in text:
            await update.message.reply_text("صباح النور والسرور!")

# --- دوال لعبة XO (تيك تاك تو) ---

def get_empty_cells(board):
    """إرجاع قائمة بجميع الخلايا الفارغة على اللوحة."""
    cells = []
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] == ' ':
                cells.append((r, c))
    return cells

def check_win(board):
    """التحقق من حالة الفوز."""
    for i in range(BOARD_SIZE):
        if board[i][0] == board[i][1] == board[i][2] != ' ': return board[i][0]
        if board[0][i] == board[1][i] == board[2][i] != ' ': return board[0][i]
    if board[0][0] == board[1][1] == board[2][2] != ' ': return board[0][0]
    if board[0][2] == board[1][1] == board[2][0] != ' ': return board[0][2]
    return None

def check_draw(board):
    """التحقق من حالة التعادل."""
    return not check_win(board) and not get_empty_cells(board)

def get_board_markup(chat_id):
    """إنشاء لوحة المفاتيح المضمنة (Inline Keyboard) للعبة XO."""
    game_state = XO_GAMES.get(chat_id, {})
    board = game_state.get('board', [[' ']*BOARD_SIZE for _ in range(BOARD_SIZE)])
    
    keyboard = []
    for r in range(BOARD_SIZE):
        row_buttons = []
        for c in range(BOARD_SIZE):
            callback_data = f"XO_{r}_{c}"
            row_buttons.append(InlineKeyboardButton(EMOJIS[board[r][c]], callback_data=callback_data))
        keyboard.append(row_buttons)
        
    return InlineKeyboardMarkup(keyboard)

def bot_move(board):
    """منطق الذكاء الاصطناعي البسيط للبوت (اللاعب O)."""
    empty_cells = get_empty_cells(board)
    if not empty_cells: return None
    
    for marker in ['O', 'X']:
        for r, c in empty_cells:
            board[r][c] = marker
            if check_win(board) == marker:
                board[r][c] = ' '
                return (r, c)
            board[r][c] = ' '
            
    if board[1][1] == ' ': return (1, 1)
    corners = [(0, 0), (0, 2), (2, 0), (2, 2)]
    random.shuffle(corners)
    for r, c in corners:
        if board[r][c] == ' ': return (r, c)
            
    return random.choice(empty_cells)


# --- معالجات XO (XO Handlers) ---

async def start_xo_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بدء عملية اختيار وضع اللعب عند كتابة 'XO' أو 'xo' (بدون قيود على الألعاب السابقة)."""
    chat_id = update.effective_chat.id
    
    keyboard = [
        [InlineKeyboardButton("🧑‍🤝‍🧑 لعب ضد إنسان آخر", callback_data="XO_MODE_PVP")],
        [InlineKeyboardButton("🤖 لعب ضد البوت", callback_data="XO_MODE_PVB")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎮 **اختر وضع اللعب:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def xo_mode_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج لاختيار وضع اللعب (PVP أو PVB)."""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    mode = query.data
    
    # تهيئة حالة اللعبة الجديدة (تجاوز أي لعبة قديمة)
    XO_GAMES[chat_id] = {
        'board': [[' ']*BOARD_SIZE for _ in range(BOARD_SIZE)],
        'player_x': user_id, 
        'player_o': None,
        'turn': 'X',
        'message_id': query.message.message_id
    }
    
    if mode == "XO_MODE_PVP":
        text = (f"🎮 **بدء لعبة XO (إنسان ضد إنسان)!**\n\n"
                f"**اللاعب X** هو **{query.from_user.first_name}**.\n\n"
                f"**اللاعب O:** يرجى الضغط على أي مربع للانضمام والبدء.")
        
    elif mode == "XO_MODE_PVB":
        XO_GAMES[chat_id]['player_o'] = BOT_O_ID
        text = (f"🎮 **بدء لعبة XO (ضد البوت)!**\n\n"
                f"**أنت** هو اللاعب X ({EMOJIS['X']}).\n"
                f"**البوت** هو اللاعب O ({EMOJIS['O']}).\n\n"
                f"**الدور الحالي:** {EMOJIS['X']}")
        
    await query.edit_message_text(text=text, reply_markup=get_board_markup(chat_id), parse_mode='Markdown')

async def process_xo_move(chat_id, user_id, r, c, context: ContextTypes.DEFAULT_TYPE):
    """دالة مساعدة لمعالجة حركة اللاعب وتحديث اللوحة."""
    game = XO_GAMES[chat_id]
    
    if r != -1 and c != -1: 
        if game['board'][r][c] != ' ': return 
        game['board'][r][c] = game['turn']
    
    winner = check_win(game['board'])
    if winner or check_draw(game['board']):
        final_text = f"🏆 **انتهت اللعبة!** فاز اللاعب {EMOJIS.get(winner, ' ')} 🎉" if winner else "🤝 **انتهت اللعبة!** تعادل. 😩"
        await context.bot.edit_message_text(chat_id=chat_id, message_id=game['message_id'], text=final_text, reply_markup=get_board_markup(chat_id), parse_mode='Markdown')
        del XO_GAMES[chat_id]
        return
        
    game['turn'] = 'O' if game['turn'] == 'X' else 'X'

    player_x_info = await context.bot.get_chat_member(chat_id, game['player_x'])
    player_x_name = player_x_info.user.first_name
    player_o_name = "البوت" if game['player_o'] == BOT_O_ID else \
                    (await context.bot.get_chat_member(chat_id, game['player_o'])).user.first_name if game['player_o'] else "ينتظر لاعب O"
    
    current_turn_text = f"**الدور الحالي:** {EMOJIS[game['turn']]}"
    
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=game['message_id'],
        text=f"🎮 **اللاعب X:** {player_x_name}\n**اللاعب O:** {player_o_name}\n\n{current_turn_text}",
        reply_markup=get_board_markup(chat_id),
        parse_mode='Markdown'
    )
    return True

async def xo_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج ضغطات أزرار لوحة XO (تم التصحيح لمنع الحركة المزدوجة لـ X في بداية PVP)."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    
    if chat_id not in XO_GAMES: await query.edit_message_text("❌ انتهت اللعبة."); return
        
    game = XO_GAMES[chat_id]
    is_bot_o = game['player_o'] == BOT_O_ID

    # 1. تحليل الحركة المطلوبة
    try: _, r_str, c_str = query.data.split('_'); r, c = int(r_str), int(c_str)
    except ValueError: return
    
    # 2. انضمام اللاعب O (PVP)
    if game['player_o'] is None:
        # إذا لم يكن اللاعب O معينًا
        if user_id != game['player_x']:
            # المستخدم ليس X، إذن ينضم كلاعب O
            game['player_o'] = user_id
            await process_xo_move(chat_id, user_id, -1, -1, context) # تحديث الرسالة فقط (لا حركة)
            await query.answer(f"أنت الآن اللاعب O. دور اللاعب X لتبدأ.", show_alert=True)
            return
        else:
            # اللاعب X ضغط مرة أخرى قبل انضمام O
            await query.answer("🚫 يرجى انتظار انضمام اللاعب O أولاً!", show_alert=True)
            return
    
    # 3. التحقق من الدور (بعد التأكد من تعيين player_o)
    is_player_x = user_id == game['player_x']
    is_player_o = user_id == game['player_o']
    
    if game['turn'] == 'X' and not is_player_x: await query.answer("🚫 ليس دورك!", show_alert=True); return
    if game['turn'] == 'O' and not is_player_o and not is_bot_o: await query.answer("🚫 ليس دورك!", show_alert=True); return
    
    # 4. تنفيذ الحركة
    if game['board'][r][c] != ' ': await query.answer("❌ هذا المربع مأخوذ!", show_alert=True); return
        
    move_successful = await process_xo_move(chat_id, user_id, r, c, context)
    
    # 5. دور البوت (إذا كانت PVB وكانت الحركة ناجحة)
    if move_successful and is_bot_o and game['turn'] == 'O':
        r_bot, c_bot = bot_move(game['board'])
        await process_xo_move(chat_id, BOT_O_ID, r_bot, c_bot, context)

# --- معالج الرسائل ---
async def protection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ترتيب أولويات الحماية"""
    if not update.message or update.message.text is None: return

    if await check_for_flood(update, context): return
    if await check_for_links(update, context): return
    if await check_for_blacklisted_words(update, context): return

# --- أوامر الإدارة والإعدادات (Handlers) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f'🛡️ **Ahemmad** جاهز للحماية. يرجى تعييني كمشرف ومنحي صلاحيات الحظر والحذف.', parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id in SUPER_ADMIN_IDS:
        await update.message.reply_text("✅ نظام Ahemmad يعمل بكامل طاقته ويراقب المجموعة.", parse_mode='Markdown')
    else:
        await update.message.reply_text("⛔ لا تمتلك الصلاحية الكافية.", parse_mode='Markdown')

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin_permission(update, context, 'can_restrict_members'):
        await update.message.reply_text("⛔ تحتاج صلاحية تقييد الأعضاء.", parse_mode='Markdown'); return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ يجب الرد على رسالة المستخدم.", parse_mode='Markdown'); return
    target_user, chat_id = update.message.reply_to_message.from_user, update.effective_chat.id
    duration_minutes = int(context.args[0]) if context.args and context.args[0].isdigit() else 30
    until_date = int(time.time()) + (duration_minutes * 60)
    try:
        await context.bot.restrict_chat_member(chat_id, target_user.id, can_send_messages=False, until_date=until_date)
        await update.message.reply_text(f"✅ تم كتم المستخدم **@{target_user.username or target_user.first_name}** لمدة **{duration_minutes}** دقيقة.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"فشل كتم المستخدم: {e}")
        await update.message.reply_text("⚠️ فشل تنفيذ الكتم.", parse_mode='Markdown')

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin_permission(update, context, 'can_restrict_members'):
        await update.message.reply_text("⛔ لا يمكنك حظر الأعضاء. تحتاج صلاحية تقييد الأعضاء.", parse_mode='Markdown'); return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ يجب الرد على رسالة المستخدم المراد حظره.", parse_mode='Markdown'); return
    target_user, chat_id = update.message.reply_to_message.from_user, update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target_user.id)
        await update.message.reply_text(f"❌ تم حظر المستخدم **@{target_user.username or target_user.first_name}** بشكل دائم.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"فشل حظر المستخدم: {e}")
        await update.message.reply_text("⚠️ فشل تنفيذ الحظر.", parse_mode='Markdown')

async def toggle_link_filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not await check_admin_permission(update, context, 'can_restrict_members'):
        await update.message.reply_text("⛔ لا تمتلك صلاحية المشرف المطلوبة.", parse_mode='Markdown'); return
    if not context.args or context.args[0].lower() not in ['on', 'off']:
        await update.message.reply_text("❌ الاستخدام: `/toggle_links on` أو `/toggle_links off`", parse_mode='Markdown'); return
    new_state = context.args[0].lower() == 'on'
    db_generator = get_db(); db: Session = next(db_generator)
    try:
        group = get_or_create_group(chat_id, db); group.link_filtering_enabled = new_state
        db.commit()
        status = "مفعلة" if new_state else "معطلة"
        await update.message.reply_text(f"✅ **Ahemmad:** فلترة الروابط أصبحت: **{status}**.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"خطأ في تحديث إعدادات DB: {e}")
        await update.message.reply_text("⚠️ حدث خطأ في قاعدة البيانات.", parse_mode='Markdown')
    finally:
        db.close()

async def add_blacklisted_word_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not await check_admin_permission(update, context, 'can_delete_messages'):
        await update.message.reply_text("⛔ لا تمتلك صلاحية حذف الرسائل.", parse_mode='Markdown'); return
    if not context.args:
        await update.message.reply_text("❌ الاستخدام: `/add_word كلمة_مسيئة`", parse_mode='Markdown'); return
    word = " ".join(context.args).lower().strip()
    db_generator = get_db(); db: Session = next(db_generator)
    try:
        existing_setting = db.query(GroupSetting).filter(GroupSetting.group_id == chat_id, GroupSetting.setting_key == 'blacklisted_words', GroupSetting.setting_value == word).first()
        if existing_setting:
            await update.message.reply_text(f"⚠️ الكلمة (**{word}**) موجودة بالفعل.", parse_mode='Markdown'); return

        new_word_setting = GroupSetting(group_id=chat_id, setting_key='blacklisted_words', setting_value=word)
        db.add(new_word_setting); db.commit()
        await update.message.reply_text(f"✅ تم إضافة الكلمة **{word}** إلى قائمة المحظورات.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"خطأ في إضافة كلمة محظورة: {e}")
        await update.message.reply_text("⚠️ حدث خطأ في قاعدة البيانات.", parse_mode='Markdown')
    finally:
        db.close()


def main() -> None:
    # استخدام os.environ للحصول على التوكن (TOKEN) من Render، أو استخدام BOT_TOKEN من config.py
    token = os.environ.get("TOKEN") or BOT_TOKEN 
    if not token:
        logger.error("خطأ: لم يتم العثور على التوكن (TOKEN أو BOT_TOKEN).")
        return
        
    init_db() 
    application = Application.builder().token(token).build()

    # 1. تسجيل معالجات الأوامر
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("mute", mute_command)) 
    application.add_handler(CommandHandler("ban", ban_command)) 
    application.add_handler(CommandHandler("toggle_links", toggle_link_filter_command))
    application.add_handler(CommandHandler("add_word", add_blacklisted_word_command))
    
    # 2. تسجيل معالجات لعبة XO
    xo_pattern = re.compile(r'^(xo|XO)$', flags=re.IGNORECASE)
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(xo_pattern), start_xo_by_text))
    application.add_handler(CallbackQueryHandler(xo_mode_select_handler, pattern=r'^XO_MODE_'))
    application.add_handler(CallbackQueryHandler(xo_button_handler, pattern=r'^XO_[0-9]_[0-9]$'))


    # 3. معالج الرسائل العامة (الحماية أولاً)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, protection_handler))
    
    # 4. معالج الردود التلقائية (ثانياً)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_greetings))


    logger.info("Ahemmad يبدأ عمليات المراقبة الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
