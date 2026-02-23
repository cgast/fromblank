# fromblank

A self-building website. Start with a blank page, describe what you want, and watch it come to life.

## How it works

1. Visit any URL — you get a blank white page with a blinking cursor
2. Type a description of the page you want (e.g. "a landing page for a dog walking service in Hamburg")
3. Claude generates the full page in real-time with streaming
4. The page is saved and served at that URL
5. Append `?build` to any URL to modify or rebuild the page

## Setup

### Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

### Local development

```bash
# Clone and enter the repo
git clone <repo-url> && cd fromblank

# Create a virtual environment
python -m venv venv && source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000` to start building.

### Docker

```bash
docker build -t fromblank .

docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v fromblank-data:/app/data \
  fromblank
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Your Anthropic API key (required) |
| `DATABASE_PATH` | `./data/pages.db` | Path to the SQLite database file |
| `PORT` | `8000` | Server port |
| `HOST` | `0.0.0.0` | Server bind address |

## Architecture

- **Backend**: FastAPI with SQLite storage
- **LLM**: Claude (claude-sonnet-4-20250514) via the Anthropic Python SDK
- **Frontend**: Vanilla HTML/CSS/JS — the generated pages are the product
- **Streaming**: Responses stream in real-time for a live "building" effect

### Routes

- `GET /{path}` — Serve a saved page, or the blank shell if none exists
- `GET /{path}?build` — Overlay a prompt input on top of the current page for rebuilding
- `POST /api/generate` — Generate or rebuild a page via Claude
