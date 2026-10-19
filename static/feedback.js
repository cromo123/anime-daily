"use strict";

const feedbackForm = document.querySelector("#feedback-form");
const feedbackStatus = document.querySelector("#feedback-status");
const feedbackSubmit = document.querySelector("#feedback-submit");
let sendingFeedback = false;
let pendingFeedback = null;

function updateFeedbackFields() {
  const isBug = feedbackForm.elements.kind.value === "bug";
  document.querySelector("#bug-fields").hidden = !isBug;
  document.querySelector("#general-fields").hidden = isBug;
}

function feedbackId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

feedbackForm.addEventListener("change", updateFeedbackFields);
feedbackForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (sendingFeedback || !feedbackForm.reportValidity()) return;
  const fields = new FormData(feedbackForm);
  const isBug = fields.get("kind") === "bug";
  const data = {
    kind: fields.get("kind"), message: fields.get("message").trim(),
    expected_behavior: isBug ? fields.get("expected_behavior").trim() || null : null,
    steps: isBug ? fields.get("steps").trim() || null : null,
    category: fields.get(isBug ? "bug_category" : "feedback_category") || null,
    challenge_date: isBug ? fields.get("challenge_date") || null : null,
    device_context: isBug ? fields.get("device_context").trim() || null : null,
    email: fields.get("email").trim() || null, website: fields.get("website"),
  };
  const fingerprint = JSON.stringify(data);
  if (!pendingFeedback || pendingFeedback.fingerprint !== fingerprint) {
    pendingFeedback = { fingerprint, id: feedbackId() };
  }
  sendingFeedback = true;
  feedbackSubmit.disabled = true;
  feedbackSubmit.textContent = "Sending…";
  feedbackStatus.hidden = true;
  try {
    const response = await fetch("/feedback/submit", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ id: pendingFeedback.id, ...data }),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result?.accepted) {
      const detail = result?.detail;
      throw new Error(Array.isArray(detail)
        ? detail.map((item) => `${item.field}: ${item.message}`).join(" ")
        : typeof detail === "string" ? detail : "The report could not be saved. Please try again.");
    }
    feedbackStatus.textContent = `Thank you — your report was saved privately. Reference: ${result.id}.`;
    feedbackStatus.className = "feedback-success";
    feedbackForm.reset();
    updateFeedbackFields();
    pendingFeedback = null;
  } catch (error) {
    feedbackStatus.textContent = error.message || "Connection problem. Your text is still here; please try again.";
    feedbackStatus.className = "request-error";
  } finally {
    sendingFeedback = false;
    feedbackSubmit.disabled = false;
    feedbackSubmit.textContent = "Send report";
    feedbackStatus.hidden = false;
    feedbackStatus.focus();
  }
});

updateFeedbackFields();
