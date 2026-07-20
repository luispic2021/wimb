const directionSelect = document.querySelector("#direction");
const stopSelect = document.querySelector("#stop");
const refreshButton = document.querySelector("#refresh");
const statusPanel = document.querySelector("#status");

const stateMessages = {
  no_service: ["Route 154 is not operating now", "There are no applicable timetable runs for this service day."],
  no_live_vehicles: ["No live Route 154 vehicle", "The timetable is available, but 511 is not reporting a live Route 154 vehicle."],
  no_usable_realtime_data: ["Live vehicle, limited evidence", "A vehicle exists, but WIMB cannot support a factual deviation at a completed stop yet."],
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error?.message || "WIMB could not complete the request.");
    error.code = payload.error?.code || "request_failed";
    throw error;
  }
  return payload;
}

function showState(title, copy, isError = false) {
  statusPanel.setAttribute("aria-busy", "false");
  statusPanel.innerHTML = `
    <div class="state-card${isError ? " error" : ""}">
      <p class="state-title">${escapeHtml(title)}</p>
      <p class="state-copy">${escapeHtml(copy)}</p>
    </div>`;
}

function showLoading(copy) {
  statusPanel.setAttribute("aria-busy", "true");
  statusPanel.innerHTML = `
    <div class="state-card">
      <div class="pulse" aria-hidden="true"></div>
      <p class="state-title">Checking Route 154…</p>
      <p class="state-copy">${escapeHtml(copy)}</p>
    </div>`;
}

function errorState(error) {
  const known = {
    realtime_feed_stale: ["Realtime feed is stale", "WIMB will not present old observations as live. Try again shortly."],
    upstream_authentication_failed: ["Transit service unavailable", "The server cannot authenticate with 511 right now."],
    upstream_temporarily_unavailable: ["511 is temporarily unavailable", "Please try refreshing in a moment."],
    server_not_ready: ["WIMB is not ready", "Server configuration needs attention."],
  };
  const [title, copy] = known[error.code] || ["Couldn’t load bus status", error.message];
  showState(title, copy, true);
}

function formatTime(value) {
  return new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function busCard(bus) {
  const evidence = bus.evidence_stop_name
    ? `${escapeHtml(bus.deviation_label)} as of ${escapeHtml(bus.evidence_stop_name)}`
    : escapeHtml(bus.deviation_label);
  const vehicle = bus.vehicle_id ? ` · Vehicle ${escapeHtml(bus.vehicle_id)}` : "";
  return `
    <article class="bus-card">
      <div class="bus-topline">
        <h3 class="bus-identity">Bus ${bus.run_number} of ${bus.run_total}</h3>
        <span class="scheduled">Scheduled ${formatTime(bus.scheduled_time)}</span>
      </div>
      <p class="deviation">${evidence}</p>
      <p class="evidence">${escapeHtml(bus.freshness)}${vehicle}</p>
    </article>`;
}

function renderStatus(payload) {
  statusPanel.setAttribute("aria-busy", "false");
  const stateMessage = stateMessages[payload.data_status];
  const notice = stateMessage
    ? `<div class="state-card"><p class="state-title">${stateMessage[0]}</p><p class="state-copy">${stateMessage[1]}</p></div>`
    : "";
  const cards = payload.buses.map(busCard).join("");
  const exhausted = payload.no_additional_buses
    ? `<div class="state-card"><p class="state-title">End of today’s timetable</p><p class="state-copy">No additional buses are scheduled in this direction today.</p></div>`
    : "";
  statusPanel.innerHTML = `
    <p class="status-heading">${escapeHtml(payload.direction_label)} · ${escapeHtml(payload.stop_name)}</p>
    ${notice}${cards}${exhausted}`;
}

async function loadStatus() {
  if (!directionSelect.value || !stopSelect.value) return;
  refreshButton.disabled = true;
  showLoading("Reading the latest confirmed evidence.");
  try {
    const query = new URLSearchParams({
      direction_id: directionSelect.value,
      stop_id: stopSelect.value,
    });
    renderStatus(await api(`/api/v1/routes/154/status?${query}`));
  } catch (error) {
    errorState(error);
  } finally {
    refreshButton.disabled = false;
  }
}

async function loadStops() {
  stopSelect.disabled = true;
  refreshButton.disabled = true;
  showLoading("Loading stops in timetable order.");
  try {
    const payload = await api(`/api/v1/routes/154/stops?direction_id=${encodeURIComponent(directionSelect.value)}`);
    stopSelect.innerHTML = payload.stops
      .map((stop) => `<option value="${escapeHtml(stop.stop_id)}">${escapeHtml(stop.name)}</option>`)
      .join("");
    stopSelect.disabled = payload.stops.length === 0;
    if (payload.stops.length) await loadStatus();
    else showState("No stops found", "This direction has no published Route 154 stops.");
  } catch (error) {
    errorState(error);
  }
}

async function start() {
  try {
    const payload = await api("/api/v1/routes/154/directions");
    directionSelect.innerHTML = payload.directions
      .map((direction) => `<option value="${direction.direction_id}">${escapeHtml(direction.label)}</option>`)
      .join("");
    directionSelect.disabled = false;
    await loadStops();
  } catch (error) {
    directionSelect.innerHTML = "<option>Unavailable</option>";
    errorState(error);
  }
}

directionSelect.addEventListener("change", loadStops);
stopSelect.addEventListener("change", loadStatus);
refreshButton.addEventListener("click", loadStatus);
start();
