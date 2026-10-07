# Agent guidance

This file is a short entry point, not a second specification. Read only the linked section relevant to your task; inspect the current code and tests before changing behavior. Keep new documentation in English, link to deeper material instead of copying it here, and distinguish implemented behavior from future goals.

## Where to look

- [VISION.md](VISION.md): long-term strategy, target architecture, non-goals, and acceptance gates. It describes goals, not a live-control authorization.
- [README.md](README.md): currently documented feature set, assumptions, entity IDs, and offline test context.
- `custom_components/senec_marstek_gate/`: implementation. Start with `__init__.py` and `runtime.py` for the HA entry point, `quality.py` and `classify.py` for inputs, `authority.py` and `handover.py` for ownership observation, `controller.py` for intents, and `writer.py` and `feedback.py` for guarded output and readback.
- `tests/` and `.github/workflows/`: executable offline behavior and CI. Inspect the matching test before editing a module.

The existing VISION document may contain German text; do not silently reinterpret or rewrite it as part of an unrelated code change. Write any new or substantively revised documentation in English.

## Non-negotiable boundaries

- Home Assistant access is read-only by default. Do not turn on live battery control, switch device modes, set targets, change automations, install into HA, merge, or release without the relevant explicit authorization and safety evidence. An offline test pass or a green CI run is not hardware acceptance.
- SENEC remains autonomous. Do not use `sensor.senec_house_power` for control, accounting, diagnostics, or plausibility checks.
- Treat the two Venus devices as a coordinated pair. Manual priority ON revokes gate authority; OFF never grants it. Unknown or stale values are not zero; uncertain ownership or an ambiguous partial handover must fail closed, without speculative compensating switches.
- A HA switch state or Omnibattery pool snapshot is not proof of exclusive write authority. Do not wire the writer into the normal config entry or relax `production_ready=False` without proven sole-writer control, independent stop evidence, review, and separate live approval.
- Keep credentials and undated live readings out of code, fixtures, logs, and documentation. For historical energy claims include time range, source, units, measurement point, sign convention, and data quality.

## Change workflow

1. Identify the affected layer using the links above; read its current implementation, related tests, and any active PR notes. Do not assume a feature branch is already merged.
2. Add a failing offline regression for behavior changes, especially cancellation, late HA service effects, event races, unload, manual override, and partial two-device transitions. Preserve the safety boundaries above.
3. Run `python3 -m unittest discover -s tests -q`, `python3 -m compileall -q custom_components/senec_marstek_gate tests`, and `git diff --check`. For a release candidate also run `python3 scripts/check_release_version.py vX.Y.Z` with the intended tag and verify CI/HACS for that exact commit.
4. Record what was actually tested and what remains unproven. Keep installation and operational instructions in [README.md](README.md), architecture and future direction in [VISION.md](VISION.md), and this file as a compact routing and safety guide. Do not duplicate detailed procedures across layers.
