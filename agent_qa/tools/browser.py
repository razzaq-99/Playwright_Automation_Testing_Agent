from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Error, Locator, Page, Playwright, TimeoutError as PlaywrightTimeoutError, async_playwright

from agent_qa.agent.state import (
    AssertToolCall,
    BrowserPageContext,
    ClickToolCall,
    ContextToolCall,
    FailureArtifactToolCall,
    FillToolCall,
    NavigateToolCall,
    ScrollToolCall,
    ToolCall,
    ToolOutcome,
)
from agent_qa.config import Settings
from agent_qa.tools.dom_parser import interactive_markdown, sanitize


class BrowserController:
    """Isolated Playwright context with schema-backed, retry-friendly tool methods."""

    def __init__(self, settings: Settings, run_dir: Path) -> None:
        self.settings = settings
        self.run_dir = run_dir
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.browser_logs: list[dict[str, str]] = []
        self._tracing_active = False

    async def start(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.settings.headless, slow_mo=self.settings.slow_mo_ms
        )
        self.context = await self.browser.new_context(viewport={"width": 1440, "height": 960})
        self.context.set_default_timeout(self.settings.action_timeout_ms)
        self.context.set_default_navigation_timeout(self.settings.navigation_timeout_ms)
        await self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self._tracing_active = True
        self.page = await self.context.new_page()
        self.page.on("console", self._on_console)
        self.page.on("pageerror", lambda exc: self.browser_logs.append({"level": "pageerror", "message": str(exc)}))

    def _on_console(self, message: Any) -> None:
        self.browser_logs.append({"level": message.type, "message": message.text})

    def _require_page(self) -> Page:
        if not self.page:
            raise RuntimeError("BrowserController.start() must be called before using a tool")
        return self.page

    async def execute(self, call: ToolCall) -> ToolOutcome:
        try:
            if isinstance(call, NavigateToolCall):
                return await self.navigate_to_url(call.url)
            if isinstance(call, ContextToolCall):
                context = await self.get_page_context()
                return ToolOutcome(success=True, message="Captured page context", data=context.model_dump())
            if isinstance(call, ClickToolCall):
                return await self.click_element(call.selector_or_text)
            if isinstance(call, FillToolCall):
                return await self.fill_input(call.selector_or_text, call.value)
            if isinstance(call, ScrollToolCall):
                return await self.scroll_page(call.direction, call.pixels)
            if isinstance(call, AssertToolCall):
                return await self.assert_visual_or_text(call.target_condition)
            if isinstance(call, FailureArtifactToolCall):
                paths = await self.capture_failure_artifact(call.step_name)
                return ToolOutcome(success=True, message="Failure artifact captured", data=paths)
            return ToolOutcome(success=False, message="Unsupported tool call", error_type="UnsupportedTool")
        except (PlaywrightTimeoutError, Error, ValueError, RuntimeError) as exc:
            return ToolOutcome(success=False, message=str(exc), error_type=type(exc).__name__)

    async def navigate_to_url(self, url: str) -> ToolOutcome:
        page = self._require_page()
        response = await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=min(self.settings.navigation_timeout_ms, 8_000))
        except PlaywrightTimeoutError:
            # SPAs frequently keep polling; DOM-ready is still a meaningful state.
            pass
        status = response.status if response else None
        if status is not None and status >= 400:
            return ToolOutcome(success=False, message=f"Navigation returned HTTP {status}", data={"url": page.url})
        return ToolOutcome(success=True, message=f"Navigated to {page.url}", data={"url": page.url, "http_status": status})

    async def get_page_context(self) -> BrowserPageContext:
        page = self._require_page()
        raw_screenshot = await page.screenshot(full_page=False, type="png")
        context_path = self.run_dir / "latest-context.png"
        context_path.write_bytes(raw_screenshot)
        return BrowserPageContext(
            url=page.url,
            title=await page.title(),
            interactable_markdown=await interactive_markdown(page),
            screenshot_base64=base64.b64encode(raw_screenshot).decode("ascii"),
            screenshot_path=str(context_path),
        )

    async def _locators_for(self, query: str, kind: str) -> list[Locator]:
        page = self._require_page()
        clean = query.strip()
        locators: list[Locator] = []
        if clean.startswith(("#", ".", "[", "//", "text=")) or "=" in clean:
            locators.append(page.locator(clean).first)
        locators.extend(
            [
                page.locator(f'[data-qa-agent-id="{clean}"]').first,
                page.get_by_test_id(clean).first,
                page.get_by_label(clean, exact=False).first,
                page.get_by_role("button", name=clean, exact=False).first,
                page.get_by_role("link", name=clean, exact=False).first,
                page.get_by_text(clean, exact=False).first,
            ]
        )
        if kind == "fill":
            locators.insert(3, page.get_by_placeholder(clean, exact=False).first)
            locators.insert(4, page.locator(f'input[name="{clean}"], textarea[name="{clean}"]').first)
        return locators

    async def _first_visible(self, query: str, kind: str) -> Locator:
        errors: list[str] = []
        for locator in await self._locators_for(query, kind):
            try:
                if await locator.count() and await locator.is_visible():
                    return locator
            except Error as exc:
                errors.append(str(exc))
        raise ValueError(f"No visible {kind} target found for '{query}'")

    async def click_element(self, selector_or_text: str) -> ToolOutcome:
        locator = await self._first_visible(selector_or_text, "click")
        await locator.click()
        return ToolOutcome(success=True, message=f"Clicked '{selector_or_text}'")

    async def fill_input(self, selector_or_text: str, value: str) -> ToolOutcome:
        locator = await self._first_visible(selector_or_text, "fill")
        await locator.fill(value)
        return ToolOutcome(success=True, message=f"Filled '{selector_or_text}'", data={"value_length": len(value)})

    async def scroll_page(self, direction: str, pixels: int) -> ToolOutcome:
        page = self._require_page()
        await page.mouse.wheel(0, pixels if direction == "down" else -pixels)
        return ToolOutcome(success=True, message=f"Scrolled {direction} {pixels}px")

    async def assert_visual_or_text(self, target_condition: str) -> ToolOutcome:
        """Validate a simple readable condition: text:, url:, title:, or plain visible text."""
        page = self._require_page()
        condition = target_condition.strip()
        if condition.lower().startswith("url:"):
            expected = condition.split(":", 1)[1].strip()
            ok = expected in page.url
            return ToolOutcome(success=ok, message=f"Expected URL to contain '{expected}', got '{page.url}'")
        if condition.lower().startswith("title:"):
            expected = condition.split(":", 1)[1].strip().lower()
            title = await page.title()
            return ToolOutcome(success=expected in title.lower(), message=f"Expected title to contain '{expected}', got '{title}'")
        expected = condition.split(":", 1)[1].strip() if condition.lower().startswith("text:") else condition
        locator = page.get_by_text(expected, exact=False).first
        try:
            await locator.wait_for(state="visible")
            return ToolOutcome(success=True, message=f"Visible text assertion passed: '{expected}'")
        except PlaywrightTimeoutError:
            visible_text = sanitize((await page.locator("body").inner_text())[:1000], 1000)
            return ToolOutcome(success=False, message=f"Text assertion failed: '{expected}' not visible", data={"page_text_excerpt": visible_text})

    async def capture_failure_artifact(self, step_name: str) -> dict[str, str]:
        page = self._require_page()
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", step_name).strip("-") or "failure"
        screenshot = self.run_dir / f"{safe_name}.png"
        dom = self.run_dir / f"{safe_name}.html"
        logs = self.run_dir / f"{safe_name}-browser-console.json"
        await page.screenshot(path=str(screenshot), full_page=True)
        dom.write_text(await page.content(), encoding="utf-8")
        logs.write_text(json.dumps(self.browser_logs, indent=2), encoding="utf-8")
        return {"screenshot_path": str(screenshot), "dom_snapshot_path": str(dom), "browser_logs_path": str(logs)}

    async def stop(self, save_trace: bool) -> str | None:
        trace_path: str | None = None
        if self.context and self._tracing_active:
            if save_trace:
                trace = self.run_dir / "trace.zip"
                await self.context.tracing.stop(path=str(trace))
                trace_path = str(trace)
            else:
                await self.context.tracing.stop()
            self._tracing_active = False
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        return trace_path

