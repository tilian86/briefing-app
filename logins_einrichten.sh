#!/bin/zsh
# Zugangsdaten fuer die Selbst-Anmeldung (SWP/Tagblatt, GEA) in den Schluesselbund legen.
# Fragt E-Mail und Passwort ab; das Passwort tippst du verdeckt ein und es landet nur
# im Schluesselbund (Dienste briefing-login-swp / briefing-login-gea).
# Aufruf: briefing-logins   (oder ./logins_einrichten.sh)
set -u
cd "${0:A:h}"

for ziel in swp gea; do
  case $ziel in
    swp) name="SWP/Tagblatt" ;;
    gea) name="GEA" ;;
  esac
  dienst="briefing-login-$ziel"
  print ""
  read "mail?E-Mail für $name (leer lassen = überspringen): "
  [[ -z "$mail" ]] && continue
  # Alte Eintraege dieses Dienstes entfernen, damit genau einer uebrig bleibt.
  while security delete-generic-password -s "$dienst" >/dev/null 2>&1; do :; done
  print "Jetzt das $name-Passwort (beim Tippen siehst du nichts), danach noch einmal zur Kontrolle:"
  if security add-generic-password -U -s "$dienst" -a "$mail" -w; then
    print "✓ $name gespeichert"
  else
    print "⚠️ $name nicht gespeichert"
  fi
done

print ""
read "ok?Jetzt einmal echt testen (abmelden, selbst anmelden, prüfen)? (j/n) "
if [[ "$ok" == [jJ]* ]]; then
  python3 selbst_anmeldung.py --testen swp gea
fi
