/**
 * BreatheEasy front end entry point. Plain ES modules, no build step.
 * The household profile is stored only in this browser (localStorage).
 */

import { postWithFallback } from "./api.js";
import { $, announce } from "./dom.js";
import { HOME_INPUT_IDS, addMember, focusMember, readHome, readMembers, writeHome } from "./household.js";
import { initLocation, showLocation } from "./location.js";
import { loadProfile, saveProfile } from "./profile.js";
import { renderPlan } from "./render.js";
import { MAX_MEMBERS, validateHousehold } from "./validation.js";

const state = { location: null, lastRequest: null };

function persist() {
  saveProfile({ location: state.location, members: readMembers(), home: readHome(), language: $("#language").value });
}

function setLocation(location) {
  state.location = location;
  showLocation(location);
  persist();
}

/** Disable a button while `task` runs, so double clicks can't send duplicate requests. */
async function withBusy(button, task) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await task();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

async function onPlan() {
  const members = readMembers();
  const problem = validateHousehold(state.location, members);
  if (problem) {
    if (problem.index !== undefined) focusMember(problem.index);
    announce(problem.message, true);
    return;
  }
  persist();
  const body = { location: state.location, members, home: readHome(), language: $("#language").value };
  announce("Checking the air and preparing your plan…");
  await withBusy($("#plan-btn"), async () => {
    try {
      const plan = await postWithFallback("/api/plan", body);
      state.lastRequest = body;
      renderPlan(plan);
      announce("Your plan is ready.");
      $("#results-heading").focus();
    } catch (err) {
      announce(err.message, true);
    }
  });
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
  answer.textContent = "Thinking…";
  await withBusy($("#ask-form button"), async () => {
    try {
      const result = await postWithFallback("/api/ask", { ...state.lastRequest, question });
      answer.textContent = result.answer;
    } catch (err) {
      answer.textContent = "";
      announce(err.message, true);
    }
  });
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

function restoreProfile() {
  const profile = loadProfile();
  if (profile?.location) setLocation(profile.location);
  if (profile?.home) writeHome(profile.home);
  if (profile?.language) $("#language").value = profile.language === "hi" ? "hi" : "en";
  const members = profile?.members?.length ? profile.members.slice(0, MAX_MEMBERS) : [{}];
  for (const member of members) addMember(member, persist);
}

function init() {
  restoreProfile();
  initLocation(setLocation);
  $("#add-member").addEventListener("click", () => {
    const node = addMember({}, persist);
    if (node) $(".m-name", node).focus();
    persist();
  });
  for (const id of [...HOME_INPUT_IDS, "#language"]) $(id).addEventListener("change", persist);
  $("#plan-btn").addEventListener("click", onPlan);
  $("#copy-btn").addEventListener("click", onCopy);
  $("#ask-form").addEventListener("submit", onAsk);
}

init(); // module scripts are deferred: the DOM is ready here
