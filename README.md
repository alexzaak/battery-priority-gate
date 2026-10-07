# SENEC–Marstek Gate

![Inactive gate integration icon](custom_components/senec_marstek_gate/brand/icon.png)

A Home Assistant integration and offline decision engine for the **future** coordinated operation of two Marstek Venus batteries alongside an autonomous SENEC system. The released code currently installs an **inactive, read-only diagnostic entry**: it does not take ownership of the batteries or issue device commands. See [VISION.md](VISION.md) for the long-term design and [INSTALL.md](INSTALL.md) for upgrade and rollback details.

## Prerequisites

**For diagnostic installation:**

- A Home Assistant instance; [HACS](https://www.hacs.xyz/) for the installation path below. One existing gate config entry may be reused; do not create a duplicate.
- To see meaningful source and ownership observations, a SENEC system and **two** Marstek Venus devices represented by Home Assistant entities, plus Omnibattery for the current two-device pool. The diagnostic entry can still be installed with missing entities; it must not treat missing data as zero or as an authorization.
- HA integrations or configuration providing the seven agreed source entities: `sensor.senec_enfluri_net_power_total`, `sensor.senec_battery_state_power`, `sensor.senec_solar_generated_power`, `sensor.marstek_venus_{1,2}_battery_power`, and `sensor.marstek_venus_{1,2}_battery_soc`. The brace notation means one entity for each Venus. The separate `sensor.marstek_venus_{1,2}_ac_power` sensors are for future stop/readback verification, **not** battery-direction inputs.
- For ownership observations: `sensor.omnibattery_integration_status`, both `switch.marstek_venus_{1,2}_battery_manual_mode` entities, the two `input_boolean.marstek_gate_venus_{1,2}_manueller_vorrang` helpers, `input_boolean.marstek_wartung_beide_manuell`, and `automation.marstek_wartung_beide_manuell_und_0_w`. These must refer to the actual devices; a similarly named entity is not interchangeable.

**For development only:** Python 3.13 and the standard library are sufficient to run the offline tests. No live HA or battery connection is needed.

## Installation (diagnostics only)

1. Back up Home Assistant and, if already installed manually, preserve and compare `/config/custom_components/senec_marstek_gate/`. HACS may replace that directory. Identify the existing config entry and a tested rollback path before upgrading it.
2. In HACS, add `https://github.com/alexzaak/battery-priority-gate` as a custom repository of type **Integration**. Download the published, reviewed release (the `main` package documented here is v0.5.2); do not select an unreviewed feature branch for HA.
3. Restart Home Assistant in a controlled manner. If a gate config entry already exists, **keep it**. Otherwise add the SENEC–Marstek Gate integration once via **Settings → Devices & services → Add integration**. The config flow creates a diagnostic-only entry.
4. Read back the loaded entry, HA logs, and `sensor.senec_marstek_gate_status`: expect state `inaktiv`, `production_ready=False`, `loop_state=inactive`, and the loaded `runtime_version` matching the intended release. Also inspect `source_checks`, `handover_observation`, and unchanged Venus/Omnibattery states. A HACS download or manifest version alone does not prove that HA loaded the new Python code.
5. If these checks fail, restore the saved integration directory, restart HA, and verify again. **Do not** test an upgrade by switching Manual modes, setpoints, or automations. Detailed checks and rollback boundaries are in [INSTALL.md](INSTALL.md).

## Functionality

- Read-only HA status sensor with inventory reason codes (`source_checks`) and joint Venus/Omnibattery observation (`handover_observation`); neither field grants write authority.
- Offline quality assessment of units, metadata, numeric values, per-source `last_reported` freshness, and *externally supplied* topology/sign/heartbeat attestations. `last_updated` is not a substitute for a device report.
- Offline direction classification and counterflow detection: SENEC and Venus battery power use **positive = charging, negative = discharging**; the Enfluri grid meter uses **positive = import, negative = export**. Deadbands in tests are synthetic, not house settings. `sensor.senec_house_power` is excluded.
- Offline authority model, bounded two-device power-budget planner, and isolated guarded writer/feedback components. Their simulated intents and readbacks are **not** wired into the installed entry's device-control path. A HA setpoint report or AC sensor timestamp alone does not establish a physical stop.
- Synthetic scenario tests plus a small, dated historical regression fixture in `fixtures/incident_2026-10-05_062900.json`. It does not prove measurement topology, causality, or production readiness.

## Current development status

- [x] v0.5.2 on `main`: installable diagnostic entry with an inactive loop and `production_ready=False`.
- [x] Read-only source inventory and two-Venus pool/priority observation; pool state is **not** exclusive-writer proof.
- [x] Offline quality, direction, authority, planner, guarded writer, and fresh HA feedback logic with automated tests.
- [ ] A reviewed and merged coordinated takeover/return implementation. Work on this exists separately in [PR #9](https://github.com/alexzaak/battery-priority-gate/pull/9); it is **not** part of the installed `main` entry and must not be treated as approved.
- [ ] A production-validated controller binding, active control loop, or live battery authorization. None is enabled by installation.

## TODO before any live control

- [ ] Resolve all PR #9 acceptance sub-checkpoints, including transient pool events, late service effects, complete offline regression coverage, exact-commit CI/HACS results, and independent review. Merge is a separate decision.
- [ ] Prove exclusive, conflict-free write ownership against Omnibattery, the maintenance automation, app control, and other writers; provide an authenticated one-use approval and a safe, explicit return/recovery procedure for **both** Venus devices.
- [ ] Validate each source's physical measurement point, sign, freshness, and hardware heartbeat; establish PV-surplus evidence, calibrated limits/deadbands, and a non-duplicated energy balance. Tibber remains a plausibility source, not automatic failover; SENEC remains autonomous.
- [ ] Demonstrate independently verifiable zero-setpoint and AC-stop behavior, timeouts/late-effect containment, alerts, and rollback with both devices. Offline tests and optimistic HA states are insufficient.
- [ ] Only after technical acceptance, backup/restore verification, and **Alex's separate explicit live approval**, connect a production binding and verify the exact HA change. No single-device pilot or shadow mode is a substitute for acceptance.

For contributors, start with [AGENTS.md](AGENTS.md); for offline tests run `python3 -m unittest discover -s tests -q` and `python3 -m compileall -q custom_components/senec_marstek_gate tests`.
