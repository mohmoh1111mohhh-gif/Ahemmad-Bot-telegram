# main.py - منظومة Ahemmad 

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import BOT_TOKEN, SUPER_ADMIN_IDS
from database import init_db, get_db, Group, GroupSetting, Session
import logging
import time
from collections import defaultdict
import re
from telegram.constants import ChatType

# --- الإضافات الجديدة لميزة يوتيوب ---
import os 
from yt_dlp import YoutubeDL 
# --- نهاية الإضافات ---

# تهيئة التسجيل
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- متغيرات ووظائف مساعدة ---
URL_REGEX = re.compile(r'(https?://[^\s]+|t\.me/[^\s]+|@\w+|telegram\.me/[^\s]+)', re.IGNORECASE)
FLOOD_TRACKER = defaultdict(lambda: defaultdict(list))
FLOOD_LIMIT = 5
FLOOD_WINDOW = 3

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

# --- دالة جديدة للبحث في يوتيوب ---
async def youtube_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """البحث في يوتيوب وإرسال ملف صوتي."""
    message = update.message
    text = message.text
    chat_id = update.effective_chat.id

    # 1. استخراج كلمة البحث باستخدام Regex
    match = re.search(r'^(يوت|يوتيوب)\s+(.+)', text, re.IGNORECASE)
    
    if not match: return
    
    search_query = match.group(2).strip()
    
    if not search_query:
        await message.reply_text("❌ الرجاء كتابة كلمة البحث بعد كلمة (يوت).")
        return

    # إرسال رسالة "جاري البحث..."
    status_message = await message.reply_text(f"🔍 جاري البحث عن: **{search_query}** وتحويله إلى ملف صوتي...", parse_mode='Markdown')

    # إنشاء مسار ملف مؤقت فريد
    audio_file_path = f"audio_temp_{chat_id}.mp3"
    
    # 2. إعداد خيارات التحميل لـ yt-dlp
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': audio_file_path,
        'quiet': True,
        'skip_download': False,
        'default_search': 'ytsearch',
        'max_downloads': 1
    }

    try:
        # 3. البحث وتحميل الفيديو الأول
        with YoutubeDL(ydl_opts) as ydl:
            # البحث عن الفيديو الأول فقط
            info = ydl.extract_info(f"ytsearch1:{search_query}", download=True)
            
            if not info or not info.get('entries'):
                await status_message.edit_text("❌ لم يتم العثور على نتائج مطابقة لطلبك.")
                return
            
            video_info = info['entries'][0]

        # 4. إرسال الملف الصوتي
        with open(audio_file_path, 'rb') as audio_file:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=video_info.get('title'),
                performer=video_info.get('channel'),
                caption=f"🎧 المصدر: **{video_info.get('title')}**\nالقناة: {video_info.get('channel')}",
                parse_mode='Markdown'
            )
        
        await status_message.delete() # حذف رسالة "جاري البحث..."
        
    except Exception as e:
        logger.error(f"خطأ في عملية يوتيوب: {e}")
        await status_message.edit_text("⚠️ حدث خطأ أثناء جلب الملف الصوتي. تأكد من توفر مكتبات `yt-dlp` و `ffmpeg`.")
        
    finally:
        # 5. الحذف المضمون للملف (لضمان عدم حفظ أي بيانات)
        if os.path.exists(audio_file_path):
            os.remove(audio_file_path)

# --- نهاية دالة يوتيوب ---

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
    if not BOT_TOKEN:
        logger.error("خطأ: لم يتم العثور على AHMMAD_TOKEN. تأكد من إعداد ملف .env.")
        return
        
    init_db() # إنشاء الجداول عند البدء
    application = Application.builder().token(BOT_TOKEN).build()

    # تسجيل الـ Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("mute", mute_command)) 
    application.add_handler(CommandHandler("ban", ban_command)) 
    application.add_handler(CommandHandler("toggle_links", toggle_link_filter_command))
    application.add_handler(CommandHandler("add_word", add_blacklisted_word_command))
    
    # --- المعالج الجديد: البحث في يوتيوب ---
    youtube_filter = filters.Regex(r'^(يوت|يوتيوب)\s+', flags=re.IGNORECASE) 
    application.add_handler(MessageHandler(filters.TEXT & youtube_filter, youtube_search_handler))
    # ------------------------------------

    # معالج الرسائل العامة (يتم تمرير الرسائل إليه لعمليات الحماية التلقائية)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, protection_handler))

    logger.info("Ahemmad يبدأ عمليات المراقبة الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
