/** Household profile persistence. Stays only in this browser; the app works if storage is blocked. */

const STORAGE_KEY = "breatheeasy.profile.v1";

export function saveProfile(profile) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch {
    /* storage unavailable (private mode): app still works */
  }
}

export function loadProfile() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}
