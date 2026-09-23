#!/usr/bin/env python3
"""Dump the live DDL of all blast_* and amyl_* tables and views to stdout.

No data, no grants, no credentials: the connection settings come from blast_db.cfg().
DEFINER clauses and AUTO_INCREMENT counters are stripped so the result is portable."""
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/python"))
import blast_db
import pymysql

c = pymysql.connect(**blast_db.cfg()).cursor()
c.execute("SELECT table_name, table_type FROM information_schema.tables "
          "WHERE table_schema = DATABASE() AND (table_name LIKE 'blast\\_%' OR table_name LIKE 'amyl\\_%') "
          "ORDER BY table_type, table_name")        # BASE TABLE before VIEW
objekte = c.fetchall()

print("-- DDL of the BLAST/DIAMOND + AmyloDeep schema in MariaDB.")
print("-- Dumped from the live database on dell-3660; no data, no grants, no credentials.")
print(f"-- {sum(1 for _, t in objekte if t == 'BASE TABLE')} tables, "
      f"{sum(1 for _, t in objekte if t == 'VIEW')} views.\n")
for name, typ in objekte:
    view = typ == "VIEW"
    c.execute(f"SHOW CREATE {'VIEW' if view else 'TABLE'} `{name}`")
    ddl = c.fetchone()[1]
    ddl = re.sub(r"/\*!5001\d DEFINER=[^*]+\*/ ?", "", ddl)
    ddl = re.sub(r"DEFINER=`[^`]+`@`[^`]+` ?", "", ddl)
    ddl = re.sub(r" AUTO_INCREMENT=\d+", "", ddl)
    print(f"-- {'View' if view else 'Table'}: {name}")
    print(ddl, end=";\n\n")
