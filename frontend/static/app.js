const { Room, RoomEvent, Track } = LivekitClient;

let selectedCustomer = null;
let room = null;

const customerListEl = document.getElementById("customer-list");
const customerDetailEl = document.getElementById("customer-detail");
const callBtn = document.getElementById("call-btn");
const callStatusEl = document.getElementById("call-status");
const traceLogEl = document.getElementById("trace-log");
const timelineLaneLabelsEl = document.getElementById("timeline-lane-labels");
const timelineScrollEl = document.getElementById("timeline-scroll");
const timelineTrackEl = document.getElementById("timeline-track");
const timelineRulerEl = document.getElementById("timeline-ruler");
const timelineCanvasEl = document.getElementById("timeline-canvas");
const timelineClockEl = document.getElementById("timeline-clock");

// Latency data that belongs ONLY on the timeline, never in the narrative text
// log (mixing them in there is what got called out as messy). The span_* pair
// draws every growing bar; metrics.stt is the open-mic heartbeat; metrics.eou
// is the turn-taking marker; expert.llm_call / metrics.{llm,tts} are legacy
// latency-only rows superseded by spans. expert.tool_call is deliberately NOT
// here — it stays in the narrative log so tool name/args/result are visible.
const TIMELINE_ONLY_EVENTS = new Set([
  "timeline.span_start",
  "timeline.span_end",
  "metrics.stt",
  "metrics.eou",
  "metrics.llm",
  "metrics.tts",
  "expert.llm_call",
]);

// Lane order top-to-bottom on the chronological timeline. The lane categories
// match the backend span `lane` values exactly (llm-orch / tts / llm-expert /
// tool); stt + eou are the heartbeat baseline and turn-taking marker.
const TIMELINE_LANES = [
  { category: "stt", label: "STT heartbeat" },
  { category: "llm-orch", label: "LLM (orchestrator)" },
  { category: "tts", label: "TTS" },
  { category: "eou", label: "turn-taking" },
  { category: "llm-expert", label: "LLM (expert)" },
  { category: "tool", label: "tool call" },
];
const TIMELINE_PIXELS_PER_MS = 0.06; // ~60px per second of call time
const TIMELINE_LEFT_PAD = 8; // px gap before x=0 so the first block/tick isn't flush against the edge
const TIMELINE_TICK_MS = 2000; // one time-axis gridline + label every 2s

// Real-time clock model: all on-screen geometry is positioned by the BROWSER's
// own receipt clock (performance.now()), not backend `ts`, so growth is smooth
// and immune to agent<->browser clock skew. callStartPerf is the single fixed
// origin (x=0); backend duration_ms is used only for a completed bar's final
// label (the authoritative number).
let callStartPerf = null; // performance.now() at call start / first timeline event
let callEndPerf = null; // performance.now() when the call ended; freezes the timeline
let timelineWidthPx = 0; // current scroll-content width; grows with wall-clock time
let lastAxisExtentPx = 0; // throttles axis rebuilds to ~once per tick interval
const openSpans = {}; // span_id -> { lane, label, startPerf, el, labelEl }
let playheadEl = null;

function xFor(perfMs) {
  return TIMELINE_LEFT_PAD + (perfMs - callStartPerf) * TIMELINE_PIXELS_PER_MS;
}

function laneCenterPct(category) {
  const laneIndex = TIMELINE_LANES.findIndex((lane) => lane.category === category);
  if (laneIndex < 0) return null;
  return ((laneIndex + 0.5) / TIMELINE_LANES.length) * 100;
}

function ensureOrigin() {
  if (callStartPerf === null) callStartPerf = performance.now();
}

// Format a completed bar's frozen label: "1.3s" once it's a second or more,
// else "420ms". The live counter always uses ms so it visibly ticks.
function formatDurationMs(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

// Header call-duration clock, e.g. "1:07.3".
function formatClock(ms) {
  const totalSec = ms / 1000;
  const m = Math.floor(totalSec / 60);
  const s = Math.floor(totalSec % 60);
  const tenths = Math.floor((ms % 1000) / 100);
  return `${m}:${String(s).padStart(2, "0")}.${tenths}`;
}

function nearRightEdge() {
  const gap = timelineScrollEl.scrollWidth - (timelineScrollEl.scrollLeft + timelineScrollEl.clientWidth);
  return gap < 60;
}

function initTimeline() {
  timelineLaneLabelsEl.innerHTML =
    `<div class="lane-spacer"></div>` +
    TIMELINE_LANES.map(
      (lane) => `<div class="lane"><span class="dot ${lane.category}"></span>${lane.label}</div>`
    ).join("");

  // Persistent per-lane center guides + the STT "mic is open the whole call"
  // baseline. Positioned by percentage of the canvas height, so they need no
  // pixel measurement and never need rebuilding.
  const laneCount = TIMELINE_LANES.length;
  TIMELINE_LANES.forEach((lane, i) => {
    const centerPct = ((i + 0.5) / laneCount) * 100;
    if (lane.category === "stt") {
      const baseline = document.createElement("div");
      baseline.className = "stt-baseline";
      baseline.style.top = `${centerPct}%`;
      baseline.title = "Mic channel is open for the whole call; STT flushes ~5s of audio throughput every 5s (silence included).";
      timelineCanvasEl.appendChild(baseline);
    } else {
      const guide = document.createElement("div");
      guide.className = "timeline-lane-guide";
      guide.style.top = `${centerPct}%`;
      timelineCanvasEl.appendChild(guide);
    }
  });

  // Vertical "now" playhead spanning ruler + all lanes, advanced every frame
  // by the animation loop so the eye has a moving marker of the current
  // wall-clock moment even when nothing is happening.
  playheadEl = document.createElement("div");
  playheadEl.className = "timeline-playhead";
  timelineTrackEl.appendChild(playheadEl);
}

// Redraw the time axis: a vertical gridline + "Ns" label every TIMELINE_TICK_MS,
// spanning the current scroll-content width. Cheap to rebuild wholesale since
// events are infrequent.
function renderTimeAxis() {
  timelineRulerEl.innerHTML = "";
  timelineCanvasEl.querySelectorAll(".timeline-gridline").forEach((el) => el.remove());
  // Cover the whole visible width even when the call so far fits on screen, so
  // the axis always reads as a full ruler rather than stopping at the last event.
  const extent = Math.max(timelineWidthPx, timelineScrollEl.clientWidth);
  if (extent <= 0) return;

  const tickPx = TIMELINE_TICK_MS * TIMELINE_PIXELS_PER_MS;
  for (let sec = 0, x = TIMELINE_LEFT_PAD; x <= extent; sec += TIMELINE_TICK_MS / 1000, x += tickPx) {
    const gridline = document.createElement("div");
    gridline.className = "timeline-gridline";
    gridline.style.left = `${x}px`;
    timelineCanvasEl.appendChild(gridline);

    const label = document.createElement("div");
    label.className = "timeline-tick-label";
    label.style.left = `${x}px`;
    label.textContent = `${sec}s`;
    timelineRulerEl.appendChild(label);
  }
}

async function loadCustomers() {
  const res = await fetch("/api/customers");
  const customers = await res.json();

  customerListEl.innerHTML = "";
  customers.forEach((customer) => {
    const card = document.createElement("div");
    card.className = "customer-card";
    card.innerHTML = `
      <div class="customer-name">${customer.name}</div>
      <div class="customer-account">${customer.account_number}</div>
      <span class="pill ${customer.subscription_status}">${customer.subscription_tier}</span>
    `;
    card.addEventListener("click", () => selectCustomer(customer, card));
    customerListEl.appendChild(card);
  });
}

function selectCustomer(customer, card) {
  selectedCustomer = customer;
  document.querySelectorAll(".customer-card").forEach((el) => el.classList.remove("selected"));
  card.classList.add("selected");

  const devicesHtml = customer.devices
    .map((d) => `<li>${d.name} <span class="badge ${d.status}">${d.status}</span></li>`)
    .join("");

  const ordersHtml =
    customer.orders.map((o) => `<li>${o.item} <span class="badge ${o.status}">${o.status}</span></li>`).join("") ||
    '<li class="empty">No orders</li>';

  customerDetailEl.innerHTML = `
    <h2>${customer.name}</h2>
    <p class="meta">${customer.account_number} · ${customer.phone_number}</p>
    <p class="meta">${customer.subscription_tier} — <span class="pill ${customer.subscription_status}">${customer.subscription_status}</span></p>
    <h3>Devices</h3>
    <ul class="device-list">${devicesHtml}</ul>
    <h3>Recent orders</h3>
    <ul class="order-list">${ordersHtml}</ul>
  `;

  if (!room) callBtn.disabled = false;
}

// --- Voice waveforms --------------------------------------------------------
// Two oscilloscope-style meters: one modulating with the caller's mic, one with
// the agent's synthesized speech. Each taps a LiveKit track's MediaStreamTrack
// into a Web Audio AnalyserNode and redraws the time-domain samples per frame.
let audioCtx = null;
const waveforms = {
  you: { canvas: null, analyser: null, raf: null, color: "#5b8def" },
  agent: { canvas: null, analyser: null, raf: null, color: "#3fb27f" },
};

function drawWaveform(wf) {
  const { canvas, analyser, color } = wf;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 200;
  const cssH = canvas.clientHeight || 32;
  if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const mid = cssH / 2;
  if (!analyser) {
    // Idle: flat centerline.
    ctx.strokeStyle = "rgba(255,255,255,0.15)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, mid);
    ctx.lineTo(cssW, mid);
    ctx.stroke();
    return;
  }

  const buf = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(buf);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < buf.length; i++) {
    const x = (i / (buf.length - 1)) * cssW;
    const y = mid + ((buf[i] - 128) / 128) * (mid - 2);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function runWaveform(wf) {
  const loop = () => {
    drawWaveform(wf);
    wf.raf = requestAnimationFrame(loop);
  };
  if (wf.raf === null) loop();
}

function attachWaveform(key, mediaStreamTrack) {
  if (!mediaStreamTrack) return;
  const wf = waveforms[key];
  if (!wf.canvas) wf.canvas = document.getElementById(key === "you" ? "wave-you" : "wave-agent");
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") audioCtx.resume();

  // Re-attaching (e.g. after a mic/device switch) — drop the old source first
  // so it isn't left connected to a dead/replaced MediaStreamTrack.
  if (wf.source) {
    try { wf.source.disconnect(); } catch (_) {}
  }

  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  const src = audioCtx.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
  src.connect(analyser); // NOT connected to destination — playback is handled elsewhere, this only reads
  wf.analyser = analyser;
  wf.source = src;
  runWaveform(wf);
}

function teardownWaveforms() {
  for (const key of Object.keys(waveforms)) {
    const wf = waveforms[key];
    if (wf.raf !== null) {
      cancelAnimationFrame(wf.raf);
      wf.raf = null;
    }
    if (wf.source) {
      try { wf.source.disconnect(); } catch (_) {}
      wf.source = null;
    }
    wf.analyser = null;
    if (wf.canvas) drawWaveform(wf); // one final paint -> flatline
  }
}

async function startCall() {
  if (!selectedCustomer || room) return;

  callBtn.disabled = true;
  callStatusEl.textContent = "connecting…";

  // Clear any prior call's timeline and anchor a fresh origin, so the header
  // timer and all bar geometry share one fixed origin for this call.
  resetTimeline();

  try {
    const res = await fetch("/api/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_number: selectedCustomer.account_number }),
    });
    if (!res.ok) throw new Error(`token request failed (${res.status})`);
    const { token, url } = await res.json();

    room = new Room();

    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) {
        const el = track.attach();
        el.autoplay = true;
        document.body.appendChild(el);
        // Tap the agent's audio for its waveform (playback stays via `el`).
        attachWaveform("agent", track.mediaStreamTrack);
      }
    });

    room.on(RoomEvent.ActiveDeviceChanged, (kind) => {
      // Fires when the OS default input/output device changes (e.g. the
      // caller switches mics, or unplugs/plugs a headset). LiveKit swaps the
      // underlying MediaStreamTrack for the mic publication, but our "you"
      // waveform had already grabbed a reference to the OLD track at call
      // start and never re-read it — flatlining on switch was the bug.
      if (kind !== "audioinput") return;
      const micPub = room?.localParticipant.getTrackPublication(Track.Source.Microphone);
      attachWaveform("you", micPub?.track?.mediaStreamTrack);
    });

    room.on(RoomEvent.Disconnected, () => {
      callStatusEl.textContent = "call ended";
      callBtn.textContent = "Start Call";
      callBtn.disabled = false;
      room = null;
      freezeTimeline();
      teardownWaveforms();
    });

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);

    // Tap the caller's mic for its waveform.
    const micPub = room.localParticipant.getTrackPublication(Track.Source.Microphone);
    attachWaveform("you", micPub?.track?.mediaStreamTrack);

    callStatusEl.textContent = `connected as ${selectedCustomer.name} — say hello!`;
    callBtn.textContent = "End Call";
    callBtn.disabled = false;
  } catch (err) {
    console.error(err);
    callStatusEl.textContent = `error: ${err.message}`;
    callBtn.disabled = false;
    room = null;
    freezeTimeline();
    teardownWaveforms();
  }
}

async function endCall() {
  if (room) {
    await room.disconnect();
    room = null;
  }
  callStatusEl.textContent = "idle";
  callBtn.textContent = "Start Call";
  freezeTimeline();
  teardownWaveforms();
}

callBtn.addEventListener("click", () => (room ? endCall() : startCall()));

function connectTraceStream() {
  const source = new EventSource("/api/logs/stream");
  source.onmessage = (e) => {
    const record = JSON.parse(e.data);
    const { event } = record;

    // Only draw on the timeline while a call is live (or before the first call,
    // for standalone testing). Once frozen by End Call, ignore trailing latency
    // events for the chart — but still log them to the narrative trace.
    if (callEndPerf === null) {
      switch (event) {
        case "timeline.span_start":
          handleSpanStart(record);
          break;
        case "timeline.span_end":
          handleSpanEnd(record);
          break;
        case "metrics.stt":
          handleSttHeartbeat(record);
          break;
        case "metrics.eou":
          handleEouMarker(record);
          break;
      }
    }
    if (!TIMELINE_ONLY_EVENTS.has(event)) {
      appendTraceLine(record);
    }
  };
}

function appendTraceLine(record) {
  const { ts, event, ...fields } = record;
  const time = new Date(ts * 1000).toLocaleTimeString();
  const fieldsText = Object.entries(fields)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join("  ");

  const line = document.createElement("div");
  line.className = "trace-line just-added";
  line.innerHTML = `<span class="trace-ts">${time}</span><span class="trace-event">${escapeHtml(event)}</span><span class="trace-fields">${escapeHtml(fieldsText)}</span>`;
  traceLogEl.appendChild(line);
  traceLogEl.scrollTop = traceLogEl.scrollHeight;
}

// Grow the scroll content to fit the furthest point on screen and (throttled)
// refresh the axis. Follow the playhead by scrolling ONLY when the user is
// already parked near the right edge — so scrolling back to inspect history is
// never yanked away.
function growTimeline(rightEdgePx, follow) {
  const needed = rightEdgePx + 40;
  if (needed > timelineWidthPx) {
    timelineWidthPx = needed;
    timelineTrackEl.style.width = `${needed}px`;
    maybeRenderAxis();
  }
  if (follow) {
    // Keep the playhead ~80px in from the right edge instead of snapping to the
    // very end, so newly opened bars have room to grow into view.
    const target = rightEdgePx - timelineScrollEl.clientWidth + 80;
    if (target > timelineScrollEl.scrollLeft) timelineScrollEl.scrollLeft = target;
  }
}

// Rebuild the ruler at most ~once per tick interval of growth (the axis is
// pixel-anchored to x=0, so it only needs redrawing when a new gridline is due)
// — cheap enough to survive being reachable from the per-frame loop.
function maybeRenderAxis() {
  const extent = Math.max(timelineWidthPx, timelineScrollEl.clientWidth);
  const tickPx = TIMELINE_TICK_MS * TIMELINE_PIXELS_PER_MS;
  if (lastAxisExtentPx === 0 || extent >= lastAxisExtentPx + tickPx) {
    renderTimeAxis();
    lastAxisExtentPx = extent;
  }
}

// Wipe the previous call's bars/heartbeats/gridlines and re-anchor the origin,
// so each call starts from a clean x=0. Keeps the structural guides/baseline/
// playhead (they live in the canvas/track but aren't per-call content).
function resetTimeline() {
  timelineCanvasEl
    .querySelectorAll(".timeline-block, .timeline-heartbeat, .timeline-gridline")
    .forEach((el) => el.remove());
  for (const id in openSpans) delete openSpans[id];
  timelineWidthPx = 0;
  lastAxisExtentPx = 0;
  timelineTrackEl.style.width = "";
  timelineScrollEl.scrollLeft = 0;
  callEndPerf = null;
  callStartPerf = performance.now();
  timelineClockEl.textContent = formatClock(0);
  renderTimeAxis();
}

// Freeze the timeline when the call ends: the header clock stops at the final
// duration, the canvas/playhead stop advancing (the animation loop gates on
// callEndPerf), and any spans still open get pinned + finalized. Idempotent.
function freezeTimeline() {
  if (callStartPerf === null || callEndPerf !== null) return;
  callEndPerf = performance.now();

  for (const id in openSpans) {
    const span = openSpans[id];
    const width = Math.max(2, xFor(callEndPerf) - xFor(span.startPerf));
    span.el.style.width = `${width}px`;
    span.el.classList.remove("running");
    span.el.classList.add("done");
    const finalMs = Math.round(callEndPerf - span.startPerf);
    span.labelEl.textContent = formatDurationMs(finalMs);
    delete openSpans[id];
  }

  timelineClockEl.textContent = formatClock(callEndPerf - callStartPerf);
  if (playheadEl) playheadEl.style.left = `${xFor(callEndPerf)}px`;
}

// --- Span lifecycle ---------------------------------------------------------

function handleSpanStart(record) {
  ensureOrigin();
  const { span_id, lane, label } = record;
  const centerPct = laneCenterPct(lane);
  if (centerPct === null) return; // unknown lane — ignore gracefully

  const startPerf = performance.now();
  const left = xFor(startPerf);

  const block = document.createElement("div");
  block.className = `timeline-block ${lane} running`;

  const detail = label || record.tool || record.expert || lane;
  block.title = detail;
  block.style.left = `${left}px`;
  block.style.width = "0px";
  // Slim bar vertically centered in its lane, with breathing room above/below.
  block.style.top = `calc(${centerPct}% - 5px)`;

  const labelEl = document.createElement("div");
  labelEl.className = "timeline-block-label";
  labelEl.textContent = "0ms";
  block.appendChild(labelEl);

  timelineCanvasEl.appendChild(block);
  openSpans[span_id] = { lane, label: detail, startPerf, el: block, labelEl };
}

function handleSpanEnd(record) {
  const span = openSpans[record.span_id];
  if (!span) return; // end for an unknown/never-opened span — ignore gracefully

  const endPerf = performance.now();
  const right = xFor(endPerf);
  const width = Math.max(2, right - xFor(span.startPerf));
  span.el.style.width = `${width}px`;
  span.el.classList.remove("running");
  span.el.classList.add("done");

  // The backend's measured duration_ms is authoritative for the frozen label;
  // fall back to the receipt-clock delta if it wasn't provided.
  const finalMs = record.duration_ms != null ? record.duration_ms : Math.round(endPerf - span.startPerf);
  const text = formatDurationMs(finalMs);
  span.labelEl.textContent = text;
  span.el.title = `${span.label} · ${text}`;

  delete openSpans[record.span_id];
  growTimeline(right, nearRightEdge());
}

// STT is NOT a discrete step — LiveKit/Deepgram flushes ~5s of open-mic audio
// throughput every 5s regardless of whether anyone spoke. Draw a slim, faint
// heartbeat tick on the persistent STT baseline, deliberately a different
// visual language from the growing per-turn bars.
function handleSttHeartbeat(record) {
  ensureOrigin();
  const durationMs = record.audio_duration_ms ?? 0;
  const centerPct = laneCenterPct("stt");
  const x = xFor(performance.now());
  const tick = document.createElement("div");
  tick.className = "timeline-heartbeat";
  tick.title = `STT heartbeat · ~${Math.round(durationMs / 1000)}s of open-mic audio flushed (includes silence, not a spoken turn)`;
  tick.style.left = `${x}px`;
  tick.style.top = `calc(${centerPct}% - 6px)`;
  timelineCanvasEl.appendChild(tick);
  // Extend the axis to keep pace with the mic, but don't chase the view to an
  // ambient tick.
  growTimeline(x + 2, false);
}

// Turn-taking is an after-the-fact instant measurement, not a live operation:
// draw a short static marker bar ending at the current playhead, labelled with
// the end-of-utterance delay.
function handleEouMarker(record) {
  ensureOrigin();
  const delayMs = record.end_of_utterance_delay_ms ?? 0;
  const centerPct = laneCenterPct("eou");
  const endX = xFor(performance.now());
  const width = Math.max(3, delayMs * TIMELINE_PIXELS_PER_MS);

  const block = document.createElement("div");
  block.className = "timeline-block eou done just-added";
  const text = formatDurationMs(delayMs);
  block.title = `turn-taking · ${text}`;
  block.style.left = `${endX - width}px`;
  block.style.width = `${width}px`;
  block.style.top = `calc(${centerPct}% - 5px)`;

  const labelEl = document.createElement("div");
  labelEl.className = "timeline-block-label";
  labelEl.textContent = text;
  block.appendChild(labelEl);

  timelineCanvasEl.appendChild(block);
  growTimeline(endX, nearRightEdge());
}

// --- Wall-clock animation loop ----------------------------------------------
// Advances "now" every frame so the header timer ticks, the canvas grows
// rightward, the playhead moves, and every OPEN span's bar grows + its live ms
// counter updates — all continuously, even when no new events are arriving.
function animateTimeline() {
  // Advance only while a call is live: started, and not yet frozen by End Call.
  if (callStartPerf !== null && callEndPerf === null) {
    const nowPerf = performance.now();
    const elapsed = nowPerf - callStartPerf;
    timelineClockEl.textContent = formatClock(elapsed);

    const nowX = xFor(nowPerf);
    const follow = nearRightEdge();
    growTimeline(nowX, follow);
    if (playheadEl) playheadEl.style.left = `${nowX}px`;

    for (const id in openSpans) {
      const span = openSpans[id];
      const left = xFor(span.startPerf);
      span.el.style.width = `${Math.max(2, nowX - left)}px`;
      span.labelEl.textContent = `${Math.round(nowPerf - span.startPerf)}ms`;
    }
  }
  requestAnimationFrame(animateTimeline);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

initTimeline();
renderTimeAxis();
window.addEventListener("resize", renderTimeAxis);
loadCustomers();
connectTraceStream();
requestAnimationFrame(animateTimeline);

// Paint idle flatlines so the waveform meters read as present-but-quiet before
// (and between) calls, rather than blank boxes.
for (const key of Object.keys(waveforms)) {
  waveforms[key].canvas = document.getElementById(key === "you" ? "wave-you" : "wave-agent");
  drawWaveform(waveforms[key]);
}
