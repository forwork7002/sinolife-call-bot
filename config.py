"""Sozlamalar — .env faylidan o'qiladi."""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "").replace(" ", "")
    return {int(x) for x in raw.split(",") if x}


def _hm(name: str, default: str | None) -> time | None:
    raw = os.getenv(name, default or "") or ""
    if not raw.strip():
        return None
    h, m = raw.strip().split(":")
    return time(int(h), int(m))


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK", "")
_u = urlparse(BITRIX_WEBHOOK)
PORTAL_URL = f"{_u.scheme}://{_u.netloc}"

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
WORK_START = _hm("WORK_START", "09:00")
WORK_END = _hm("WORK_END", "22:00")
LUNCH_START = _hm("LUNCH_START", None)   # bo'sh = tushlik yo'q
LUNCH_END = _hm("LUNCH_END", None)
WORK_DAYS = {int(x) for x in os.getenv("WORK_DAYS", "1,2,3,4,5,6").split(",") if x}  # 1=dush ... 7=yaksh

ADMIN_IDS = _ids("ADMIN_IDS")            # rahbar(lar)ning Telegram ID si
EXCLUDE_USER_IDS = _ids("EXCLUDE_USER_IDS")
# Bitrix24 dagi bo'lim rahbarlari (ROPlar, direktor) hisobotga kirmaydi — faqat sotuvchilar.
# O'chirish uchun .env ga EXCLUDE_HEADS=0 yozing.
EXCLUDE_HEADS = os.getenv("EXCLUDE_HEADS", "1").strip() not in ("0", "no", "false", "")
# Faqat sotuvchilar: nomida shu so'z bor bo'limlar a'zolari olinadi (masalan "Sevinch(ROP)").
# Логистика, Операцион, Регистрация va boshqa bo'limlar hisobotga kirmaydi.
SALES_DEPT_KEYWORD = os.getenv("SALES_DEPT_KEYWORD", "ROP").strip()

SLOT_MIN = 30                            # hisoblash bo'lagi (daqiqa)
# Guruhlarga avto-hisobot har necha daqiqada keladi (30 ning karralisi: 30, 60, 90...)
REPORT_EVERY_MIN = max(30, (int(os.getenv("REPORT_EVERY_MIN", "60")) // 30) * 30)
DOZVON_MIN_SEC = int(os.getenv("DOZVON_MIN_SEC", "10"))
LONG_CLIENT_MIN = int(os.getenv("LONG_CLIENT_MIN", "15"))   # 1 mijoz bilan shundan ko'p — ogohlantirish
REPORT_DELAY_MIN = int(os.getenv("REPORT_DELAY_MIN", "2"))
STATE_FILE = BASE_DIR / "state.json"

# 1 soatlik norma, yuqoridan pastga: (belgi, nomi, daqiqa, dozvon)
LEVELS = [
    ("🟢", "Yaxshi", 30, 5),
    ("🟡", "Minimal", 20, 3),
]
LOW = ("🔴", "Normadan past")
