#!/usr/bin/env python3
"""Zugang zu den NCBI-nr-Metadaten: Datenbank wagodb, alle Tabellen blast_*.
Zugangsdaten aus DB_CFG in ~/mqtt-listener.py, damit hier kein Passwort liegt."""
import ast, os


def cfg():
    ini = os.environ.get("BLAST_DB_INI")          # optional: [client] host/port/user/password/database
    if ini:
        import configparser
        k = configparser.ConfigParser()
        k.read(ini)
        c = dict(k["client"])
        if "port" in c:
            c["port"] = int(c["port"])
        c.setdefault("database", "wagodb")
        return c
    src = open(os.path.expanduser("~/mqtt-listener.py")).read()
    i = src.index("DB_CFG")
    i = src.index("dict(", i) + 4
    depth, j = 0, i
    while True:                                  # Klammern zaehlen statt raten
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    call = ast.parse("dict" + src[i:j + 1], mode="eval").body
    d = {k.arg: k.value.value for k in call.keywords if isinstance(k.value, ast.Constant)}
    d["database"] = "wagodb"
    return d
