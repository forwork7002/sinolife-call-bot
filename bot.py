"""Sinolife — sotuvchilar qo'ng'iroq nazorati boti (Bitrix24 → Telegram)."""
from __future__ import annotations

import asyncio
import functools
import logging
import sys
import time as _time
from datetime import date, datetime, timedelta

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      LinkPreviewOptions, ReplyKeyboardMarkup, ReplyKeyboardRemove,
                      ReplyParameters, Update)
from telegram.constants import ChatType, ParseMode
from telegram.error import ChatMigrated, Forbidden
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config as C
import stats as S
from bitrix import Bitrix, BitrixError
from store import Store

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
for n in ("httpx", "apscheduler"):
    logging.getLogger(n).setLevel(logging.WARNING)
log = logging.getLogger("bot")

bx = Bitrix(C.BITRIX_WEBHOOK)
store = Store(C.STATE_FILE)
S.EXCLUDED.update(store.excluded_ids())
_cache: dict[date, tuple[float, S.DayData]] = {}
_lock = asyncio.Lock()
_flag_sent: dict[tuple[int, date], set] = {}

KB = ReplyKeyboardMarkup([["📊 Holat", "👤 Sotuvchi"], ["📅 Kecha"]], resize_keyboard=True)


# Guruhda tugmalar faqat so'ragan adminga ko'rinadi (selective) — oddiy a'zolarga ochilmaydi.
KB_ADMIN = ReplyKeyboardMarkup([["📊 Holat", "👤 Sotuvchi"], ["📅 Kecha"]], resize_keyboard=True,
                               selective=True)


def kb_for(chat) -> ReplyKeyboardMarkup:
    """Shaxsiy chatda — oddiy tugmalar. Guruhda — faqat buyruq bergan admin ko'radigan tugmalar."""
    return KB if chat.type == ChatType.PRIVATE else KB_ADMIN


def reply_to(update: Update) -> int | None:
    """Guruhda tugmalar faqat shu odamga ko'rinishi uchun javob uning xabariga reply bo'ladi."""
    m = update.effective_message
    return m.message_id if (m and update.effective_chat.type != ChatType.PRIVATE) else None


# ================= umumiy =================

async def get_day(d: date, force: bool = False) -> S.DayData:
    ttl = 60 if d >= datetime.now(C.TZ).date() else 3600
    async with _lock:
        hit = _cache.get(d)
        if hit and not force and _time.time() - hit[0] < ttl:
            return hit[1]
        data = await S.build_day(bx, d, store.member_deps())
        _cache[d] = (_time.time(), data)
        return data


async def send_long(bot, chat_id: int, text: str, reply_markup=None, reply_to: int | None = None):
    # Telegram 4096 belgidan uzun xabarni qabul qilmaydi — bo'laklarga bo'lamiz.
    # <pre> jadval ikkiga bo'linsa, teglar buzilmasligi uchun yopib/qayta ochamiz.
    chunks, cur, in_pre = [], "", False
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur + ("</pre>" if in_pre else ""))
            cur = "<pre>" if in_pre else ""
        cur += line + "\n"
        if "<pre>" in line:
            in_pre = True
        if "</pre>" in line:
            in_pre = False
    if cur.strip():
        chunks.append(cur)
    for k, ch in enumerate(chunks):
        last = k == len(chunks) - 1
        rp = ReplyParameters(reply_to, allow_sending_without_reply=True) if (last and reply_to) else None
        await bot.send_message(chat_id, ch, parse_mode=ParseMode.HTML,
                               link_preview_options=LinkPreviewOptions(is_disabled=True),
                               reply_markup=reply_markup if last else None, reply_parameters=rp)


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in C.ADMIN_IDS)


def scope_of(update: Update):
    """(sarlavha, bo'limlar) yoki None. bo'limlar None = butun kompaniya."""
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return ("Butun kompaniya", None) if is_admin(update) else None
    g = store.get(chat.id)
    if g:
        return g["title"], (set(g["deps"]) if g.get("deps") else None)
    return None


_chat_admins: dict[tuple[int, int], tuple[float, bool]] = {}


async def can_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Guruhda buyruq/tugmalardan faqat adminlar foydalanadi: bot admini (ADMIN_IDS)
    yoki shu Telegram guruhining administratorlari. Shaxsiy chatda — scope_of tekshiradi."""
    chat, user = update.effective_chat, update.effective_user
    if chat.type == ChatType.PRIVATE or is_admin(update):
        return True
    if not user:
        return False
    key = (chat.id, user.id)
    hit = _chat_admins.get(key)
    if hit and _time.time() - hit[0] < 600:
        return hit[1]
    try:
        m = await context.bot.get_chat_member(chat.id, user.id)
        ok = m.status in ("administrator", "creator")
    except Exception:
        ok = False
    _chat_admins[key] = (_time.time(), ok)
    return ok


def scoped(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await can_use(update, context):
            if update.callback_query:
                await update.callback_query.answer("⛔ Faqat adminlar uchun", show_alert=True)
            return   # oddiy a'zolar yozsa — jim turadi, guruhni xabar bilan to'ldirmaydi
        sc = scope_of(update)
        if sc is None:
            if update.callback_query:
                await update.callback_query.answer("⛔ Ruxsat yo'q", show_alert=True)
            elif update.effective_chat.type == ChatType.PRIVATE:
                await update.effective_message.reply_text(
                    f"⛔ Ruxsat yo'q. Sizning ID: <code>{update.effective_user.id}</code>",
                    parse_mode=ParseMode.HTML)
            elif is_admin(update):
                await update.effective_message.reply_text("Bu guruh hali ulanmagan. /ulash buyrug'ini bering.")
            return
        try:
            return await fn(update, context, *sc)
        except BitrixError as e:
            log.exception("Bitrix xatosi")
            await update.effective_message.reply_text(f"❌ Bitrix24 xatosi: {e}")
    return wrapper


def admin_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            if update.callback_query:
                await update.callback_query.answer("Faqat admin", show_alert=True)
            return
        try:
            return await fn(update, context)
        except BitrixError as e:
            log.exception("Bitrix xatosi")
            await update.effective_message.reply_text(f"❌ Bitrix24 xatosi: {e}")
    return wrapper


# ================= buyruqlar =================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await can_use(update, context):
        return   # guruhdagi oddiy a'zo — tugmalar ochilmaydi
    sc = scope_of(update)
    if sc is None:
        if update.effective_chat.type == ChatType.PRIVATE:
            await update.effective_message.reply_text(
                f"⛔ Ruxsat yo'q. Sizning ID: <code>{update.effective_user.id}</code>",
                parse_mode=ParseMode.HTML)
        elif is_admin(update):
            await update.effective_message.reply_text("Bu guruhni ulash uchun /ulash bosing.")
        return
    await update.effective_message.reply_text(
        do_quote=update.effective_chat.type != ChatType.PRIVATE,
        text=
        f"Salom! Jamoa: {sc[0]}\n\n"
        f"📊 Holat — hozirgi holat (har {C.REPORT_EVERY_MIN} daqiqada o'zi ham yuboriladi)\n"
        "👤 Sotuvchi — bitta sotuvchi: soatma-soat va qo'ng'iroqlar ro'yxati\n"
        "📅 Kecha — kechagi yakuniy hisobot",
        reply_markup=kb_for(update.effective_chat))


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.effective_message.reply_text(
        f"Sizning ID: <code>{u.id if u else '-'}</code>\nChat ID: <code>{update.effective_chat.id}</code>",
        parse_mode=ParseMode.HTML)


@scoped
async def cmd_status(update, context, title, deps):
    msg = await update.effective_message.reply_text("⏳ Bitrix24 dan olinmoqda...")
    now = datetime.now(C.TZ)
    day = await get_day(now.date())
    await msg.delete()
    sel = S.team(day, deps)
    await send_long(context.bot, update.effective_chat.id,
                    S.status_report(day, sel, now, title, recent_live=True),
                    kb_for(update.effective_chat), reply_to(update))
    if (hr := S.hour_report(day, sel, now, title)):
        await send_long(context.bot, update.effective_chat.id, hr)


@scoped
async def cmd_yesterday(update, context, title, deps):
    msg = await update.effective_message.reply_text("⏳ Bitrix24 dan olinmoqda...")
    d = datetime.now(C.TZ).date() - timedelta(days=1)
    day = await get_day(d)
    end = datetime.combine(d, C.WORK_END, tzinfo=C.TZ)
    await msg.delete()
    await send_long(context.bot, update.effective_chat.id,
                    S.status_report(day, S.team(day, deps), end, title, final=True),
                    kb_for(update.effective_chat), reply_to(update))


@scoped
async def cmd_sellers(update, context, title, deps):
    msg = await update.effective_message.reply_text("⏳ Bitrix24 dan olinmoqda...")
    day = await get_day(datetime.now(C.TZ).date())
    await msg.delete()
    people = sorted(S.team(day, deps), key=lambda s: s.name)
    if not people:
        await update.effective_message.reply_text("Bugun bu jamoada sotuvchi topilmadi.")
        return
    ds = day.day.strftime("%Y%m%d")
    btns = [InlineKeyboardButton(s.name[:30], callback_data=f"s:{s.uid}:{ds}") for s in people]
    await update.effective_message.reply_text(
        "Sotuvchini tanlang:", reply_markup=InlineKeyboardMarkup([btns[i:i + 2] for i in range(0, len(btns), 2)]))


async def _seller_from_cb(q, deps):
    kind, uid, ds = q.data.split(":")
    day = await get_day(datetime.strptime(ds, "%Y%m%d").date())
    s = day.sellers.get(int(uid))
    if s and deps is not None and not (s.deps & deps):
        s = None
    return day, s, ds


@scoped
async def on_seller(update, context, title, deps):
    q = update.callback_query
    await q.answer()
    day, s, ds = await _seller_from_cb(q, deps)
    if not s:
        await q.message.reply_text("Ma'lumot topilmadi.")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📞 Qo'ng'iroqlar ro'yxati (isbot)",
                                                     callback_data=f"c:{s.uid}:{ds}")]])
    await send_long(context.bot, q.message.chat.id, S.seller_report(day, s, datetime.now(C.TZ)), kb)


@scoped
async def on_calls(update, context, title, deps):
    q = update.callback_query
    await q.answer()
    day, s, _ = await _seller_from_cb(q, deps)
    if not s:
        await q.message.reply_text("Ma'lumot topilmadi.")
        return
    await send_long(context.bot, q.message.chat.id, S.calls_report(day, s))


# ================= sotuvchi bo'lmaganlarni chiqarish (admin) =================

async def _exclude_kb() -> InlineKeyboardMarkup | None:
    today = datetime.now(C.TZ).date()
    people: dict[int, str] = {}
    for d in (today, today - timedelta(days=1)):
        for s in (await get_day(d)).sellers.values():
            people.setdefault(s.uid, s.name)
    for k, name in store.excluded.items():
        people.setdefault(int(k), name)
    if not people:
        return None
    ex = store.excluded_ids()
    order = sorted(people.items(), key=lambda x: (x[0] not in ex, x[1]))
    btns = [InlineKeyboardButton(f"{'🚫' if uid in ex else '✅'} {name}"[:40], callback_data=f"x:{uid}")
            for uid, name in order]
    return InlineKeyboardMarkup([btns[i:i + 2] for i in range(0, len(btns), 2)])


EXCLUDE_HELP = ("Kimlar hisobotda ko'rinsin?\n"
                "✅ — sotuvchi, hisobotda bor\n"
                "🚫 — sotuvchi emas, hisobotdan chiqarilgan\n\n"
                "Ismni bossangiz holati almashadi.")


@admin_only
async def cmd_exclude(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("⏳ Bitrix24 dan olinmoqda...")
    kb = await _exclude_kb()
    if kb is None:
        await msg.edit_text("Bugun va kecha qo'ng'iroq qilgan xodim topilmadi.")
        return
    await msg.edit_text(EXCLUDE_HELP, reply_markup=kb)


@admin_only
async def on_exclude(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = int(q.data.split(":", 1)[1])
    name = store.excluded.get(str(uid))
    if name is None:
        for d in _cache.values():
            if uid in d[1].sellers:
                name = d[1].sellers[uid].name
                break
    name = name or f"ID {uid}"
    removed = store.toggle_excluded(uid, name)
    S.EXCLUDED.clear()
    S.EXCLUDED.update(store.excluded_ids())
    await q.answer(f"{name}: hisobotdan chiqarildi" if removed else f"{name}: hisobotga qaytarildi")
    await q.edit_message_text(EXCLUDE_HELP, reply_markup=await _exclude_kb())


# ================= guruhlarni ulash (admin) =================

@admin_only
async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("Bu buyruq guruh ichida beriladi: botni guruhga qo'shing va /ulash yozing.")
        return
    users = await bx.users()
    deps = await bx.departments()
    count: dict[int, int] = {}
    sample: dict[int, list[str]] = {}
    for uid, u in users.items():
        if not S._active(u):
            continue
        for d in S._udeps(u):
            count[d] = count.get(d, 0) + 1
            sample.setdefault(d, []).append((u.get("NAME") or "").strip())
    btns = [[InlineKeyboardButton("🏢 Hammasi (butun kompaniya)", callback_data="g:all")]]
    for d in sorted(count, key=lambda d: (deps.get(d, {}).get("NAME") or "", d)):
        name = deps.get(d, {}).get("NAME") or f"Bo'lim {d}: " + ", ".join(sample[d][:3])
        btns.append([InlineKeyboardButton(f"{name} ({count[d]})"[:60], callback_data=f"g:{d}")])
    await update.effective_message.reply_text(
        "Bu guruh qaysi jamoaning hisobotini olsin?\n(qavs ichida — xodimlar soni)",
        reply_markup=InlineKeyboardMarkup(btns))


@admin_only
async def on_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split(":", 1)[1]
    chat_id = q.message.chat.id
    if val == "all":
        title, dep_list = "Butun kompaniya", None
    else:
        deps = await bx.departments()
        did = int(val)
        title = deps.get(did, {}).get("NAME") or f"Bo'lim {did}"
        dep_list = sorted(S.expand_deps(did, deps)) if deps else [did]
    store.bind(chat_id, title, dep_list)
    _cache.clear()
    await q.edit_message_text(
        f"✅ Ulandi. Bu guruh endi «{title}» hisobotini oladi.\n"
        f"Har {C.REPORT_EVERY_MIN} daqiqada, {C.WORK_START:%H:%M}–{C.WORK_END:%H:%M} oralig'ida.")
    now = datetime.now(C.TZ)
    day = await get_day(now.date(), force=True)
    sel = S.team(day, set(dep_list) if dep_list else None)
    # Eski versiyadan qolgan, hammaga ko'rinadigan tugmalarni guruhdan tozalaymiz
    await send_long(context.bot, chat_id, S.status_report(day, sel, now, title, recent_live=True),
                    ReplyKeyboardRemove())


@admin_only
async def cmd_unbind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store.unbind(update.effective_chat.id)
    _cache.clear()
    await update.effective_message.reply_text("Bu guruh uzildi, hisobotlar endi kelmaydi.")


@admin_only
async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not store.groups:
        await update.effective_message.reply_text("Hali birorta guruh ulanmagan.")
        return
    lines = ["Ulangan guruhlar:"]
    for cid, g in store.groups.items():
        try:
            name = (await context.bot.get_chat(int(cid))).title
        except Exception:
            name = cid
        lines.append(f"• {name} → {g['title']}")
    await update.effective_message.reply_text("\n".join(lines))


@admin_only
async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text("⏳ Tekshirilmoqda...")
    await msg.edit_text(await check_text())


async def check_text() -> str:
    lines = []
    try:
        sc = set(await bx.scopes())
    except BitrixError as e:
        return f"❌ Vebhuk ishlamadi: {e}"
    for need, label in (("telephony", "Telefoniya"), ("user", "Foydalanuvchilar"),
                        ("department", "Bo'limlar (ixtiyoriy, nomlar uchun)")):
        lines.append(f"{'✅' if need in sc else '❌'} {label} ({need})")
    users = await bx.users()
    deps = await bx.departments()
    now = datetime.now(C.TZ)
    start = datetime.combine(now.date(), C.WORK_START, tzinfo=C.TZ)
    calls = await bx.calls_between(start, start + timedelta(days=1))
    ok = sum(1 for c in calls if str(c.get("CALL_FAILED_CODE")) == "200")
    heads = S.dept_heads(deps)
    sd = S.sales_deps(deps)
    sellers = [u for uid, u in users.items()
               if S._active(u) and S._udeps(u) & sd and not (C.EXCLUDE_HEADS and uid in heads)]
    lines.append(f"Sotuv bo'limlari («{C.SALES_DEPT_KEYWORD}»): {len(sd)} ta · sotuvchilar: {len(sellers)} ta"
                 if sd else "❌ Sotuv bo'limlari topilmadi — hamma xodim olinadi (department huquqini tekshiring)")
    hn = ", ".join(S._name(users.get(h), h) for h in sorted(heads))
    lines += [f"Xodimlar: {len(users)} · bo'limlar: {len(deps)}",
              (f"Hisobotga kirmaydigan rahbarlar (ROP): {len(heads)} — {hn}" if C.EXCLUDE_HEADS and heads
               else "Rahbarlar (ROP) aniqlanmadi — /chiqarish orqali qo'lda olib tashlang"),
              f"Bugungi qo'ng'iroqlar: {len(calls)} (ulangan: {ok})",
              f"Ulangan guruhlar: {len(store.groups)}"]
    if calls:
        rec = any(c.get("CALL_RECORD_URL") for c in calls)
        crm = any(c.get("CRM_ENTITY_ID") for c in calls)
        lines.append(f"CRM havolalar: {'bor' if crm else 'yoq'} · yozuvlar havolasi: {'bor' if rec else 'yoq'}")
    return "\n".join(lines)


# ================= avto-hisobot (har REPORT_EVERY_MIN daqiqada) =================

async def job_report(context: ContextTypes.DEFAULT_TYPE):
    if not store.groups:
        return
    now = datetime.now(C.TZ)
    if now.isoweekday() not in C.WORK_DAYS:
        return
    try:
        day = await get_day(now.date(), force=True)
    except Exception:
        log.exception("Avto-hisobot: Bitrix'dan olib bo'lmadi")
        return
    done = [i for i, sl in enumerate(day.slots) if sl.state(now) == "done"]
    if not done:
        return
    final = done[-1] == len(day.slots) - 1
    for cid, g in list(store.groups.items()):
        cid = int(cid)
        deps = set(g["deps"]) if g.get("deps") else None
        sel = S.team(day, deps)
        flags = None
        if not final:   # oraliq hisobotda faqat YANGI ogohlantirishlar
            sent = _flag_sent.setdefault((cid, day.day), set())
            flags = []
            for s in sel:
                for ph, cs, t in S.long_clients(s):
                    if (s.uid, ph) not in sent:
                        sent.add((s.uid, ph))
                        flags.append((s, ph, cs, t))
        texts = [S.status_report(day, sel, now, g["title"], final=final, flags=flags)]
        if not final and (hr := S.hour_report(day, sel, now, g["title"])):
            texts.append(hr)
        try:
            for text in texts:
                await send_long(context.bot, cid, text)
        except ChatMigrated as e:
            store.move(cid, e.new_chat_id)
            for text in texts:
                await send_long(context.bot, e.new_chat_id, text)
        except Forbidden:
            log.warning("Bot guruhdan chiqarilgan: %s — uzildi", cid)
            store.unbind(cid)
        except Exception:
            log.exception("Yuborib bo'lmadi: %s", cid)


def schedule(app: Application):
    days = tuple(sorted(d % 7 for d in C.WORK_DAYS))   # PTB: 0=yakshanba
    slots = S.day_slots(datetime.now(C.TZ).date())
    for k, sl in enumerate(slots):
        since = int((sl.end - slots[0].start).total_seconds() // 60)
        # faqat har REPORT_EVERY_MIN daqiqada (masalan 10:00, 11:00, ...) va ish kuni oxirida
        if since % C.REPORT_EVERY_MIN and k != len(slots) - 1:
            continue
        t = (sl.end + timedelta(minutes=C.REPORT_DELAY_MIN)).timetz()
        app.job_queue.run_daily(job_report, time=t, days=days)
    log.info("Avto-hisobot: har %s daqiqada, %s–%s", C.REPORT_EVERY_MIN, C.WORK_START, C.WORK_END)


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("holat", "Hozirgi holat"),
        BotCommand("sotuvchi", "Bitta sotuvchi + qo'ng'iroqlar ro'yxati"),
        BotCommand("kecha", "Kechagi yakuniy hisobot"),
        BotCommand("ulash", "Guruhni jamoaga ulash (admin)"),
        BotCommand("uzish", "Guruhni uzish (admin)"),
        BotCommand("guruhlar", "Ulangan guruhlar (admin)"),
        BotCommand("chiqarish", "Sotuvchi bo'lmaganlarni hisobotdan chiqarish (admin)"),
        BotCommand("tekshir", "Bitrix24 ulanishini tekshirish (admin)"),
        BotCommand("id", "ID ni ko'rish"),
    ])


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Xatolik", exc_info=context.error)


def main():
    if not C.BOT_TOKEN or not C.BITRIX_WEBHOOK:
        sys.exit(".env faylida BOT_TOKEN va BITRIX_WEBHOOK bo'lishi kerak")
    app = Application.builder().token(C.BOT_TOKEN).post_init(post_init).build()
    add = app.add_handler
    add(CommandHandler("start", cmd_start))
    add(CommandHandler("id", cmd_id))
    add(CommandHandler("holat", cmd_status))
    add(CommandHandler("kecha", cmd_yesterday))
    add(CommandHandler("sotuvchi", cmd_sellers))
    add(CommandHandler("ulash", cmd_bind))
    add(CommandHandler("uzish", cmd_unbind))
    add(CommandHandler("guruhlar", cmd_groups))
    add(CommandHandler("tekshir", cmd_check))
    add(CommandHandler("chiqarish", cmd_exclude))
    add(MessageHandler(filters.Regex(r"^📊 Holat$"), cmd_status))
    add(MessageHandler(filters.Regex(r"^👤 Sotuvchi$"), cmd_sellers))
    add(MessageHandler(filters.Regex(r"^📅 Kecha$"), cmd_yesterday))
    add(CallbackQueryHandler(on_seller, pattern=r"^s:"))
    add(CallbackQueryHandler(on_calls, pattern=r"^c:"))
    add(CallbackQueryHandler(on_bind, pattern=r"^g:"))
    add(CallbackQueryHandler(on_exclude, pattern=r"^x:"))
    app.add_error_handler(on_error)
    schedule(app)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    if "--check" in sys.argv:
        print(asyncio.run(check_text()))
    else:
        main()
