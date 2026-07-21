const { Room, RoomEvent, Track } = LivekitClient;

let selectedCustomer = null;
let room = null;

const customerListEl = document.getElementById("customer-list");
const customerDetailEl = document.getElementById("customer-detail");
const callBtn = document.getElementById("call-btn");
const callStatusEl = document.getElementById("call-status");
const traceLogEl = document.getElementById("trace-log");

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

async function startCall() {
  if (!selectedCustomer || room) return;

  callBtn.disabled = true;
  callStatusEl.textContent = "connecting…";

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
      }
    });

    room.on(RoomEvent.Disconnected, () => {
      callStatusEl.textContent = "call ended";
      callBtn.textContent = "Start Call";
      callBtn.disabled = false;
      room = null;
    });

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);

    callStatusEl.textContent = `connected as ${selectedCustomer.name} — say hello!`;
    callBtn.textContent = "End Call";
    callBtn.disabled = false;
  } catch (err) {
    console.error(err);
    callStatusEl.textContent = `error: ${err.message}`;
    callBtn.disabled = false;
    room = null;
  }
}

async function endCall() {
  if (room) {
    await room.disconnect();
    room = null;
  }
  callStatusEl.textContent = "idle";
  callBtn.textContent = "Start Call";
}

callBtn.addEventListener("click", () => (room ? endCall() : startCall()));

function connectTraceStream() {
  const source = new EventSource("/api/logs/stream");
  source.onmessage = (e) => {
    const record = JSON.parse(e.data);
    const { ts, event, ...fields } = record;
    const time = new Date(ts * 1000).toLocaleTimeString();
    const fieldsText = Object.entries(fields)
      .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
      .join("  ");

    const line = document.createElement("div");
    line.className = "trace-line";
    line.innerHTML = `<span class="trace-ts">${time}</span><span class="trace-event">${escapeHtml(event)}</span><span class="trace-fields">${escapeHtml(fieldsText)}</span>`;
    traceLogEl.appendChild(line);
    traceLogEl.scrollTop = traceLogEl.scrollHeight;
  };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

loadCustomers();
connectTraceStream();
