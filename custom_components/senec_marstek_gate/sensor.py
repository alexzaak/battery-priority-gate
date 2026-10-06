"""Constant diagnostic; no readings drive decisions in the installation shell."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([GateInstallStatus(entry.entry_id)])


class GateInstallStatus(SensorEntity):
    """Indicate that installation is inert, not a claim of safety acceptance."""

    _attr_name = 'SENEC–Marstek Gate Status'
    _attr_icon = 'mdi:shield-off-outline'
    _attr_native_value = 'inaktiv'

    def __init__(self, entry_id: str) -> None:
        self._attr_unique_id = f'{entry_id}_install_status'
