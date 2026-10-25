"use strict";

const SAMPLE = [
  "linus@kernel.org",
  "not-a-real-mailbox-2468fj@gmail.com",
  "no-mailbox-here@yahoo.example",
  "test-mailbox-9911@mailinator.com",
  "something@example.com",
  "nope@invalid-domain-qz8kx2.test.invalid",
  "plainaddress",
  "two@@at-signs.com",
];

const $ = (sel) => document.querySelector(sel);
const esc = (s) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const BADGES = {
  deliverable: ["✔ likely to be delivered", "tag tag-green"],
  bounce: ["✖ likely to bounce", "tag tag-red"],
  unknown: ["? unknown", "tag tag-gray"],
};

let latestResults = [];

function parseList() {
  const seen = new Set();
  const out = [];
  for (const line of $("#emails").value.split("\n")) {
    const value = line.trim();
    if (!value) continue;
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}

function updateCounter() {
  const n = new Set(parseList().map((s) => s.toLowerCase())).size;
  $("#counter").textContent = `${n} address${n === 1 ? "" : "es"}`;
}

function setError(msg) {
  const el = $("#error");
  el.hidden = !msg;
  el.textContent = msg || "";
}

async function loadEnvironment() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return;
    const data = await res.json();
    const banner = $("#env-banner");
    if (data.smtp_available === false) {
      banner.hidden = false;
      banner.innerHTML =
        "<b>Heads-up:</b> this deployment cannot open outbound SMTP connections (port 25 is " +
        "blocked on most shared hosting). Syntax and DNS checks still work, but any valid " +
        "domain will come back <b>unknown</b> because mailbox existence can't be confirmed here. " +
        "Self-hosting the app on a server with open port 25 unlocks full mailbox verification.";
    }
  } catch {
    /* keep the banner hidden; per-result notes will explain too */
  }
}

function rowHtml(r) {
  const [text, cls] = BADGES[r.verdict] || ["?", "tag tag-gray"];
  const tags = [];
  if (r.disposable) tags.push('<span class="tag tag-yellow" title="Address at a temporary/throwaway inbox provider">disposable</span>');
  if (r.catch_all === true) tags.push('<span class="tag tag-plain" title="The domain accepts every address, so acceptance proves little">catch-all</span>');
  const notes = (r.notes || []).length
    ? `<ul class="notes">${r.notes.map((n) => `<li><small>${esc(String(n))}</small></li>`).join("")}</ul>`
    : "";
  return `
    <tr>
      <td class="addr">${esc(r.email)}</td>
      <td><span class="${cls}">${text}</span>${tags.map((t) => " " + t).join("")}</td>
      <td class="why">${esc(r.reason || "")}${notes}</td>
      <td></td>
    </tr>`;
}

function renderResults(data) {
  latestResults = data.results;
  const s = data.summary;
  $("#summary").textContent =
    `Results — ${s.deliverable} likely to deliver, ${s.bounce} likely to bounce, ${s.unknown} unknown`;
  $("#results tbody").innerHTML = data.results.map(rowHtml).join("");
  $("#results-section").hidden = false;
  $("#download").disabled = false;
}

function csvCell(v) {
  const s = v == null ? "" : String(v);
  return '"' + s.replace(/"/g, '""') + '"';
}

function downloadCsv() {
  const rows = [["email", "verdict", "disposable", "catch_all", "reason", "notes"],
    ...latestResults.map((r) => [
      r.email, r.verdict, r.disposable ? "yes" : "no",
      r.catch_all == null ? "" : r.catch_all ? "yes" : "no",
      r.reason, (r.notes || []).map((n) => String(n)).join(" | "),
    ])];
  const csv = rows.map((r) => r.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "email-check-results.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

async function runCheck() {
  const emails = parseList();
  setError("");
  if (!emails.length) {
    setError("Paste at least one email address.");
    return;
  }
  const btn = $("#check");
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Checking…";
  try {
    const res = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ emails }),
    });
    const data = await res.json().catch(() => ({ error: "bad response from server" }));
    if (!res.ok) {
      setError(data.error || `Server returned ${res.status}.`);
      return;
    }
    renderResults(data);
  } catch (err) {
    setError("The request failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#check").addEventListener("click", runCheck);
  $("#clear").addEventListener("click", () => { $("#emails").value = ""; updateCounter(); setError(""); $("#results-section").hidden = true; });
  $("#download").addEventListener("click", downloadCsv);
  $("#fill-sample").addEventListener("click", () => { $("#emails").value = SAMPLE.join("\n"); updateCounter(); setError(""); });
  $("#emails").addEventListener("input", updateCounter);
  updateCounter();
  loadEnvironment();
});
