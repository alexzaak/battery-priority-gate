"""SENEC–Marstek Gate installation shell: deliberately no actuator or policy loop."""
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

PLATFORMS = ['sensor']


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register an inert diagnostic sensor; never infer control consent from setup."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the diagnostic platform without changing devices."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
