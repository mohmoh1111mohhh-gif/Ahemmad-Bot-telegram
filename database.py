# database.py - نموذج البيانات لـ Ahemmad (SQLAlchemy)
from sqlalchemy import create_engine, Column, BigInteger, String, Boolean, Integer, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
import os

# يتم استدعاء مسار DB من ملف .env
DB_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/ahemmad_db") 

# **(التعديل الهام):** تحويل الرابط لاستخدام مشغل pg8000 الذي تم تثبيته
ENGINE_URL = DB_URL.replace("postgresql://", "postgresql+pg8000://")

Base = declarative_base()

class Group(Base):
    """جدول إعدادات المجموعة (لكل مجموعة إعدادات خاصة)"""
    __tablename__ = 'groups'
    id = Column(BigInteger, primary_key=True)
    welcome_message = Column(Text, default=None)
    link_filtering_enabled = Column(Boolean, default=True)
    flood_sensitivity = Column(Integer, default=3)
    admin_log_channel = Column(BigInteger, default=None)
    settings = relationship("GroupSetting", back_populates="group", cascade="all, delete-orphan")

class GroupSetting(Base):
    """جدول القوائم المخصصة للمجموعة (مثل الكلمات المحظورة)"""
    __tablename__ = 'group_settings'
    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey('groups.id'))
    setting_key = Column(String(50))
    setting_value = Column(Text)
    group = relationship("Group", back_populates="settings")

# **(التعديل):** استخدام ENGINE_URL الجديد والمعدل
engine = create_engine(ENGINE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """إنشاء الجداول عند أول تشغيل"""
    Base.metadata.create_all(bind=engine)

def get_db():
    """الحصول على جلسة DB وإغلاقها تلقائياً (Generator)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()