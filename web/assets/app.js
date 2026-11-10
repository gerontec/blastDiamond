// Query form of the DIAMOND search (blastDiamond): organism suggestions, ad-hoc search with live status,
// hit table, full search by mail, links from mails (?job=… / ?confirm=…&token=…).
// Plain JavaScript, no libraries; talks to api.php on the same origin.
(function () {
  "use strict";
  const app = document.getElementById("app");
  const t = k => app.dataset["t" + k.split("_").map(s => s[0].toUpperCase() + s.slice(1)).join("")] || k;
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const result = $("result");
  let pollTimer = null, runSince = null;

  // ---------------------------------------------------------------- sequence or accession
  document.querySelectorAll("input[name=qkind]").forEach(r => r.addEventListener("change", () => {
    const acc = document.querySelector("input[name=qkind]:checked").value === "acc";
    $("box_seq").hidden = acc;
    $("box_acc").hidden = !acc;
  }));

  // ---------------------------------------------------------------- organisms
  const taxa = new Map();          // taxid -> name
  const options = new Map();       // option text -> {taxid, name}
  let tTimer = null;

  function chips() {
    $("orgchips").innerHTML = Array.from(taxa, ([id, name]) =>
      '<span class="badge text-bg-light border me-1 mb-1">' + esc(name) + ' <small class="text-body-secondary">' + esc(id) +
      '</small> <button type="button" class="btn-close btn-close-sm ms-1" data-id="' + esc(id) + '" aria-label="' + esc(t("remove")) + '"></button></span>').join("");
  }
  $("orgchips").addEventListener("click", e => {
    if (e.target.dataset.id) { taxa.delete(Number(e.target.dataset.id)); chips(); }
  });
  $("org").addEventListener("input", () => {
    const v = $("org").value.trim();
    if (options.has(v)) {                       // a suggestion was picked
      const o = options.get(v);
      if (taxa.size < 20) taxa.set(o.taxid, o.name);
      $("org").value = "";
      chips();
      return;
    }
    clearTimeout(tTimer);
    if (v.length < 3) return;
    tTimer = setTimeout(async () => {
      try {
        const r = await fetch("api.php?r=taxsearch&q=" + encodeURIComponent(v));
        if (!r.ok) return;
        const rows = await r.json();
        options.clear();
        $("orglist").innerHTML = "";
        rows.forEach(x => {
          const text = x.name + " (" + x.rank + ", " + x.taxid + ")";
          options.set(text, { taxid: Number(x.taxid), name: x.name });
          const o = document.createElement("option");
          o.value = text;
          $("orglist").appendChild(o);
        });
      } catch (e) { /* suggestions are optional */ }
    }, 300);
  });

  // ---------------------------------------------------------------- request body
  function body() {
    const b = { db: $("dbsel").value, mode: $("mode").value, sensitivity: $("sens").value, evalue: $("evalue").value.trim() || "0.001",
                max_target_seqs: $("maxhits").value || "25" };
    if (document.querySelector("input[name=qkind]:checked").value === "acc") {
      b.acc = $("acc").value.trim();
      if ($("range").value.trim()) b.range = $("range").value.trim();
    } else {
      b.seq = $("seq").value;
    }
    if (taxa.size) b.taxonlist = Array.from(taxa.keys()).join(",");
    if ($("len_min").value) b.len_min = $("len_min").value;
    if ($("len_max").value) b.len_max = $("len_max").value;
    if ($("new_days").value) b.new_days = $("new_days").value;
    return b;
  }

  async function post(r, b) {
    const res = await fetch("api.php?r=" + r, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
    let d = {};
    try { d = await res.json(); } catch (e) { d = { error: res.status + " " + res.statusText }; }
    return [res.status, d];
  }

  function banner(cls, html) { $("banner").innerHTML = html ? '<div class="alert alert-' + cls + '">' + html + "</div>" : ""; }

  // ---------------------------------------------------------------- ad-hoc search
  $("sform").addEventListener("submit", async ev => {
    ev.preventDefault();
    banner("", "");
    const b = body();
    if (b.db === "nr" && !b.taxonlist && !b.len_min && !b.len_max && !b.new_days) { banner("info", esc(t("need_presel"))); return; }
    $("go").disabled = true;
    const [code, d] = await post("adhoc", b);
    $("go").disabled = false;
    if (code !== 202) { banner("warning", esc(d.error || code)); return; }
    history.replaceState(null, "", location.pathname + "?job=" + d.job_id + (new URLSearchParams(location.search).get("lang") ? "&lang=" + new URLSearchParams(location.search).get("lang") : ""));
    runSince = null;
    poll(d.job_id);
  });

  async function poll(id) {
    clearTimeout(pollTimer);
    let j;
    try {
      const r = await fetch("api.php?r=job&id=" + encodeURIComponent(id), { cache: "no-store" });
      j = await r.json();
      if (!r.ok) { result.innerHTML = '<div class="alert alert-warning">' + esc(j.error || r.status) + "</div>"; return; }
    } catch (e) { pollTimer = setTimeout(() => poll(id), 5000); return; }
    render(j);
    if (j.status === "queued" || j.status === "running" || j.status === "unconfirmed") {
      pollTimer = setTimeout(() => poll(id), j.status === "unconfirmed" ? 15000 : 2000);
    }
  }

  function render(j) {
    const head = '<h2 class="h5">' + esc(t("st_" + j.status)) + "</h2>";
    if (j.status === "queued" || j.status === "running" || j.status === "unconfirmed") {
      if (j.status === "running" && !runSince) runSince = Date.now();
      const secs = runSince ? Math.round((Date.now() - runSince) / 1000) : 0;
      result.innerHTML = '<div class="alert alert-info d-flex align-items-center gap-3"><div class="spinner-border spinner-border-sm"></div><div>' + head +
        (j.status === "queued" ? esc(t("position")) + " " + esc(j.position) : "") +
        (j.status === "running" ? esc(t("elapsed")) + " " + secs + " s" + (j.budget_s ? " / max. " + esc(j.budget_s) + " s" : "") : "") +
        (j.preselect ? '<div class="small text-body-secondary">' + esc(j.preselect) + "</div>" : "") + "</div></div>";
      return;
    }
    if (j.status === "rejected") {
      result.innerHTML = '<div class="alert alert-warning">' + head + "<p class=\"mb-1\">" + esc(j.error) + '</p><p class="small mb-0">' + esc(t("rejected_hint")) + "</p></div>";
      $("fullbox").open = true;
      return;
    }
    if (j.status === "failed") {
      result.innerHTML = '<div class="alert alert-danger">' + head + esc(j.error) + "</div>";
      return;
    }
    // done
    const hits = j.hits || [];
    let html = '<div class="d-flex flex-wrap align-items-center gap-2 mb-2"><h2 class="h5 mb-0 me-auto">' + esc(t("hits")) + ": " + hits.length +
      ' <small class="text-body-secondary">(' + esc(j.seconds) + " s" + (j.preselect ? ", " + esc(j.preselect) : "") + ")</small></h2>" +
      (j.xlsx_url && hits.length ? '<a class="btn btn-success" href="api.php?r=jobxlsx&id=' + esc(j.job_id) + '">' + esc(t("excel")) + "</a>" : "") + "</div>";
    html += '<p class="small text-body-secondary">' + esc(t("keep")) + ' <code class="user-select-all">' + esc(location.origin + location.pathname + "?job=" + j.job_id) + "</code></p>";
    if (!hits.length) {
      html += '<div class="alert alert-light border">' + esc(t("nohits")) + "</div>";
    } else {
      html += '<div class="table-responsive border rounded"><table class="table table-sm table-hover table-striped align-middle mb-0 sortable"><thead class="table-light"><tr>' +
        "<th>" + esc(t("col_query")) + "</th><th>" + esc(t("col_subject")) + "</th><th>" + esc(t("col_title")) + "</th><th>" + esc(t("col_org")) +
        '</th><th data-num>' + esc(t("col_ident")) + '</th><th data-num>' + esc(t("col_len")) + '</th><th data-num>' + esc(t("col_evalue")) +
        '</th><th data-num>' + esc(t("col_bits")) + "</th></tr></thead><tbody>";
      hits.forEach(h => {
        html += "<tr><td class=\"small\">" + esc(h.qseqid) + '</td><td class="small text-nowrap"><a href="https://www.ncbi.nlm.nih.gov/protein/' +
          encodeURIComponent(h.sseqid) + '" target="_blank" rel="noopener">' + esc(h.sseqid) + '</a></td><td class="small">' +
          esc((h.title || "").slice(0, 160)) + '</td><td class="small"><em>' + esc((h.taxa || []).slice(0, 3).join("; ")) + "</em></td>" +
          '<td data-v="' + esc(h.pident) + '">' + esc(h.pident) + '</td><td data-v="' + esc(h.length) + '">' + esc(h.length) +
          '</td><td data-v="' + esc(h.evalue) + '">' + esc(Number(h.evalue).toExponential(1)) + '</td><td data-v="' + esc(h.bitscore) + '">' + esc(h.bitscore) + "</td></tr>";
      });
      html += "</tbody></table></div>";
    }
    result.innerHTML = html;
    sortable(result.querySelector("table.sortable"));
  }

  function sortable(table) {
    if (!table) return;
    table.querySelectorAll("thead th").forEach((th, i) => {
      th.classList.add("sort");
      th.addEventListener("click", () => {
        const num = th.hasAttribute("data-num"), dir = th.dataset.dir === "asc" ? "desc" : "asc";
        table.querySelectorAll("thead th").forEach(o => delete o.dataset.dir);
        th.dataset.dir = dir;
        const rows = Array.from(table.tBodies[0].rows);
        const key = r => num ? (parseFloat(r.cells[i].dataset.v) || 0) : r.cells[i].textContent.trim().toLowerCase();
        rows.sort((a, b) => { const x = key(a), y = key(b); return (x < y ? -1 : x > y ? 1 : 0) * (dir === "asc" ? 1 : -1); });
        rows.forEach(r => table.tBodies[0].appendChild(r));
      });
    });
  }

  // ---------------------------------------------------------------- full search by mail
  $("fform").addEventListener("submit", async ev => {
    ev.preventDefault();
    const b = body();
    b.email = $("email").value.trim();
    b.db = "nr";                       // the full search by mail always covers the whole nr
    const [code, d] = await post("full", b);
    $("fullstatus").innerHTML = code === 202
      ? '<div class="alert alert-success mb-0">' + esc(t("full_sent")) + "</div>"
      : '<div class="alert alert-warning mb-0">' + esc(d.error || code) + "</div>";
  });

  // ---------------------------------------------------------------- links from mails
  const qs = new URLSearchParams(location.search);
  if (qs.get("confirm") && qs.get("token")) {
    fetch("api.php?r=confirm&id=" + encodeURIComponent(qs.get("confirm")) + "&token=" + encodeURIComponent(qs.get("token")), { cache: "no-store" })
      .then(r => r.json().then(d => [r.ok, d]))
      .then(([ok, d]) => {
        banner(ok ? "success" : "warning", esc(ok ? t("confirmed") : (d.error || "")));
        if (ok) poll(qs.get("confirm"));
      });
  } else if (qs.get("job")) {
    poll(qs.get("job"));
  }
  if (qs.get("acc")) {             // "search similar" from the list of newest sequences
    $("qk_acc").checked = true;
    $("qk_acc").dispatchEvent(new Event("change"));
    $("acc").value = qs.get("acc");
  }
})();
