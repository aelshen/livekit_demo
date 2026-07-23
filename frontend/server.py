"""Minimal demo console: a customer picker + live call + live trace viewer.

Serves the static frontend (frontend/static/) plus three small APIs:
  GET  /api/customers    mock customers with devices/orders, for the sidebar
  POST /api/token        mints a LiveKit access token for the browser to join a room
  GET  /api/logs/stream  Server-Sent Events tail of logs/trace.jsonl

The selected customer's account number is encoded directly in the room name
(support-{account_number}-{random}); agent/main.py parses it back out and
auto-identifies the caller instead of asking for a phone/account number —
that's what makes the sidebar picker actually mean something to the call.

Run alongside the MCP server and the agent worker:
    python -m mcp_server.server --http --port 8089
    python -m agent.main dev
    python -m frontend.server
Then open http://localhost:8090
"""

import asyncio
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from livekit import api
from pydantic import BaseModel

from mcp_server.data import store

load_dotenv()

STATIC_DIR = Path(__file__).parent / "static"
TRACE_FILE = Path(__file__).parent.parent / "logs" / "trace.jsonl"

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/customers")
async def list_customers():
    return [
        {**customer, "orders": store.get_orders(customer["account_number"])}
        for customer in store.list_customers()
    ]


class TokenRequest(BaseModel):
    account_number: str


@app.post("/api/token")
async def create_token(req: TokenRequest):
    room_name = f"support-{req.account_number}-{secrets.token_hex(4)}"
    token = (
        api.AccessToken()
        .with_identity(f"web-{req.account_number}")
        .with_name(req.account_number)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    return {"token": token, "url": os.environ["LIVEKIT_URL"], "room": room_name}


@app.get("/api/logs/stream")
async def stream_logs():
    """Server-Sent Events tail of logs/trace.jsonl, starting from end-of-file.

    Reopens the file if it gets deleted/recreated or truncated (e.g.
    `scripts/view_trace.py --clear`, or a fresh trace.jsonl after a restart)
    — otherwise a long-lived browser connection keeps reading from an
    orphaned file handle and silently never sees another line.
    """

    async def event_source():
        TRACE_FILE.parent.mkdir(exist_ok=True)
        TRACE_FILE.touch(exist_ok=True)

        f = open(TRACE_FILE)
        f.seek(0, os.SEEK_END)
        inode = os.fstat(f.fileno()).st_ino

        try:
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.strip()}\n\n"
                    continue

                try:
                    current = os.stat(TRACE_FILE)
                except FileNotFoundError:
                    current = None

                if current is None or current.st_ino != inode or current.st_size < f.tell():
                    f.close()
                    TRACE_FILE.touch(exist_ok=True)
                    f = open(TRACE_FILE)
                    inode = os.fstat(f.fileno()).st_ino
                    continue

                await asyncio.sleep(0.3)
        finally:
            f.close()

    return StreamingResponse(event_source(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
