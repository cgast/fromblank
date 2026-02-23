import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.storage import init_db, get_page, save_page
from app.generator import generate_page_stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

SHELL_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "shell.html")
OVERLAY_JS = """
<div id="build-overlay">
  <div class="overlay-card">
    <div class="overlay-path">%(path)s</div>
    <form id="rebuild-form">
      <textarea id="rebuild-prompt" placeholder="Describe what to change...">%(last_prompt)s</textarea>
      <div class="overlay-actions">
        <button type="button" class="btn-cancel" onclick="cancelOverlay()">Cancel</button>
        <button type="submit" class="btn-build" id="rebuild-btn">Build</button>
      </div>
      <div class="overlay-error" id="rebuild-error"></div>
      <div class="overlay-building" id="rebuild-building">
        <div class="spinner-small"></div>
        <span>Building...</span>
      </div>
    </form>
  </div>
</div>
<link rel="stylesheet" href="/static/shell.css">
<script>
function cancelOverlay() {
    const url = new URL(window.location);
    url.searchParams.delete('build');
    window.location.href = url.pathname;
}

document.getElementById('rebuild-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const prompt = document.getElementById('rebuild-prompt').value.trim();
    if (!prompt) return;

    const btn = document.getElementById('rebuild-btn');
    const building = document.getElementById('rebuild-building');
    const errorEl = document.getElementById('rebuild-error');

    btn.disabled = true;
    building.style.display = 'flex';
    errorEl.style.display = 'none';

    try {
        const response = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: '%(path)s',
                prompt: prompt,
                mode: 'rebuild'
            })
        });

        if (!response.ok) {
            throw new Error('Generation failed: ' + response.statusText);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let html = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            html += decoder.decode(value, { stream: true });
        }
        html += decoder.decode();

        document.open();
        document.write(html);
        document.close();

        const cleanUrl = window.location.pathname;
        window.history.replaceState({}, '', cleanUrl);

    } catch (error) {
        errorEl.textContent = error.message;
        errorEl.style.display = 'block';
        btn.disabled = false;
        building.style.display = 'none';
    }
});

// Close overlay on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') cancelOverlay();
});
</script>
"""


class GenerateRequest(BaseModel):
    path: str
    prompt: str
    mode: str = "create"  # "create" or "rebuild"


@app.post("/api/generate")
async def api_generate(req: GenerateRequest):
    """Generate a page via Claude and save it."""
    path = req.path.strip()
    if not path.startswith("/"):
        path = "/" + path

    current_html = None
    if req.mode == "rebuild":
        page = get_page(path)
        if page:
            current_html = page["html_content"]

    async def stream_and_save():
        chunks = []
        async for chunk in generate_page_stream(req.prompt, current_html):
            chunks.append(chunk)
            yield chunk
        full_html = "".join(chunks)
        save_page(path, full_html, req.prompt)

    return StreamingResponse(stream_and_save(), media_type="text/html")


@app.get("/{path:path}")
async def serve_page(path: str, request: Request):
    """Serve a page from the database, the build overlay, or the blank shell."""
    # Normalize path
    if not path or path == "":
        path = "/"
    elif not path.startswith("/"):
        path = "/" + path

    is_build = "build" in request.query_params
    page = get_page(path)

    if is_build and page:
        # Serve the current page with the build overlay injected
        html = page["html_content"]
        last_prompt = ""
        if page["prompt_history"]:
            last_prompt = page["prompt_history"][-1].replace("'", "\\'").replace("\n", "\\n")

        overlay_html = OVERLAY_JS % {"path": _escape_html(path), "last_prompt": last_prompt}

        # Inject overlay before </body>
        if "</body>" in html.lower():
            idx = html.lower().rfind("</body>")
            html = html[:idx] + overlay_html + html[idx:]
        else:
            html = html + overlay_html

        return HTMLResponse(content=html)

    if is_build and not page:
        # No page exists yet — show blank shell (it functions as a build interface)
        return _serve_shell()

    if page:
        # Serve the stored page
        return HTMLResponse(content=page["html_content"])

    # No page exists — serve the blank shell
    return _serve_shell()


def _serve_shell() -> HTMLResponse:
    with open(SHELL_HTML_PATH, "r") as f:
        return HTMLResponse(content=f.read())


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
