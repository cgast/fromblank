import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from app.storage import init_db, get_page, save_page
from app.generator import generate_page_stream

logger = logging.getLogger("fromblank")

# --- Configuration ---
API_SECRET = os.getenv("API_SECRET", "")  # Optional: set to require auth on /api/generate
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))  # max requests per window
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # window in seconds

# File extensions that are never valid page paths — scanners probe these
BLOCKED_EXTENSIONS = {
    ".txt", ".xml", ".json", ".env", ".yml", ".yaml", ".php", ".asp", ".aspx",
    ".jsp", ".cgi", ".pl", ".bak", ".old", ".orig", ".swp", ".sql", ".db",
    ".log", ".ini", ".cfg", ".conf", ".toml", ".htaccess", ".htpasswd",
    ".git", ".svn", ".ds_store", ".ico",
}

# Well-known paths that scanners probe — block these explicitly
BLOCKED_PATHS = {
    "/robots.txt", "/sitemap.xml", "/config.json", "/package.json",
    "/composer.json", "/.env", "/.git/config", "/wp-login.php",
    "/wp-admin", "/admin", "/administrator", "/.well-known",
    "/favicon.ico", "/sse", "/mcp", "/mcp-sse", "/graphql",
    "/api", "/api/", "/.git", "/.svn", "/.DS_Store",
    "/xmlrpc.php", "/wp-cron.php", "/server-status", "/server-info",
}


# --- Rate limiter (in-memory, per-IP) ---
class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        # Prune expired entries
        self._hits[key] = [t for t in hits if now - t < self.window]
        if len(self._hits[key]) >= self.max_requests:
            return False
        self._hits[key].append(now)
        return True


rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)


# --- Security headers middleware ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Remove server identification
        response.headers.pop("server", None)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,       # Disable /docs
    redoc_url=None,      # Disable /redoc
    openapi_url=None,    # Disable /openapi.json
)

app.add_middleware(SecurityHeadersMiddleware)

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
        const headers = { 'Content-Type': 'application/json' };
        if (window.__API_SECRET__) {
            headers['Authorization'] = 'Bearer ' + window.__API_SECRET__;
        }
        const response = await fetch('/api/generate', {
            method: 'POST',
            headers: headers,
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
async def api_generate(req: GenerateRequest, request: Request):
    """Generate a page via Claude and save it."""
    # --- Auth check ---
    if API_SECRET:
        provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if provided != API_SECRET:
            logger.warning("Unauthorized /api/generate attempt from %s", request.client.host)
            raise HTTPException(status_code=401, detail="Unauthorized")

    # --- Rate limit ---
    client_ip = request.client.host
    if not rate_limiter.is_allowed(client_ip):
        logger.warning("Rate limited %s on /api/generate", client_ip)
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")

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

    # --- Block scanner probes ---
    path_lower = path.lower()
    if path_lower in BLOCKED_PATHS:
        raise HTTPException(status_code=404, detail="Not found")

    # Block paths with suspicious file extensions (unless page exists in DB)
    _, ext = os.path.splitext(path_lower)
    if ext in BLOCKED_EXTENSIONS and not get_page(path):
        raise HTTPException(status_code=404, detail="Not found")

    is_build = "build" in request.query_params
    page = get_page(path)

    if is_build and page:
        # Serve the current page with the build overlay injected
        html = page["html_content"]
        last_prompt = ""
        if page["prompt_history"]:
            last_prompt = page["prompt_history"][-1].replace("'", "\\'").replace("\n", "\\n")

        overlay_html = OVERLAY_JS % {"path": _escape_html(path), "last_prompt": last_prompt}

        # Inject API secret for authenticated requests
        if API_SECRET:
            secret_script = f'<script>window.__API_SECRET__="{_escape_js(API_SECRET)}";</script>'
            overlay_html = secret_script + overlay_html

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
        html = f.read()
    # Inject API secret so the frontend can authenticate
    if API_SECRET:
        secret_script = f'<script>window.__API_SECRET__="{_escape_js(API_SECRET)}";</script>'
        html = html.replace("</head>", secret_script + "</head>", 1)
    return HTMLResponse(content=html)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _escape_js(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'").replace("\n", "\\n")
