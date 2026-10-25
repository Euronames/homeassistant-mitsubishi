"""Manually invoked, administrator-only read probes. No automatic polling."""

from __future__ import annotations

import probatio as vol
import requests.exceptions
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN

SERVICE_READ_AIR_PURIFIER_STATUS = "read_air_purifier_status"


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register a fixed read request, never a generic raw-command service."""
    if hass.services.has_service(DOMAIN, SERVICE_READ_AIR_PURIFIER_STATUS):
        return

    async def async_read_air_purifier_status(call: ServiceCall) -> ServiceResponse:
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if coordinator is None:
            raise ServiceValidationError(
                "The selected Mitsubishi configuration entry is not loaded"
            )
        if not coordinator.last_update_success:
            raise ServiceValidationError("The selected Mitsubishi device is unavailable")
        probe = getattr(coordinator.controller, "fetch_air_purifier_status", None)
        if not callable(probe):
            raise ServiceValidationError(
                "This probe requires the matching experimental pymitsubishi library"
            )

        try:
            result = await hass.async_add_executor_job(probe)
        except ValueError:
            raise ServiceValidationError(
                "The adapter did not return a valid air purifier status response"
            ) from None
        except (requests.exceptions.RequestException, TimeoutError):
            raise HomeAssistantError(
                "Could not read air purifier status from the Wi-Fi adapter"
            ) from None

        # Do not publish the XML, credentials, host, identity, or normal HVAC state.
        # In particular, do not feed a partial response into the normal coordinator.
        return {
            "status": "not_returned" if result.enabled is None else "reported",
            "enabled": result.enabled,
            "response_groups": [f"0x{group:02x}" for group in result.response_groups],
        }

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_READ_AIR_PURIFIER_STATUS,
        async_read_air_purifier_status,
        schema=vol.Schema({vol.Required("config_entry_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the service when the last configuration entry has unloaded."""
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_READ_AIR_PURIFIER_STATUS)
