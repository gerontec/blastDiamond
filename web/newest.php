<?php
/* The 60 Homo sapiens sequences longer than 10 aa most recently added to NCBI nr (cached hourly by the worker, api.php?r=newest). */
declare(strict_types=1);
$lang = (($_GET['lang'] ?? '') === 'en' || (!isset($_GET['lang'])
        && !str_starts_with(strtolower($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? 'de'), 'de'))) ? 'en' : 'de';
$T = [
 'de' => ['title' => 'Die 60 neuesten menschlichen Sequenzen in nr (länger als 10 Aminosäuren)', 'back' => '← zur Suche',
          'lead' => 'Proteinsequenzen von Homo sapiens, die zuletzt in die Datenbank aufgenommen wurden (tägliche Ergänzung aus GenBank und RefSeq). Die Liste wird stündlich erneuert.',
          'updated' => 'Stand', 'acc' => 'Accession', 'desc' => 'Beschreibung', 'len' => 'Länge (aa)', 'ncbi' => 'NCBI-Datum',
          'added' => 'aufgenommen', 'src' => 'Quelle', 'search' => 'Ähnliche suchen', 'loading' => 'Lade …', 'error' => 'Liste gerade nicht verfügbar.'],
 'en' => ['title' => 'The 60 newest human sequences in nr (longer than 10 amino acids)', 'back' => '← back to the search',
          'lead' => 'Protein sequences of Homo sapiens most recently added to the database (daily additions from GenBank and RefSeq). The list is renewed every hour.',
          'updated' => 'As of', 'acc' => 'Accession', 'desc' => 'Description', 'len' => 'Length (aa)', 'ncbi' => 'NCBI date',
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
<title><?= h($T['title']) ?></title>
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
  <h1 class="h3"><?= h($T['title']) ?></h1>
  <p class="text-body-secondary"><?= h($T['lead']) ?></p>
  <div id="list"><div class="text-body-secondary"><?= h($T['loading']) ?></div></div>
</main>
<script src="assets/newest.js"></script>
</body>
</html>
