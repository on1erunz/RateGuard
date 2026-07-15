const state = { observations: [], historyDays: 30, activeCheckinFrom: "", tab: "current" };
const statusLabel = { available: "可售", sold_out: "售罄", manual_review: "待人工核验", price_hidden: "价格待核验", error: "采集异常" };
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "-").replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c]);
const money = (value) => value == null || value === "" ? "-" : `¥${Number(value).toFixed(0)}`;
const displayTime = (value) => value ? String(value).replace("T", " ") : "-";

function setOptions(element, values, label) {
  const current = element.value;
  element.innerHTML = `<option value="">全部${label}</option>` + values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  if (values.includes(current)) element.value = current;
}

function latest(rows) {
  const byKey = new Map();
  [...rows].sort((a, b) => String(a.fetched_at).localeCompare(String(b.fetched_at))).forEach((row) => {
    byKey.set([row.platform, row.hotel_id, row.room_id, row.rate_plan_key, row.checkin].join("|"), row);
  });
  return [...byKey.values()].sort((a, b) => String(a.hotel_name).localeCompare(String(b.hotel_name)) || Number(a.price_yuan ?? Infinity) - Number(b.price_yuan ?? Infinity));
}

function filteredRows() {
  const platform = $("#platform-filter").value;
  const hotel = $("#hotel-filter").value;
  const checkin = $("#date-filter").value;
  return state.observations.filter((r) => (!platform || r.platform === platform) && (!hotel || r.hotel_name === hotel) && (!checkin || r.checkin === checkin));
}

function activeRows(rows) {
  return rows.filter((row) => !state.activeCheckinFrom || String(row.checkin) >= state.activeCheckinFrom);
}

function hotelCell(row) {
  const own = row.is_own_hotel ? '<span class="own-badge">本店</span> ' : "";
  return `${own}${escapeHtml(row.hotel_name)}`;
}

function renderTable(target, rows) {
  const headers = ["平台", "酒店", "入住日期", "房型", "当前价格", "上一轮", "变动", "状态", "更新时间"];
  const body = rows.map((r) => {
    const delta = r.price_delta_yuan == null ? "-" : `${Number(r.price_delta_yuan) > 0 ? "+" : ""}${Number(r.price_delta_yuan).toFixed(0)}`;
    const deltaClass = Number(r.price_delta_yuan) > 0 ? "up" : Number(r.price_delta_yuan) < 0 ? "down" : "";
    return `<tr><td>携程</td><td>${hotelCell(r)}</td><td>${escapeHtml(r.checkin)}</td><td>${escapeHtml(r.room_name)}</td><td class="number">${money(r.price_yuan)}</td><td class="number">${money(r.previous_price_yuan)}</td><td class="number ${deltaClass}">${delta}</td><td><span class="status ${escapeHtml(r.status)}">${statusLabel[r.status] || escapeHtml(r.status)}</span></td><td>${displayTime(r.fetched_at)}</td></tr>`;
  }).join("") || `<tr><td colspan="9">没有符合条件的记录。</td></tr>`;
  target.innerHTML = `<thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
}

function lowestByHotel(rows) {
  return [...rows.reduce((map, row) => {
    const previous = map.get(row.hotel_id);
    if (!previous || Number(row.price_yuan) < Number(previous.price_yuan)) map.set(row.hotel_id, row);
    return map;
  }, new Map()).values()];
}

function render() {
  const allRows = filteredRows();
  const current = latest(activeRows(allRows));
  const available = current.filter((r) => r.status === "available");
  const ownLowest = lowestByHotel(available.filter((r) => r.is_own_hotel))[0];
  const changed = current.filter((r) => Math.abs(Number(r.price_delta_yuan || 0)) >= 10);
  $("#metrics").innerHTML = [
    ["已监测酒店", new Set(current.map((r) => r.hotel_id)).size],
    ["本店引流价", money(ownLowest?.price_yuan)],
    ["当前可售价格计划", available.length],
    ["本轮变动 ≥ ¥10", changed.length],
  ].map(([label, value]) => `<div class="metric"><p>${label}</p><strong>${value}</strong></div>`).join("");

  const lows = lowestByHotel(available).sort((a, b) => Number(a.price_yuan) - Number(b.price_yuan));
  const max = Math.max(...lows.map((r) => Number(r.price_yuan)), 1);
  $("#lowest-prices").innerHTML = lows.map((r) => {
    const own = r.is_own_hotel;
    const label = `${own ? "本店引流价 · " : ""}${escapeHtml(r.hotel_name)}`;
    return `<div class="bar-row ${own ? "own-price" : ""}"><div class="bar-label"><span>${label}</span><strong>${money(r.price_yuan)}</strong></div><div class="track"><div class="fill" style="width:${(Number(r.price_yuan) / max) * 100}%"></div></div></div>`;
  }).join("") || "暂无可售价格。";
  renderTable($("#current-table"), current);
  renderTable($("#history-table"), [...allRows].sort((a, b) => String(b.fetched_at).localeCompare(String(a.fetched_at))));
  renderTable($("#status-table"), current.filter((r) => r.status !== "available"));
}

function applyData(data) {
  state.observations = data.observations || [];
  state.historyDays = data.history_days || 30;
  state.activeCheckinFrom = data.active_checkin_from || "";
  $("#history-days").textContent = state.historyDays;
  const rows = state.observations;
  setOptions($("#platform-filter"), ["ctrip"], "平台");
  setOptions($("#hotel-filter"), [...new Set(rows.map((r) => r.hotel_name))].sort(), "酒店");
  setOptions($("#date-filter"), [...new Set(rows.map((r) => r.checkin))].sort(), "入住日期");
  $("#updated-at").textContent = `数据更新时间：${displayTime(data.generated_at)}（最近 ${data.history_days} 天）`;
  $("#active-window").textContent = state.activeCheckinFrom ? `当前价格仅显示入住日期不早于 ${state.activeCheckinFrom} 的数据；历史记录仍可查询。` : "";
  render();
}

async function load() {
  $("#error").hidden = true;
  try {
    const response = await fetch(`/api/dashboard?at=${Date.now()}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "读取失败");
    applyData(data);
  } catch (error) {
    $("#error").textContent = `看板数据暂不可用：${error.message}`;
    $("#error").hidden = false;
  }
}

document.querySelectorAll(".tabs button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".tabs button, .tab-panel").forEach((el) => el.classList.remove("active"));
  button.classList.add("active");
  $(`#${button.dataset.tab}`).classList.add("active");
}));
["#platform-filter", "#hotel-filter", "#date-filter"].forEach((selector) => $(selector).addEventListener("change", render));
$("#refresh").addEventListener("click", load);
$("#download-future-prices").addEventListener("click", () => {
  window.location.href = `/api/future-room-prices?at=${Date.now()}`;
});
load();
