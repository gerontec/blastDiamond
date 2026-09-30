<?php
// Minimale JSON-API auf nr_full.dmnd (DIAMOND) und die blast_*-Metadaten in wagodb.
// Metadaten frei (Ratenbegrenzung je IP), Suchen nur mit X-Api-Key; ausgefuehrt vom
// Worker ~/python/blast_api_worker.py, immer nur ein Auftrag gleichzeitig.
declare(strict_types=1);

const RATE_PER_MIN = 60;
const MAX_LETTERS = 100000;
const MAX_RECORDS = 50;
const MAX_OPEN_JOBS = 3;
const RAM_MAX_GB = 48;         // wie blast_api_worker.py: Grenze = min(RAM_MAX_GB, MemAvailable - RAM_RESERVE_GB)
const RAM_RESERVE_GB = 4;
const AMYLO_MAX = 5000;        // Fenster = Laenge - window + 1; 5000 Reste sind ~8 min auf der P4
const SENS = ['fast', 'mid-sensitive', 'sensitive', 'more-sensitive', 'very-sensitive', 'ultra-sensitive'];
// Anfrage per Accession statt Sequenz: Residuen aus der DIAMOND-nr, maskierte Bereiche (X) holt nr_seq.py bei NCBI
const NR_SEQ_PY = '/home/gh/iver_sim/mamba/envs/iver/bin/python';
const NR_SEQ = '/home/gh/python/nr_seq.py';

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: X-Api-Key, Content-Type');
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') exit;

function out(array $data, int $code = 200): never
{
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
    exit;
}

function fail(string $msg, int $code): never
{
    out(['error' => $msg], $code);
}

function param(string $name, ?string $default = null): ?string
{
    static $body = null;
    if ($body === null) {
        $body = [];
        if (str_starts_with($_SERVER['CONTENT_TYPE'] ?? '', 'application/json')) {
            $body = json_decode(file_get_contents('php://input') ?: '[]', true) ?: [];
        }
    }
    $v = $body[$name] ?? $_POST[$name] ?? $_GET[$name] ?? $default;
    return $v === null ? null : trim((string)$v);
}

$ini = parse_ini_file('/etc/blast_api.ini');
try {
    $db = new PDO("mysql:host=localhost;dbname={$ini['db']};charset=utf8mb4", $ini['user'], $ini['pass'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (Throwable $e) {
    fail('Datenbank nicht erreichbar', 503);
}

// Ratenbegrenzung: IPv4-Adresse bzw. IPv6-/64
$ip = inet_pton($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1') ?: "\0";
if (strlen($ip) === 16) $ip = substr($ip, 0, 8);
$minute = intdiv(time(), 60);
$db->prepare('INSERT INTO blast_api_hits (ip, minute, n) VALUES (?, ?, 1) ON DUPLICATE KEY UPDATE n = n + 1')
   ->execute([$ip, $minute]);
$st = $db->prepare('SELECT n FROM blast_api_hits WHERE ip = ? AND minute = ?');
$st->execute([$ip, $minute]);
if ((int)$st->fetchColumn() > RATE_PER_MIN) {
    header('Retry-After: 60');
    fail('Zu viele Anfragen (max. ' . RATE_PER_MIN . '/min)', 429);
}

function status(PDO $db): array
{
    $r = $db->query("SELECT release_id, nr_datum, nr_sequenzen, dmnd_sequenzen, dmnd_hash, meta_importiert
                     FROM blast_release WHERE quelle = 'nr' AND aktiv = 1 ORDER BY nr_datum DESC LIMIT 1")->fetch() ?: [];
    $idx = (int)$db->query("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE()
                            AND table_name = 'blast_acc' AND index_name = 'idx_acc'")->fetchColumn();
    return [$r, $idx > 0];
}

function ram_status(PDO $db): array
{
    // Apache sieht /proc/meminfo nicht (ProcSubset=pid): der Worker schreibt den Stand alle 30 s
    $st = $db->query('SELECT frei_gb, gesamt_gb, swap_frei_gb, stand FROM blast_ram_status WHERE id = 1')->fetch() ?: [];
    $frei = (float)($st['frei_gb'] ?? 0);
    $k = $db->query('SELECT mode, sensitivity, COUNT(*) AS messungen, ROUND(MAX((ram_gb - 0.5) / eff_gletters), 2) AS gb_je_mrd_reste
                     FROM blast_ram_mess WHERE ok = 1 AND eff_gletters >= 0.05 GROUP BY mode, sensitivity')->fetchAll();
    return ['frei_gb' => round($frei, 1), 'gesamt_gb' => round((float)($st['gesamt_gb'] ?? 0), 1),
            'swap_frei_gb' => round((float)($st['swap_frei_gb'] ?? 0), 1), 'stand' => $st['stand'] ?? null, 'grenze_gb' => round(min(RAM_MAX_GB, $frei - RAM_RESERVE_GB), 1),
            'reserve_gb' => RAM_RESERVE_GB, 'max_gb' => RAM_MAX_GB, 'messwerte' => $k];
}

function taxon_names(PDO $db, array $taxids): array
{
    $taxids = array_values(array_unique(array_filter(array_map('intval', $taxids))));
    if (!$taxids) return [];
    $st = $db->prepare('SELECT taxid, name FROM blast_taxon WHERE taxid IN (' . implode(',', array_fill(0, count($taxids), '?')) . ')');
    $st->execute($taxids);
    return array_column($st->fetchAll(), 'name', 'taxid');
}

function api_key(PDO $db): array
{
    $k = $_SERVER['HTTP_X_API_KEY'] ?? '';
    if ($k === '') fail('X-Api-Key fehlt', 401);
    $st = $db->prepare('SELECT key_id, name, jobs_pro_tag FROM blast_api_key WHERE key_hash = ? AND aktiv = 1');
    $st->execute([hash('sha256', $k)]);
    return $st->fetch() ?: fail('Ungueltiger API-Schluessel', 403);
}

$base = 'https://' . ($_SERVER['HTTP_HOST'] ?? 'yt.heissa.de') . strtok($_SERVER['REQUEST_URI'] ?? '/blast/api.php', '?');

switch (param('r', 'help')) {

case 'help':
    out([
        'api' => 'DIAMOND-Suche gegen NCBI nr + Metadaten (dell-3660)',
        'endpunkte' => [
            'GET ?r=release' => 'geladener nr-Stand, Tagesdeltas, Bereitschaft',
            'GET ?r=acc&acc=YP_009724390.1' => 'Sequenz-Metadaten zu einer Accession (ohne .Version: neueste)',
            'GET ?r=taxon&taxid=9606 | &name=Homo sapiens' => 'Taxon mit Abstammungslinie',
            'POST ?r=search (X-Api-Key)' => 'seq (FASTA oder rohe Sequenz) ODER acc=YP_009724390.1 [range=319-541] (Sequenz aus nr), mode=blastp|blastx, taxonlist=4751,9606 (samt Untertaxa), len_min=100, len_max=400 (Laenge der Treffersequenz), neu_tage=90 (bei NCBI angelegt in den letzten N Tagen), evalue=0.001, max_target_seqs=25, sensitivity=' . implode('|', SENS),
            'GET ?r=job&id=…' => 'Status und Treffer (mit Titel und Taxon aus MariaDB)',
            'POST ?r=amylo (X-Api-Key)' => 'Amyloid-Neigung je Fenster (AmyloDeep auf der Tesla P4): '
                . 'acc=YP_009724390.1 [von=194&bis=203] oder seq=…, window=4..40 (Standard 10)',
            'GET ?r=amylojob&id=…' => 'Status und Fensterwerte des AmyloDeep-Auftrags',
        ],
        'ram' => 'Vor jeder Suche: Bedarf = 0,5 GB + k * min(Reste der DB, block_size) (Mrd. Reste), k aus Messungen '
            . 'frueherer Suchen (?r=release -> ram.messwerte); block_size (2 .. 0.1) so, dass der Bedarf unter '
            . 'min(' . RAM_MAX_GB . ' GB, freier Speicher - ' . RAM_RESERVE_GB . ' GB) bleibt, sonst Warten. '
            . 'Der Auftrag meldet block_size, ram_schaetz_gb, ram_gb (gemessen) und ram_frei_gb.',
        'grenzen' => ['anfragen_pro_minute' => RATE_PER_MIN, 'max_buchstaben' => MAX_LETTERS,
                      'max_sequenzen' => MAX_RECORDS, 'offene_auftraege_pro_schluessel' => MAX_OPEN_JOBS],
        'schluessel' => 'auf Anfrage bei gh@heissa.de',
    ]);

case 'release':
    [$nr, $idx] = status($db);
    if (!$nr) fail('Kein nr-Stand geladen', 503);
    $st = $db->prepare("SELECT quelle, datei, DATE(nr_datum) AS datum, nr_sequenzen AS neue_sequenzen
                        FROM blast_release WHERE quelle <> 'nr' AND nr_datum >= ? ORDER BY nr_datum");
    $st->execute([$nr['nr_datum']]);
    $q = $db->query("SELECT COUNT(*) FROM blast_job WHERE status IN ('queued', 'running')")->fetchColumn();
    out([
        'nr' => $nr,
        'tagesdeltas' => $st->fetchAll(),
        'bereit' => ['suche' => $nr['dmnd_hash'] !== null, 'metadaten' => $nr['meta_importiert'] !== null && $idx],
        'warteschlange' => (int)$q,
        'ram' => ram_status($db),
    ]);

case 'acc':
    $acc = param('acc', '');
    if (!preg_match('/^[A-Za-z0-9_]{3,30}(\.\d{1,4})?$/', $acc)) fail('acc ungueltig', 400);
    [, $idx] = status($db);
    if (!$idx) fail('Accession-Index wird noch aufgebaut', 503);
    if (str_contains($acc, '.')) {
        $st = $db->prepare('SELECT oid, acc, taxid FROM blast_acc WHERE acc = ? LIMIT 1');
        $st->execute([$acc]);
    } else {
        $st = $db->prepare('SELECT oid, acc, taxid FROM blast_acc WHERE acc LIKE ? ORDER BY acc DESC LIMIT 1');
        $st->execute([$acc . '.%']);
    }
    $a = $st->fetch() ?: fail('Accession nicht in der Datenbank', 404);
    $st = $db->prepare('SELECT oid, len AS laenge, n_acc, title AS titel, quelle, datei, nr_stand, ncbi_datum, hinzugefuegt
                        FROM blast_seq_herkunft WHERE oid = ?');
    $st->execute([$a['oid']]);
    $seq = $st->fetch();
    $st = $db->prepare('SELECT acc, taxid FROM blast_acc WHERE oid = ? ORDER BY pos LIMIT 200');
    $st->execute([$a['oid']]);
    $all = $st->fetchAll();
    $names = taxon_names($db, array_column($all, 'taxid'));
    foreach ($all as &$x) $x['taxon'] = $names[$x['taxid']] ?? null;
    out(['acc' => $a['acc'], 'taxid' => (int)$a['taxid'], 'taxon' => $names[$a['taxid']] ?? null,
         'sequenz' => $seq, 'identische_eintraege' => $all]);

case 'taxon':
    if (param('name') !== null) {
        $st = $db->prepare('SELECT taxid FROM blast_taxon WHERE name = ? LIMIT 1');
        $st->execute([param('name')]);
        $taxid = (int)($st->fetchColumn() ?: fail('Name unbekannt', 404));
    } else {
        $taxid = (int)param('taxid', '0');
    }
    $st = $db->prepare('SELECT taxid, parent, `rank`, name FROM blast_taxon WHERE taxid = ?');
    $line = [];
    for ($t = $taxid, $i = 0; $t && $i < 80; $i++) {
        $st->execute([$t]);
        $row = $st->fetch();
        if (!$row) break;
        $line[] = ['taxid' => (int)$row['taxid'], 'rank' => $row['rank'], 'name' => $row['name']];
        if ((int)$row['parent'] === $t) break;
        $t = (int)$row['parent'];
    }
    if (!$line) fail('Taxon unbekannt (oder Taxonomie noch nicht geladen)', 404);
    out(['taxon' => $line[0], 'linie' => array_reverse($line)]);

case 'search':
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') fail('POST erwartet', 405);
    $key = api_key($db);
    [$nr] = status($db);
    if (!$nr || $nr['dmnd_hash'] === null) fail('DIAMOND-Datenbank noch nicht bereit', 503);

    $mode = param('mode', 'blastp');
    if (!in_array($mode, ['blastp', 'blastx'], true)) fail('mode: blastp oder blastx', 400);
    $sens = param('sensitivity', 'sensitive');
    if (!in_array($sens, SENS, true)) fail('sensitivity: ' . implode('|', SENS), 400);
    $evalue = (float)param('evalue', '0.001');
    if ($evalue <= 0 || $evalue > 10) fail('evalue: 0 < e <= 10', 400);
    $max = (int)param('max_target_seqs', '25');
    if ($max < 1 || $max > 500) fail('max_target_seqs: 1..500', 400);
    $tax = param('taxonlist', '');
    if ($tax !== '' && !preg_match('/^\d{1,8}(,\d{1,8}){0,19}$/', $tax)) fail('taxonlist: bis 20 TaxIDs, kommagetrennt', 400);
    // Vorauswahl ueber MariaDB (dmnd_vorauswahl.py): nur die passenden Sequenzen werden durchsucht
    $lmin = param('len_min', '');
    $lmax = param('len_max', '');
    $neu = param('neu_tage', '');
    foreach (['len_min' => $lmin, 'len_max' => $lmax, 'neu_tage' => $neu] as $k => $v) {
        if ($v !== '' && (!ctype_digit($v) || (int)$v < 1 || (int)$v > 100000)) fail("$k: ganze Zahl 1..100000", 400);
    }
    if ($lmin !== '' && $lmax !== '' && (int)$lmin > (int)$lmax) fail('len_min > len_max', 400);

    $raw = str_replace("\r", '', param('seq', ''));
    $acc = param('acc', '');
    if ($acc !== '') {
        if ($raw !== '') fail('seq oder acc, nicht beides', 400);
        if ($mode !== 'blastp') fail('acc nur mit mode=blastp (nr enthaelt Proteine)', 400);
        if (!preg_match('/^[A-Za-z0-9_]{3,30}(\.\d{1,4})?$/', $acc)) fail('acc ungueltig', 400);
        $range = param('range', '');
        if ($range !== '' && (!preg_match('/^(\d{1,6})-(\d{1,6})$/', $range, $m) || $m[1] < 1 || $m[1] > $m[2])) {
            fail('range: von-bis, z.B. 319-541', 400);
        }
        $cmd = [NR_SEQ_PY, NR_SEQ, $acc];
        if ($range !== '') array_push($cmd, '--range', $range);
        $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
        $s = $p ? trim(stream_get_contents($pipes[1])) : '';
        if ($p) proc_close($p);
        if ($s === '') fail("Accession $acc nicht in der nr (dann seq uebergeben)", 404);
        $raw = '>' . $acc . ($range !== '' ? '_' . $range : '') . "\n" . $s;
    }
    if ($raw === '') fail('seq oder acc fehlt', 400);
    if ($raw[0] !== '>') $raw = ">query\n" . $raw;
    $fasta = '';
    $n = $letters = 0;
    foreach (preg_split('/^>/m', $raw, -1, PREG_SPLIT_NO_EMPTY) as $rec) {
        [$head, $body] = array_pad(explode("\n", $rec, 2), 2, '');
        $id = preg_replace('/[^A-Za-z0-9_.|-]/', '_', strtok(trim($head), " \t") ?: 'query' . ($n + 1));
        $s = strtoupper(preg_replace('/\s+/', '', $body));
        if ($s === '') fail("Sequenz $id ist leer", 400);
        if (!preg_match('/^[A-Z*-]+$/', $s)) fail("Sequenz $id enthaelt ungueltige Zeichen", 400);
        $fasta .= '>' . substr($id, 0, 60) . "\n" . chunk_split($s, 80, "\n");
        $n++;
        $letters += strlen($s);
    }
    if ($n > MAX_RECORDS) fail('Hoechstens ' . MAX_RECORDS . ' Sequenzen', 413);
    if ($letters > MAX_LETTERS) fail('Hoechstens ' . MAX_LETTERS . ' Buchstaben', 413);

    $st = $db->prepare("SELECT SUM(erstellt >= CURDATE()), SUM(status IN ('queued', 'running')) FROM blast_job WHERE key_id = ?");
    $st->execute([$key['key_id']]);
    [$today, $open] = array_map('intval', $st->fetch(PDO::FETCH_NUM));
    if ($today >= $key['jobs_pro_tag']) fail("Tageskontingent ({$key['jobs_pro_tag']}) erschoepft", 429);
    if ($open >= MAX_OPEN_JOBS) fail('Hoechstens ' . MAX_OPEN_JOBS . ' offene Auftraege je Schluessel', 429);

    $id = bin2hex(random_bytes(12));
    $db->prepare('INSERT INTO blast_job (job_id, key_id, mode, sensitivity, evalue, max_target_seqs, taxonlist, len_min, len_max, neu_tage,
                  query, n_query, query_letters) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
       ->execute([$id, $key['key_id'], $mode, $sens, $evalue, $max, $tax ?: null, $lmin === '' ? null : (int)$lmin,
                  $lmax === '' ? null : (int)$lmax, $neu === '' ? null : (int)$neu, $fasta, $n, $letters]);
    $pos = $db->query("SELECT COUNT(*) FROM blast_job WHERE status IN ('queued', 'running')")->fetchColumn();
    out(['job_id' => $id, 'status' => 'queued', 'position' => (int)$pos, 'url' => "$base?r=job&id=$id"], 202);

case 'job':
    $id = param('id', '');
    if (!preg_match('/^[0-9a-f]{24}$/', $id)) fail('id ungueltig', 400);
    $st = $db->prepare('SELECT job_id, status, mode, sensitivity, evalue, max_target_seqs, taxonlist, len_min, len_max, neu_tage,
                               n_query, query_letters, erstellt, gestartet, fertig, sekunden, release_id, dmnd_hash, n_hits,
                               vorauswahl, block_size, ram_schaetz_gb, ram_gb, ram_frei_gb, error
                        FROM blast_job WHERE job_id = ?');
    $st->execute([$id]);
    $job = $st->fetch() ?: fail('Auftrag unbekannt', 404);
    if ($job['status'] === 'queued') {
        $st = $db->prepare("SELECT COUNT(*) FROM blast_job WHERE status IN ('queued', 'running') AND erstellt <= ?");
        $st->execute([$job['erstellt']]);
        $job['position'] = (int)$st->fetchColumn();
    }
    if ($job['status'] === 'done') {
        [, $idx] = status($db);
        $st = $db->prepare($idx
            ? 'SELECT h.qseqid, h.sseqid, h.pident, h.length, h.mismatch, h.gapopen, h.qstart, h.qend, h.sstart, h.send,
                      h.evalue, h.bitscore, h.staxids, s.title, a.taxid
               FROM blast_job_hit h LEFT JOIN blast_acc a ON a.acc = h.sseqid LEFT JOIN blast_seq s ON s.oid = a.oid
               WHERE h.job_id = ? ORDER BY h.n'
            : 'SELECT * FROM blast_job_hit WHERE job_id = ? ORDER BY n');
        $st->execute([$id]);
        $hits = $st->fetchAll();
        $names = taxon_names($db, array_merge(...array_map(fn($h) => explode(';', $h['staxids']), $hits ?: [['staxids' => '']])));
        foreach ($hits as &$h) {
            unset($h['job_id'], $h['n'], $h['taxid']);
            $h['taxa'] = array_values(array_filter(array_map(fn($t) => $names[(int)$t] ?? null, explode(';', $h['staxids']))));
        }
        $job['hits'] = $hits;
    }
    out($job);

case 'amylo':
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') fail('POST erwartet', 405);
    $key = api_key($db);
    $w = (int)param('window', '10');
    if ($w < 4 || $w > 40) fail('window: 4..40', 400);
    $acc = param('acc', '');
    $raw = str_replace(["\r", "\n", ' '], '', param('seq', ''));
    if (($acc === '') === ($raw === '')) fail('entweder acc oder seq', 400);
    $von = param('von') !== null ? (int)param('von') : null;
    $bis = param('bis') !== null ? (int)param('bis') : null;
    if ($von !== null && $von < 1) fail('von: ab 1', 400);
    if ($von !== null && $bis !== null && $bis < $von + $w - 1) fail('Bereich kuerzer als window', 400);
    if ($acc !== '') {
        if (!preg_match('/^[A-Za-z0-9_]{3,30}(\.\d{1,4})?$/', $acc)) fail('acc ungueltig', 400);
    } else {
        $raw = strtoupper($raw);
        if (!preg_match('/^[A-Z]{' . $w . ',' . AMYLO_MAX . '}$/', $raw))
            fail('seq: nur Buchstaben, ' . $w . '..' . AMYLO_MAX . ' Reste', 400);
    }
    $st = $db->prepare("SELECT SUM(erstellt >= CURDATE()), SUM(status IN ('queued', 'running')) "
                       . 'FROM amyl_job WHERE key_id = ?');
    $st->execute([$key['key_id']]);
    [$today, $open] = array_map('intval', $st->fetch(PDO::FETCH_NUM));
    if ($today >= $key['jobs_pro_tag']) fail("Tageskontingent ({$key['jobs_pro_tag']}) erschoepft", 429);
    if ($open >= MAX_OPEN_JOBS) fail('Hoechstens ' . MAX_OPEN_JOBS . ' offene Auftraege je Schluessel', 429);

    $id = bin2hex(random_bytes(12));
    $db->prepare('INSERT INTO amyl_job (job_id, key_id, acc, bereich_von, bereich_bis, query, window_size) '
                 . 'VALUES (?, ?, ?, ?, ?, ?, ?)')
       ->execute([$id, $key['key_id'], $acc ?: null, $von, $bis, $raw ?: null, $w]);
    $pos = $db->query("SELECT COUNT(*) FROM amyl_job WHERE status IN ('queued', 'running')")->fetchColumn();
    out(['job_id' => $id, 'status' => 'queued', 'position' => (int)$pos, 'url' => "$base?r=amylojob&id=$id"], 202);

case 'amylojob':
    $id = param('id', '');
    if (!preg_match('/^[0-9a-f]{24}$/', $id)) fail('id ungueltig', 400);
    $st = $db->prepare('SELECT job_id, status, acc, bereich_von, bereich_bis, window_size, erstellt, gestartet, '
                       . 'fertig, sekunden, lauf_id, seq_id, error FROM amyl_job WHERE job_id = ?');
    $st->execute([$id]);
    $job = $st->fetch() ?: fail('Auftrag unbekannt', 404);
    if ($job['status'] === 'queued') {
        $st = $db->prepare("SELECT COUNT(*) FROM amyl_job WHERE status IN ('queued', 'running') AND erstellt <= ?");
        $st->execute([$job['erstellt']]);
        $job['position'] = (int)$st->fetchColumn();
    }
    if ($job['status'] === 'done') {
        $st = $db->prepare('SELECT s.acc, s.laenge, e.bewertet_von, e.bewertet_bis, e.avg_prob, e.max_prob, '
                           . 'e.max_pos, SUBSTRING(s.sequenz, e.max_pos, ?) AS max_fenster '
                           . 'FROM amyl_ergebnis e JOIN amyl_sequenz s ON s.seq_id = e.seq_id '
                           . 'WHERE e.lauf_id = ? AND e.seq_id = ?');
        $st->execute([$job['window_size'], $job['lauf_id'], $job['seq_id']]);
        $job['ergebnis'] = $st->fetch() ?: null;
        $st = $db->prepare('SELECT f.pos, SUBSTRING(s.sequenz, f.pos, ?) AS fenster, f.prob FROM amyl_fenster f '
                           . 'JOIN amyl_sequenz s ON s.seq_id = f.seq_id '
                           . 'WHERE f.lauf_id = ? AND f.seq_id = ? ORDER BY f.pos');
        $st->execute([$job['window_size'], $job['lauf_id'], $job['seq_id']]);
        $job['fenster'] = $st->fetchAll();
    }
    out($job);

default:
    fail('Unbekannter Endpunkt, siehe ?r=help', 404);
}
