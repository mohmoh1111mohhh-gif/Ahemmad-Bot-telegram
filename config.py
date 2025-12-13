# config.py
import os
from dotenv import load_dotenv

# يحمل متغيرات البيئة من ملف .env
load_dotenv()

# مفتاح API الخاص بالبوت
# **(تم التصحيح):** قراءة اسم المتغير AHMMAD_TOKEN
BOT_TOKEN = os.getenv("AHMMAD_TOKEN")

# معرفات المشرفين الرئيسيين للبوت (صحيح)
SUPER_ADMIN_IDS = [6499543059]