/** Household form: family member cards and home equipment. */

import { $, $$, announce } from "./dom.js";
import { MAX_MEMBERS } from "./validation.js";

/** Add a member card (pre-filled from `data`); `onChange` runs whenever the household changes. */
export function addMember(data, onChange) {
  const container = $("#members");
  if (container.children.length >= MAX_MEMBERS) {
    announce(`You can add up to ${MAX_MEMBERS} family members.`, true);
    return null;
  }
  const node = $("#member-template").content.firstElementChild.cloneNode(true);
  $(".m-name", node).value = data.nickname || "";
  $(".m-age", node).value = data.age_group || "adult";
  for (const box of $$(".m-risk", node)) box.checked = (data.risk_factors || []).includes(box.value);
  for (const box of $$(".m-act", node)) box.checked = (data.activities || []).includes(box.value);
  $(".m-remove", node).addEventListener("click", () => {
    node.remove();
    renumberMembers();
    onChange();
    announce("Family member removed.");
    $("#add-member").focus();
  });
  node.addEventListener("change", onChange);
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

export function readMembers() {
  return $$("#members .member").map((node) => ({
    nickname: $(".m-name", node).value.trim(),
    age_group: $(".m-age", node).value,
    risk_factors: $$(".m-risk:checked", node).map((b) => b.value),
    activities: $$(".m-act:checked", node).map((b) => b.value),
  }));
}

export function focusMember(index) {
  $$("#members .m-name")[index]?.focus();
}

const HOME_FIELDS = { has_purifier: "#has-purifier", has_n95: "#has-n95", has_ac: "#has-ac" };
export const HOME_INPUT_IDS = Object.values(HOME_FIELDS);

export function readHome() {
  return Object.fromEntries(Object.entries(HOME_FIELDS).map(([key, id]) => [key, $(id).checked]));
}

export function writeHome(home) {
  for (const [key, id] of Object.entries(HOME_FIELDS)) $(id).checked = Boolean(home[key]);
}
