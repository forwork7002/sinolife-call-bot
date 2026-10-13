# Sinolife qo'ng'iroq boti — o'rnatish yo'riqnomasi

## Bot nima qiladi
- Bitrix24 dan har bir sotuvchining telefonda gaplashgan vaqtini va dozvonlarini oladi.
- **Har 30 daqiqada** (08:30–18:00) har bir guruhga o'z jamoasining hisobotini yuboradi, 18:00 da kun yakunini yuboradi.
- Baholash (1 soat uchun): 🟢 30 daq + 5 dozvon · 🟡 20 daq + 3 dozvon · 🔴 bundan kam.
- Bitta mijoz bilan jami 15 daqiqadan ko'p gaplashilgan bo'lsa ⚠️ bilan ko'rsatadi: raqam, vaqtlar, CRM havolasi.
- Isbot: 👤 Sotuvchi → 📞 qo'ng'iroqlar ro'yxati (har qo'ng'iroq vaqti, raqami, davomiyligi, CRM havolasi).

---

## 1-qadam. Telegram bot (2 daqiqa)
1. @BotFather → `/revoke` → botingizni tanlang → **yangi token** oling (eskisi chatda ochiq yozilgan edi).
2. @userinfobot ga yozing → o'z **ID** raqamingizni yozib oling.

## 2-qadam. Bitrix24 vebhuk huquqlari (2 daqiqa)
Bitrix24 → Разработчикам → Интеграции → vebhukingiz (rest/10/...) → Изменить.
Shu belgilar qo'yilgan bo'lsin:
- **Телефония** (telephony)
- **Пользователи** (user)
- **Подразделения / Структура компании** (department) — jamoa nomlari chiqishi uchun

Saqlang.

## 3-qadam. Serverga o'rnatish (5 daqiqa)
Kerak: DigitalOcean'da Ubuntu 22.04 yoki 24.04 server va uning IP manzili.

Kompyuteringizda `sinolife-call-bot.zip` yuklab olingan papkada terminal (Windows'da PowerShell) oching:

```
scp sinolife-call-bot.zip root@SERVER_IP:/root/
ssh root@SERVER_IP
```

Serverga kirgach:

```
apt install -y unzip && unzip -o sinolife-call-bot.zip && bash sinolife-call-bot/install.sh
```

Skript 3 ta narsani so'raydi: **yangi token**, **Bitrix vebhuk**, **sizning ID**.

**GitHub orqali (Claude in Chrome uchun):**
```
export DEBIAN_FRONTEND=noninteractive; apt-get install -y git && rm -rf /root/sinolife-call-bot && git clone https://github.com/forwork7002/sinolife-call-bot.git /root/sinolife-call-bot && BOT_TOKEN='TOKEN' BITRIX_WEBHOOK='VEBHUK' ADMIN_IDS='ID' bash /root/sinolife-call-bot/install.sh
```
Yangilash: `cd /root/sinolife-call-bot && git pull && bash install.sh`
Oxirida tekshiruv natijasi chiqadi: ✅ belgilar va bugungi qo'ng'iroqlar soni. Shu — isbot, bot Bitrix'dan ma'lumot olayapti.

## 4-qadam. Guruhlarni ulash
1. Botga shaxsiy chatda `/start` yozing — sizga butun kompaniya ochiq.
2. Har bir ROP guruhiga botni qo'shing va **admin qiling** (tugmalar ishlashi uchun).
3. Guruhda `/ulash` yozing → shu guruh qaysi jamoani ko'rishini tanlang (masalan ROP jamoasi bo'limi).
4. Rahbarlar guruhi uchun «🏢 Hammasi» ni tanlang.

Har guruh **faqat o'z jamoasini** ko'radi. `/guruhlar` — qaysi guruh qaysi jamoaga ulanganini ko'rsatadi, `/uzish` — guruhni uzadi.

---

## Keyinchalik kerak bo'ladigan buyruqlar (serverda)
| Nima | Buyruq |
|---|---|
| Sozlamani o'zgartirish | `nano /opt/sinolife-call-bot/.env` keyin `systemctl restart sinolife-call-bot` |
| Bot holati | `systemctl status sinolife-call-bot` |
| Xatolarni ko'rish | `journalctl -u sinolife-call-bot -n 50` |
| Yangi versiya o'rnatish | yangi zip'ni yuklab, 3-qadamdagi buyruqni qayta bering (sozlamalar saqlanadi) |

Tushlik bo'lsa `.env` da `LUNCH_START=13:00`, `LUNCH_END=14:00` yozing. O'sha vaqt ☕ bo'lib baholanmaydi.
ROPlarning o'zi hisobotda chiqmasin desangiz, ularning Bitrix ID sini `EXCLUDE_USER_IDS` ga yozing.
