"""SENEC–Marstek Gate HA lifecycle; no control activated on setup."""
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event

from .runtime import GateRuntime

PLATFORMS = ['sensor']
DOMAIN = 'senec_marstek_gate'


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register an inert diagnostic sensor and a stopped controller cycle."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    runtime = GateRuntime(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    runtime.unsub = async_track_time_interval(hass, runtime.async_tick, timedelta(seconds=10))
    runtime.unsub_events = async_track_state_change_event(hass, [
        'input_boolean.marstek_wartung_beide_manuell',
        'input_boolean.marstek_gate_venus_1_manueller_vorrang',
        'input_boolean.marstek_gate_venus_2_manueller_vorrang',
        'switch.marstek_venus_1_battery_manual_mode',
        'switch.marstek_venus_2_battery_manual_mode',
    ], runtime.handle_state_change)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Cancel the timer after successfully unloading the diagnostic platform."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        runtime.close()
    return True
