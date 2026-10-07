# Vision: SENEC–Marstek Gate

## Zweck und langfristiges Ziel

Dieses Projekt soll in Home Assistant eine nachvollziehbare, sichere Koordination zwischen dem **autonom bleibenden SENEC-Speicher** und **beiden Marstek Venus** ermöglichen. Ziel ist, gegensinniges Laden und Entladen zu vermeiden und – nur bei nachweislich geeigneter Datenlage und Hoheit – die beiden Venus innerhalb bestätigter Grenzen am Netzfluss und einem belegten PV-Überschuss auszurichten. Versorgungssicherheit, Geräteautonomie und ein eindeutiger manueller Rückweg haben Vorrang vor Optimierung. Das Gate ist kein Ersatz für SENEC-, Marstek- oder Omnibattery-Schutzfunktionen.

Das Projekt verwendet ausschließlich Home Assistant als Integrations- und Bedienebene. Der bestehende, installierbare Stand auf `main` (v0.5.2) ist **inaktiv**: keine produktiv attestierte `ControllerBinding`, keine Live-Steuerung und `production_ready=False`. Die auf einem separaten Feature-Branch entwickelte gemeinsame Übergabe/Rückgabe ist noch keine freigegebene Funktion. Diese Vision beschreibt Ziele, **nicht** den bereits erreichten oder zur Aktivierung freigegebenen Funktionsumfang; für den aktuellen Stand siehe [README.md](README.md) und [INSTALL.md](INSTALL.md).

## Leitplanken

- **SENEC bleibt autonom.** Das Gate liest geeignete Signale, schaltet aber keine SENEC-Aktoren. `sensor.senec_house_power` wird weder für Regelung noch für Bilanz, Diagnose oder Plausibilisierung verwendet.
- **Beide Venus gemeinsam betrachten.** Keine Ein-Gerät-Pilotsteuerung und kein Shadow Mode als Ersatz für die technische Abnahme. Manuell geführte Geräte werden nicht angesteuert; bei unklarer Zuordnung verlieren beide die Gate-Hoheit und es wird gewarnt.
- **Manueller Vorrang gewinnt.** Vorrang ON entzieht Hoheit; OFF erteilt sie niemals automatisch zurück. Neustart, unbekannter Zustand und Interlock-Verlust sind keine impliziten Freigaben.
- **Eine Schreibhoheit zur Zeit.** Ein beobachteter Manual-Schalter oder Omnibattery-Pool beweist noch keine Exklusivität gegenüber Omnibattery, Wartungsautomation, App oder anderen Schreibern. Ohne nachgewiesene Konfliktfreiheit darf das Gate keine Sollwerte setzen.
- **Fail-closed statt vermuteter Null.** Fehlende, alte oder widersprüchliche Zustände werden nicht als 0 W interpretiert. Ein Timeout, ein verspätet wirkender Dienstaufruf oder ein nicht unabhängig bestätigter 0-W-Stopp verhindern die Freigabe; unklare Teilzustände verlangen manuelle Recovery statt blindem Rückschalten.
- **Explizite Freigaben trennen.** Code-Review, grüne Offline-Tests, Installation in HA, technische Hardware-Abnahme und die ausdrückliche Live-Freigabe sind unterschiedliche Schritte. Keiner ersetzt den nächsten.

## Zielarchitektur

```text
HA-Sensoren und Ereignisse (read-only)
  → Quellenadapter: Metadaten, Frische, Messpunkt, Vorzeichen, Heartbeat
  → Qualitäts-/Richtungsklassifikation und PV-Nachweis
  → Hoheits- und Übergabeautomat für Venus 1 + 2
  → reiner Entscheidungsplaner (Intents, gemeinsames Netzbudget)
  → zentraler, gesperrter HA-Schreibpfad mit Vorab-Guard
  → frischer Sollwert-Readback + unabhängige AC-Stopp-Beobachtung
  → Status/Alarm/Recovery und prüfbare Ereignisprotokolle
```

**Mess- und Qualitätsgrenze:** Primärsignale sind `sensor.senec_battery_state_power`, `sensor.senec_enfluri_net_power_total`, `sensor.senec_solar_generated_power` sowie `sensor.marstek_venus_{1,2}_battery_power` und `sensor.marstek_venus_{1,2}_battery_soc`. `sensor.marstek_venus_{1,2}_ac_power` ist ein *separater* Beobachtungspunkt für die AC-/Stoppprüfung, nicht ein aus der Batterieleistung abgeleiteter Wert. Einheiten, `device_class`, `state_class`, `last_reported`, tatsächliche Aktualisierung, Topologie und Vorzeichen müssen pro Quelle geprüft werden. Netzbezug und -einspeisung sind am Messpunkt ausdrücklich zu unterscheiden; aus PV-Erzeugung und Netzeinspeisung allein folgt kein belegter PV-Überschuss. Die Hauslast darf nicht durch Addition überlappender Messpunkte doppelt gezählt werden. Tibber ist zunächst Plausibilitätsquelle; ein Wechsel der Regelquelle braucht eigene Zustimmung und erneute Abnahme.

**Hoheitsgrenze:** `authority.py` modelliert Berechtigung und Widerruf. Die gemeinsame Übergabe zwischen Omnibattery und Gate soll als begrenzte, einmalig autorisierte Transaktion mit exakt geprüfter Poolzuordnung und frischer Stopp-Bestätigung erfolgen. Teilwechsel, Cancellation, Unload, Wartung, verlorene Pool-Ereignisse oder unklare externe Wirkung führen zu gesperrter Hoheit und dokumentierter Recovery. Es darf keinen zweiten konkurrierenden Sollwertschreiber geben. Eine beobachtete HA-Schalterstellung allein ist weder Identitäts- noch Schreibhoheitsbeweis.

**Entscheidungs- und Schreibgrenze:** `controller.py` liefert unter validierten Eingangsdaten lediglich begrenzte Intents. Lade- und Entladeleistung teilen sich ein gemeinsames Netzbudget und sind an attestierte Geräte- und SoC-Grenzen gebunden. `writer.py` ist der einzige beabsichtigte HA-Sollwertpfad; vor *jedem* Befehl werden Hoheit, Wartung, Quellengüte und Grenzen erneut geprüft. Nach Nullbefehlen sind frische Sollwertberichte **und** unabhängige, zeitlich passende AC-Berichte beider Geräte erforderlich. HA-Zustände allein beweisen keinen physischen Hardware-Stopp; dafür ist eine eigene technische Abnahme nötig.

**Betriebsgrenze:** Der normale HA-Config-Entry bleibt ohne produktiv bestätigte Bindung und Aktivierungsfreigabe inaktiv. Status, Grundcodes und Alarme sollen die Ursache einer Sperre verständlich machen, ohne undatierte Live-Messwerte als dauerhafte Dokumentation auszugeben. Recovery wird als expliziter, überprüfbarer Bedienablauf gestaltet; unbekannte Gerätezustände werden nicht durch automatische Kompensationsschaltungen „repariert“.

## Entwicklungsweg und Abnahmetore

1. **Offline-Kern absichern:** Qualitätsprüfung, Klassifikation, Hoheitsautomat, Planer und Writer mit reproduzierbaren Regressionen für Vorzeichen, Datenlücken, Gegenfluss, Budget, Restart, Manual-Vorrang und Fehlerfälle pflegen. Synthetische Schwellen und angenommene `Evidence`-Werte sind keine Hauskalibrierung.
2. **Gemeinsame Übergabe/Rückgabe fertig prüfen:** Beide Venus als eine Transaktion behandeln; ein historisches Verlust-Ereignis darf nicht durch einen später wieder gültigen Snapshot überstimmt werden. Deadlines und nachlaufende, nicht abbrechbare Dienste sicher abgrenzen. Vollständige Offline-Tests, CI/HACS und unabhängiges Review am *gleichen* finalen Commit. Der laufende PR ist hierfür eine Implementierungsarbeit, keine Live-Freigabe.
3. **HA-Adapter und exklusive Hoheit nachweisen:** Tatsächliche Entity-IDs, Writer, Automation und Servicepfade inventarisieren; Omnibattery- und Gate-Schreibrecht konfliktfrei trennen. Authentifizierte Einmal-Freigabe, Frische-/Heartbeat-Prüfung, echte Guards, Limits und Alarmierung an den HA-Laufzeitpfad binden. Keine Schutzbedingung durch einen Konfigurationsschalter umgehen.
4. **Technische Abnahme beider Geräte:** Messpunkte, Vorzeichen, PV-Bilanz und Geräte-/SoC-Grenzen kalibrieren; frische Rückmeldungen, AC-Nullsequenz, Fehlerverhalten, Backup und nachvollziehbaren Rollback mit geeigneten Verfahren prüfen. Keine produktive Steuerung aus einem synthetischen Test oder einem optimistischen HA-State ableiten.
5. **Gesonderte Live-Entscheidung:** Erst nach dokumentierten Nachweisen und ausdrücklicher Freigabe von Alex eine kleinste, kontrollierte HA-Änderung vornehmen, exakt zurücklesen und beobachten. Andernfalls bleibt die Integration inaktiv. Laufende Änderungen an Schutzannahmen erfordern erneute Prüfung und Freigabe.

## Bewusst außerhalb des Ziels

- SENEC aktiv steuern oder dessen internen Regler ersetzen.
- Einen einzigen Venus isoliert produktiv übernehmen oder manuelle Bedienung überstimmen.
- Mit bloßen Netz-/PV-Momentwerten garantierte Einsparungen oder physisch gesicherten PV-Überschuss behaupten.
- Aus HACS-Installation, grünem CI oder `production_ready`-Text ohne Nachweise eine Live-Freigabe ableiten.

**Erfolg bedeutet:** Beide Venus werden nur bei bestätigter gemeinsamer Hoheit, belastbaren Energiequellen und überprüfbarem Stopp koordiniert; bei Unsicherheit ist die Ursache sichtbar und die Steuerung bleibt sicher gesperrt. Messbarer Nutzen kann erst nach einer gesondert freigegebenen, datierten Auswertung mit Datenqualität beurteilt werden.
