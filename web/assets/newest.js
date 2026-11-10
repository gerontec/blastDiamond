// List of the newest human sequences (api.php?r=newest, cached hourly by the worker).
(function () {
  "use strict";
  const root = document.getElementById("newest");
  const t = k => root.dataset["t" + k[0].toUpperCase() + k.slice(1)] || k;
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // an older link pointed at newest.php#pathogen, where both lists were on one page
  if (location.hash === "#pathogen" && !/[?&]list=/.test(location.search)) {
    location.replace("newest.php?lang=" + encodeURIComponent(root.dataset.lang || "de") + "&list=pathogen");
    return;
  }
  const list = document.getElementById("list");
  if (list) fetch("api.php?r=newest").then(r => r.ok ? r.json() : Promise.reject(r.status)).then(d => {
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

  // second table: the proteins most recently flagged as a virulence factor or toxin (api.php?r=newest_pathogen)
  const box = document.getElementById("pathogen");
  // German names for the categories that have one; the rest keeps the English name of blast_flag_bit
  const LABEL = root.dataset.lang === "de" ? {
    "Toxin": "Toxin", "Virulence": "Virulenzfaktor", "Neurotoxin": "Neurotoxin",
    "Ion channel impairing toxin": "Ionenkanal-Toxin", "Hemostasis impairing toxin": "Blutstillungs-Toxin",
    "Cytolysis": "Zelllyse", "Hemolysis": "Hämolyse", "Myotoxin": "Myotoxin", "Cardiotoxin": "Kardiotoxin",
    "Enterotoxin": "Enterotoxin", "Dermonecrotic toxin": "dermonekrotisches Toxin",
    "Antibiotic resistance": "Antibiotikaresistenz", "Superantigen": "Superantigen",
    "Postsynaptic neurotoxin": "postsynaptisches Neurotoxin",
    "Presynaptic neurotoxin": "präsynaptisches Neurotoxin", "Vasoactive": "vasoaktiv",
    "Antimicrobial": "antimikrobiell", "Allergen": "Allergen"
  } : {};
  if (box) fetch("api.php?r=newest_pathogen").then(r => r.ok ? r.json() : Promise.reject(r.status)).then(d => {
    const items = d.items || [];
    if (!items.length) { box.innerHTML = '<div class="text-body-secondary">' + esc(t("pempty")) + "</div>"; return; }
    let foot = esc(t("updated")) + ": " + esc(d.updated) + " · " + esc(d.reference_proteins) + " " + esc(t("refs"));
    if (d.last_scan) foot += " · " + esc(d.last_scan.date) + ": " + esc(d.last_scan.flagged) + " / " + esc(d.last_scan.new_sequences);
    let html = '<p class="small text-body-secondary">' + foot + "</p>" +
      '<div class="table-responsive border rounded"><table class="table table-sm table-hover table-striped align-middle mb-0"><thead class="table-light"><tr>' +
      "<th>" + esc(t("acc")) + "</th><th>" + esc(t("org")) + "</th><th>" + esc(t("desc")) + "</th><th>" + esc(t("len")) +
      "</th><th>" + esc(t("kind")) + "</th><th>" + esc(t("ref")) + "</th><th>" + esc(t("pid")) + "</th><th>" +
      esc(t("ncbi")) + "</th><th></th></tr></thead><tbody>";
    items.forEach(x => {
      const title = (x.title || "").replace(/^\S+\s/, "");
      const kind = (x.kinds || []).map(k => LABEL[k] || k).join(", ");
      const strong = x.pident >= 90 ? ' class="fw-semibold"' : "";
      html += '<tr><td class="text-nowrap"><a href="https://www.ncbi.nlm.nih.gov/protein/' + encodeURIComponent(x.acc) +
        '" target="_blank" rel="noopener">' + esc(x.acc) + "</a></td>" +
        '<td class="small">' + esc(x.organism) + "</td>" +
        '<td class="small">' + esc(title) + "</td><td>" + esc(x.length) + "</td>" +
        '<td class="small">' + esc(kind) + "</td>" +
        '<td class="small"><a href="https://www.uniprot.org/uniprotkb/' + encodeURIComponent(x.ref_acc) +
        '" target="_blank" rel="noopener">' + esc(x.ref_acc) + "</a> " + esc((x.ref_name || "").split(" (")[0]) + "</td>" +
        "<td" + strong + ">" + esc(x.pident.toFixed(1)) + " %</td>" +
        '<td class="text-nowrap small">' + esc(x.ncbi_date) + "</td>" +
        '<td><a class="btn btn-outline-primary btn-sm text-nowrap" href="./?lang=' + esc(root.dataset.lang) +
        "&acc=" + encodeURIComponent(x.acc) + '">' + esc(t("search")) + "</a></td></tr>";
    });
    box.innerHTML = html + "</tbody></table></div>";
  }).catch(() => { box.innerHTML = '<div class="alert alert-warning">' + esc(t("error")) + "</div>"; });
})();
