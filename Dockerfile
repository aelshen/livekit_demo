# Single image shared by all three services (mcp-server, agent, frontend) —
# same dependencies, different entrypoints. See docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-fetches model files the LiveKit plugins need at runtime (Silero VAD,
# the turn-detector's ONNX model) — without this the agent container fetches
# them lazily on first job and the turn detector errors out until it does.
RUN python -m livekit.agents download-files

# Overridden per-service by docker-compose.yml's `command:`.
CMD ["python", "-m", "mcp_server.server", "--http", "--port", "8089"]
