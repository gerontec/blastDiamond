<?php
/* The 60 Homo sapiens sequences longer than 10 aa most recently added to NCBI nr (cached hourly by the worker, api.php?r=newest). */
declare(strict_types=1);
$lang = (($_GET['lang'] ?? '') === 'en' || (!isset($_GET['lang'])
        && !str_starts_with(strtolower($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? 'de'), 'de'))) ? 'en' : 'de';
$T = [
 'de' => ['page' => 'Die 60 neuesten Sequenzen in nr',
          'title' => 'Menschliche Sequenzen (länger als 10 Aminosäuren)', 'back' => '← zur Suche',
          'lead' => 'Proteinsequenzen von Homo sapiens, die zuletzt in die Datenbank aufgenommen wurden (tägliche Ergänzung aus GenBank und RefSeq). Die Liste wird stündlich erneuert.',
          'updated' => 'Stand', 'acc' => 'Accession', 'desc' => 'Beschreibung', 'len' => 'Länge (aa)', 'ncbi' => 'NCBI-Datum',
          'ptitle' => 'Sequenzen mit Pathogenitäts-Merkmal (ab 85 % Identität)',
          'plead' => 'Neu aufgenommene Proteine, die einem handgeprüften Virulenzfaktor oder Toxin aus Swiss-Prot '
                   . 'ähnlich sind (Stichwort Virulence KW-0843 oder Toxin KW-0800). Das Merkmal gilt für das '
                   . 'einzelne Protein, nicht für den Organismus: das Spike von SARS-CoV-2 steht hier, sein '
                   . 'Nukleokapsid nicht. Gezeigt werden nur Treffer ab 85 % Identität - das ist praktisch '
                   . 'dasselbe Protein. Schwächere Ähnlichkeiten werden mitgeführt, aber nicht gezeigt: ein '
                   . 'konserviertes Enzym ohne jede Rolle bei einer Infektion erreicht 50 bis 60 %.',
          'org' => 'Organismus', 'ref' => 'ähnlich zu (Swiss-Prot)', 'pid' => 'Identität', 'kind' => 'Art',
          'virulence' => 'Virulenzfaktor', 'toxin' => 'Toxin', 'refs' => 'Referenzproteine',
          'pempty' => 'Noch keine markierten Sequenzen.',
          'added' => 'aufgenommen', 'src' => 'Quelle', 'search' => 'Ähnliche suchen', 'loading' => 'Lade …', 'error' => 'Liste gerade nicht verfügbar.'],
 'en' => ['page' => 'The 60 newest sequences in nr',
          'title' => 'Human sequences (longer than 10 amino acids)', 'back' => '← back to the search',
          'lead' => 'Protein sequences of Homo sapiens most recently added to the database (daily additions from GenBank and RefSeq). The list is renewed every hour.',
          'updated' => 'As of', 'acc' => 'Accession', 'desc' => 'Description', 'len' => 'Length (aa)', 'ncbi' => 'NCBI date',
          'ptitle' => 'Sequences carrying a pathogenicity mark (85 % identity or more)',
          'plead' => 'Newly added proteins that are similar to a manually curated virulence factor or toxin in '
                   . 'Swiss-Prot (keyword Virulence KW-0843 or Toxin KW-0800). The mark belongs to the single '
                   . 'protein, not to the organism: the spike of SARS-CoV-2 is here, its nucleocapsid is not. '
                   . 'Only hits of 85 % identity or more are shown - that is practically the same protein. '
                   . 'Weaker similarities are kept but not shown: a conserved enzyme with no role in an '
                   . 'infection reaches 50 to 60 %.',
          'org' => 'Organism', 'ref' => 'similar to (Swiss-Prot)', 'pid' => 'identity', 'kind' => 'Kind',
          'virulence' => 'virulence factor', 'toxin' => 'toxin', 'refs' => 'reference proteins',
          'pempty' => 'No flagged sequences yet.',
          'added' => 'added', 'src' => 'Source', 'search' => 'Search similar', 'loading' => 'Loading …', 'error' => 'List not available right now.'],
][$lang];
function h($s): string { return htmlspecialchars((string)($s ?? ''), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
header('Cache-Control: public, max-age=300');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'");
?><!doctype html>
<html lang="<?= $lang ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= h($T['page']) ?></title>
<link rel="stylesheet" href="assets/bootstrap.min.css">
<link rel="stylesheet" href="assets/app.css">
</head>
<body>
<nav class="navbar bg-body-tertiary border-bottom mb-4">
  <div class="container">
    <a class="navbar-brand fw-semibold" href="./?lang=<?= $lang ?>">DIAMOND · NCBI nr</a>
    <div class="small"><a href="?lang=de" class="<?= $lang === 'de' ? 'fw-bold' : '' ?>">DE</a> · <a href="?lang=en" class="<?= $lang === 'en' ? 'fw-bold' : '' ?>">EN</a></div>
  </div>
</nav>
<main class="container mb-5" id="newest" data-lang="<?= $lang ?>"
  <?php foreach ($T as $k => $v): ?> data-t-<?= h($k) ?>="<?= h($v) ?>"<?php endforeach; ?>>
  <p><a href="./?lang=<?= $lang ?>"><?= h($T['back']) ?></a></p>
  <h1 class="h3"><?= h($T['page']) ?></h1>

  <h2 class="h4 mt-4" id="human"><?= h($T['title']) ?></h2>
  <p class="text-body-secondary"><?= h($T['lead']) ?></p>
  <div id="list"><div class="text-body-secondary"><?= h($T['loading']) ?></div></div>

  <h2 class="h4 mt-5" id="pathogen-head"><?= h($T['ptitle']) ?></h2>
  <p class="text-body-secondary small"><?= h($T['plead']) ?></p>
  <div id="pathogen" style="scroll-margin-top:1rem"><div class="text-body-secondary"><?= h($T['loading']) ?></div></div>
</main>
<script src="assets/newest.js"></script>
</body>
</html>
