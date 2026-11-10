// List of the newest human sequences (api.php?r=newest, cached hourly by the worker).
(function () {
  "use strict";
  const root = document.getElementById("newest");
  const t = k => root.dataset["t" + k[0].toUpperCase() + k.slice(1)] || k;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const list = document.getElementById("list");
  fetch("api.php?r=newest").then(r => r.ok ? r.json() : Promise.reject(r.status)).then(d => {
    let html = '<p class="small text-body-secondary">' + esc(t("updated")) + ": " + esc(d.updated) + "</p>" +
      '<div class="table-responsive border rounded"><table class="table table-sm table-hover table-striped align-middle mb-0"><thead class="table-light"><tr>' +
      "<th>" + esc(t("acc")) + "</th><th>" + esc(t("desc")) + "</th><th>" + esc(t("len")) + "</th><th>" + esc(t("ncbi")) + "</th><th>" +
      esc(t("added")) + "</th><th>" + esc(t("src")) + "</th><th></th></tr></thead><tbody>";
    (d.items || []).forEach(x => {
      const title = (x.title || "").replace(/^\S+\s/, "");
      html += '<tr><td class="text-nowrap"><a href="https://www.ncbi.nlm.nih.gov/protein/' + encodeURIComponent(x.acc) + '" target="_blank" rel="noopener">' +
        esc(x.acc) + "</a></td><td class=\"small\">" + esc(title) + "</td><td>" + esc(x.length) + '</td><td class="text-nowrap small">' + esc(x.ncbi_date) +
        '</td><td class="text-nowrap small">' + esc((x.added || "").slice(0, 10)) + '</td><td class="small">' + esc(x.source) +
        '</td><td><a class="btn btn-outline-primary btn-sm text-nowrap" href="./?lang=' + esc(root.dataset.lang) + "&acc=" + encodeURIComponent(x.acc) + '">' +
        esc(t("search")) + "</a></td></tr>";
    });
    list.innerHTML = html + "</tbody></table></div>";
  }).catch(() => { list.innerHTML = '<div class="alert alert-warning">' + esc(t("error")) + "</div>"; });
})();
