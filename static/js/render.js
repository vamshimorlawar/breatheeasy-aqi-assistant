/** Renders a plan response into the results section (textContent only, never innerHTML). */

import { $, el } from "./dom.js";

const STATUS_TEXT = { go: "Go", caution: "Caution", avoid: "Avoid" };
const TREND_TEXT = {
  improving: "📉 Air is expected to improve over the next few hours.",
  worsening: "📈 Air is expected to get worse over the next few hours.",
  steady: "➡️ Air should stay about the same for the next few hours.",
  unknown: "",
};
const MIN_CHART_MAX_AQI = 200; // keeps bars comparable on clean-air days

export function renderPlan({ baseline, ai, ai_status: aiStatus, share_message: share }) {
  $("#results").hidden = false;
  $("#res-place").textContent = baseline.location_label;
  renderBriefing(ai, aiStatus);
  renderCurrent(baseline);
  renderWindow(baseline);
  renderForecast(baseline);
  renderMembers(baseline.members, ai?.member_notes || []);
  $("#home-actions").replaceChildren(...baseline.household_actions.map((a) => el("li", { text: a })));
  $("#share-text").value = share;
  $("#whatsapp-link").href = `https://wa.me/?text=${encodeURIComponent(share)}`;
  $("#disclaimer").textContent = baseline.disclaimer;
  $("#answer").textContent = "";
}

/** AI briefing, falling back gracefully to rule-based guidance. */
function renderBriefing(ai, aiStatus) {
  $("#ai-card").classList.toggle("unavailable", !ai);
  if (ai) {
    $("#ai-headline").textContent = ai.headline;
    $("#ai-summary").textContent = ai.summary;
    return;
  }
  $("#ai-headline").textContent = "Your safety plan";
  $("#ai-summary").textContent =
    aiStatus === "disabled"
      ? "AI briefing is turned off. Below is the standard safety guidance for your family."
      : "The AI briefing is unavailable right now. Below is the standard safety guidance for your family.";
}

function renderCurrent(baseline) {
  const current = baseline.current;
  $("#aqi-number").textContent = current.aqi ?? "–";
  const badge = $("#aqi-band");
  badge.textContent = current.band ? current.band.label : "Unknown";
  badge.style.setProperty("--band", current.band ? current.band.color : "");
  const parts = [];
  if (current.pm2_5 != null) parts.push(`PM2.5 ${current.pm2_5} µg/m³`);
  if (current.pm10 != null) parts.push(`PM10 ${current.pm10} µg/m³`);
  $("#aqi-detail").textContent = parts.join(" · ");
  $("#aqi-trend").textContent = TREND_TEXT[baseline.trend] || "";
}

function renderWindow(baseline) {
  const win = baseline.best_window;
  $("#best-window").textContent = win ? `${win.label} (AQI ~${win.avg_aqi})` : "Not enough forecast data";
  $("#ventilation").textContent = baseline.ventilation;
}

/** 24-hour forecast as an accessible list (bars are decorative; values are text). */
function renderForecast(baseline) {
  const maxAqi = Math.max(MIN_CHART_MAX_AQI, ...baseline.hourly.map((h) => h.aqi || 0));
  const bestStart = baseline.best_window?.start ?? null;
  $("#forecast").replaceChildren(...baseline.hourly.map((hour) => forecastItem(hour, maxAqi, bestStart)));
}

function forecastItem(hour, maxAqi, bestStart) {
  const bar = el("span", { className: "bar", "aria-hidden": "true" });
  bar.style.setProperty("--band", hour.band ? hour.band.color : "");
  bar.style.width = `${Math.max(2, ((hour.aqi || 0) / maxAqi) * 100)}%`;
  const band = hour.band ? hour.band.label : "no data";
  const item = el("li", {}, [
    el("span", { text: hour.label }),
    bar,
    el("span", { className: "val", text: hour.aqi != null ? `${hour.aqi} · ${band}` : "no data" }),
  ]);
  if (hour.time === bestStart) {
    item.classList.add("best");
    item.append(el("span", { className: "visually-hidden", text: " (start of cleanest window)" }));
  }
  return item;
}

function renderMembers(members, memberNotes) {
  const notes = new Map(memberNotes.map((n) => [n.nickname, n.note]));
  $("#member-plans").replaceChildren(...members.map((member) => memberCard(member, notes.get(member.nickname))));
}

function memberCard(member, note) {
  const card = el("article", { className: "card member-plan" });
  card.append(
    el("h4", {}, [
      el("span", { text: member.nickname }),
      el("span", { className: `pill ${member.overall_status}`, text: STATUS_TEXT[member.overall_status] }),
    ]),
  );
  if (note) card.append(el("p", { className: "note", text: note }));
  card.append(el("p", {}, [el("strong", { text: "Mask: " }), member.mask]));
  if (member.activities.length) {
    const items = member.activities.map((a) =>
      el("li", {}, [el("strong", { text: `${a.label} (${STATUS_TEXT[a.status]}): ` }), a.advice]),
    );
    card.append(el("ul", {}, items));
  }
  for (const warning of member.warnings) card.append(el("p", { className: "warn", text: `⚠️ ${warning}` }));
  return card;
}
