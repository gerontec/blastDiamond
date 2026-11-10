<?php
/*
 * Query form for the DIAMOND search on NCBI nr (github.com/gerontec/blastDiamond), PHP + Bootstrap 5 served
 * locally (no CDN: no visitor address goes to third parties). The page itself holds no data; assets/app.js talks
 * to api.php on the same origin (the web server proxies it to the API host).
 *
 * Ad-hoc searches run only if they fit the time budget of the API (300 s); a search of the whole nr is offered
 * by mail, one per address and day.
 */
declare(strict_types=1);

$lang = (($_GET['lang'] ?? '') === 'en' || (!isset($_GET['lang'])
        && !str_starts_with(strtolower($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? 'de'), 'de'))) ? 'en' : 'de';

$T = [
 'de' => [
  'title' => 'DIAMOND-Suche in NCBI nr',
  'lead' => 'Ähnliche Proteine zu einer Sequenz finden – in der vollständigen NCBI-Proteindatenbank nr (über 1,1 Milliarden Sequenzen), täglich aktualisiert.',
  'notice_h' => 'Hinweis: privat finanziertes Projekt',
  'notice' => 'Dieser Dienst wird privat betrieben und finanziert. Abfragen, die voraussichtlich länger als 5 Minuten rechnen, werden deshalb nicht ausgeführt. Grenzen Sie die Suche über Organismus oder Sequenzlänge ein. Eine Suche über die gesamte nr ist einmal pro Tag und E-Mail-Adresse möglich; das Ergebnis kommt per Mail.',
  'q_seq' => 'Sequenz', 'q_acc' => 'Accession aus nr', 'seq_ph' => "FASTA oder reine Sequenz, z. B.\n>meinProtein\nMFVFLVLLPLVSSQCVNLTT…",
  'acc_ph' => 'z. B. YP_009724390.1', 'range' => 'Bereich (optional)', 'range_ph' => 'z. B. 319-541',
  'program' => 'Programm', 'blastp' => 'blastp – Protein gegen Protein', 'blastx' => 'blastx – DNA/RNA, übersetzt gegen Protein',
  'sens' => 'Sensitivität', 'sens_help' => 'Höher = findet entferntere Verwandte, rechnet aber deutlich länger.',
  'presel' => 'Eingrenzung (für die Sofort-Suche nötig)', 'org' => 'Organismus oder Gruppe', 'org_ph' => 'Name eintippen, z. B. Homo sapiens, Fungi, Coronaviridae',
  'org_help' => 'Einschließlich aller Untergruppen. Bis zu 20 Einträge.', 'len' => 'Länge der Treffer-Sequenz (Aminosäuren)',
  'len_min' => 'von', 'len_max' => 'bis', 'advanced' => 'Weitere Einstellungen', 'evalue' => 'E-Wert (Schwelle)',
  'maxhits' => 'Treffer pro Anfrage-Sequenz', 'go' => 'Sofort suchen (max. 5 Minuten)',
  'full_h' => 'Vollsuche über die gesamte nr per E-Mail', 'full_text' => 'Ohne Eingrenzung, ohne Zeitgrenze. Einmal pro Tag und Adresse. Sie erhalten zuerst einen Bestätigungslink; die Suche startet erst nach dem Klick. Eine Vollsuche dauert je nach Sensitivität 30 Minuten bis mehrere Stunden.',
  'email' => 'E-Mail-Adresse', 'full_go' => 'Vollsuche anfordern',
  'st_queued' => 'In der Warteschlange', 'st_running' => 'Suche läuft', 'st_done' => 'Fertig', 'st_failed' => 'Fehlgeschlagen',
  'st_rejected' => 'Nicht ausgeführt', 'st_unconfirmed' => 'Wartet auf Bestätigung', 'position' => 'Position',
  'elapsed' => 'Laufzeit', 'hits' => 'Treffer', 'excel' => 'Als Excel herunterladen', 'nohits' => 'Keine Treffer unterhalb des E-Werts.',
  'rejected_hint' => 'Tipp: Organismus enger fassen, Längenfenster setzen, niedrigere Sensitivität wählen – oder unten die Vollsuche per Mail anfordern.',
  'full_sent' => 'Fast geschafft: Wir haben einen Bestätigungslink an Ihre Adresse geschickt. Die Suche startet, sobald Sie ihn öffnen (24 Stunden gültig).',
  'confirmed' => 'Danke, die Adresse ist bestätigt. Die Vollsuche ist eingereiht; das Ergebnis kommt per Mail.',
  'col_query' => 'Anfrage', 'col_subject' => 'Treffer', 'col_title' => 'Beschreibung', 'col_org' => 'Organismus',
  'col_ident' => 'Identität %', 'col_len' => 'Länge', 'col_evalue' => 'E-Wert', 'col_bits' => 'Bit-Score',
  'remove' => 'entfernen', 'experts' => 'Für Programme: JSON-API', 'source' => 'Quellcode',
  'data' => 'Daten: NCBI nr (täglich ergänzt). Suche: DIAMOND (Buchfink et al., Nat. Methods 2021).',
  'keep' => 'Link zu diesem Ergebnis:', 'need_presel' => 'Für die Sofort-Suche bitte einen Organismus, ein Längenfenster oder „letzte N Tage“ angeben – oder die Vollsuche per Mail nutzen.',
  'newest' => 'Die 30 neuesten menschlichen Sequenzen in nr',
  'new_days' => 'Nur Sequenzen, die NCBI in den letzten N Tagen veröffentlicht oder aktualisiert hat', 'new_days_ph' => 'z. B. 7',
 ],
 'en' => [
  'title' => 'DIAMOND search in NCBI nr',
  'lead' => 'Find proteins similar to a sequence in the complete NCBI protein database nr (more than 1.1 billion sequences), updated daily.',
  'notice_h' => 'Note: privately funded project',
  'notice' => 'This service is run and paid for privately. Searches that are expected to take longer than 5 minutes are therefore not run. Narrow the search by organism or sequence length. A search of the whole nr is possible once a day per e-mail address; the result is sent by mail.',
  'q_seq' => 'Sequence', 'q_acc' => 'Accession from nr', 'seq_ph' => "FASTA or plain sequence, e.g.\n>myProtein\nMFVFLVLLPLVSSQCVNLTT…",
  'acc_ph' => 'e.g. YP_009724390.1', 'range' => 'Range (optional)', 'range_ph' => 'e.g. 319-541',
  'program' => 'Program', 'blastp' => 'blastp – protein against protein', 'blastx' => 'blastx – DNA/RNA, translated against protein',
  'sens' => 'Sensitivity', 'sens_help' => 'Higher = finds more distant relatives, but takes much longer.',
  'presel' => 'Restriction (needed for the instant search)', 'org' => 'Organism or group', 'org_ph' => 'Type a name, e.g. Homo sapiens, Fungi, Coronaviridae',
  'org_help' => 'Including all sub-groups. Up to 20 entries.', 'len' => 'Length of the hit sequence (amino acids)',
  'len_min' => 'from', 'len_max' => 'to', 'advanced' => 'More settings', 'evalue' => 'E-value (threshold)',
  'maxhits' => 'Hits per query sequence', 'go' => 'Search now (max. 5 minutes)',
  'full_h' => 'Full search of the whole nr by e-mail', 'full_text' => 'No restriction, no time limit. Once a day per address. You first receive a confirmation link; the search starts after you open it. A full search takes 30 minutes to several hours depending on the sensitivity.',
  'email' => 'E-mail address', 'full_go' => 'Request full search',
  'st_queued' => 'Queued', 'st_running' => 'Searching', 'st_done' => 'Done', 'st_failed' => 'Failed',
  'st_rejected' => 'Not run', 'st_unconfirmed' => 'Waiting for confirmation', 'position' => 'Position',
  'elapsed' => 'Time', 'hits' => 'Hits', 'excel' => 'Download as Excel', 'nohits' => 'No hits below the e-value.',
  'rejected_hint' => 'Tip: narrow the organism, set a length window, choose a lower sensitivity – or request the full search by mail below.',
  'full_sent' => 'Almost done: a confirmation link has been sent to your address. The search starts once you open it (valid for 24 hours).',
  'confirmed' => 'Thank you, the address is confirmed. The full search is queued; the result comes by mail.',
  'col_query' => 'Query', 'col_subject' => 'Hit', 'col_title' => 'Description', 'col_org' => 'Organism',
  'col_ident' => 'Identity %', 'col_len' => 'Length', 'col_evalue' => 'E-value', 'col_bits' => 'Bit score',
  'remove' => 'remove', 'experts' => 'For programs: JSON API', 'source' => 'Source code',
  'data' => 'Data: NCBI nr (extended daily). Search: DIAMOND (Buchfink et al., Nat. Methods 2021).',
  'keep' => 'Link to this result:', 'need_presel' => 'For the instant search please give an organism, a length window or "last N days" – or use the full search by mail.',
  'newest' => 'The 30 newest human sequences in nr',
  'new_days' => 'Only sequences NCBI published or updated in the last N days', 'new_days_ph' => 'e.g. 7',
 ],
][$lang];

function h($s): string { return htmlspecialchars((string)($s ?? ''), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }

$sens = ['fast', 'mid-sensitive', 'sensitive', 'more-sensitive', 'very-sensitive', 'ultra-sensitive'];

header('Cache-Control: public, max-age=300');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'");
?><!doctype html>
<html lang="<?= $lang ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= h($T['title']) ?></title>
<link rel="stylesheet" href="assets/bootstrap.min.css">
<link rel="stylesheet" href="assets/app.css">
</head>
<body>
<nav class="navbar bg-body-tertiary border-bottom mb-4">
  <div class="container">
    <a class="navbar-brand fw-semibold" href="?lang=<?= $lang ?>"><?= h($T['title']) ?></a>
    <div class="small"><a href="?lang=de" class="<?= $lang === 'de' ? 'fw-bold' : '' ?>">DE</a> · <a href="?lang=en" class="<?= $lang === 'en' ? 'fw-bold' : '' ?>">EN</a></div>
  </div>
</nav>

<main class="container mb-5" id="app"
  <?php foreach ($T as $k => $v): ?> data-t-<?= h(str_replace('_', '-', $k)) ?>="<?= h($v) ?>"<?php endforeach; ?>>
  <p class="lead"><?= h($T['lead']) ?></p>
  <p><a class="fw-semibold" href="newest.php?lang=<?= $lang ?>">➜ <?= h($T['newest']) ?></a></p>

  <div class="alert alert-warning">
    <strong><?= h($T['notice_h']) ?>.</strong> <?= h($T['notice']) ?>
  </div>

  <div id="banner"></div>

  <form id="sform" class="card card-body shadow-sm mb-4" autocomplete="off">
    <div class="btn-group mb-2" role="group">
      <input type="radio" class="btn-check" name="qkind" id="qk_seq" value="seq" checked>
      <label class="btn btn-outline-primary btn-sm" for="qk_seq"><?= h($T['q_seq']) ?></label>
      <input type="radio" class="btn-check" name="qkind" id="qk_acc" value="acc">
      <label class="btn btn-outline-primary btn-sm" for="qk_acc"><?= h($T['q_acc']) ?></label>
    </div>
    <div id="box_seq">
      <textarea class="form-control font-monospace" id="seq" rows="6" placeholder="<?= h($T['seq_ph']) ?>"></textarea>
    </div>
    <div id="box_acc" class="row g-2" hidden>
      <div class="col-md-6"><input class="form-control" id="acc" placeholder="<?= h($T['acc_ph']) ?>"></div>
      <div class="col-md-6"><input class="form-control" id="range" placeholder="<?= h($T['range']) ?>: <?= h($T['range_ph']) ?>"></div>
    </div>

    <div class="row g-3 mt-1">
      <div class="col-md-6">
        <label class="form-label small text-body-secondary mb-1" for="mode"><?= h($T['program']) ?></label>
        <select class="form-select" id="mode">
          <option value="blastp"><?= h($T['blastp']) ?></option>
          <option value="blastx"><?= h($T['blastx']) ?></option>
        </select>
      </div>
      <div class="col-md-6">
        <label class="form-label small text-body-secondary mb-1" for="sens"><?= h($T['sens']) ?></label>
        <select class="form-select" id="sens">
          <?php foreach ($sens as $s): ?><option value="<?= $s ?>" <?= $s === 'fast' ? 'selected' : '' ?>><?= $s ?></option><?php endforeach; ?>
        </select>
        <div class="form-text"><?= h($T['sens_help']) ?></div>
      </div>
    </div>

    <fieldset class="border rounded p-3 mt-3">
      <legend class="float-none w-auto px-2 fs-6 mb-0"><?= h($T['presel']) ?></legend>
      <label class="form-label small text-body-secondary mb-1" for="org"><?= h($T['org']) ?></label>
      <input class="form-control" id="org" list="orglist" placeholder="<?= h($T['org_ph']) ?>">
      <datalist id="orglist"></datalist>
      <div id="orgchips" class="mt-2"></div>
      <div class="form-text"><?= h($T['org_help']) ?></div>
      <div class="row g-2 mt-1 align-items-end">
        <div class="col-12 small text-body-secondary"><?= h($T['len']) ?></div>
        <div class="col-6 col-md-3"><input class="form-control" id="len_min" type="number" min="1" max="100000" placeholder="<?= h($T['len_min']) ?>"></div>
        <div class="col-6 col-md-3"><input class="form-control" id="len_max" type="number" min="1" max="100000" placeholder="<?= h($T['len_max']) ?>"></div>
        <div class="col-12 col-md-6">
          <label class="form-label small text-body-secondary mb-1" for="new_days"><?= h($T['new_days']) ?></label>
          <input class="form-control" id="new_days" type="number" min="1" max="100000" placeholder="<?= h($T['new_days_ph']) ?>">
        </div>
      </div>
    </fieldset>

    <details class="mt-3">
      <summary class="small"><?= h($T['advanced']) ?></summary>
      <div class="row g-2 mt-1">
        <div class="col-6 col-md-3"><label class="form-label small mb-1" for="evalue"><?= h($T['evalue']) ?></label>
          <input class="form-control" id="evalue" value="0.001"></div>
        <div class="col-6 col-md-3"><label class="form-label small mb-1" for="maxhits"><?= h($T['maxhits']) ?></label>
          <input class="form-control" id="maxhits" type="number" min="1" max="500" value="25"></div>
      </div>
    </details>

    <div class="d-grid mt-3"><button class="btn btn-primary btn-lg" type="submit" id="go"><?= h($T['go']) ?></button></div>
  </form>

  <section id="result" class="mb-4"></section>

  <details class="card card-body mb-4" id="fullbox">
    <summary class="fw-semibold"><?= h($T['full_h']) ?></summary>
    <p class="small text-body-secondary mt-2 mb-2"><?= h($T['full_text']) ?></p>
    <form id="fform" class="row g-2">
      <div class="col-md-8"><input type="email" class="form-control" id="email" required placeholder="<?= h($T['email']) ?>"></div>
      <div class="col-md-4 d-grid"><button class="btn btn-outline-primary" type="submit"><?= h($T['full_go']) ?></button></div>
    </form>
    <div id="fullstatus" class="mt-3"></div>
  </details>
</main>

<footer class="border-top py-3 small text-body-secondary">
  <div class="container d-flex flex-wrap gap-3">
    <a href="api.php?r=help"><?= h($T['experts']) ?></a>
    <a href="https://github.com/gerontec/blastDiamond" target="_blank" rel="noopener"><?= h($T['source']) ?></a>
    <span><?= h($T['data']) ?></span>
  </div>
</footer>
<script src="assets/app.js"></script>
</body>
</html>
