# SENEC–Marstek Gate for Home Assistant

![SENEC–Marstek Gate icon](custom_components/senec_marstek_gate/brand/icon.png)

**SENEC–Marstek Gate** is designed to coordinate two Marstek Venus batteries with an autonomous SENEC storage system through Home Assistant. It aims to prevent opposing battery flows, use verified PV surplus for charging, and share a bounded grid-power budget between the Venus devices—without overriding manual control or taking over SENEC.

> [!IMPORTANT]
> **Product direction, not a claim of current live control.** The features and operating flow below describe the completed product. The released `main` integration (v0.5.2) is diagnostic-only: its HA control loop is inactive, `production_ready=False`, and it cannot command the batteries. Do not enable battery control on the strength of this README. See [Current development status](#current-development-status).

## Key features

*The following describes the intended, technically accepted product; items are not all available in the current release.*

- **Two batteries, one coordinated gate:** Venus 1 and Venus 2 enter and leave gate control together through an explicitly approved handover from Omnibattery. A pool indicator or Manual switch alone never grants control.
- **SENEC stays autonomous:** The gate reads validated SENEC battery, PV, and grid measurements but never sends SENEC commands or substitutes for its protection logic.
- **Avoid opposing battery flows:** When SENEC and a Venus would charge/discharge against each other, the gate blocks or stops its own eligible commands; a manually operated Venus is never commandeered.
- **PV-aware charging and grid-aware discharge:** Charge only with proven surplus; use confirmed import and device limits for discharge. The Venus devices share one bounded power budget rather than each consuming the full available grid margin.
- **Manual-first safety:** Manual priority ON, maintenance, stale measurements, uncertain ownership, service timeouts, or lost feedback revoke gate authority. Priority OFF does not silently restore it. Ambiguous partial transitions require explicit recovery, not blind compensating switching.
- **Observable decisions:** Home Assistant exposes status and reason codes; fresh setpoint and independent AC feedback are required before subsequent commands. A HA state update alone is not proof of physical AC stop.

The [vision](VISION.md) explains the architecture and acceptance gates. No savings, PV origin, or physical stop is guaranteed by a dashboard value alone.

## Requirements

| Component | What the gate needs |
| --- | --- |
| Home Assistant | An instance with the gate's custom integration; HACS is recommended for installation. |
| SENEC | Read-only battery power, Enfluri grid power, and solar generation entities from the actual installation. SENEC remains under its own controller. |
| Marstek | **Two** Venus devices with battery power and SoC entities, Manual-mode switches, and separate AC power/readback entities. The exact hardware and control interface must be validated before live use. |
| Omnibattery | The existing integration's two-device pool status, plus verified separation of its writing authority from the gate's. |
| Safety inputs | Manual-priority helpers, maintenance state/automation, source freshness and measurement-point evidence, device limits, and independently checked stop feedback. These are not validated for active operation in the current release. |

The agreed source IDs are `sensor.senec_enfluri_net_power_total`, `sensor.senec_battery_state_power`, `sensor.senec_solar_generated_power`, `sensor.marstek_venus_{1,2}_battery_power`, and `sensor.marstek_venus_{1,2}_battery_soc` (the braces stand for one entity per device). `sensor.marstek_venus_{1,2}_ac_power` is a **separate** stop-check signal, not a battery-direction input. `sensor.senec_house_power` is excluded from control, accounting, diagnostics, and plausibility checks. IDs, units, sign, measurement point, and freshness must match the actual installation; similar names are not sufficient.

## Installation

**HACS (recommended for the current diagnostic release)**

1. Back up Home Assistant and preserve the existing `/config/custom_components/senec_marstek_gate/` directory if migrating from a manual installation. Plan a restore before allowing HACS to replace that directory.
2. In HACS, add [`alexzaak/battery-priority-gate`](https://github.com/alexzaak/battery-priority-gate) as a custom **Integration** repository. Install a published, reviewed release; do not install an unreviewed feature branch as a control upgrade.
3. Restart Home Assistant. Keep an existing gate config entry, or add **SENEC–Marstek Gate** once under **Settings → Devices & services → Add integration** if none exists.
4. Confirm `sensor.senec_marstek_gate_status` is `inaktiv`, `production_ready=False`, and `loop_state=inactive`; check the loaded `runtime_version`, logs, and unchanged Venus/Omnibattery states. A download or manifest version does not prove that HA loaded new Python code.
5. If verification fails, restore the saved integration directory, restart Home Assistant, and read back the entry, status, logs, and device states again. Do not create a second config entry or experiment with battery controls to troubleshoot installation.

**Active operation is not available in this release.** The completed product will require separately approved two-device ownership transfer, validated settings and physical stop evidence before activation. Do not toggle Manual modes, setpoints, or automations to simulate setup.

## Current development status

- [x] Installable HA diagnostic integration with read-only source and two-device ownership observations.
- [x] Offline quality, direction, counterflow, authority, and shared-budget planning with guarded writer/feedback components and synthetic tests.
- [ ] Joint takeover/return accepted and merged. The in-progress implementation and its blocking review checklist are in [PR #9](https://github.com/alexzaak/battery-priority-gate/pull/9); it is not part of the installed `main` control path.
- [ ] Exclusive writer, calibrated telemetry/PV evidence, physical AC-stop attestation, and a production `ControllerBinding` verified in HA.
- [ ] Explicit live authorization and active control of both Venus devices. The current integration remains inactive.

## TODO before active use

- [ ] Close PR #9's safety and exact-commit review/CI checkpoints, then decide separately whether to merge.
- [ ] Prove conflict-free ownership against Omnibattery, the maintenance automation, app control, and other writers; implement authenticated one-use approvals and safe return/recovery for both devices.
- [ ] Validate sensor topology, sign conventions, freshness/heartbeat, PV surplus, power limits, and a non-duplicated grid budget on the real installation.
- [ ] Verify independent setpoint/AC stop feedback, late service effects, alarms, backup, and rollback with both devices; offline tests and HA snapshots are not hardware acceptance.
- [ ] Connect and accept the production HA binding only after these protections are proven and Alex explicitly approves live control. No single-device pilot or shadow mode substitutes for acceptance.

Contributing? Start with [AGENTS.md](AGENTS.md). Run offline checks with `python3 -m unittest discover -s tests -q` and `python3 -m compileall -q custom_components/senec_marstek_gate tests`.
