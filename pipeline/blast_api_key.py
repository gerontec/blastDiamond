#!/usr/bin/env python3
"""API-Schluessel fuer yt.heissa.de/blast/api.php verwalten.
  blast_api_key.py add NAME [EMAIL] [JOBS_PRO_TAG]   -> gibt den Schluessel einmalig aus
  blast_api_key.py list
  blast_api_key.py off KEY_ID"""
import os, sys, hashlib, secrets
import blast_db, pymysql

c = pymysql.connect(**blast_db.cfg(), autocommit=True).cursor()
cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
if cmd == "add":
    key = secrets.token_urlsafe(24)
    c.execute("INSERT INTO blast_api_key (key_hash, name, email, jobs_pro_tag) VALUES (%s,%s,%s,%s)",
              (hashlib.sha256(key.encode()).hexdigest(), sys.argv[2],
               sys.argv[3] if len(sys.argv) > 3 else None, int(sys.argv[4]) if len(sys.argv) > 4 else 20))
    print(f"key_id {c.lastrowid}  X-Api-Key: {key}")
elif cmd == "off":
    c.execute("UPDATE blast_api_key SET aktiv=0 WHERE key_id=%s", (int(sys.argv[2]),))
    print("deaktiviert" if c.rowcount else "unbekannt")
else:
    c.execute("SELECT k.key_id, k.name, k.email, k.jobs_pro_tag, k.aktiv, k.erstellt, "
              "(SELECT COUNT(*) FROM blast_job j WHERE j.key_id=k.key_id) FROM blast_api_key k ORDER BY 1")
    for r in c.fetchall():
        print(*r, sep="\t")
