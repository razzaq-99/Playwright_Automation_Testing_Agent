from __future__ import annotations

import re
from collections.abc import Iterable

from playwright.async_api import Page


_MAX_TEXT = 120
_MAX_ELEMENTS = 90


def sanitize(value: str | None, limit: int = _MAX_TEXT) -> str:
    """Collapse whitespace and remove control characters before model exposure."""
    clean = re.sub(r"[\x00-\x1f\x7f]", " ", value or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:limit] + ("…" if len(clean) > limit else "")


async def interactive_markdown(page: Page) -> str:
    """Return a compact, stable view of controls currently available to the agent."""
    elements = await page.evaluate(
        """() => {
          const selector = [
            'a[href]', 'button', 'input:not([type="hidden"])', 'textarea', 'select',
            '[role="button"]', '[role="link"]', '[role="textbox"]', '[role="checkbox"]',
            '[role="radio"]', '[role="combobox"]', '[contenteditable="true"]'
          ].join(',');
          return [...document.querySelectorAll(selector)]
            .filter(el => {
              const style = getComputedStyle(el);
              const box = el.getBoundingClientRect();
              return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
            })
            .slice(0, 90)
            .map((el, index) => {
              const id = el.getAttribute('data-qa-agent-id') || `qa-${index + 1}`;
              el.setAttribute('data-qa-agent-id', id);
              const labelled = el.getAttribute('aria-label') || el.getAttribute('title') || '';
              const associated = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText || '' : '';
              return {
                id,
                tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
                type: el.getAttribute('type') || '', name: el.getAttribute('name') || '',
                text: el.innerText || el.value || el.getAttribute('value') || '',
                label: labelled || associated || el.getAttribute('placeholder') || '',
                testid: el.getAttribute('data-testid') || '', href: el.getAttribute('href') || '',
                disabled: el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true'
              };
            });
        }"""
    )
    lines = ["## Interactable elements"]
    for item in elements:
        descriptor = " | ".join(
            part
            for part in (
                f"role={item['role'] or item['tag']}",
                f"text={sanitize(item['text'])}" if item["text"] else "",
                f"label={sanitize(item['label'])}" if item["label"] else "",
                f"testid={item['testid']}" if item["testid"] else "",
                f"name={item['name']}" if item["name"] else "",
                "disabled" if item["disabled"] else "",
            )
            if part
        )
        lines.append(f"- [{item['id']}] {descriptor}")
    return "\n".join(lines)

