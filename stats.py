"""Hisoblash, baholash va hisobot matnlari."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape

import config as C

WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]
LIVE, LUNCH = "⏳", "☕"
RANK = {C.LEVELS[0][0]: 0, C.LEVELS[1][0]: 1, C.LOW[0]: 2, LIVE: 3}
CRM_PATH = {"LEAD": "lead", "CONTACT": "contact", "COMPANY": "company", "DEAL": "deal"}


# ================= tuzilmalar =================

@dataclass
class Slot:
    start: datetime
    end: datetime
    lunch: bool = False

    @property
    def factor(self) -> float:   # norma koeffitsienti (1.0 = 1 soat)
        return 0.0 if self.lunch else (self.end - self.start).total_seconds() / 3600

    def state(self, now: datetime) -> str:
        if now >= self.end:
            return "done"
        return "live" if now >= self.start else "future"


@dataclass
class Call:
    start: datetime
    duration: int
    connected: bool
    incoming: bool
    phone: str
    crm_type: str
    crm_id: str
    record: str

    @property
    def dozvon(self) -> bool:
        return self.connected and self.duration >= C.DOZVON_MIN_SEC


@dataclass
class Bucket:
    talk_sec: int = 0
    dozvon: int = 0
    attempts: int = 0

    def add(self, c: Call):
        self.attempts += 1
        if c.connected:
            self.talk_sec += c.duration
        if c.dozvon:
            self.dozvon += 1


def total(buckets) -> Bucket:
    t = Bucket()
    for b in buckets:
        t.talk_sec += b.talk_sec
        t.dozvon += b.dozvon
        t.attempts += b.attempts
    return t


@dataclass
class SellerDay:
    uid: int
    name: str
    deps: frozenset
    buckets: list
    calls: list = field(default_factory=list)


@dataclass
class DayData:
    day: date
    slots: list
    sellers: dict = field(default_factory=dict)   # uid -> SellerDay


def day_slots(d: date) -> list[Slot]:
    cur = datetime.combine(d, C.WORK_START, tzinfo=C.TZ)
    end = datetime.combine(d, C.WORK_END, tzinfo=C.TZ)
    ls = datetime.combine(d, C.LUNCH_START, tzinfo=C.TZ) if C.LUNCH_START and C.LUNCH_END else None
    le = datetime.combine(d, C.LUNCH_END, tzinfo=C.TZ) if ls else None
    out = []
    while cur < end:
        nxt = min(cur + timedelta(minutes=C.SLOT_MIN), end)
        out.append(Slot(cur, nxt, bool(ls and cur < le and nxt > ls)))
        cur = nxt
    return out


def day_hours(slots) -> list[list[int]]:
    """30 daqiqalik bo'laklarni soatlarga juftlaydi: [[0,1],[2,3],...]"""
    return [list(range(i, min(i + 2, len(slots)))) for i in range(0, len(slots), 2)]


# ================= baholash =================

def need(minutes: float, dozvon: int, factor: float):
    return minutes * factor, math.ceil(dozvon * factor - 1e-9)


def grade(b: Bucket, factor: float) -> str | None:
    if factor <= 0:
        return None
    for emoji, _, m, dz in C.LEVELS:
        nm, nd = need(m, dz, factor)
        if b.talk_sec >= nm * 60 - 1e-6 and b.dozvon >= nd:
            return emoji
    return C.LOW[0]


# ================= Bitrix'dan yig'ish =================

def _active(u: dict) -> bool:
    return u.get("ACTIVE") not in (False, "N", "0", 0, None)


def _name(u: dict | None, uid: int) -> str:
    if not u:
        return f"ID {uid}"
    n = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
    return n or u.get("EMAIL") or f"ID {uid}"


def _udeps(u: dict | None) -> frozenset:
    try:
        return frozenset(int(x) for x in ((u or {}).get("UF_DEPARTMENT") or []))
    except (TypeError, ValueError):
        return frozenset()


def expand_deps(dep_id: int, deps: dict[int, dict]) -> set[int]:
    """Bo'lim + uning barcha ichki bo'limlari."""
    out, stack = set(), [dep_id]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack += [i for i, d in deps.items() if str(d.get("PARENT") or "") == str(cur)]
    return out


async def build_day(bx, d: date, member_deps: set[int]) -> DayData:
    slots = day_slots(d)
    data = DayData(d, slots)
    if not slots:
        return data
    calls = await bx.calls_between(slots[0].start, slots[-1].end)
    users = await bx.users()

    def get(uid):
        if uid not in data.sellers:
            u = users.get(uid)
            data.sellers[uid] = SellerDay(uid, _name(u, uid), _udeps(u), [Bucket() for _ in slots])
        return data.sellers[uid]

    # Ulangan jamoalarning a'zolari — bugun qo'ng'iroq qilmagan bo'lsa ham chiqadi
    for uid, u in users.items():
        if uid not in C.EXCLUDE_USER_IDS and _active(u) and _udeps(u) & member_deps:
            get(uid)

    first = slots[0].start
    for c in calls:
        try:
            uid = int(c.get("PORTAL_USER_ID") or 0)
            t = datetime.fromisoformat(c["CALL_START_DATE"])
        except (TypeError, ValueError, KeyError):
            continue
        if not uid or uid in C.EXCLUDE_USER_IDS:
            continue
        t = t.replace(tzinfo=C.TZ) if t.tzinfo is None else t.astimezone(C.TZ)
        idx = int((t - first).total_seconds() // (C.SLOT_MIN * 60))
        if not 0 <= idx < len(slots):
            continue
        call = Call(
            start=t,
            duration=int(c.get("CALL_DURATION") or 0),
            connected=str(c.get("CALL_FAILED_CODE")) == "200",
            incoming=str(c.get("CALL_TYPE")) in ("2", "3"),
            phone=str(c.get("PHONE_NUMBER") or ""),
            crm_type=str(c.get("CRM_ENTITY_TYPE") or ""),
            crm_id=str(c.get("CRM_ENTITY_ID") or ""),
            record=str(c.get("CALL_RECORD_URL") or ""),
        )
        s = get(uid)
        s.buckets[idx].add(call)
        s.calls.append(call)
    return data


def team(day: DayData, deps: set[int] | None) -> list[SellerDay]:
    if deps is None:
        return list(day.sellers.values())
    return [s for s in day.sellers.values() if s.deps & deps]


# ================= yordamchilar =================

def fmt_dur(sec: int) -> str:
    h, m = divmod(sec // 60, 60)
    return f"{h} soat {m} daq" if h else f"{m} daq"


def fmt_mmss(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def _num(x: float) -> str:
    return f"{x:g}"


def norm_phone(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 9:
        d = "998" + d
    return d


def fmt_phone(raw: str) -> str:
    d = norm_phone(raw)
    if len(d) == 12 and d.startswith("998"):
        return f"+998 {d[3:5]} {d[5:8]} {d[8:10]} {d[10:]}"
    return raw or "raqam yo'q"


def crm_url(c: Call) -> str | None:
    p = CRM_PATH.get(c.crm_type.upper())
    return f"{C.PORTAL_URL}/crm/{p}/details/{c.crm_id}/" if p and c.crm_id else None


def _links(c: Call) -> str:
    parts = []
    if (u := crm_url(c)):
        parts.append(f'<a href="{escape(u)}">CRM</a>')
    if c.record.startswith("http"):
        parts.append(f'<a href="{escape(c.record)}">🎧</a>')
    return " · ".join(parts)


NORM_LINE = ("<i>1 soat normasi: 🟢 30 daq + 5 dozvon · 🟡 20 daq + 3 dozvon · "
             "🔴 bundan kam</i>")


def hour_marks(day: DayData, s: SellerDay, now: datetime) -> str:
    out = ""
    for idx in day_hours(day.slots):
        states = [day.slots[i].state(now) for i in idx]
        if all(st == "future" for st in states):
            break
        if any(st != "done" for st in states):
            out += LIVE
            break
        f = sum(day.slots[i].factor for i in idx)
        out += grade(total(s.buckets[i] for i in idx), f) or LUNCH
    return out


# ================= uzoq suhbatlar (1 mijoz) =================

def long_clients(s: SellerDay) -> list[tuple[str, list[Call], int]]:
    by = defaultdict(list)
    for c in s.calls:
        if c.connected and norm_phone(c.phone):
            by[norm_phone(c.phone)].append(c)
    out = []
    for ph, cs in by.items():
        t = sum(c.duration for c in cs)
        if t >= C.LONG_CLIENT_MIN * 60:
            out.append((ph, cs, t))
    return sorted(out, key=lambda x: -x[2])


def flags_block(items: list[tuple[SellerDay, str, list[Call], int]]) -> list[str]:
    if not items:
        return []
    lines = ["", f"⚠️ <b>Bitta mijoz bilan uzoq suhbat</b> ({C.LONG_CLIENT_MIN} daq+) — sababini so'rang:"]
    for s, ph, cs, t in items:
        when = ", ".join(f"{c.start:%H:%M} ({c.duration // 60} daq)" for c in cs[:6])
        link = next((_links(c) for c in cs if _links(c)), "")
        lines.append(f"• <b>{escape(s.name)}</b> → {fmt_phone(ph)}: {len(cs)} ta qo'ng'iroq, "
                     f"jami <b>{fmt_dur(t)}</b>")
        lines.append(f"   {when}" + (f" · {link}" if link else ""))
    return lines


# ================= hisobotlar =================

def short_name(name: str, width: int = 11) -> str:
    """"Dilnoza Karimova" -> "Dilnoza K." (jadvalga sig'ishi uchun)."""
    p = name.split()
    s = p[0] + (f" {p[1][0]}." if len(p) > 1 and p[1] else "") if p else name
    return s if len(s) <= width else s[:width - 1] + "…"


def hm(sec: int) -> str:
    """Soniya -> "1:05" (soat:daqiqa)."""
    m = int(sec) // 60
    return f"{m // 60}:{m % 60:02d}"


def longest_gap(day: DayData, s: SellerDay, upto: datetime):
    """Ish vaqtida eng uzun 'qo'ng'iroqsiz' oraliq (tushlik hisobga olinmaydi): (boshi, oxiri, soniya)."""
    start = day.slots[0].start
    upto = min(upto, day.slots[-1].end)
    lunch = [(sl.start, sl.end) for sl in day.slots if sl.lunch]
    busy = sorted((c.start, c.start + timedelta(seconds=c.duration)) for c in s.calls if c.start < upto)
    best, cur = (start, start, 0), start
    for b0, b1 in busy + [(upto, upto)]:
        if b0 > cur:
            gap = (b0 - cur).total_seconds()
            gap -= sum(max(0.0, (min(b0, le) - max(cur, ls)).total_seconds()) for ls, le in lunch)
            if gap > best[2]:
                best = (cur, b0, int(gap))
        cur = max(cur, b1)
    return best


def reasons(day: DayData, s: SellerDay, done: list[int], fdone: float, upto: datetime) -> list[str]:
    """Nega past baho — aniq, raqamli sabablar (eng muhimi birinchi)."""
    tot = total(s.buckets[i] for i in done)
    if tot.attempts == 0:
        return ["bugun birorta ham qo'ng'iroq yo'q"]
    nm, nd = need(C.LEVELS[-1][2], C.LEVELS[-1][3], fdone)
    out = []
    if tot.talk_sec < nm * 60:
        out.append(f"gaplashgani {hm(tot.talk_sec)}, kerak edi {hm(int(nm * 60))} "
                   f"(<b>{hm(int(nm * 60) - tot.talk_sec)} kam</b>)")
    if tot.dozvon < nd:
        out.append(f"dozvon {tot.dozvon} ta, kerak edi {nd} ta")
    g0, g1, gs = longest_gap(day, s, upto)
    if gs >= 45 * 60:
        out.append(f"{g0:%H:%M}–{g1:%H:%M} orasida {fmt_dur(gs)} qo'ng'iroq qilmagan")
    if tot.attempts >= 10 and tot.dozvon / tot.attempts < 0.25:
        out.append(f"{tot.attempts} ta terdi, faqat {tot.dozvon} tasi ulandi")
    return out[:3]


def status_report(day: DayData, sellers: list[SellerDay], now: datetime, title: str,
                  final: bool = False, recent_live: bool = False,
                  flags: list | None = None) -> str:
    lvl_min = C.LEVELS[-1]
    norm = f"Norma: 1 soatda <b>{lvl_min[2]} daq</b> gaplashish + <b>{lvl_min[3]} dozvon</b>"
    if final:
        head = f"🏁 <b>{escape(title)}</b> · {day.day:%d.%m} — <b>KUN YAKUNI</b>"
    else:
        head = f"📊 <b>{escape(title)}</b> · {day.day:%d.%m} · <b>{now:%H:%M}</b>"
    lines = [head, norm]
    if not day.slots:
        return "\n".join(lines + ["Ish vaqti sozlanmagan."])

    done = [i for i, sl in enumerate(day.slots) if sl.state(now) == "done"]
    live = next((i for i, sl in enumerate(day.slots) if sl.state(now) == "live"), None)
    if not done and live is None:
        return "\n".join(lines + [f"Ish kuni hali boshlanmadi ({day.slots[0].start:%H:%M})."])
    if not sellers:
        return "\n".join(lines + ["", "Bu jamoada bugun qo'ng'iroqlar yo'q."])

    fdone = sum(day.slots[i].factor for i in done)
    upto = day.slots[done[-1]].end if done else now

    # "Soat" ustuni: oxirgi 60 daqiqa (2 ta 30 daqiqalik bo'lak)
    pool = done + ([live] if (recent_live and live is not None) else [])
    hidx = pool[-2:] if pool else None
    show_hour = bool(hidx) and not final
    if show_hour:
        h_end = now if (recent_live and live in hidx) else day.slots[hidx[-1]].end
        hl = f"{day.slots[hidx[0]].start:%H:%M}–{h_end:%H:%M}"

    rows = []
    for s in sellers:
        g = grade(total(s.buckets[i] for i in done), fdone) if fdone > 0 else LIVE
        tot = total(s.buckets[i] for i in pool)   # ko'rsatish: hozirgacha hammasi
        rows.append((g or LIVE, s, tot))
    rows.sort(key=lambda x: (RANK.get(x[0], 9), -x[2].talk_sec))

    lines.append("")
    if show_hour:
        lines.append(f"<i>Jami — bugun · Soat — {hl} · Q — qo'ng'iroq soni</i>")
        tbl = ["   № " + "Ism".ljust(11) + " " + "Jami".rjust(5) + "  Q │ Soat  Q"]
    else:
        lines.append("<i>Gap — gaplashgan vaqti · Q — qo'ng'iroq · Dz — dozvon</i>")
        tbl = ["   № " + "Ism".ljust(11) + "  " + "Gap".rjust(5) + "   Q  Dz"]
    cnt = Counter()
    for n, (g, s, tot) in enumerate(rows, 1):
        cnt[g] += 1
        nm = escape(short_name(s.name)).ljust(11)
        if show_hour:
            hb = total(s.buckets[i] for i in hidx)
            tbl.append(f"{g}{n:>2} {nm} {hm(tot.talk_sec):>5} {tot.attempts:>2} │ "
                       f"{hm(hb.talk_sec):>4} {hb.attempts:>2}")
        else:
            tbl.append(f"{g}{n:>2} {nm}  {hm(tot.talk_sec):>5} {tot.attempts:>3} {tot.dozvon:>3}")
    lines.append("<pre>" + "\n".join(tbl) + "</pre>")

    order = [C.LEVELS[0][0], C.LEVELS[1][0], C.LOW[0], LIVE]
    lines.append("  ".join(f"{e} {cnt[e]}" for e in order if cnt[e]))

    # Eng yomonlar — aniq sabab bilan
    bad = [(g, s, tot) for g, s, tot in rows if g == C.LOW[0]]
    bad.sort(key=lambda x: x[2].talk_sec)
    limit = 5 if final else 3
    if bad and fdone > 0:
        lines += ["", f"🔻 <b>Eng past natija</b> ({len(bad)} kishidan {min(limit, len(bad))} tasi):"]
        for s_ in bad[:limit]:
            _, s, _ = s_
            rs = reasons(day, s, done, fdone, upto)
            lines.append(f"<b>{escape(s.name)}</b>")
            lines += [f"   • {r}" for r in rs]
    elif fdone > 0:
        lines += ["", "✅ Normadan past xodim yo'q."]

    if flags is None:   # yakuniy hisobotda — hammasi
        flags = [(s, ph, cs, t) for s in sellers for ph, cs, t in long_clients(s)]
    if flags:
        lines += ["", f"⚠️ <b>1 mijoz bilan {C.LONG_CLIENT_MIN}+ daq</b> (sababini so'rang):"]
        for s, ph, cs, t in sorted(flags, key=lambda x: -x[3])[:5]:
            link = next((_links(c) for c in cs if _links(c)), "")
            lines.append(f"• {escape(short_name(s.name, 16))} → {fmt_phone(ph)}: {fmt_dur(t)}"
                         + (f" · {link}" if link else ""))
        if len(flags) > 5:
            lines.append(f"  … yana {len(flags) - 5} ta")
    lines += ["", "<i>Batafsil (soatma-soat, har qo'ng'iroq): 👤 Sotuvchi tugmasi</i>"]
    return "\n".join(lines)


def seller_report(day: DayData, s: SellerDay, now: datetime) -> str:
    tot = total(s.buckets)
    lines = [f"👤 <b>{escape(s.name)}</b> — {day.day:%d.%m.%Y}",
             f"Jami: <b>{fmt_dur(tot.talk_sec)}</b> · {tot.dozvon} dozvon · {tot.attempts} qo'ng'iroq"]
    if tot.attempts:
        extra = [f"dozvon ulushi {round(100 * tot.dozvon / tot.attempts)}%"]
        if tot.dozvon:
            avg = tot.talk_sec // tot.dozvon
            extra.append(f"o'rtacha suhbat {avg // 60} daq {avg % 60} son")
        lines.append(" · ".join(extra))
    longest = max((c for c in s.calls if c.connected), key=lambda c: c.duration, default=None)
    if longest:
        lines.append(f"Eng uzun suhbat: {longest.duration // 60} daq {longest.duration % 60} son ({longest.start:%H:%M}, "
                     f"{fmt_phone(longest.phone)})")
    lines += ["", NORM_LINE, "<b>Soatma-soat:</b>"]
    for idx in day_hours(day.slots):
        states = [day.slots[i].state(now) for i in idx]
        if all(st == "future" for st in states):
            break
        b = total(s.buckets[i] for i in idx)
        f = sum(day.slots[i].factor for i in idx)
        e = LIVE if any(st != "done" for st in states) else (grade(b, f) or LUNCH)
        label = f"{day.slots[idx[0]].start:%H:%M}–{day.slots[idx[-1]].end:%H:%M}"
        lines.append(f"<code>{label}</code> {e} {fmt_dur(b.talk_sec)} · {b.dozvon} dz · {b.attempts} q.")
    lines += flags_block([(s, ph, cs, t) for ph, cs, t in long_clients(s)])
    return "\n".join(lines)


def calls_report(day: DayData, s: SellerDay) -> str:
    conn = [c for c in s.calls if c.connected]
    missed = len(s.calls) - len(conn)
    lines = [f"📞 <b>{escape(s.name)}</b> — {day.day:%d.%m.%Y}, ulangan qo'ng'iroqlar ({len(conn)} ta)",
             "<i>↗️ chiquvchi · ↙️ kiruvchi · davomiyligi daq:son · qalin = 5 daqiqadan uzun</i>", ""]
    for c in sorted(conn, key=lambda c: c.start):
        dur = fmt_mmss(c.duration)
        dur = f"<b>{dur}</b>" if c.duration >= 300 else dur
        ln = f"{c.start:%H:%M} {'↙️' if c.incoming else '↗️'} {fmt_phone(c.phone)} — {dur}"
        if (lk := _links(c)):
            ln += f" · {lk}"
        lines.append(ln)
    if not conn:
        lines.append("Ulangan qo'ng'iroq yo'q.")
    lines += ["", f"Ulanmagan (javob berilmagan) qo'ng'iroqlar: {missed} ta"]
    return "\n".join(lines)
