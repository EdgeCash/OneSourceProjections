// Soft password gate (obfuscation, not security — the content is in the DOM).
// Disabled while GATE_HASH is empty. To enable: set GATE_HASH to the SHA-256
// hex of the shared password (see scripts/set_gate_password.py), and the site
// will require it once per browser (token cached in localStorage).
(function () {
  var GATE_HASH = "";               // <-- set to enable the gate
  var TOKEN = "ec-gate-ok";
  if (!GATE_HASH) return;
  try { if (localStorage.getItem(TOKEN) === GATE_HASH) return; } catch (e) {}

  function sha256(str) {
    var buf = new TextEncoder().encode(str);
    return crypto.subtle.digest("SHA-256", buf).then(function (h) {
      return Array.from(new Uint8Array(h))
        .map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
    });
  }

  function mount() {
    var o = document.createElement("div");
    o.className = "gate-overlay";
    o.innerHTML =
      '<div class="gate-box"><h1>🎯 Project 54.7</h1>' +
      '<p>Enter the access password to continue.</p>' +
      '<input type="password" id="gate-pw" autofocus placeholder="Password">' +
      '<div class="gate-err" id="gate-err"></div>' +
      '<button id="gate-go">Enter</button></div>';
    document.body.appendChild(o);
    function submit() {
      var pw = document.getElementById("gate-pw").value;
      sha256(pw).then(function (hx) {
        if (hx === GATE_HASH) {
          try { localStorage.setItem(TOKEN, GATE_HASH); } catch (e) {}
          o.remove();
        } else {
          document.getElementById("gate-err").textContent = "Incorrect password.";
        }
      });
    }
    document.getElementById("gate-go").addEventListener("click", submit);
    document.getElementById("gate-pw").addEventListener("keydown", function (e) {
      if (e.key === "Enter") submit();
    });
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
