"""Explicit UI setup; creating an entry never enables any battery control."""
import voluptuous as vol
from homeassistant import config_entries

DOMAIN = 'senec_marstek_gate'


class SenecMarstekGateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Allow exactly one diagnostic-only instance."""
        if self._async_current_entries():
            return self.async_abort(reason='single_instance_allowed')
        if user_input is not None:
            return self.async_create_entry(title='SENEC–Marstek Gate (inaktiv)', data={})
        return self.async_show_form(step_id='user', data_schema=vol.Schema({}))
