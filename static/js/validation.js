/**
 * Pure helpers (no DOM access) shared by the UI and unit-tested with `node --test`.
 * Patterns mirror the server's Pydantic rules so users get instant, friendly feedback.
 */

export const MAX_MEMBERS = 8;
const NICKNAME = /^[\p{L}\p{M}\p{N}_][\p{L}\p{M}\p{N}_ .'-]{0,29}$/u;
const LABEL_DISALLOWED = /[^\p{L}\p{M}\p{N}_ ,.'()-]/gu;

/** "Delhi, NCT, India" from a geocoding result, capped at the server's 80-char limit. */
export function placeLabel(place) {
  return [place.name, place.admin1, place.country].filter(Boolean).join(", ").slice(0, 80);
}

/** Strip characters the server rejects in place labels. */
export function safeLabel(label) {
  const cleaned = label.replace(LABEL_DISALLOWED, "").trim().slice(0, 80);
  return cleaned || "Selected place";
}

/**
 * Check the household before sending it.
 * @returns {{message: string, index?: number} | null} the first problem, or null if valid
 */
export function validateHousehold(location, members) {
  if (!location) return { message: "Please choose your location first (step 1)." };
  if (!members.length) return { message: "Please add at least one family member." };
  const seen = new Set();
  for (const [index, member] of members.entries()) {
    if (!NICKNAME.test(member.nickname)) {
      return { message: `Family member ${index + 1} needs a nickname (letters, numbers and spaces only).`, index };
    }
    const key = member.nickname.toLowerCase();
    if (seen.has(key)) {
      return { message: `Two family members are called "${member.nickname}". Please use different nicknames.`, index };
    }
    seen.add(key);
  }
  return null;
}

/** Copy only the listed keys (used to send the server nothing beyond its schema). */
export function pick(source, keys) {
  const out = {};
  for (const key of keys) if (source && key in source) out[key] = source[key];
  return out;
}

/** Human-readable message from a FastAPI error body. */
export function errorMessage(data) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail) && data.detail[0]?.msg) {
    return "Please check your details: " + data.detail[0].msg.replace(/^Value error, /, "");
  }
  return "Something went wrong. Please try again.";
}
