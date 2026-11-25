/** Server API calls, plus the browser-side forecast fallback. */

import { announce } from "./dom.js";
import { errorMessage, pick } from "./validation.js";

const AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality";
const AIR_FIELDS = ["time", "us_aqi", "pm2_5", "pm10"];

export async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* non-JSON body */
  }
  if (!response.ok) {
    const error = new Error(errorMessage(data));
    error.status = response.status;
    throw error;
  }
  return data;
}

/**
 * Free hosts share one outbound IP, which can use up the data provider's daily quota.
 * The server then answers 502 and the browser fetches the public forecast itself,
 * sending only the fields the server's strict schema accepts.
 */
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

/** POST JSON; on 502 retry once with a browser-fetched forecast (stored on `body` for reuse). */
export async function postWithFallback(path, body) {
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
