# SENEC–Marstek Gate: inaktive HA-Integration v0.1.0

## Umfang

`custom_components/senec_marstek_gate/` ist ein per HA-UI konfigurierbares Integrationspaket. Die Installation erzeugt ausschließlich einen Statussensor mit dem Wert `inaktiv`. Sie registriert **keinen Aktordienst**, abonniert keine Geräteereignisse und schreibt weder SENEC noch Venus oder Omnibattery. Das Offline-Paket `gate.py`, `quality.py`, `classify.py` ist enthalten, wird jedoch in dieser Version **nicht** mit HA-Livewerten verbunden. Insbesondere ist `pv_surplus_proven=False`; synthetische Testschwellen sind keine Produktionswerte.

## Installation (erst nach nachweislichem Zugriff auf HA `/config`)

1. Bestehendes Backup/Restore prüfen; die aktuelle HA-Konfiguration und etwaige vorhandene Datei `/config/custom_components/senec_marstek_gate` vor Kopieren lesen. Bestehende Dateien nicht blind ersetzen.
2. Nur den Ordner `senec_marstek_gate` nach `/config/custom_components/` kopieren. Keine Token oder Secrets in Dateien schreiben.
3. HA über seine unterstützte Oberfläche kontrolliert neu starten. Nach Neustart in *Einstellungen → Geräte & Dienste → Integration hinzufügen* **SENEC–Marstek Gate (inaktiv)** einmal hinzufügen. Ein Setup-Dialog erklärt ausdrücklich, dass keine Steuerung aktiviert wird.
4. Config-Entry `loaded` sowie `sensor.senec_marstek_gate_status` (generierter Name kann abweichen; über Registry/Unique-ID prüfen) mit Zustand `inaktiv` und Systemlog zurücklesen. Beide Venus und Wartung müssen unverändert sein. Installation erst danach als erfolgreich bezeichnen.
5. Rollback: Config-Entry entfernen, HA stoppen oder Integrationsordner entfernen, HA neu starten; Geräte bleiben unberührt. Das Entfernen eines Config-Entry allein löscht keine Dateien im `custom_components`-Ordner.

**Stand 06.10.2026:** Alex hat das Paket selbst installiert; HA meldete den Config-Entry `senec_marstek_gate` als `loaded` und den registrierten Statussensor `sensor.senec_marstek_gate_status=inaktiv`. Die Dateien auf HA konnten nicht bytegenau mit dem Repository verglichen werden. Tests und CI laufen ausschließlich offline; kein aktiver Gate-Controller. Keine produktive Gate-Steuerung ohne spätere gesonderte technische Abnahme und Freigabe.
