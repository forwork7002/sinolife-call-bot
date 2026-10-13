"""Bitrix24 REST (vebhuk)."""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime

import httpx

log = logging.getLogger("bitrix")


class BitrixError(Exception):
    pass


class Bitrix:
    def __init__(self, webhook: str):
        self.base = webhook.rstrip("/") + "/"
        self._client = httpx.AsyncClient(timeout=40)
        self._users: dict[int, dict] = {}
        self._users_at = 0.0
        self._deps: dict[int, dict] = {}
        self._deps_at = 0.0

    async def call(self, method: str, params: dict | None = None) -> dict:
        for attempt in range(5):
            try:
                r = await self._client.post(f"{self.base}{method}.json", json=params or {})
            except httpx.HTTPError as e:
                if attempt == 4:
                    raise BitrixError(f"{method}: tarmoq xatosi ({e})")
                await asyncio.sleep(2)
                continue
            try:
                data = r.json()
            except ValueError:
                raise BitrixError(f"{method}: HTTP {r.status_code}")
            err = data.get("error")
            if err == "QUERY_LIMIT_EXCEEDED":
                await asyncio.sleep(1 + attempt)
                continue
            if err:
                raise BitrixError(f"{method}: {err} — {data.get('error_description', '')}")
            return data
        raise BitrixError(f"{method}: QUERY_LIMIT_EXCEEDED")

    async def list_all(self, method: str, params: dict) -> list[dict]:
        items, start = [], 0
        while True:
            data = await self.call(method, {**params, "start": start})
            items.extend(data.get("result") or [])
            if "next" not in data:
                return items
            start = data["next"]
            await asyncio.sleep(0.35)

    async def scopes(self) -> list[str]:
        return (await self.call("scope")).get("result") or []

    async def calls_between(self, start: datetime, end: datetime) -> list[dict]:
        return await self.list_all("voximplant.statistic.get", {
            "FILTER": {">=CALL_START_DATE": start.isoformat(), "<CALL_START_DATE": end.isoformat()},
            "SORT": "ID", "ORDER": "ASC",
        })

    async def users(self) -> dict[int, dict]:
        if self._users and _time.time() - self._users_at < 3600:
            return self._users
        rows = await self.list_all("user.get", {})
        self._users = {int(u["ID"]): u for u in rows}
        self._users_at = _time.time()
        return self._users

    async def departments(self) -> dict[int, dict]:
        """Bo'limlar. 'department' huquqi bo'lmasa — bo'sh qaytadi."""
        if self._deps and _time.time() - self._deps_at < 3600:
            return self._deps
        try:
            rows = await self.list_all("department.get", {})
            self._deps = {int(d["ID"]): d for d in rows}
        except BitrixError as e:
            log.warning("department.get ishlamadi: %s", e)
            self._deps = {}
        self._deps_at = _time.time()
        return self._deps
