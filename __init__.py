"""N.E.K.O. plugin entry point for the FF14 Companion."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, neko_plugin, plugin_entry, timer_interval


class BridgeError(RuntimeError):
    """Raised when the local FF14 Bridge cannot be reached or parsed."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        return None


def _request_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        # This plugin only accepts loopback Bridge URLs. Bypass Windows/system
        # proxies explicitly so local requests cannot be sent to Clash or another
        # configured HTTP proxy.
        with build_opener(_NoRedirect, ProxyHandler({})).open(request, timeout=timeout) as response:
            if response.status != 200:
                raise BridgeError(f"Bridge returned HTTP {response.status}")
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("Bridge request failed") from exc
    if not isinstance(payload, dict):
        raise BridgeError("Bridge response must be a JSON object")
    return payload


@neko_plugin
class FF14CompanionPlugin(NekoPluginBase):
    """Queries the user-installed local FF14 Bridge and exposes context to N.E.K.O."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._event_cursor: int | None = None
        self._last_spoken_at: dict[str, datetime] = {}
        self._last_any_spoken_at: datetime | None = None
        self._pending_messages: list[tuple[int, str, str, int, datetime]] = []

    async def _settings(self) -> tuple[str, float]:
        config = await self.config.dump()
        companion = config.get("companion", {})
        base_url = str(companion.get("bridge_base_url", "http://127.0.0.1:17891")).rstrip("/")
        timeout = float(companion.get("request_timeout_seconds", 3))
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise BridgeError("Bridge URL must use HTTP on localhost")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BridgeError("Bridge URL must not contain credentials or query parameters")
        if not 0.2 <= timeout <= 10:
            raise BridgeError("Request timeout must be between 0.2 and 10 seconds")
        return base_url, timeout

    async def _get(self, path: str) -> dict[str, Any]:
        base_url, timeout = await self._settings()
        return await asyncio.to_thread(_request_json, f"{base_url}{path}", timeout)

    def _report(self, text: str, priority: int = 5) -> dict[str, Any]:
        """Ask N.E.K.O. to present a tool result in the active conversation."""
        result = self.push_message(
            source="ff14_companion",
            visibility=["chat"],
            ai_behavior="respond",
            parts=[{"type": "text", "text": text}],
            priority=max(0, min(priority, 10)),
        )
        if not result.get("submitted"):
            self.logger.warning("N.E.K.O. rejected an FF14 query report: {}", result.get("reason", "unknown"))
        return result

    @plugin_entry(
        id="bridge_status",
        name="检查 FF14 Bridge",
        description="Check whether the local FF14 Bridge and its ACT data source are connected.",
    )
    async def bridge_status(self, **_):
        try:
            status = await self._get("/health")
            overlay = status.get("overlayPlugin", "unknown")
            message = (
                "FF14 Bridge 已连接，ACT / OverlayPlugin 数据源状态："
                f"{overlay}。请用一句简短中文告诉玩家检查结果；若数据源未连接，提醒检查 ACT 的 OverlayPlugin WebSocket。"
            )
            submitted = self._report(message)
            return Ok({"connected": True, "bridge": status, "message_submitted": bool(submitted.get("submitted"))})
        except BridgeError as exc:
            self.logger.info("FF14 Bridge health check unavailable: {}", str(exc))
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="current_game_state",
        name="查询当前区域与战斗状态",
        description="Read the current zone, duty, combat state, and data source status from the local FF14 Bridge.",
    )
    async def current_game_state(self, **_):
        try:
            state = await self._get("/v1/state")
            zone = state.get("zoneName") or (f"区域编号 {state['zoneId']}" if state.get("zoneId") is not None else "未知区域")
            duty = state.get("dutyName")
            combat = "正在战斗" if state.get("inCombat") else "当前不在战斗中"
            message = f"FF14 当前状态：{zone}；{combat}。"
            if duty:
                message += f"当前副本：{duty}。"
            message += "请用猫娘语气简短告知玩家，不要猜测缺失信息。"
            submitted = self._report(message)
            return Ok({**state, "message_submitted": bool(submitted.get("submitted"))})
        except BridgeError as exc:
            self.logger.info("FF14 state query unavailable: {}", str(exc))
            return Err(SdkError(str(exc)))

    @plugin_entry(
        id="speak_companion_message",
        name="让猫娘主动说一句",
        description="Submit a short FF14 companion message to the active N.E.K.O. conversation. Actual voice playback depends on the host voice session.",
    )
    async def speak_companion_message(self, text: str, **_):
        clean_text = " ".join(text.split())[:500]
        if not clean_text:
            return Err(SdkError("Message text is required"))
        result = self.push_message(
            source="ff14_companion",
            visibility=["chat"],
            ai_behavior="respond",
            parts=[{"type": "text", "text": clean_text}],
            priority=5,
        )
        if not result.get("submitted"):
            return Err(SdkError(f"N.E.K.O. did not accept the message: {result.get('reason', 'unknown')}"))
        return Ok({"submitted": True, "playback_confirmed": False})

    @timer_interval(
        id="event_pump",
        seconds=2,
        name="FF14 event listener",
        auto_start=True,
    )
    async def event_pump(self, **_):
        """Poll the bounded Bridge event cursor and apply opt-in proactive rules."""
        try:
            config = await self.config.dump()
            settings = config.get("companion", {})
            batch = await self._get(f"/v1/events/recent?after={self._event_cursor or 0}")
        except (BridgeError, TypeError, ValueError) as exc:
            self.logger.debug("FF14 event poll unavailable: {}", str(exc))
            return Ok({"status": "bridge_unavailable"})

        next_cursor = batch.get("nextCursor")
        if not isinstance(next_cursor, int):
            return Err(SdkError("Bridge event batch has no valid nextCursor"))
        if self._event_cursor is None:
            self._event_cursor = next_cursor
            return Ok({"status": "listening", "cursor": next_cursor})

        events = batch.get("events", [])
        if not isinstance(events, list):
            return Err(SdkError("Bridge event batch has an invalid events list"))
        if batch.get("dropped"):
            self.logger.warning("FF14 event backlog exceeded the Bridge retention window")

        enabled = bool(settings.get("proactive_speech_enabled", False))
        max_age = _bounded_int(settings.get("max_event_age_seconds", 15), 1, 120, 15)
        trigger_threshold = _bounded_int(settings.get("min_trigger_priority", 8), 0, 10, 8)
        zone_enabled = bool(settings.get("zone_greeting_enabled", False))
        zone_cooldown = _bounded_int(settings.get("zone_greeting_cooldown_seconds", 180), 30, 3600, 180)
        now = datetime.now(timezone.utc)

        for event in events:
            if not isinstance(event, dict):
                continue
            sequence = event.get("sequence")
            if not isinstance(sequence, int) or sequence <= self._event_cursor:
                continue
            self._event_cursor = sequence
            timestamp = _event_time(event.get("timestamp"))
            if timestamp is None or abs((now - timestamp).total_seconds()) > max_age:
                continue
            if not enabled:
                continue

            if event.get("source") == "triggernometry":
                priority = _bounded_int(event.get("priority", 0), 0, 10, 0)
                payload = event.get("payload") or {}
                text = payload.get("text") if isinstance(payload, dict) else None
                if priority >= trigger_threshold and isinstance(text, str) and text.strip():
                    await self._speak_once(
                        category=f"trigger:{event.get('type', 'event')}:{text.strip()[:120]}",
                        text=f"FF14 的高优先级触发信息：{text.strip()[:350]}。请以猫娘语气简短、准确地播报，不要补充未提供的机制信息。",
                        cooldown_seconds=5,
                        priority=priority,
                    )
            elif event.get("type") == "ChangeZone" and zone_enabled:
                zone_id = event.get("zoneId")
                zone_label = f"区域编号 {zone_id}" if isinstance(zone_id, int) else "一个新区域"
                await self._speak_once(
                    category="zone_greeting",
                    text=f"玩家刚进入{zone_label}。请以猫娘身份简短打招呼，不要猜测地点名称或剧情内容。",
                    cooldown_seconds=zone_cooldown,
                    priority=3,
                )

        await self._flush_speech_queue(
            max_age,
            _bounded_int(settings.get("global_speech_cooldown_seconds", 5), 1, 60, 5),
        )
        self._event_cursor = max(self._event_cursor, next_cursor)
        return Ok({"status": "listening", "cursor": self._event_cursor, "received": len(events)})

    async def _speak_once(self, category: str, text: str, cooldown_seconds: int, priority: int):
        now = datetime.now(timezone.utc)
        last = self._last_spoken_at.get(category)
        if last is not None and (now - last).total_seconds() < cooldown_seconds:
            return
        if any(item[1] == category for item in self._pending_messages):
            return
        self._pending_messages.append((max(0, min(priority, 10)), category, text, cooldown_seconds, now))
        self._pending_messages.sort(key=lambda item: (-item[0], item[4]))
        if len(self._pending_messages) > 32:
            self._pending_messages.pop()

    async def _flush_speech_queue(self, max_age: int, global_cooldown: int):
        now = datetime.now(timezone.utc)
        self._pending_messages = [
            item for item in self._pending_messages
            if (now - item[4]).total_seconds() <= max_age
        ]
        if not self._pending_messages:
            return
        if self._last_any_spoken_at is not None and (now - self._last_any_spoken_at).total_seconds() < global_cooldown:
            return

        priority, category, text, _, _ = self._pending_messages.pop(0)
        result = self._report(text, priority)
        if result.get("submitted"):
            self._last_spoken_at[category] = now
            self._last_any_spoken_at = now
        else:
            self.logger.warning("N.E.K.O. rejected an FF14 proactive message: {}", result.get("reason", "unknown"))

    @plugin_entry(
        id="recent_encounter_summary",
        name="获取最近战斗摘要",
        description="Retrieve a summary generated by FF14 Bridge for the most recent encounter, when available.",
    )
    async def recent_encounter_summary(self, **_):
        try:
            summary = await self._get("/v1/encounters/latest/summary")
            details = summary.get("summary") if isinstance(summary.get("summary"), dict) else summary
            if summary.get("available") is False:
                message = "FF14 Bridge 目前还没有可用的战斗摘要。请用简短中文告诉玩家进入一场战斗后再查询。"
            else:
                zone = details.get("zoneName") or "未知区域"
                duration = details.get("duration") or "时长未知"
                damage = details.get("damage")
                deaths = details.get("deaths")
                message = f"最近战斗摘要：{zone}，战斗时长 {duration}。"
                if damage is not None:
                    message += f"队伍总伤害 {damage}。"
                if deaths is not None:
                    message += f"死亡次数 {deaths}。"
                message += "请用猫娘语气简短总结，不要推断个人表现或未提供的机制。"
            submitted = self._report(message)
            return Ok({**summary, "message_submitted": bool(submitted.get("submitted"))})
        except BridgeError as exc:
            self.logger.info("FF14 encounter summary unavailable: {}", str(exc))
            return Err(SdkError(str(exc)))


def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _event_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None

