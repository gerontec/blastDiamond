<?php
// Minimal JSON API on nr_full.dmnd (DIAMOND) and the blast_* metadata in wagodb.
// Metadata is open (rate limit per IP), searches need an X-Api-Key; they are run by the
// worker ~/python/blast_api_worker.py, one job at a time.
// Former German parameter names are still accepted as aliases: neu_tage -> new_days, von/bis -> from/to.
declare(strict_types=1);

const RATE_PER_MIN = 60;
const MAX_LETTERS = 100000;
const MAX_RECORDS = 50;
const MAX_OPEN_JOBS = 3;
const RAM_MAX_GB = 48;         // as in blast_api_worker.py: limit = min(RAM_MAX_GB, MemAvailable - RAM_RESERVE_GB)
const RAM_RESERVE_GB = 4;
const AMYLO_MAX = 5000;        // windows = length - window + 1; 5000 residues take ~8 min on the P4
const SENS = ['fast', 'mid-sensitive', 'sensitive', 'more-sensitive', 'very-sensitive', 'ultra-sensitive'];
// query by accession instead of sequence: residues from the DIAMOND nr, masked stretches (X) fetched from NCBI by nr_seq.py
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
    fail('database not reachable', 503);
}

// rate limit: IPv4 address or IPv6 /64
$ip = inet_pton($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1') ?: "\0";
if (strlen($ip) === 16) $ip = substr($ip, 0, 8);
$minute = intdiv(time(), 60);
$db->prepare('INSERT INTO blast_api_hits (ip, minute, n) VALUES (?, ?, 1) ON DUPLICATE KEY UPDATE n = n + 1')
   ->execute([$ip, $minute]);
$st = $db->prepare('SELECT n FROM blast_api_hits WHERE ip = ? AND minute = ?');
$st->execute([$ip, $minute]);
if ((int)$st->fetchColumn() > RATE_PER_MIN) {
    header('Retry-After: 60');
    fail('too many requests (max. ' . RATE_PER_MIN . '/min)', 429);
}

function status(PDO $db): array
{
    $r = $db->query("SELECT release_id, nr_datum AS nr_date, nr_sequenzen AS nr_sequences, dmnd_sequenzen AS dmnd_sequences,
                            dmnd_hash, meta_importiert AS metadata_imported
                     FROM blast_release WHERE quelle = 'nr' AND aktiv = 1 ORDER BY nr_datum DESC LIMIT 1")->fetch() ?: [];
    $idx = (int)$db->query("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE()
                            AND table_name = 'blast_acc' AND index_name = 'idx_acc'")->fetchColumn();
    return [$r, $idx > 0];
}

function ram_status(PDO $db): array
{
    // Apache cannot see /proc/meminfo (ProcSubset=pid): the worker writes the state every 30 s
    $st = $db->query('SELECT free_gb, total_gb, swap_free_gb, updated FROM blast_ram_status WHERE id = 1')->fetch() ?: [];
    $free = (float)($st['free_gb'] ?? 0);
    $k = $db->query('SELECT mode, sensitivity, COUNT(*) AS measurements, ROUND(MAX((ram_gb - 0.5) / eff_gletters), 2) AS gb_per_gletters
                     FROM blast_ram_log WHERE ok = 1 AND eff_gletters >= 0.05 GROUP BY mode, sensitivity')->fetchAll();
    return ['free_gb' => round($free, 1), 'total_gb' => round((float)($st['total_gb'] ?? 0), 1),
            'swap_free_gb' => round((float)($st['swap_free_gb'] ?? 0), 1), 'updated' => $st['updated'] ?? null,
            'limit_gb' => round(min(RAM_MAX_GB, $free - RAM_RESERVE_GB), 1),
            'reserve_gb' => RAM_RESERVE_GB, 'max_gb' => RAM_MAX_GB, 'k_measured' => $k];
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
    if ($k === '') fail('X-Api-Key missing', 401);
    $st = $db->prepare('SELECT key_id, name, jobs_pro_tag AS jobs_per_day FROM blast_api_key WHERE key_hash = ? AND aktiv = 1');
    $st->execute([hash('sha256', $k)]);
    return $st->fetch() ?: fail('invalid API key', 403);
}

function quota(PDO $db, string $table, array $key): void
{
    $st = $db->prepare("SELECT SUM(erstellt >= CURDATE()), SUM(status IN ('queued', 'running')) FROM $table WHERE key_id = ?");
    $st->execute([$key['key_id']]);
    [$today, $open] = array_map('intval', $st->fetch(PDO::FETCH_NUM));
    if ($today >= $key['jobs_per_day']) fail("daily quota ({$key['jobs_per_day']}) used up", 429);
    if ($open >= MAX_OPEN_JOBS) fail('at most ' . MAX_OPEN_JOBS . ' open jobs per key', 429);
}

$base = 'https://' . ($_SERVER['HTTP_HOST'] ?? 'yt.heissa.de') . strtok($_SERVER['REQUEST_URI'] ?? '/blast/api.php', '?');

switch (param('r', 'help')) {

case 'help':
    out([
        'api' => 'DIAMOND search against NCBI nr + metadata (dell-3660)',
        'endpoints' => [
            'GET ?r=release' => 'loaded nr release, daily deltas, readiness, queue, memory',
            'GET ?r=acc&acc=YP_009724390.1' => 'sequence metadata for an accession (without .version: latest)',
            'GET ?r=taxon&taxid=9606 | &name=Homo sapiens' => 'taxon with its lineage',
            'POST ?r=search (X-Api-Key)' => 'seq (FASTA or raw sequence) OR acc=YP_009724390.1 [range=319-541] (sequence from nr), '
                . 'mode=blastp|blastx, taxonlist=4751,9606 (including sub-taxa), len_min=100, len_max=400 (length of the hit '
                . 'sequence), new_days=90 (created at NCBI within the last N days), evalue=0.001, max_target_seqs=25, '
                . 'sensitivity=' . implode('|', SENS),
            'GET ?r=job&id=…' => 'status and hits (with title and taxon from MariaDB)',
            'POST ?r=amylo (X-Api-Key)' => 'amyloid propensity per window (AmyloDeep on the Tesla P4): '
                . 'acc=YP_009724390.1 [from=194&to=203] or seq=…, window=4..40 (default 10)',
            'GET ?r=amylojob&id=…' => 'status and window scores of the AmyloDeep job',
        ],
        'memory' => 'Before every search: need = 0.5 GB + k * min(DB letters, block_size) (billions of letters), k from '
            . 'measurements of earlier searches (?r=release -> ram.k_measured); block_size (2 .. 0.1) is chosen so '
            . 'that the need stays below min(' . RAM_MAX_GB . ' GB, free memory - ' . RAM_RESERVE_GB . ' GB), '
            . 'otherwise the job waits. The job reports preselect, block_size, ram_est_gb, ram_gb (measured) and ram_free_gb.',
        'limits' => ['requests_per_minute' => RATE_PER_MIN, 'max_letters' => MAX_LETTERS,
                     'max_sequences' => MAX_RECORDS, 'open_jobs_per_key' => MAX_OPEN_JOBS],
        'keys' => 'on request from gh@heissa.de',
    ]);

case 'release':
    [$nr, $idx] = status($db);
    if (!$nr) fail('no nr release loaded', 503);
    $st = $db->prepare("SELECT quelle AS source, datei AS file, DATE(nr_datum) AS date, nr_sequenzen AS new_sequences
                        FROM blast_release WHERE quelle <> 'nr' AND nr_datum >= ? ORDER BY nr_datum");
    $st->execute([$nr['nr_date']]);
    $q = $db->query("SELECT COUNT(*) FROM blast_job WHERE status IN ('queued', 'running')")->fetchColumn();
    out([
        'nr' => $nr,
        'daily_deltas' => $st->fetchAll(),
        'ready' => ['search' => $nr['dmnd_hash'] !== null, 'metadata' => $nr['metadata_imported'] !== null && $idx],
        'queue' => (int)$q,
        'ram' => ram_status($db),
    ]);

case 'acc':
    $acc = param('acc', '');
    if (!preg_match('/^[A-Za-z0-9_]{3,30}(\.\d{1,4})?$/', $acc)) fail('invalid acc', 400);
    [, $idx] = status($db);
    if (!$idx) fail('accession index is still being built', 503);
    if (str_contains($acc, '.')) {
        $st = $db->prepare('SELECT oid, acc, taxid FROM blast_acc WHERE acc = ? LIMIT 1');
        $st->execute([$acc]);
    } else {
        $st = $db->prepare('SELECT oid, acc, taxid FROM blast_acc WHERE acc LIKE ? ORDER BY acc DESC LIMIT 1');
        $st->execute([$acc . '.%']);
    }
    $a = $st->fetch() ?: fail('accession not in the database', 404);
    $st = $db->prepare('SELECT oid, len AS length, n_acc, title, quelle AS source, datei AS file, nr_stand AS nr_release,
                               ncbi_datum AS ncbi_date, hinzugefuegt AS added
                        FROM blast_seq_herkunft WHERE oid = ?');
    $st->execute([$a['oid']]);
    $seq = $st->fetch();
    $st = $db->prepare('SELECT acc, taxid FROM blast_acc WHERE oid = ? ORDER BY pos LIMIT 200');
    $st->execute([$a['oid']]);
    $all = $st->fetchAll();
    $names = taxon_names($db, array_column($all, 'taxid'));
    foreach ($all as &$x) $x['taxon'] = $names[$x['taxid']] ?? null;
    out(['acc' => $a['acc'], 'taxid' => (int)$a['taxid'], 'taxon' => $names[$a['taxid']] ?? null,
         'sequence' => $seq, 'identical_entries' => $all]);

case 'taxon':
    if (param('name') !== null) {
        $st = $db->prepare('SELECT taxid FROM blast_taxon WHERE name = ? LIMIT 1');
        $st->execute([param('name')]);
        $taxid = (int)($st->fetchColumn() ?: fail('unknown name', 404));
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
    if (!$line) fail('unknown taxon (or taxonomy not loaded yet)', 404);
    out(['taxon' => $line[0], 'lineage' => array_reverse($line)]);

case 'search':
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') fail('POST expected', 405);
    $key = api_key($db);
    [$nr] = status($db);
    if (!$nr || $nr['dmnd_hash'] === null) fail('DIAMOND database not ready yet', 503);

    $mode = param('mode', 'blastp');
    if (!in_array($mode, ['blastp', 'blastx'], true)) fail('mode: blastp or blastx', 400);
    $sens = param('sensitivity', 'sensitive');
    if (!in_array($sens, SENS, true)) fail('sensitivity: ' . implode('|', SENS), 400);
    $evalue = (float)param('evalue', '0.001');
    if ($evalue <= 0 || $evalue > 10) fail('evalue: 0 < e <= 10', 400);
    $max = (int)param('max_target_seqs', '25');
    if ($max < 1 || $max > 500) fail('max_target_seqs: 1..500', 400);
    $tax = param('taxonlist', '');
    if ($tax !== '' && !preg_match('/^\d{1,8}(,\d{1,8}){0,19}$/', $tax)) fail('taxonlist: up to 20 TaxIDs, comma-separated', 400);
    // pre-selection via MariaDB (dmnd_preselect.py): only the matching sequences are searched
    $lmin = param('len_min', '');
    $lmax = param('len_max', '');
    $new = param('new_days', param('neu_tage', ''));
    foreach (['len_min' => $lmin, 'len_max' => $lmax, 'new_days' => $new] as $k => $v) {
        if ($v !== '' && (!ctype_digit($v) || (int)$v < 1 || (int)$v > 100000)) fail("$k: integer 1..100000", 400);
    }
    if ($lmin !== '' && $lmax !== '' && (int)$lmin > (int)$lmax) fail('len_min > len_max', 400);

    $raw = str_replace("\r", '', param('seq', ''));
    $acc = param('acc', '');
    if ($acc !== '') {
        if ($raw !== '') fail('seq or acc, not both', 400);
        if ($mode !== 'blastp') fail('acc only with mode=blastp (nr holds proteins)', 400);
        if (!preg_match('/^[A-Za-z0-9_]{3,30}(\.\d{1,4})?$/', $acc)) fail('invalid acc', 400);
        $range = param('range', '');
        if ($range !== '' && (!preg_match('/^(\d{1,6})-(\d{1,6})$/', $range, $m) || $m[1] < 1 || $m[1] > $m[2])) {
            fail('range: from-to, e.g. 319-541', 400);
        }
        $cmd = [NR_SEQ_PY, NR_SEQ, $acc];
        if ($range !== '') array_push($cmd, '--range', $range);
        $p = proc_open($cmd, [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
        $s = $p ? trim(stream_get_contents($pipes[1])) : '';
        if ($p) proc_close($p);
        if ($s === '') fail("accession $acc not in nr (pass seq instead)", 404);
        $raw = '>' . $acc . ($range !== '' ? '_' . $range : '') . "\n" . $s;
    }
    if ($raw === '') fail('seq or acc missing', 400);
    if ($raw[0] !== '>') $raw = ">query\n" . $raw;
    $fasta = '';
    $n = $letters = 0;
    foreach (preg_split('/^>/m', $raw, -1, PREG_SPLIT_NO_EMPTY) as $rec) {
        [$head, $body] = array_pad(explode("\n", $rec, 2), 2, '');
        $id = preg_replace('/[^A-Za-z0-9_.|-]/', '_', strtok(trim($head), " \t") ?: 'query' . ($n + 1));
        $s = strtoupper(preg_replace('/\s+/', '', $body));
        if ($s === '') fail("sequence $id is empty", 400);
        if (!preg_match('/^[A-Z*-]+$/', $s)) fail("sequence $id contains invalid characters", 400);
        $fasta .= '>' . substr($id, 0, 60) . "\n" . chunk_split($s, 80, "\n");
        $n++;
        $letters += strlen($s);
    }
    if ($n > MAX_RECORDS) fail('at most ' . MAX_RECORDS . ' sequences', 413);
    if ($letters > MAX_LETTERS) fail('at most ' . MAX_LETTERS . ' letters', 413);
    quota($db, 'blast_job', $key);

    $id = bin2hex(random_bytes(12));
    $db->prepare('INSERT INTO blast_job (job_id, key_id, mode, sensitivity, evalue, max_target_seqs, taxonlist, len_min, len_max, new_days,
                  query, n_query, query_letters) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
       ->execute([$id, $key['key_id'], $mode, $sens, $evalue, $max, $tax ?: null, $lmin === '' ? null : (int)$lmin,
                  $lmax === '' ? null : (int)$lmax, $new === '' ? null : (int)$new, $fasta, $n, $letters]);
    $pos = $db->query("SELECT COUNT(*) FROM blast_job WHERE status IN ('queued', 'running')")->fetchColumn();
    out(['job_id' => $id, 'status' => 'queued', 'position' => (int)$pos, 'url' => "$base?r=job&id=$id"], 202);

case 'job':
    $id = param('id', '');
    if (!preg_match('/^[0-9a-f]{24}$/', $id)) fail('invalid id', 400);
    $st = $db->prepare('SELECT job_id, status, mode, sensitivity, evalue, max_target_seqs, taxonlist, len_min, len_max, new_days,
                               n_query, query_letters, erstellt AS created, gestartet AS started, fertig AS finished,
                               sekunden AS seconds, release_id, dmnd_hash, n_hits,
                               preselect, block_size, ram_est_gb, ram_gb, ram_free_gb, error
                        FROM blast_job WHERE job_id = ?');
    $st->execute([$id]);
    $job = $st->fetch() ?: fail('unknown job', 404);
    if ($job['status'] === 'queued') {
        $st = $db->prepare("SELECT COUNT(*) FROM blast_job WHERE status IN ('queued', 'running') AND erstellt <= ?");
        $st->execute([$job['created']]);
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
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') fail('POST expected', 405);
    $key = api_key($db);
    $w = (int)param('window', '10');
    if ($w < 4 || $w > 40) fail('window: 4..40', 400);
    $acc = param('acc', '');
    $raw = str_replace(["\r", "\n", ' '], '', param('seq', ''));
    if (($acc === '') === ($raw === '')) fail('either acc or seq', 400);
    $from = param('from', param('von'));
    $to = param('to', param('bis'));
    $from = $from !== null ? (int)$from : null;
    $to = $to !== null ? (int)$to : null;
    if ($from !== null && $from < 1) fail('from: 1 or more', 400);
    if ($from !== null && $to !== null && $to < $from + $w - 1) fail('range shorter than window', 400);
    if ($acc !== '') {
        if (!preg_match('/^[A-Za-z0-9_]{3,30}(\.\d{1,4})?$/', $acc)) fail('invalid acc', 400);
    } else {
        $raw = strtoupper($raw);
        if (!preg_match('/^[A-Z]{' . $w . ',' . AMYLO_MAX . '}$/', $raw))
            fail('seq: letters only, ' . $w . '..' . AMYLO_MAX . ' residues', 400);
    }
    quota($db, 'amyl_job', $key);

    $id = bin2hex(random_bytes(12));
    $db->prepare('INSERT INTO amyl_job (job_id, key_id, acc, bereich_von, bereich_bis, query, window_size) '
                 . 'VALUES (?, ?, ?, ?, ?, ?, ?)')
       ->execute([$id, $key['key_id'], $acc ?: null, $from, $to, $raw ?: null, $w]);
    $pos = $db->query("SELECT COUNT(*) FROM amyl_job WHERE status IN ('queued', 'running')")->fetchColumn();
    out(['job_id' => $id, 'status' => 'queued', 'position' => (int)$pos, 'url' => "$base?r=amylojob&id=$id"], 202);

case 'amylojob':
    $id = param('id', '');
    if (!preg_match('/^[0-9a-f]{24}$/', $id)) fail('invalid id', 400);
    $st = $db->prepare('SELECT job_id, status, acc, bereich_von AS range_from, bereich_bis AS range_to, window_size, '
                       . 'erstellt AS created, gestartet AS started, fertig AS finished, sekunden AS seconds, '
                       . 'lauf_id AS run_id, seq_id, error FROM amyl_job WHERE job_id = ?');
    $st->execute([$id]);
    $job = $st->fetch() ?: fail('unknown job', 404);
    if ($job['status'] === 'queued') {
        $st = $db->prepare("SELECT COUNT(*) FROM amyl_job WHERE status IN ('queued', 'running') AND erstellt <= ?");
        $st->execute([$job['created']]);
        $job['position'] = (int)$st->fetchColumn();
    }
    if ($job['status'] === 'done') {
        $st = $db->prepare('SELECT s.acc, s.laenge AS length, e.bewertet_von AS scored_from, e.bewertet_bis AS scored_to, '
                           . 'e.avg_prob, e.max_prob, e.max_pos, SUBSTRING(s.sequenz, e.max_pos, ?) AS max_window '
                           . 'FROM amyl_ergebnis e JOIN amyl_sequenz s ON s.seq_id = e.seq_id '
                           . 'WHERE e.lauf_id = ? AND e.seq_id = ?');
        $st->execute([$job['window_size'], $job['run_id'], $job['seq_id']]);
        $job['result'] = $st->fetch() ?: null;
        $st = $db->prepare('SELECT f.pos, SUBSTRING(s.sequenz, f.pos, ?) AS `window`, f.prob FROM amyl_fenster f '
                           . 'JOIN amyl_sequenz s ON s.seq_id = f.seq_id '
                           . 'WHERE f.lauf_id = ? AND f.seq_id = ? ORDER BY f.pos');
        $st->execute([$job['window_size'], $job['run_id'], $job['seq_id']]);
        $job['windows'] = $st->fetchAll();
    }
    out($job);

default:
    fail('unknown endpoint, see ?r=help', 404);
}
