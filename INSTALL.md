# SENEC–Marstek Gate v0.5.0 – HA-Steuerloop, gesperrt

## Funktionsstand

Die Integration registriert einen 10-s-HA-Zeitgeber und Zustandsereignisse für Wartung, manuelle Vorränge und die beiden Venus-Manual-Schalter. Der Loop verarbeitet HA-State-Objekte über `quality.assess()`, die Hoheitsmaschine und `controller.evaluate()`; der isolierte Sollwertschreiber ist nur bei einer von außen bestätigten `ControllerBinding` erreichbar. **Der normale HA-Config-Entry erstellt keine solche Bindung.** Jeder Tick gibt deshalb sofort `inactive` zurück – ohne Auswertung, Shadow Mode oder Aktoraufruf. Statussensor: `inaktiv`, `production_ready=False`, `runtime_version=0.5.0`, `loop_state=inactive` sowie read-only `source_checks`. SENEC bleibt autonom; `sensor.senec_house_power` wird nicht verwendet.

Die Funktionstests mit HA-förmigen StateMachine-/Service-Objekten beweisen den Codepfad, **nicht** physische Messpunkte, exklusive Schreibhoheit, AC-Leerlauf oder Live-Tauglichkeit. Eine breite Implementierungsfreigabe wird nicht als attestierte Gerätetopologie oder automatische Batterieübernahme ausgelegt.

## Upgrade des bestehenden Config-Entry

1. Vor dem HACS-Download vorhandenen HA-Bestand, Backup/Restore und Rollback für `/config/custom_components/senec_marstek_gate/` prüfen. HACS kann den Ordner ersetzen. **Keinen zweiten Config-Entry erstellen.**
2. Release `v0.5.0` in HACS laden und HA kontrolliert neu starten. Vor einem Upgrade tatsächlichen Zugriff/Backup klären; HACS „aktuell“ allein ist kein Datei- oder Python-Code-Beweis.
3. Exakten bestehenden HA-Config-Entry (`loaded`), `sensor.senec_marstek_gate_status` mit `runtime_version=0.5.0`, `loop_state=inactive`, `production_ready=False`, Home-Assistant-Log und unveränderte Venus-/Omnibattery-Zustände rücklesen. Bleibt `runtime_version` alt, wurde neuer Python-Code noch nicht geladen.
4. Bei Fehler: gesicherten Integrationsordner wiederherstellen, HA neu starten und exakt zurücklesen. Niemals wegen eines Update-Fehlers Automatik-/Manual-Schalter oder Sollwerte experimentell umschalten.

**NO-GO für aktive Batteriesteuerung:** Ausstehend sind physisch validierte Einheiten/Vorzeichen/Messpunkte, PV-Überschussbeweis, echte frische Guard-/Readback-/AC-Bindung und exklusiver Schreiber gegenüber Omnibattery sowie der Wartungsautomation, danach technische Abnahme und explizite Live-Freigabe. Synthetische Testgrenzen sind keine Hausparameter.
