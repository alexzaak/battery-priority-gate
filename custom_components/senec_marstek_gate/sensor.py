"""Read-only source inventory; diagnostic status stays inaktiv."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .source_diagnostics import inspect_sources
from .version import VERSION


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
        self._attr_extra_state_attributes = {
            'production_ready': False, 'runtime_version': VERSION, 'source_checks': {},
        }

    async def async_update(self) -> None:
        """Poll only HA's in-memory state machine; never call services or devices."""
        self._attr_extra_state_attributes = {
            'production_ready': False,
            'runtime_version': VERSION,
            'source_checks': inspect_sources(self.hass.states),
        }
