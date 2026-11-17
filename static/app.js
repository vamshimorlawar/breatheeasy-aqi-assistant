"use strict";

/*
 * BreatheEasy front end. Plain JS, no build step.
 * All dynamic text is inserted with textContent (never innerHTML) to prevent XSS.
 * The household profile is stored only in this browser (localStorage).
 */
(() => {
  const STORAGE_KEY = "breatheeasy.profile.v1";
  const MAX_MEMBERS = 8;
  const STATUS_TEXT = { go: "Go", caution: "Caution", avoid: "Avoid" };
  const TREND_TEXT = {
    improving: "📉 Air is expected to improve over the next few hours.",
    worsening: "📈 Air is expected to get worse over the next few hours.",
    steady: "➡️ Air should stay about the same for the next few hours.",
    unknown: "",
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = { location: null, lastRequest: null };

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === "text") node.textContent = value;
      else if (key === "className") node.className = value;
      else node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) if (child) node.append(child);
    return node;
  }

  function announce(message, isError = false) {
    const status = $("#status");
    status.textContent = message;
    status.classList.toggle("error", isError);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let data = null;
    try { data = await response.json(); } catch { /* non-JSON body */ }
    if (!response.ok) {
      let message = "Something went wrong. Please try again.";
      if (data && typeof data.detail === "string") message = data.detail;
      else if (data && Array.isArray(data.detail) && data.detail[0]?.msg) {
        message = "Please check your details: " + data.detail[0].msg.replace(/^Value error, /, "");
      }
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  // If the server's shared data quota is used up (it answers 502), the browser fetches the
  // public forecast itself and sends only the fields the server's strict schema accepts.
  const AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality";
  const AIR_FIELDS = ["time", "us_aqi", "pm2_5", "pm10"];

  function pick(source, keys) {
    const out = {};
    for (const key of keys) if (source && key in source) out[key] = source[key];
    return out;
  }

  async function fetchAirInBrowser(location) {
    const params = new URLSearchParams({
      latitude: location.latitude.toFixed(2), // ~1 km grid, same privacy rounding as the server
      longitude: location.longitude.toFixed(2),
      hourly: "us_aqi,pm2_5,pm10",
      current: "us_aqi,pm2_5,pm10",
      forecast_days: "3",
      timezone: "auto",
    });
    const response = await fetch(`${AIR_URL}?${params}`, { credentials: "omit", referrerPolicy: "no-referrer" });
    if (!response.ok) throw new Error("Air-quality data is unavailable right now. Please try again later.");
    const data = await response.json();
    return {
      timezone: typeof data.timezone === "string" ? data.timezone : "auto",
      current: pick(data.current, AIR_FIELDS),
      hourly: pick(data.hourly, AIR_FIELDS),
    };
  }

  async function postWithFallback(path, body) {
    const send = () => api(path, { method: "POST", body: JSON.stringify(body) });
    try {
      return await send();
    } catch (err) {
      if (err.status !== 502 || body.air) throw err;
      announce("Our data source is busy, fetching the forecast directly…");
      body.air = await fetchAirInBrowser(body.location);
      return send();
    }
  }

  // ------------------------------------------------------------ storage

  function saveProfile() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        location: state.location,
        members: readMembers(),
        home: readHome(),
        language: $("#language").value,
      }));
    } catch { /* storage unavailable (private mode): app still works */ }
  }

  function loadProfile() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    } catch {
      return null;
    }
  }

  // ------------------------------------------------------------ location

  function setLocation(location) {
    state.location = location;
    $("#selected-location").textContent = location
      ? `📍 Selected: ${location.label}`
      : "No location selected yet.";
    saveProfile();
  }

  function placeLabel(place) {
    return [place.name, place.admin1, place.country].filter(Boolean).join(", ").slice(0, 80);
  }

  async function onSearch(event) {
    event.preventDefault();
    const query = $("#city-input").value.trim();
    const list = $("#search-results");
    list.replaceChildren();
    if (query.length < 2) {
      announce("Type at least 2 letters of a city name.", true);
      return;
    }
    announce("Searching…");
    try {
      const places = await api(`/api/geocode?q=${encodeURIComponent(query)}`);
      if (!places.length) {
        announce(`No places found for "${query}".`, true);
        return;
      }
      for (const place of places) {
        const label = placeLabel(place);
        const btn = el("button", { type: "button", className: "btn btn-secondary", text: label });
        btn.addEventListener("click", () => {
          setLocation({ latitude: place.latitude, longitude: place.longitude, label: safeLabel(label) });
          list.replaceChildren();
          announce(`Location set to ${label}.`);
          $("#plan-btn").focus();
        });
        list.append(el("li", {}, btn));
      }
      announce(`${places.length} place(s) found. Choose one below.`);
      list.querySelector("button")?.focus();
    } catch (err) {
      announce(err.message, true);
    }
  }

  function safeLabel(label) {
    // Mirror the server's allowed characters for place labels.
    const cleaned = label.replace(/[^\p{L}\p{M}\p{N}_ ,.'()-]/gu, "").trim().slice(0, 80);
    return cleaned || "Selected place";
  }

  function onGeolocate() {
    if (!("geolocation" in navigator)) {
      announce("Location is not available in this browser. Please search for your city.", true);
      return;
    }
    announce("Finding your location…");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({
          latitude: Number(pos.coords.latitude.toFixed(3)),
          longitude: Number(pos.coords.longitude.toFixed(3)),
          label: "your location",
        });
        announce("Location set to your current position.");
      },
      () => announce("Couldn't get your location. Please search for your city instead.", true),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 },
    );
  }

  // ------------------------------------------------------------ household

  function addMember(data = {}) {
    const container = $("#members");
    if (container.children.length >= MAX_MEMBERS) {
      announce(`You can add up to ${MAX_MEMBERS} family members.`, true);
      return;
    }
    const node = $("#member-template").content.firstElementChild.cloneNode(true);
    $(".m-name", node).value = data.nickname || "";
    $(".m-age", node).value = data.age_group || "adult";
    for (const box of $$(".m-risk", node)) box.checked = (data.risk_factors || []).includes(box.value);
    for (const box of $$(".m-act", node)) box.checked = (data.activities || []).includes(box.value);
    $(".m-remove", node).addEventListener("click", () => {
      node.remove();
      renumberMembers();
      saveProfile();
      announce("Family member removed.");
      $("#add-member").focus();
    });
    node.addEventListener("change", saveProfile);
    container.append(node);
    renumberMembers();
    return node;
  }

  function renumberMembers() {
    const members = $$("#members .member");
    members.forEach((node, i) => {
      $(".member-legend", node).textContent = `Family member ${i + 1}`;
      $(".m-remove", node).hidden = members.length === 1;
      $(".m-remove", node).setAttribute("aria-label", `Remove family member ${i + 1}`);
    });
  }

  function readMembers() {
    return $$("#members .member").map((node) => ({
      nickname: $(".m-name", node).value.trim(),
      age_group: $(".m-age", node).value,
      risk_factors: $$(".m-risk:checked", node).map((b) => b.value),
      activities: $$(".m-act:checked", node).map((b) => b.value),
    }));
  }

  function readHome() {
    return {
      has_purifier: $("#has-purifier").checked,
      has_n95: $("#has-n95").checked,
      has_ac: $("#has-ac").checked,
    };
  }

  function validate(members) {
    if (!state.location) return "Please choose your location first (step 1).";
    if (!members.length) return "Please add at least one family member.";
    const seen = new Set();
    for (const [i, m] of members.entries()) {
      if (!/^[\p{L}\p{M}\p{N}_][\p{L}\p{M}\p{N}_ .'-]{0,29}$/u.test(m.nickname)) {
        $$("#members .m-name")[i].focus();
        return `Family member ${i + 1} needs a nickname (letters, numbers and spaces only).`;
      }
      const key = m.nickname.toLowerCase();
      if (seen.has(key)) return `Two family members are called "${m.nickname}". Please use different nicknames.`;
      seen.add(key);
    }
    return null;
  }

  // ------------------------------------------------------------ plan

  async function onPlan() {
    const members = readMembers();
    const problem = validate(members);
    if (problem) {
      announce(problem, true);
      return;
    }
    saveProfile();
    const body = {
      location: state.location,
      members,
      home: readHome(),
      language: $("#language").value,
    };
    const button = $("#plan-btn");
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    announce("Checking the air and preparing your plan…");
    try {
      const plan = await postWithFallback("/api/plan", body);
      state.lastRequest = body;
      renderPlan(plan);
      announce("Your plan is ready.");
      $("#results-heading").focus();
    } catch (err) {
      announce(err.message, true);
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  function renderPlan({ baseline, ai, ai_status: aiStatus, share_message: share }) {
    $("#results").hidden = false;
    $("#res-place").textContent = baseline.location_label;

    // AI briefing (falls back gracefully to rule-based guidance)
    const aiCard = $("#ai-card");
    aiCard.classList.toggle("unavailable", !ai);
    if (ai) {
      $("#ai-headline").textContent = ai.headline;
      $("#ai-summary").textContent = ai.summary;
    } else {
      $("#ai-headline").textContent = "Your safety plan";
      $("#ai-summary").textContent = aiStatus === "disabled"
        ? "AI briefing is turned off. Below is the standard safety guidance for your family."
        : "The AI briefing is unavailable right now. Below is the standard safety guidance for your family.";
    }

    // Current air
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

    // Best window and ventilation
    const win = baseline.best_window;
    $("#best-window").textContent = win ? `${win.label} (AQI ~${win.avg_aqi})` : "Not enough forecast data";
    $("#ventilation").textContent = baseline.ventilation;

    // 24h forecast as an accessible list
    const maxAqi = Math.max(200, ...baseline.hourly.map((h) => h.aqi || 0));
    const bestStart = win ? win.start : null;
    const forecast = $("#forecast");
    forecast.replaceChildren(...baseline.hourly.map((h) => {
      const bar = el("span", { className: "bar", "aria-hidden": "true" });
      bar.style.setProperty("--band", h.band ? h.band.color : "");
      bar.style.width = `${Math.max(2, ((h.aqi || 0) / maxAqi) * 100)}%`;
      const band = h.band ? h.band.label : "no data";
      const item = el("li", {}, [
        el("span", { text: h.label }),
        bar,
        el("span", { className: "val", text: h.aqi != null ? `${h.aqi} · ${band}` : "no data" }),
      ]);
      if (h.time === bestStart) {
        item.classList.add("best");
        item.append(el("span", { className: "visually-hidden", text: " (start of cleanest window)" }));
      }
      return item;
    }));

    // Member plans
    const notes = new Map((ai?.member_notes || []).map((n) => [n.nickname, n.note]));
    $("#member-plans").replaceChildren(...baseline.members.map((m) => {
      const card = el("article", { className: "card member-plan" });
      card.append(el("h4", {}, [
        el("span", { text: m.nickname }),
        el("span", { className: `pill ${m.overall_status}`, text: STATUS_TEXT[m.overall_status] }),
      ]));
      if (notes.has(m.nickname)) card.append(el("p", { className: "note", text: notes.get(m.nickname) }));
      card.append(el("p", {}, [el("strong", { text: "Mask: " }), m.mask]));
      if (m.activities.length) {
        card.append(el("ul", {}, m.activities.map((a) => el("li", {}, [
          el("strong", { text: `${a.label} (${STATUS_TEXT[a.status]}): ` }), a.advice,
        ]))));
      }
      for (const w of m.warnings) card.append(el("p", { className: "warn", text: `⚠️ ${w}` }));
      return card;
    }));

    $("#home-actions").replaceChildren(...baseline.household_actions.map((a) => el("li", { text: a })));

    $("#share-text").value = share;
    $("#whatsapp-link").href = `https://wa.me/?text=${encodeURIComponent(share)}`;
    $("#disclaimer").textContent = baseline.disclaimer;
    $("#answer").textContent = "";
  }

  async function onCopy() {
    const text = $("#share-text").value;
    try {
      await navigator.clipboard.writeText(text);
      announce("Message copied. Paste it into your group chat.");
    } catch {
      $("#share-text").select();
      announce("Press Ctrl+C (or Cmd+C) to copy the selected message.");
    }
  }

  async function onAsk(event) {
    event.preventDefault();
    const question = $("#question").value.trim();
    const answer = $("#answer");
    if (question.length < 3) {
      announce("Please type a question (at least 3 characters).", true);
      return;
    }
    if (!state.lastRequest) {
      announce("Get today's plan first, then ask a question.", true);
      return;
    }
    const button = $("#ask-form button");
    button.disabled = true;
    answer.textContent = "Thinking…";
    try {
      const res = await postWithFallback("/api/ask", { ...state.lastRequest, question });
      answer.textContent = res.answer;
    } catch (err) {
      answer.textContent = "";
      announce(err.message, true);
    } finally {
      button.disabled = false;
    }
  }

  // ------------------------------------------------------------ init

  function init() {
    const profile = loadProfile();
    if (profile?.location) setLocation(profile.location);
    if (profile?.home) {
      $("#has-purifier").checked = !!profile.home.has_purifier;
      $("#has-n95").checked = !!profile.home.has_n95;
      $("#has-ac").checked = !!profile.home.has_ac;
    }
    if (profile?.language) $("#language").value = profile.language === "hi" ? "hi" : "en";
    const members = profile?.members?.length ? profile.members.slice(0, MAX_MEMBERS) : [{}];
    members.forEach((m) => addMember(m));

    $("#search-form").addEventListener("submit", onSearch);
    $("#geo-btn").addEventListener("click", onGeolocate);
    $("#add-member").addEventListener("click", () => {
      const node = addMember();
      if (node) $(".m-name", node).focus();
      saveProfile();
    });
    for (const id of ["#has-purifier", "#has-n95", "#has-ac", "#language"]) {
      $(id).addEventListener("change", saveProfile);
    }
    $("#plan-btn").addEventListener("click", onPlan);
    $("#copy-btn").addEventListener("click", onCopy);
    $("#ask-form").addEventListener("submit", onAsk);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
