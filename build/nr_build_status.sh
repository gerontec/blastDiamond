#!/bin/sh
# Eine Zeile zum Stand des nr-Baus: laeuft der Prozess, welcher Chunk zuletzt, wie alt ist der
# letzte Logeintrag, wie viele Fehlerzeilen seit dem letzten Start. Fuer Ueberwachungsschleifen.
LOG="$HOME/python/nr_build.log"
pgrep -f "python3 nr_build.py" >/dev/null && p=laeuft || p=WEG
chunk=$(grep -o "Chunk [0-9]*:" "$LOG" | tail -1 | tr -dc 0-9)
alter=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
fehler=$(awk '/=== nr_build: Start/{n=0} /FEHLER|Traceback|Killed/{n++} END{print n+0}' "$LOG")
echo "$p chunk=${chunk:-0}/231 alter=${alter}s fehler=$fehler"
