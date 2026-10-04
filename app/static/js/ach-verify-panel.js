/**
 * Finish ACH bank setup (microdeposit verification).
 * ==================================================
 * 2026-10-08. A venue could start bank setup and get parked in Stripe's
 * `requires_action` state, but nothing in the app could complete it. The only
 * route through was Stripe's hosted page, whose link we stored and never
 * showed — so a venue that chose bank payment simply could not pay, with no
 * message explaining why.
 *
 * Stripe picks one of two flows and the form has to ask for the right one:
 *   amounts          two small deposits; the venue types both values
 *   descriptor_code  one $0.01 deposit with a 6-character code on the
 *                    statement, e.g. SM11AA
 *
 * The hosted Stripe page stays available as a fallback. It is the only thing
 * that works if the deposits never arrive and the venue needs Stripe support,
 * so it is offered rather than hidden.
 *
 * window.gfAchVerify.mount('achVerifyPanel', venueId)
 */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function styles() {
    if (document.getElementById("gfAchStyles")) return;
    var el = document.createElement("style");
    el.id = "gfAchStyles";
    el.textContent = [
      ".gf-ach { border:1px solid rgba(245,158,11,0.45); background:rgba(245,158,11,0.07);",
      "  border-radius:10px; padding:16px 18px; margin:0 0 18px; }",
      ".gf-ach h3 { margin:0 0 6px; font-size:0.95rem; color:#fbbf24; font-weight:700; }",
      ".gf-ach p { margin:0 0 12px; font-size:0.84rem; color:var(--text-gray); line-height:1.55; }",
      ".gf-ach-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }",
      ".gf-ach input { padding:7px 10px; font-size:0.85rem; background:#151b28;",
      "  border:1px solid var(--border); border-radius:6px; color:var(--text); width:110px; }",
      ".gf-ach input.code { width:150px; text-transform:uppercase; letter-spacing:0.08em; }",
      ".gf-ach button { padding:8px 16px; font-size:0.84rem; font-weight:700; cursor:pointer;",
      "  border:none; border-radius:6px; background:var(--cyan); color:#06121a; }",
      ".gf-ach button[disabled] { opacity:0.6; cursor:default; }",
      ".gf-ach-msg { margin-top:10px; font-size:0.8rem; min-height:18px; }",
      ".gf-ach-msg.err { color:#ef4444; } .gf-ach-msg.ok { color:#34d399; }",
      ".gf-ach-alt { margin-top:12px; font-size:0.78rem; color:var(--text-muted); }",
      ".gf-ach-alt a { color:var(--cyan); }"
    ].join("\n");
    document.head.appendChild(el);
  }

  async function render(host, venueId) {
    var r, d;
    try {
      r = await fetch("/api/stripe/venue/" + venueId + "/ach-pending-verification",
                      { credentials: "include" });
      d = r.ok ? await r.json() : null;
    } catch (e) { d = null; }

    if (!d || !d.pending) { host.innerHTML = ""; return; }

    styles();
    var last4 = d.bank_last4 ? " ending " + esc(d.bank_last4) : "";
    // An older row saved before the type was stored: ask for amounts, the
    // commoner flow, and leave the hosted page as the way out.
    var byCode = d.microdeposit_type === "descriptor_code";

    var fields = byCode
      ? '<input class="code" id="gfAchCode" maxlength="12" placeholder="SM11AA" ' +
        'aria-label="Descriptor code">'
      : '<input id="gfAchA1" inputmode="decimal" placeholder="0.32" aria-label="First deposit amount">' +
        '<input id="gfAchA2" inputmode="decimal" placeholder="0.45" aria-label="Second deposit amount">';

    var explain = byCode
      ? "Stripe sent a $0.01 deposit to your bank account" + last4 +
        ". Your statement shows a 6-character code next to it, starting with SM. Enter it below."
      : "Stripe sent two small deposits to your bank account" + last4 +
        ". They usually arrive in 1–2 business days. Enter both amounts below.";

    host.innerHTML =
      '<div class="gf-ach">' +
        "<h3>Finish setting up your bank account</h3>" +
        "<p>" + explain + "</p>" +
        '<div class="gf-ach-row">' + fields +
          '<button type="button" id="gfAchGo">Verify</button>' +
        "</div>" +
        '<div class="gf-ach-msg" id="gfAchMsg"></div>' +
        (d.verification_url
          ? '<div class="gf-ach-alt">Deposits never turned up? ' +
            '<a href="' + esc(d.verification_url) + '" target="_blank" rel="noopener">' +
            "Verify on Stripe instead</a>.</div>"
          : "") +
      "</div>";

    var msg = host.querySelector("#gfAchMsg");
    var btn = host.querySelector("#gfAchGo");

    function say(text, kind) {
      msg.className = "gf-ach-msg" + (kind ? " " + kind : "");
      msg.textContent = text;
    }

    btn.addEventListener("click", async function () {
      var body;
      if (byCode) {
        var code = (host.querySelector("#gfAchCode").value || "").trim().toUpperCase();
        if (!code) { say("Enter the code from your statement.", "err"); return; }
        body = { descriptor_code: code };
      } else {
        var a = host.querySelector("#gfAchA1").value.trim();
        var b = host.querySelector("#gfAchA2").value.trim();
        if (!a || !b) { say("Enter both deposit amounts.", "err"); return; }
        body = { amounts: [a, b] };
      }
      btn.disabled = true;
      say("Checking…");
      try {
        var res = await fetch("/api/stripe/venue/" + venueId + "/ach-verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(body)
        });
        var out = await res.json().catch(function () { return {}; });
        if (!res.ok) {
          // Stripe's own message says how many attempts remain, which is the
          // one thing a venue needs here, so it is shown rather than replaced.
          say(out.detail || "That didn't match. Check the amounts and try again.", "err");
          btn.disabled = false;
          return;
        }
        if (!out.ok) { say(out.message || "Still processing.", "err"); btn.disabled = false; return; }
        say("Verified — your bank account is ready to use.", "ok");
        setTimeout(function () { render(host, venueId); }, 1800);
      } catch (e) {
        say("Could not reach the server. Try again.", "err");
        btn.disabled = false;
      }
    });
  }

  window.gfAchVerify = {
    mount: function (hostId, venueId) {
      var host = typeof hostId === "string" ? document.getElementById(hostId) : hostId;
      if (!host || !venueId) return;
      render(host, venueId);
    }
  };
})();
