"use strict";

// Dependency-free browser-style form checks, with no network or application DB.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

async function main() {
  const handlers = {};
  const fields = {
    kind: "bug", message: "A reproducible display issue.", expected_behavior: "A title",
    steps: "Open the game", bug_category: "Higher Score", feedback_category: "Suggestion",
    challenge_date: "2026-09-20", device_context: "Desktop", email: "", website: "",
  };
  let resets = 0;
  const form = {
    elements: { kind: { value: "bug" } },
    addEventListener: (type, handler) => { handlers[type] = handler; },
    reportValidity: () => true,
    reset: () => { resets += 1; },
  };
  const status = { focus() {}, hidden: true };
  const button = {};
  const bugFields = {};
  const generalFields = {};
  const elements = {
    "#feedback-form": form, "#feedback-status": status, "#feedback-submit": button,
    "#bug-fields": bugFields, "#general-fields": generalFields,
  };
  const calls = [];
  let resolveRequest;
  const context = {
    document: { querySelector: (selector) => elements[selector] }, crypto: webcrypto,
    FormData: class { get(key) { return fields[key]; } },
    fetch: (_url, options) => {
      calls.push(JSON.parse(options.body));
      return new Promise((resolve) => { resolveRequest = resolve; });
    },
  };
  vm.runInNewContext(fs.readFileSync("static/feedback.js", "utf8"), context);
  const submit = () => handlers.submit({ preventDefault() {} });
  const first = submit();
  await submit();
  assert.equal(calls.length, 1, "A rapid second click must not submit twice");
  assert.equal(button.disabled, true);
  resolveRequest({ ok: false, json: async () => ({ detail: "Please try again." }) });
  await first;
  assert.equal(button.disabled, false);
  assert.equal(status.textContent, "Please try again.");
  assert.equal(resets, 0, "Failed requests must preserve the typed report");
  const retry = submit();
  assert.equal(calls[0].id, calls[1].id, "Unchanged retries use one durable idempotency ID");
  resolveRequest({ ok: true, json: async () => ({ accepted: true, id: calls[1].id }) });
  await retry;
  assert.equal(resets, 1);
  assert.match(status.textContent, /saved privately/);
  assert.equal(button.disabled, false);
  fields.kind = form.elements.kind.value = "feedback";
  fields.message = "An accessibility suggestion.";
  handlers.change();
  assert.equal(bugFields.hidden, true);
  assert.equal(generalFields.hidden, false);
  const general = submit();
  assert.notEqual(calls[2].id, calls[1].id);
  assert.equal(calls[2].category, "Suggestion");
  assert.equal(calls[2].challenge_date, null);
  assert.equal(calls[2].expected_behavior, null);
  resolveRequest({ ok: true, json: async () => ({ accepted: true, id: calls[2].id }) });
  await general;
  console.log("feedback form checks passed");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
