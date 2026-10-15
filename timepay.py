"""TimePay (timepay24.uz) — Face ID davomat: kim soat nechada keldi.

Login/parol FAQAT .env dan o'qiladi (TIMEPAY_LOGIN, TIMEPAY_PASSWORD).
Token o'zi yangilanadi: access eskirsa -> refresh, refresh ham eskirsa -> qayta login.
Tokenlar logga yozilmaydi.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time as _time
from datetime import date, datetime

import httpx

log = logging.getLogger("timepay")
BASE = "https://api.app.time-pay.uz/api/v1"


class TimePayError(Exception):
    pass


def _jwt_exp(token: str) -> float:
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return float(json.loads(base64.urlsafe_b64decode(p)).get("exp") or 0)
    except Exception:
        return 0.0


class TimePay:
    def __init__(self, login: str, password: str, login_field: str = ""):
        self.login, self.password = login, password
        self.fields = [login_field] if login_field else ["username", "phone_number"]
        self._c = httpx.AsyncClient(timeout=30)
        self._access = self._refresh = ""
        self._emp: dict[int, dict] = {}
        self._emp_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.login and self.password)

    # ---------- autentifikatsiya ----------
    async def _do_login(self):
        last = ""
        for f in self.fields:
            r = await self._c.post(f"{BASE}/user/employee/password-login/",
                                   json={f: self.login, "password": self.password})
            if r.status_code == 200:
                d = r.json()
                self._access, self._refresh = d.get("access", ""), d.get("refresh", "")
                if self._access:
                    self.fields = [f]          # ishlagan maydonni eslab qolamiz
                    return
            last = f"HTTP {r.status_code}"
        raise TimePayError(f"login ishlamadi ({last}) — TIMEPAY_LOGIN/TIMEPAY_PASSWORD ni tekshiring")

    async def _do_refresh(self):
        r = await self._c.post(f"{BASE}/user/token/refresh/", json={"refresh": self._refresh})
        if r.status_code != 200 or not r.json().get("access"):
            raise TimePayError("refresh ishlamadi")
        d = r.json()
        self._access = d["access"]
        self._refresh = d.get("refresh") or self._refresh

    async def _headers(self) -> dict:
        if not self._access:
            await self._do_login()
        elif _time.time() > _jwt_exp(self._access) - 60:
            try:
                await self._do_refresh()
            except Exception:
                await self._do_login()
        return {"Authorization": f"Bearer {self._access}"}

    async def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(2):
            r = await self._c.request(method, f"{BASE}{path}", headers=await self._headers(), **kw)
            if r.status_code == 401 and attempt == 0:
                self._access = ""            # token bekor bo'lgan — qayta kiramiz
                continue
            if r.status_code >= 400:
                raise TimePayError(f"{path}: HTTP {r.status_code}")
            return r.json()
        raise TimePayError(f"{path}: 401")

    async def _all(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> list[dict]:
        out, offset = [], 0
        while True:
            q = {**(params or {}), "offset": offset, "limit": 100}
            d = await self._req(method, path, params=q, **({"json": body} if body is not None else {}))
            items = d.get("results") if isinstance(d, dict) else d
            out.extend(items or [])
            if not isinstance(d, dict) or not d.get("next") or not items:
                return out
            offset += len(items)

    # ---------- ma'lumot ----------
    async def daily_attendance(self, d: date) -> list[dict]:
        return await self._all("POST", "/user/employee-daily-stats/", body={"date": d.isoformat()})

    async def employees(self, d: date) -> dict[int, dict]:
        if self._emp and _time.time() - self._emp_at < 3600:
            return self._emp
        rows = await self._all("GET", "/user/employees/", params={"role": "EMPLOYEE", "date": d.isoformat()})
        self._emp = {int(e["id"]): e for e in rows if e.get("id") is not None}
        self._emp_at = _time.time()
        return self._emp


# ---------- yordamchilar: vaqt va ism/telefon bo'yicha moslash ----------

def parse_time(v, d: date, tz) -> datetime | None:
    """'08:52', '08:52:13', '2026-10-15T08:52:13+05:00' — hammasini datetime ga."""
    if not v:
        return None
    s = str(v).strip()
    try:
        if "T" in s or len(s) > 10:
            t = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return t.replace(tzinfo=tz) if t.tzinfo is None else t.astimezone(tz)
        m = re.match(r"^(\d{1,2}):(\d{2})", s)
        if m:
            return datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)), tzinfo=tz)
    except ValueError:
        pass
    return None


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
                ["a", "b", "v", "g", "d", "e", "yo", "j", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                 "s", "t", "u", "f", "x", "ts", "ch", "sh", "sh", "", "i", "", "e", "yu", "ya", "o", "q", "g", "h"]))


def name_key(name: str) -> frozenset:
    """'Alimova Nafisa 221' / 'Нафиса Алимова' -> {'alimova','nafisa'} (tartibsiz, raqamsiz)."""
    s = "".join(_CYR.get(ch, ch) for ch in (name or "").lower())
    s = re.sub(r"[`'ʻʼ‘’]", "", s)
    return frozenset(w for w in re.split(r"[^a-z]+", s) if len(w) >= 2)


def phone_key(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-9:] if len(d) >= 9 else ""


def match_bitrix(tp_emp: dict, att_item: dict, bx_users: dict[int, dict], active) -> int | None:
    """TimePay xodimini Bitrix24 foydalanuvchisiga moslaydi: avval telefon, keyin ism (kamida 2 so'z mos).
    Bir nechta nomzod chiqsa — moslamaydi (xato odamni ko'rsatmaslik uchun)."""
    ph = phone_key(tp_emp.get("phone_number"))
    if ph:
        hits = [uid for uid, u in bx_users.items() if active(u) and ph in {
            phone_key(u.get(k)) for k in ("PERSONAL_MOBILE", "PERSONAL_PHONE", "WORK_PHONE", "UF_PHONE_INNER")}]
        if len(hits) == 1:
            return hits[0]
    full = att_item.get("full_name") or f"{tp_emp.get('first_name') or ''} {tp_emp.get('last_name') or ''}"
    k = name_key(full)
    if len(k) < 2:
        return None
    hits = [uid for uid, u in bx_users.items()
            if active(u) and len(k & name_key(f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}")) >= 2]
    return hits[0] if len(hits) == 1 else None
