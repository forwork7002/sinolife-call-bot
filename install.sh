#!/usr/bin/env bash
# Sinolife qo'ng'iroq boti — o'rnatish / yangilash
set -e
APP=/opt/sinolife-call-bot
SRC="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

echo "=== 1/4 Kerakli dasturlar o'rnatilmoqda ==="
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "XATO: Python 3.10+ kerak (Ubuntu 22.04 yoki 24.04 server ishlating)"; exit 1; }

echo "=== 2/4 Bot fayllari ko'chirilmoqda ==="
mkdir -p "$APP"
cp "$SRC"/*.py "$SRC"/requirements.txt "$APP"/
cd "$APP"
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

echo "=== 3/4 Sozlamalar ==="
if [ ! -f .env ]; then
  # Buyruq ichida berilgan bo'lsa (BOT_TOKEN=... bash install.sh) — so'ramaydi
  TOKEN="${BOT_TOKEN:-}"; HOOK="${BITRIX_WEBHOOK:-}"; ADMINS="${ADMIN_IDS:-}"
  [ -n "$TOKEN" ]  || read -rp "Telegram bot TOKEN (@BotFather dan): " TOKEN
  [ -n "$HOOK" ]   || read -rp "Bitrix vebhuk (https://....bitrix24.kz/rest/...): " HOOK
  [ -n "$ADMINS" ] || read -rp "Admin Telegram ID (vergul bilan, @userinfobot dan): " ADMINS
  cat > .env <<ENV
BOT_TOKEN=$TOKEN
BITRIX_WEBHOOK=$HOOK
ADMIN_IDS=$ADMINS
WORK_START=08:30
WORK_END=18:00
# Tushlik bo'lsa yozing, masalan 13:00 va 14:00
LUNCH_START=
LUNCH_END=
# 1=dushanba ... 7=yakshanba
WORK_DAYS=1,2,3,4,5,6
DOZVON_MIN_SEC=10
LONG_CLIENT_MIN=15
# Hisobotga kirmasin (masalan ROPlar), Bitrix user ID lar vergul bilan
EXCLUDE_USER_IDS=
ENV
  chmod 600 .env
  echo ".env yaratildi"
else
  echo ".env bor — eski sozlamalar saqlandi"
fi

echo "=== 4/4 Doimiy ishlash (systemd) ==="
cat > /etc/systemd/system/sinolife-call-bot.service <<UNIT
[Unit]
Description=Sinolife qo'ng'iroq nazorati boti
After=network-online.target

[Service]
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable sinolife-call-bot >/dev/null
systemctl restart sinolife-call-bot

echo
echo "=== Bitrix24 tekshiruvi ==="
venv/bin/python bot.py --check || true
echo
sleep 3
if systemctl is-active --quiet sinolife-call-bot; then
  echo "✅ BOT ISHLAYAPTI. Telegram'da botga /start yozing."
else
  echo "❌ Bot ishga tushmadi. Sababini ko'rish: journalctl -u sinolife-call-bot -n 50"
fi
