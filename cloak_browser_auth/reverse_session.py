from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cloak_browser_auth import config, debug_session

DEFAULT_CDP = "http://127.0.0.1:9333"


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"


@dataclass
class NetworkEntry:
    reqid: int
    request_id: str
    url: str
    method: str
    resource_type: str
    status: int | None = None
    mime_type: str | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    request_body: str | None = None
    response_body: str | None = None
    failed: bool = False
    error_text: str | None = None
    initiator: dict[str, Any] | None = None
    timestamp: float = 0.0
    set_cookie: list[str] = field(default_factory=list)


@dataclass
class ConsoleEntry:
    msgid: int
    type: str
    text: str
    timestamp: float
    url: str | None = None
    line: int | None = None


@dataclass
class ScriptEntry:
    script_id: str
    url: str
    source_map_url: str | None = None
    length: int | None = None


@dataclass
class WsFrame:
    connection_id: int
    opcode: int
    payload: str
    direction: str
    timestamp: float


class ReverseSession:
    """In-process reverse-engineering session owned by the MCP process.

    Primary path: launch CloakBrowser via Python and keep the Playwright/Cloak
    context in this process. Tools operate on that context directly.

    Optional path: attach to an existing CDP endpoint (legacy/external).
    """

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._owns_context: bool = False
        self._profile_id: str | None = None
        self._profile_path: str | None = None
        self._pages: list[Any] = []
        self._selected_page_idx = 0
        self._selected_frame_idx = 0
        self._cdp_by_page: dict[int, Any] = {}
        self._network: dict[str, NetworkEntry] = {}
        self._network_order: list[str] = []
        self._next_reqid = 1
        self._console: list[ConsoleEntry] = []
        self._next_msgid = 1
        self._scripts: dict[str, ScriptEntry] = {}
        self._breakpoints: dict[str, dict[str, Any]] = {}
        self._xhr_breakpoints: set[str] = set()
        self._paused: dict[str, Any] | None = None
        self._ws_connections: dict[str, dict[str, Any]] = {}
        self._ws_frames: list[WsFrame] = []
        self._next_ws_id = 1
        self._lock = asyncio.Lock()
        self._attached_cdp = ""
        self._domains_enabled: set[int] = set()

    @property
    def active(self) -> bool:
        return self._browser is not None and self._context is not None

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "active": self.active,
            "mode": "owned" if self._owns_context else ("attached" if self.active else None),
            "profile_id": self._profile_id,
            "profile_path": self._profile_path,
            "cdp_http": self._attached_cdp or None,
            "pages": len(self._pages),
            "selected_page_idx": self._selected_page_idx if self._pages else None,
            "selected_frame_idx": self._selected_frame_idx,
            "network_count": len(self._network_order),
            "console_count": len(self._console),
            "scripts_count": len(self._scripts),
            "breakpoints": len(self._breakpoints),
            "xhr_breakpoints": sorted(self._xhr_breakpoints),
            "paused": self._paused is not None,
            "ws_connections": len(self._ws_connections),
            "ws_frames": len(self._ws_frames),
        }

    async def ensure_ready(self) -> None:
        """Ensure a live context exists. Prefer owned Python-launched session."""
        async with self._lock:
            if self.active:
                await self._refresh_pages()
                return
            endpoint = self._resolve_cdp_optional()
            if endpoint:
                await self._attach_unlocked(endpoint)
                return
        raise RuntimeError(
            "No active browser session. Call cloak_debug_open(profile_id=..., url=[...]) first; "
            "it launches Cloak in-process via the Python cloakbrowser API."
        )

    async def open_profile(
        self,
        profile_id: str = "shared-main",
        urls: list[str] | None = None,
        headless: bool | None = None,
    ) -> dict[str, Any]:
        """Launch CloakBrowser in this MCP process and keep the context for tools."""
        async with self._lock:
            if self.active:
                if self._owns_context and self._profile_id == profile_id:
                    opened: list[str] = []
                    for url in urls or []:
                        page = await self._context.new_page()  # type: ignore[union-attr]
                        await page.goto(url, wait_until="domcontentloaded")
                        opened.append(_safe_url(page.url))
                    await self._refresh_pages()
                    if self._pages:
                        await self._ensure_page_domains(self._pages[self._selected_page_idx])
                    return {
                        "ok": True,
                        "action": "open",
                        "reused": True,
                        "mode": "owned",
                        "profile_id": self._profile_id,
                        "profile_path": self._profile_path,
                        "opened": opened,
                        "pages": self._page_summaries(),
                    }
                await self._detach_unlocked()

            from cloakbrowser import launch_persistent_context_async  # type: ignore[import-untyped]

            from cloak_browser_auth.config import Registry

            registry = Registry.load()
            if profile_id not in registry.profiles:
                raise ValueError(f"unknown profile: {profile_id}")
            profile = registry.profiles[profile_id]
            profile_path = registry.resolve_profile_path(profile)
            profile_path.mkdir(parents=True, exist_ok=True)

            # Close external holder if it locks the same profile/port.
            try:
                existing = debug_session.load_session()
                if existing is not None:
                    debug_session.close_session()
            except Exception:
                pass

            context = await launch_persistent_context_async(
                str(profile_path),
                headless=profile.headless if headless is None else headless,
            )
            self._context = context
            self._browser = getattr(context, "browser", None)
            self._owns_context = True
            self._profile_id = profile_id
            self._profile_path = str(profile_path)
            self._attached_cdp = ""
            self._playwright = None

            opened_urls: list[str] = []
            for url in urls or []:
                if not url.startswith(("https://", "http://", "about:")):
                    raise ValueError(f"unsupported url: {url}")
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded")
                opened_urls.append(_safe_url(page.url))
            if not opened_urls and context.pages:
                # keep default blank page if any
                pass
            await self._refresh_pages()
            if self._pages:
                self._selected_page_idx = 0
                await self._ensure_page_domains(self._pages[0])
            return {
                "ok": True,
                "action": "open",
                "reused": False,
                "mode": "owned",
                "profile_id": profile_id,
                "profile_path": str(profile_path),
                "opened": opened_urls,
                "pages": self._page_summaries(),
                "control": "python-cloakbrowser-in-process",
            }


    async def new_tab(self, url: str) -> dict[str, Any]:
        await self.ensure_ready()
        assert self._context is not None
        page = await self._context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await self._refresh_pages()
        self._selected_page_idx = max(0, len(self._pages) - 1)
        await self._ensure_page_domains(self._pages[self._selected_page_idx])
        return {
            "ok": True,
            "mode": "owned" if self._owns_context else "attached",
            "url": _safe_url(page.url),
            "page_idx": self._selected_page_idx,
            "pages": self._page_summaries(),
        }

    async def list_pages(self) -> dict[str, Any]:
        await self.ensure_ready()
        return {
            "ok": True,
            "mode": "owned" if self._owns_context else "attached",
            "pages": self._page_summaries(),
            "selected_page_idx": self._selected_page_idx,
        }

    async def ensure_attached(self, cdp_http: str | None = None) -> dict[str, Any]:
        """Optional/legacy: attach to an external CDP endpoint."""
        async with self._lock:
            if self.active and self._owns_context and not (cdp_http or "").strip():
                await self._refresh_pages()
                return {
                    "ok": True,
                    "action": "attach",
                    "mode": "owned",
                    "reused": True,
                    "profile_id": self._profile_id,
                    "pages": self._page_summaries(),
                }
            endpoint = (cdp_http or "").strip() or self._resolve_cdp_optional()
            if not endpoint:
                raise RuntimeError(
                    "No browser session. Prefer cloak_debug_open (in-process Python launch). "
                    "CDP attach is only a fallback."
                )
            if self.active and self._attached_cdp == endpoint and not self._owns_context:
                await self._refresh_pages()
                return {"ok": True, "action": "attach", "cdp_http": endpoint, "mode": "attached", "reused": True}
            if self.active:
                await self._detach_unlocked()
            await self._attach_unlocked(endpoint)
            return {
                "ok": True,
                "action": "attach",
                "cdp_http": endpoint,
                "mode": "attached",
                "reused": False,
                "pages": self._page_summaries(),
            }

    def _resolve_cdp_optional(self) -> str | None:
        """Legacy only. New holders intentionally expose no CDP endpoint."""
        session = debug_session.load_session()
        if session is not None:
            cdp = (session.cdp_http or "").strip()
            if cdp and debug_session._port_open(session.port):
                # Old holders that still published a real CDP port.
                return cdp
            return None
        return None

    async def _attach_unlocked(self, endpoint: str) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
        if not self._browser.contexts:
            raise RuntimeError(f"CDP endpoint has no browser contexts: {endpoint}")
        self._context = self._browser.contexts[0]
        self._owns_context = False
        self._profile_id = None
        self._profile_path = None
        self._attached_cdp = endpoint
        await self._refresh_pages()
        if self._pages:
            await self._ensure_page_domains(self._pages[self._selected_page_idx])

    async def detach(self) -> dict[str, Any]:
        async with self._lock:
            owned = self._owns_context
            await self._detach_unlocked()
            return {"ok": True, "action": "detach", "closed_owned_browser": owned}

    async def close_session(self) -> dict[str, Any]:
        """Close owned browser or detach external session."""
        return await self.detach()

    async def _detach_unlocked(self) -> None:
        for cdp in list(self._cdp_by_page.values()):
            try:
                await cdp.detach()
            except Exception:
                pass
        self._cdp_by_page.clear()
        self._pages = []
        if self._context is not None and self._owns_context:
            try:
                await self._context.close()
            except Exception:
                pass
        elif self._browser is not None and not self._owns_context:
            # Attached mode: disconnect only.
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._owns_context = False
        self._profile_id = None
        self._profile_path = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._attached_cdp = ""
        self._domains_enabled.clear()
        self._paused = None

    async def _refresh_pages(self) -> None:
        assert self._context is not None
        self._pages = list(self._context.pages)
        if self._selected_page_idx >= len(self._pages):
            self._selected_page_idx = max(0, len(self._pages) - 1)
        self._selected_frame_idx = 0

    def _page_summaries(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, page in enumerate(self._pages):
            try:
                url = page.url
                title = ""
            except Exception:
                url, title = "about:blank", ""
            rows.append(
                {
                    "page_idx": idx,
                    "url": _safe_url(url),
                    "title": title,
                    "selected": idx == self._selected_page_idx,
                }
            )
        return rows

    def _require_page(self) -> Any:
        if not self._pages:
            raise RuntimeError("No pages in reverse session")
        if self._selected_page_idx < 0 or self._selected_page_idx >= len(self._pages):
            self._selected_page_idx = 0
        return self._pages[self._selected_page_idx]

    def _require_frame(self) -> Any:
        page = self._require_page()
        frames = page.frames
        if self._selected_frame_idx < 0 or self._selected_frame_idx >= len(frames):
            self._selected_frame_idx = 0
        return frames[self._selected_frame_idx]

    async def _cdp(self, page: Any | None = None) -> Any:
        page = page or self._require_page()
        key = id(page)
        if key not in self._cdp_by_page:
            self._cdp_by_page[key] = await page.context.new_cdp_session(page)
            await self._ensure_page_domains(page)
        return self._cdp_by_page[key]

    async def _ensure_page_domains(self, page: Any) -> None:
        key = id(page)
        if key in self._domains_enabled:
            return
        cdp = await self._cdp(page)
        domain_calls: list[tuple[str, dict[str, object]]] = [
            ("Network.enable", {}),
            ("Runtime.enable", {}),
            ("Debugger.enable", {}),
            ("Log.enable", {}),
            ("Page.enable", {}),
            ("Console.enable", {}),
        ]
        for domain, params in domain_calls:
            try:
                await cdp.send(domain, params)
            except Exception:
                pass
        cdp.on("Network.requestWillBeSent", self._on_request)
        cdp.on("Network.responseReceived", self._on_response)
        cdp.on("Network.loadingFinished", lambda payload: asyncio.create_task(self._on_loading_finished(payload)))
        cdp.on("Network.loadingFailed", self._on_loading_failed)
        cdp.on("Runtime.consoleAPICalled", self._on_console_api)
        cdp.on("Runtime.exceptionThrown", self._on_exception)
        cdp.on("Debugger.scriptParsed", self._on_script_parsed)
        cdp.on("Debugger.paused", self._on_paused)
        cdp.on("Debugger.resumed", self._on_resumed)
        cdp.on("Network.webSocketCreated", self._on_ws_created)
        cdp.on("Network.webSocketFrameSent", self._on_ws_sent)
        cdp.on("Network.webSocketFrameReceived", self._on_ws_recv)
        cdp.on("Network.webSocketClosed", self._on_ws_closed)
        self._domains_enabled.add(key)

    def _on_request(self, payload: dict[str, Any]) -> None:
        request = payload.get("request") or {}
        request_id = str(payload.get("requestId") or "")
        if not request_id:
            return
        entry = NetworkEntry(
            reqid=self._next_reqid,
            request_id=request_id,
            url=str(request.get("url") or ""),
            method=str(request.get("method") or "GET"),
            resource_type=str(payload.get("type") or "Other"),
            request_headers={str(k): str(v) for k, v in (request.get("headers") or {}).items()},
            request_body=request.get("postData"),
            initiator=payload.get("initiator"),
            timestamp=float(payload.get("timestamp") or time.time()),
        )
        self._next_reqid += 1
        self._network[request_id] = entry
        self._network_order.append(request_id)
        # XHR breakpoint simulation: if URL matches and debugger available, we only record match.
        url = entry.url
        for pattern in self._xhr_breakpoints:
            if pattern in url:
                entry.initiator = {
                    **(entry.initiator or {}),
                    "xhrBreakpointMatched": pattern,
                }

    def _on_response(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("requestId") or "")
        entry = self._network.get(request_id)
        if entry is None:
            return
        response = payload.get("response") or {}
        entry.status = int(response.get("status") or 0) or None
        entry.mime_type = response.get("mimeType")
        headers = {str(k): str(v) for k, v in (response.get("headers") or {}).items()}
        entry.response_headers = headers
        set_cookie = []
        for key, value in headers.items():
            if key.lower() == "set-cookie":
                set_cookie.append(value)
        entry.set_cookie = set_cookie

    async def _on_loading_finished(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("requestId") or "")
        entry = self._network.get(request_id)
        if entry is None or self._context is None:
            return
        try:
            page = self._require_page()
            cdp = await self._cdp(page)
            body = await cdp.send("Network.getResponseBody", {"requestId": request_id})
            text = body.get("body") or ""
            if body.get("base64Encoded"):
                try:
                    text = base64.b64decode(text).decode("utf-8", errors="replace")
                except Exception:
                    text = f"<base64 {len(text)} chars>"
            # Cap body to keep MCP responses small.
            entry.response_body = text[:200_000]
        except Exception:
            pass

    def _on_loading_failed(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("requestId") or "")
        entry = self._network.get(request_id)
        if entry is None:
            return
        entry.failed = True
        entry.error_text = str(payload.get("errorText") or "failed")

    def _on_console_api(self, payload: dict[str, Any]) -> None:
        args = payload.get("args") or []
        parts: list[str] = []
        for arg in args:
            if "value" in arg:
                parts.append(str(arg.get("value")))
            elif arg.get("description"):
                parts.append(str(arg.get("description")))
            else:
                parts.append(str(arg.get("type")))
        stack = (payload.get("stackTrace") or {}).get("callFrames") or []
        url = stack[0].get("url") if stack else None
        line = stack[0].get("lineNumber") if stack else None
        self._console.append(
            ConsoleEntry(
                msgid=self._next_msgid,
                type=str(payload.get("type") or "log"),
                text=" ".join(parts)[:4000],
                timestamp=float(payload.get("timestamp") or time.time()),
                url=url,
                line=line,
            )
        )
        self._next_msgid += 1

    def _on_exception(self, payload: dict[str, Any]) -> None:
        detail = payload.get("exceptionDetails") or {}
        text = str(detail.get("text") or detail.get("exception", {}).get("description") or "exception")
        self._console.append(
            ConsoleEntry(
                msgid=self._next_msgid,
                type="error",
                text=text[:4000],
                timestamp=time.time(),
                url=(detail.get("url")),
                line=detail.get("lineNumber"),
            )
        )
        self._next_msgid += 1

    def _on_script_parsed(self, payload: dict[str, Any]) -> None:
        script_id = str(payload.get("scriptId") or "")
        if not script_id:
            return
        self._scripts[script_id] = ScriptEntry(
            script_id=script_id,
            url=str(payload.get("url") or f"inline:{script_id}"),
            source_map_url=payload.get("sourceMapURL"),
            length=payload.get("length"),
        )

    def _on_paused(self, payload: dict[str, Any]) -> None:
        self._paused = payload

    def _on_resumed(self, _payload: dict[str, Any] | None = None) -> None:
        self._paused = None

    def _on_ws_created(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("requestId") or "")
        self._ws_connections[request_id] = {
            "connection_id": self._next_ws_id,
            "url": str(payload.get("url") or ""),
            "request_id": request_id,
            "open": True,
        }
        self._next_ws_id += 1

    def _on_ws_sent(self, payload: dict[str, Any]) -> None:
        self._record_ws_frame(payload, "sent")

    def _on_ws_recv(self, payload: dict[str, Any]) -> None:
        self._record_ws_frame(payload, "received")

    def _record_ws_frame(self, payload: dict[str, Any], direction: str) -> None:
        request_id = str(payload.get("requestId") or "")
        conn = self._ws_connections.get(request_id) or {
            "connection_id": self._next_ws_id,
            "url": "",
            "request_id": request_id,
            "open": True,
        }
        if request_id not in self._ws_connections:
            self._ws_connections[request_id] = conn
            self._next_ws_id += 1
        response = payload.get("response") or {}
        self._ws_frames.append(
            WsFrame(
                connection_id=int(conn["connection_id"]),
                opcode=int(response.get("opcode") or 1),
                payload=str(response.get("payloadData") or "")[:50_000],
                direction=direction,
                timestamp=float(payload.get("timestamp") or time.time()),
            )
        )

    def _on_ws_closed(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("requestId") or "")
        if request_id in self._ws_connections:
            self._ws_connections[request_id]["open"] = False

    # ---- tools ----

    async def select_page(self, page_idx: int | None = None, page_size: int = 20) -> dict[str, Any]:
        await self.ensure_ready()
        await self._refresh_pages()
        if page_idx is not None:
            if page_idx < 0 or page_idx >= len(self._pages):
                raise ValueError(f"page_idx out of range: {page_idx}")
            self._selected_page_idx = page_idx
            self._selected_frame_idx = 0
            await self._ensure_page_domains(self._pages[page_idx])
        pages = self._page_summaries()
        # fill titles asynchronously-safe
        for row in pages:
            try:
                row["title"] = await self._pages[row["page_idx"]].title()
            except Exception:
                row["title"] = ""
        return {
            "ok": True,
            "action": "select_page",
            "selected_page_idx": self._selected_page_idx,
            "pages": pages[: max(1, page_size)],
        }

    async def new_page(self, url: str) -> dict[str, Any]:
        if not url.startswith(("https://", "http://", "about:")):
            raise ValueError("url must be http(s) or about:")
        await self.ensure_ready()
        assert self._context is not None
        page = await self._context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await self._refresh_pages()
        self._selected_page_idx = self._pages.index(page)
        self._selected_frame_idx = 0
        await self._ensure_page_domains(page)
        return {
            "ok": True,
            "action": "new_page",
            "page_idx": self._selected_page_idx,
            "url": _safe_url(page.url),
            "title": await page.title(),
        }

    async def navigate_page(
        self,
        type: str = "url",
        url: str | None = None,
        ignore_cache: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        page = self._require_page()
        action = type
        if action == "url":
            if not url:
                raise ValueError("url is required when type=url")
            await page.goto(url, wait_until="domcontentloaded")
        elif action == "reload":
            await page.reload(wait_until="domcontentloaded")
        elif action == "back":
            await page.go_back(wait_until="domcontentloaded")
        elif action == "forward":
            await page.go_forward(wait_until="domcontentloaded")
        else:
            raise ValueError("type must be url|reload|back|forward")
        if ignore_cache and action == "reload":
            cdp = await self._cdp(page)
            try:
                await cdp.send("Page.reload", {"ignoreCache": True})
            except Exception:
                pass
        return {
            "ok": True,
            "action": "navigate_page",
            "type": action,
            "url": _safe_url(page.url),
            "title": await page.title(),
        }

    async def select_frame(self, frame_idx: int | None = None, page_size: int = 20) -> dict[str, Any]:
        await self.ensure_ready()
        page = self._require_page()
        frames = page.frames
        rows = []
        for idx, frame in enumerate(frames):
            rows.append(
                {
                    "frame_idx": idx,
                    "url": _safe_url(frame.url),
                    "name": frame.name,
                    "selected": idx == self._selected_frame_idx,
                }
            )
        if frame_idx is not None:
            if frame_idx < 0 or frame_idx >= len(frames):
                raise ValueError(f"frame_idx out of range: {frame_idx}")
            self._selected_frame_idx = frame_idx
        return {
            "ok": True,
            "action": "select_frame",
            "selected_frame_idx": self._selected_frame_idx,
            "frames": rows[: max(1, page_size)],
        }

    async def click_element(self, selector: str, index: int = 0, timeout_ms: int = 5000) -> dict[str, Any]:
        await self.ensure_ready()
        frame = self._require_frame()
        locator = frame.locator(selector)
        count = await locator.count()
        if count == 0:
            raise RuntimeError(f"selector matched 0 elements: {selector}")
        if count > 1 and index is None:
            raise RuntimeError(f"selector matched {count} elements; pass index")
        target = locator.nth(index)
        await target.click(timeout=timeout_ms)
        return {"ok": True, "action": "click_element", "selector": selector, "index": index, "matched": count}

    async def take_screenshot(self, full_page: bool = False, file_path: str | None = None) -> dict[str, Any]:
        await self.ensure_ready()
        page = self._require_page()
        raw = await page.screenshot(full_page=full_page, type="png")
        if file_path:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return {
                "ok": True,
                "action": "take_screenshot",
                "file_path": str(path),
                "byte_length": len(raw),
            }
        # Avoid dumping huge base64 into model context by default; store under auth dir.
        config.AUTH_DIR.mkdir(parents=True, exist_ok=True)
        out = config.AUTH_DIR / f"screenshot-{int(time.time())}.png"
        out.write_bytes(raw)
        return {
            "ok": True,
            "action": "take_screenshot",
            "file_path": str(out),
            "byte_length": len(raw),
        }

    async def list_console_messages(
        self,
        msgid: int | None = None,
        type: str | None = None,
        page_size: int = 20,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        if msgid is not None:
            for item in self._console:
                if item.msgid == msgid:
                    return {"ok": True, "action": "list_console_messages", "message": item.__dict__}
            raise ValueError(f"unknown msgid: {msgid}")
        rows = self._console
        if type:
            rows = [item for item in rows if item.type == type]
        data = [item.__dict__ for item in rows[-max(1, page_size) :]]
        return {"ok": True, "action": "list_console_messages", "messages": data, "total": len(rows)}

    async def list_network_requests(
        self,
        reqid: int | None = None,
        cookie_name: str | None = None,
        resource_type: str | None = None,
        page_size: int = 30,
        include_body: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        entries = [self._network[rid] for rid in self._network_order if rid in self._network]
        if cookie_name:
            flow = []
            for entry in entries:
                for sc in entry.set_cookie:
                    if sc.startswith(f"{cookie_name}=") or f"{cookie_name}=" in sc:
                        flow.append(
                            {
                                "reqid": entry.reqid,
                                "url": _safe_url(entry.url),
                                "status": entry.status,
                                "set_cookie": sc[:500],
                            }
                        )
            return {"ok": True, "action": "list_network_requests", "cookie_name": cookie_name, "cookie_flow": flow}
        if reqid is not None:
            for entry in entries:
                if entry.reqid == reqid:
                    payload = {
                        "reqid": entry.reqid,
                        "url": _safe_url(entry.url),
                        "method": entry.method,
                        "resource_type": entry.resource_type,
                        "status": entry.status,
                        "mime_type": entry.mime_type,
                        "failed": entry.failed,
                        "error_text": entry.error_text,
                        "request_headers": entry.request_headers,
                        "response_headers": entry.response_headers,
                        "set_cookie": entry.set_cookie,
                    }
                    if include_body:
                        payload["request_body"] = (entry.request_body or "")[:100_000]
                        payload["response_body"] = (entry.response_body or "")[:100_000]
                    return {"ok": True, "action": "list_network_requests", "request": payload}
            raise ValueError(f"unknown reqid: {reqid}")
        if resource_type:
            entries = [e for e in entries if e.resource_type.lower() == resource_type.lower()]
        rows = [
            {
                "reqid": e.reqid,
                "method": e.method,
                "status": e.status,
                "resource_type": e.resource_type,
                "url": _safe_url(e.url),
                "failed": e.failed,
            }
            for e in entries[-max(1, page_size) :]
        ]
        return {"ok": True, "action": "list_network_requests", "requests": rows, "total": len(entries)}

    async def clear_network_requests(self, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("confirm=true is required")
        self._network.clear()
        self._network_order.clear()
        return {"ok": True, "action": "clear_network_requests"}

    async def get_request_initiator(self, reqid: int) -> dict[str, Any]:
        await self.ensure_ready()
        for entry in self._network.values():
            if entry.reqid == reqid:
                return {
                    "ok": True,
                    "action": "get_request_initiator",
                    "reqid": reqid,
                    "url": _safe_url(entry.url),
                    "initiator": entry.initiator,
                }
        raise ValueError(f"unknown reqid: {reqid}")

    async def get_websocket_messages(
        self,
        connection_id: int | None = None,
        page_size: int = 50,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        conns = [
            {
                "connection_id": c["connection_id"],
                "url": _safe_url(str(c.get("url") or "")),
                "open": c.get("open", False),
            }
            for c in self._ws_connections.values()
        ]
        frames = self._ws_frames
        if connection_id is not None:
            frames = [f for f in frames if f.connection_id == connection_id]
        rows = [
            {
                "connection_id": f.connection_id,
                "direction": f.direction,
                "opcode": f.opcode,
                "payload": f.payload[:2000],
                "timestamp": f.timestamp,
            }
            for f in frames[-max(1, page_size) :]
        ]
        return {
            "ok": True,
            "action": "get_websocket_messages",
            "connections": conns,
            "messages": rows,
            "total_messages": len(frames),
        }

    async def list_scripts(self, page_size: int = 50) -> dict[str, Any]:
        await self.ensure_ready()
        # Force script discovery if empty.
        if not self._scripts:
            page = self._require_page()
            cdp = await self._cdp(page)
            try:
                await cdp.send("Debugger.enable")
            except Exception:
                pass
        rows = [
            {
                "script_id": s.script_id,
                "url": s.url,
                "length": s.length,
                "source_map_url": s.source_map_url,
            }
            for s in list(self._scripts.values())[: max(1, page_size)]
        ]
        return {"ok": True, "action": "list_scripts", "scripts": rows, "total": len(self._scripts)}

    async def get_script_source(
        self,
        script_id: str | None = None,
        url: str | None = None,
        start_line: int = 1,
        end_line: int | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        sid = script_id
        if sid is None:
            if not url:
                raise ValueError("script_id or url is required")
            for script in self._scripts.values():
                if script.url == url or url in script.url:
                    sid = script.script_id
                    break
            if sid is None:
                raise ValueError(f"script not found for url: {url}")
        cdp = await self._cdp()
        result = await cdp.send("Debugger.getScriptSource", {"scriptId": sid})
        source = str(result.get("scriptSource") or "")
        lines = source.splitlines()
        start = max(1, start_line)
        end = end_line or min(len(lines), start + 200)
        end = min(end, len(lines))
        snippet = "\n".join(f"{idx}|{lines[idx - 1]}" for idx in range(start, end + 1))
        if len(snippet) > max_chars:
            snippet = snippet[:max_chars] + "\n...<truncated>..."
        return {
            "ok": True,
            "action": "get_script_source",
            "script_id": sid,
            "start_line": start,
            "end_line": end,
            "total_lines": len(lines),
            "source": snippet,
        }

    async def save_script_source(
        self,
        file_path: str,
        script_id: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        detail = await self.get_script_source(script_id=script_id, url=url, start_line=1, end_line=10**9, max_chars=10**9)
        # fetch full raw again
        sid = str(detail["script_id"])
        cdp = await self._cdp()
        result = await cdp.send("Debugger.getScriptSource", {"scriptId": sid})
        source = str(result.get("scriptSource") or "")
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return {
            "ok": True,
            "action": "save_script_source",
            "script_id": sid,
            "file_path": str(path),
            "char_length": len(source),
        }

    async def search_in_sources(
        self,
        query: str,
        case_sensitive: bool = False,
        is_regex: bool = False,
        max_matches: int = 50,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        if not self._scripts:
            await self.list_scripts()
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query if is_regex else re.escape(query), flags)
        matches: list[dict[str, Any]] = []
        cdp = await self._cdp()
        for script in self._scripts.values():
            if len(matches) >= max_matches:
                break
            try:
                result = await cdp.send("Debugger.getScriptSource", {"scriptId": script.script_id})
            except Exception:
                continue
            source = str(result.get("scriptSource") or "")
            for line_no, line in enumerate(source.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(
                        {
                            "script_id": script.script_id,
                            "url": script.url,
                            "line": line_no,
                            "preview": line[:300],
                        }
                    )
                    if len(matches) >= max_matches:
                        break
        return {
            "ok": True,
            "action": "search_in_sources",
            "query": query,
            "matches": matches,
            "count": len(matches),
        }

    async def set_breakpoint_on_text(
        self,
        text: str,
        url_filter: str | None = None,
        occurrence: int = 1,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        search = await self.search_in_sources(text, max_matches=100)
        candidates = search["matches"]
        if url_filter:
            candidates = [m for m in candidates if url_filter in str(m.get("url") or "")]
        if len(candidates) < occurrence:
            raise RuntimeError(f"text not found enough times: need {occurrence}, got {len(candidates)}")
        hit = candidates[occurrence - 1]
        cdp = await self._cdp()
        result = await cdp.send(
            "Debugger.setBreakpoint",
            {
                "location": {
                    "scriptId": hit["script_id"],
                    "lineNumber": max(0, int(hit["line"]) - 1),
                }
            },
        )
        bpid = str(result.get("breakpointId") or "")
        self._breakpoints[bpid] = {
            "breakpoint_id": bpid,
            "script_id": hit["script_id"],
            "url": hit["url"],
            "line": hit["line"],
            "text": text,
            "kind": "code",
        }
        return {"ok": True, "action": "set_breakpoint_on_text", "breakpoint": self._breakpoints[bpid]}

    async def break_on_xhr(self, url_pattern: str) -> dict[str, Any]:
        await self.ensure_ready()
        self._xhr_breakpoints.add(url_pattern)
        cdp = await self._cdp()
        try:
            await cdp.send("DOMDebugger.setXHRBreakpoint", {"url": url_pattern})
        except Exception:
            # Fallback: local match only.
            pass
        return {
            "ok": True,
            "action": "break_on_xhr",
            "url_pattern": url_pattern,
            "xhr_breakpoints": sorted(self._xhr_breakpoints),
        }

    async def remove_breakpoint(
        self,
        action: str,
        breakpoint_id: str | None = None,
        url_pattern: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        cdp = await self._cdp()
        if action == "remove_code":
            if not breakpoint_id:
                raise ValueError("breakpoint_id required")
            try:
                await cdp.send("Debugger.removeBreakpoint", {"breakpointId": breakpoint_id})
            except Exception:
                pass
            self._breakpoints.pop(breakpoint_id, None)
        elif action == "remove_xhr":
            if not url_pattern:
                raise ValueError("url_pattern required")
            try:
                await cdp.send("DOMDebugger.removeXHRBreakpoint", {"url": url_pattern})
            except Exception:
                pass
            self._xhr_breakpoints.discard(url_pattern)
        elif action == "remove_all":
            if not confirm:
                raise ValueError("confirm=true required for remove_all")
            for bpid in list(self._breakpoints):
                try:
                    await cdp.send("Debugger.removeBreakpoint", {"breakpointId": bpid})
                except Exception:
                    pass
            for pattern in list(self._xhr_breakpoints):
                try:
                    await cdp.send("DOMDebugger.removeXHRBreakpoint", {"url": pattern})
                except Exception:
                    pass
            self._breakpoints.clear()
            self._xhr_breakpoints.clear()
        else:
            raise ValueError("action must be remove_code|remove_xhr|remove_all")
        return {
            "ok": True,
            "action": "remove_breakpoint",
            "mode": action,
            "breakpoints": list(self._breakpoints.values()),
            "xhr_breakpoints": sorted(self._xhr_breakpoints),
        }

    async def list_breakpoints(self) -> dict[str, Any]:
        await self.ensure_ready()
        return {
            "ok": True,
            "action": "list_breakpoints",
            "breakpoints": list(self._breakpoints.values()),
            "xhr_breakpoints": sorted(self._xhr_breakpoints),
        }

    async def get_paused_info(self, frame_index: int = 0) -> dict[str, Any]:
        await self.ensure_ready()
        if not self._paused:
            return {"ok": True, "action": "get_paused_info", "paused": False}
        frames = self._paused.get("callFrames") or []
        summary = []
        for idx, frame in enumerate(frames[:20]):
            loc = frame.get("location") or {}
            summary.append(
                {
                    "frame_index": idx,
                    "function_name": frame.get("functionName") or "(anonymous)",
                    "script_id": loc.get("scriptId"),
                    "line": int(loc.get("lineNumber") or 0) + 1,
                    "column": int(loc.get("columnNumber") or 0) + 1,
                    "url": frame.get("url"),
                }
            )
        selected = frames[frame_index] if 0 <= frame_index < len(frames) else None
        scope_preview = []
        if selected:
            for scope in (selected.get("scopeChain") or [])[:8]:
                obj = scope.get("object") or {}
                scope_preview.append(
                    {
                        "type": scope.get("type"),
                        "name": scope.get("name"),
                        "object_id": obj.get("objectId"),
                        "description": obj.get("description"),
                    }
                )
        return {
            "ok": True,
            "action": "get_paused_info",
            "paused": True,
            "reason": self._paused.get("reason"),
            "call_frames": summary,
            "selected_frame_index": frame_index,
            "scopes": scope_preview,
        }

    async def pause_or_resume(self, action: str) -> dict[str, Any]:
        await self.ensure_ready()
        cdp = await self._cdp()
        if action == "pause":
            await cdp.send("Debugger.pause")
        elif action == "resume":
            await cdp.send("Debugger.resume")
        else:
            raise ValueError("action must be pause|resume")
        await asyncio.sleep(0.05)
        return {
            "ok": True,
            "action": "pause_or_resume",
            "mode": action,
            "paused": self._paused is not None,
        }

    async def step(self, type: str = "over") -> dict[str, Any]:
        await self.ensure_ready()
        cdp = await self._cdp()
        mapping = {
            "over": "Debugger.stepOver",
            "into": "Debugger.stepInto",
            "out": "Debugger.stepOut",
        }
        method = mapping.get(type)
        if method is None:
            raise ValueError("type must be over|into|out")
        await cdp.send(method)
        await asyncio.sleep(0.05)
        info = await self.get_paused_info()
        return {"ok": True, "action": "step", "type": type, "paused_info": info}

    async def evaluate_script(
        self,
        function: str,
        frame_index: int | None = None,
        await_promise: bool = True,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        # If paused and frame requested, evaluate on call frame.
        if self._paused is not None and frame_index is not None:
            frames = self._paused.get("callFrames") or []
            if frame_index < 0 or frame_index >= len(frames):
                raise ValueError("frame_index out of range")
            call_frame_id = frames[frame_index].get("callFrameId")
            cdp = await self._cdp()
            result = await cdp.send(
                "Debugger.evaluateOnCallFrame",
                {
                    "callFrameId": call_frame_id,
                    "expression": f"({function})()",
                    "returnByValue": True,
                    "awaitPromise": await_promise,
                },
            )
            remote = result.get("result") or {}
            return {
                "ok": True,
                "action": "evaluate_script",
                "mode": "call_frame",
                "frame_index": frame_index,
                "value": remote.get("value", remote.get("description")),
                "type": remote.get("type"),
            }
        frame = self._require_frame()
        # function is expected as JS function source: () => ...
        value = await frame.evaluate(f"({function})()")
        return {
            "ok": True,
            "action": "evaluate_script",
            "mode": "page",
            "value": value,
        }

    async def clear_site_data(self, confirm: bool = False, include_http_cache: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("confirm=true is required")
        await self.ensure_ready()
        page = self._require_page()
        origin = _safe_url(page.url)
        parsed = urlsplit(page.url)
        origin_only = f"{parsed.scheme}://{parsed.netloc}"
        cdp = await self._cdp(page)
        # Clear cookies for eTLD+ish domain via context.
        assert self._context is not None
        cookies = await self._context.cookies()
        cleared = 0
        host = parsed.hostname or ""
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").lstrip(".")
            if host.endswith(domain) or domain.endswith(host):
                try:
                    await self._context.clear_cookies(
                        name=cookie.get("name"),
                        domain=cookie.get("domain"),
                        path=cookie.get("path"),
                    )
                    cleared += 1
                except Exception:
                    pass
        try:
            await cdp.send(
                "Storage.clearDataForOrigin",
                {
                    "origin": origin_only,
                    "storageTypes": "all",
                },
            )
        except Exception:
            pass
        if include_http_cache:
            try:
                await cdp.send("Network.clearBrowserCache")
            except Exception:
                pass
        return {
            "ok": True,
            "action": "clear_site_data",
            "origin": origin_only,
            "cookies_cleared_estimate": cleared,
            "include_http_cache": include_http_cache,
            "url": origin,
        }


# Process-wide singleton used by MCP tool handlers.
SESSION = ReverseSession()
