import anthropic
import os
from typing import AsyncIterator


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """You are a web page generator. You create complete, self-contained HTML pages with inline CSS and JS.

Rules:
- Output ONLY valid HTML. No markdown, no code fences, no explanation — just the raw HTML document.
- Start with <!DOCTYPE html> and include a complete <html> document.
- All CSS must be in <style> tags within the document.
- All JavaScript must be in <script> tags within the document.
- You may use CDN links for popular libraries (Google Fonts, Font Awesome, Tailwind CSS CDN, etc.) if they enhance the page.
- Make the pages visually polished, modern, and responsive.
- Use beautiful typography, spacing, and color schemes.
- Do NOT include any build overlay, editing UI, or meta-editing functionality.
- The page should look like a real, production-quality website."""

REBUILD_SYSTEM_PROMPT = """You are a web page generator. You modify existing HTML pages based on user instructions.

Rules:
- Output ONLY the complete modified HTML. No markdown, no code fences, no explanation — just the raw HTML document.
- Start with <!DOCTYPE html> and include a complete <html> document.
- All CSS must be in <style> tags within the document.
- All JavaScript must be in <script> tags within the document.
- You may use CDN links for popular libraries if they enhance the page.
- Preserve the overall structure and content of the existing page unless the user explicitly asks to change it.
- Make requested modifications cleanly and professionally.
- Do NOT include any build overlay, editing UI, or meta-editing functionality."""


def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def generate_page(prompt: str, current_html: str | None = None) -> str:
    """Generate a page synchronously (non-streaming). Returns the full HTML."""
    client = _get_client()

    if current_html:
        system = REBUILD_SYSTEM_PROMPT
        user_message = f"Here is the current page HTML:\n\n{current_html}\n\n---\n\nUser's modification request: {prompt}"
    else:
        system = SYSTEM_PROMPT
        user_message = prompt

    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        result = stream.get_final_message()

    return result.content[0].text


async def generate_page_stream(prompt: str, current_html: str | None = None) -> AsyncIterator[str]:
    """Generate a page with streaming. Yields HTML chunks as they arrive."""
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    if current_html:
        system = REBUILD_SYSTEM_PROMPT
        user_message = f"Here is the current page HTML:\n\n{current_html}\n\n---\n\nUser's modification request: {prompt}"
    else:
        system = SYSTEM_PROMPT
        user_message = prompt

    async with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        async for text in stream.text_stream:
            yield text
