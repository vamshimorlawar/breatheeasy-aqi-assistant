/** Step 1: choose a location by city search or browser geolocation. */

import { api } from "./api.js";
import { $, announce, el } from "./dom.js";
import { placeLabel, safeLabel } from "./validation.js";

/** Wire up search and geolocation; `onSelect(location)` receives {latitude, longitude, label}. */
export function initLocation(onSelect) {
  $("#search-form").addEventListener("submit", (event) => onSearch(event, onSelect));
  $("#geo-btn").addEventListener("click", () => onGeolocate(onSelect));
}

export function showLocation(location) {
  $("#selected-location").textContent = location ? `📍 Selected: ${location.label}` : "No location selected yet.";
}

async function onSearch(event, onSelect) {
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
    list.append(...places.map((place) => resultItem(place, list, onSelect)));
    announce(`${places.length} place(s) found. Choose one below.`);
    list.querySelector("button")?.focus();
  } catch (err) {
    announce(err.message, true);
  }
}

function resultItem(place, list, onSelect) {
  const label = placeLabel(place);
  const button = el("button", { type: "button", className: "btn btn-secondary", text: label });
  button.addEventListener("click", () => {
    onSelect({ latitude: place.latitude, longitude: place.longitude, label: safeLabel(label) });
    list.replaceChildren();
    announce(`Location set to ${label}.`);
    $("#plan-btn").focus();
  });
  return el("li", {}, button);
}

function onGeolocate(onSelect) {
  if (!("geolocation" in navigator)) {
    announce("Location is not available in this browser. Please search for your city.", true);
    return;
  }
  announce("Finding your location…");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      onSelect({
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
