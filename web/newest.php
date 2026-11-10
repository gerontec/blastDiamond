<?php
/* The 60 sequences most recently added to NCBI nr, one list per view (both cached hourly by the worker):
   ?list=human     Homo sapiens, longer than 10 aa (api.php?r=newest)
   ?list=pathogen  carrying a pathogenicity mark at 85 % identity or more (api.php?r=newest_pathogen) */
declare(strict_types=1);
$list = (($_GET['list'] ?? '') === 'pathogen') ? 'pathogen' : 'human';
$lang = (($_GET['lang'] ?? '') === 'en' || (!isset($_GET['lang'])
        && !str_starts_with(strtolower($_SERVER['HTTP_ACCEPT_LANGUAGE'] ?? 'de'), 'de'))) ? 'en' : 'de';
$T = [
 'de' => ['page' => 'Die 60 neuesten Sequenzen in nr',
          'title' => 'Die 60 neuesten menschlichen Sequenzen in nr (länger als 10 Aminosäuren)', 'back' => '← zur Suche',
          'lead' => 'Proteinsequenzen von Homo sapiens, die zuletzt in die Datenbank aufgenommen wurden (tägliche Ergänzung aus GenBank und RefSeq). Die Liste wird stündlich erneuert.',
          'updated' => 'Stand', 'acc' => 'Accession', 'desc' => 'Beschreibung', 'len' => 'Länge (aa)', 'ncbi' => 'NCBI-Datum',
          'ptitle' => 'Die 60 neuesten Sequenzen mit Pathogenitäts-Merkmal (ab 85 % Identität)',
          'to_pathogen' => '➜ Die 60 neuesten Sequenzen mit Pathogenitäts-Merkmal',
          'to_human' => '➜ Die 60 neuesten menschlichen Sequenzen',
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
          'title' => 'The 60 newest human sequences in nr (longer than 10 amino acids)', 'back' => '← back to the search',
          'lead' => 'Protein sequences of Homo sapiens most recently added to the database (daily additions from GenBank and RefSeq). The list is renewed every hour.',
          'updated' => 'As of', 'acc' => 'Accession', 'desc' => 'Description', 'len' => 'Length (aa)', 'ncbi' => 'NCBI date',
          'ptitle' => 'The 60 newest sequences carrying a pathogenicity mark (85 % identity or more)',
          'to_pathogen' => '➜ The 60 newest sequences carrying a pathogenicity mark',
          'to_human' => '➜ The 60 newest human sequences',
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
<title><?= h($list === 'pathogen' ? $T['ptitle'] : $T['title']) ?></title>
<link rel="stylesheet" href="assets/bootstrap.min.css">
<link rel="stylesheet" href="assets/app.css">
</head>
<body>
<nav class="navbar bg-body-tertiary border-bottom mb-4">
  <div class="container">
    <a class="navbar-brand fw-semibold" href="./?lang=<?= $lang ?>">DIAMOND · NCBI nr</a>
    <div class="small"><a href="?lang=de&amp;list=<?= $list ?>" class="<?= $lang === 'de' ? 'fw-bold' : '' ?>">DE</a> · <a href="?lang=en&amp;list=<?= $list ?>" class="<?= $lang === 'en' ? 'fw-bold' : '' ?>">EN</a></div>
  </div>
</nav>
<main class="container mb-5" id="newest" data-lang="<?= $lang ?>" data-list="<?= $list ?>"
  <?php foreach ($T as $k => $v): ?> data-t-<?= h($k) ?>="<?= h($v) ?>"<?php endforeach; ?>>
  <p><a href="./?lang=<?= $lang ?>"><?= h($T['back']) ?></a></p>
<?php if ($list === 'pathogen'): ?>
  <h1 class="h3"><?= h($T['ptitle']) ?></h1>
  <p class="text-body-secondary"><?= h($T['plead']) ?></p>
  <div id="pathogen"><div class="text-body-secondary"><?= h($T['loading']) ?></div></div>
  <p class="mt-4 small"><a href="?lang=<?= $lang ?>"><?= h($T['to_human']) ?></a></p>
<?php else: ?>
  <h1 class="h3"><?= h($T['title']) ?></h1>
  <p class="text-body-secondary"><?= h($T['lead']) ?></p>
  <div id="list"><div class="text-body-secondary"><?= h($T['loading']) ?></div></div>
  <p class="mt-4 small"><a href="?lang=<?= $lang ?>&amp;list=pathogen"><?= h($T['to_pathogen']) ?></a></p>
<?php endif; ?>
</main>
<script src="assets/newest.js"></script>
</body>
</html>
