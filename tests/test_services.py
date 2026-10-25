"""Tests for the explicitly invoked, read-only diagnostic action.

Run with pytest-homeassistant-custom-component in a Home Assistant development
environment. These tests must not connect to an actual Wi-Fi adapter.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import probatio as vol
import pytest
import requests.exceptions
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
from pymitsubishi import MitsubishiAPI, MitsubishiController, ParsedDeviceState
from pymitsubishi.air_purifier import HVAC_OPTIONS_REQUEST

from custom_components.mitsubishi.const import DOMAIN
from custom_components.mitsubishi.services import (
    SERVICE_READ_AIR_PURIFIER_STATUS,
    async_setup_services,
    async_unload_services,
)


def setup_probe(hass, enabled=None, groups=()):
    """Install a synthetic loaded coordinator without connecting any device."""
    probe = Mock(return_value=SimpleNamespace(enabled=enabled, response_groups=groups))
    coordinator = SimpleNamespace(
        last_update_success=True,
        controller=SimpleNamespace(fetch_air_purifier_status=probe),
    )
    hass.data[DOMAIN] = {"test_entry_id": coordinator}
    async_setup_services(hass)
    return coordinator, probe


async def invoke(hass, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_READ_AIR_PURIFIER_STATUS,
        {"config_entry_id": "test_entry_id", **data},
        blocking=True,
        return_response=True,
    )


@pytest.mark.parametrize("enabled", [True, False, None])
async def test_probe_response(hass, enabled):
    coordinator, probe = setup_probe(hass, enabled, (2, 0x42))
    assert await invoke(hass) == {
        "status": "not_returned" if enabled is None else "reported",
        "enabled": enabled,
        "response_groups": ["0x02", "0x42"],
    }
    probe.assert_called_once_with()
    assert not hasattr(coordinator, "data")


async def test_service_uses_real_library_probe_without_replacing_hvac_state(hass):
    coordinator, _ = setup_probe(hass)
    api = Mock(spec=MitsubishiAPI)
    api.send_command.return_value = (
        "<LSV><CODE><VALUE>fc620130104201010100000000000000000000000018</VALUE></CODE></LSV>"
    )
    controller = coordinator.controller = MitsubishiController(api)
    cached_state = controller.state = ParsedDeviceState(cloud_connect=False, echonet_enabled=False)

    assert await invoke(hass) == {
        "status": "reported",
        "enabled": True,
        "response_groups": ["0x42"],
    }
    api.send_command.assert_called_once_with(HVAC_OPTIONS_REQUEST, connect=None)
    api.send_status_request.assert_not_called()
    assert controller.state is cached_state
    assert controller.state.cloud_connect is False
    assert controller.state.echonet_enabled is False


async def test_unknown_entry_does_not_issue_request(hass):
    _, probe = setup_probe(hass)
    with pytest.raises(ServiceValidationError):
        await invoke(hass, config_entry_id="not-loaded")
    probe.assert_not_called()


async def test_unavailable_device_does_not_issue_request(hass):
    coordinator, probe = setup_probe(hass)
    coordinator.last_update_success = False
    with pytest.raises(ServiceValidationError):
        await invoke(hass)
    probe.assert_not_called()


async def test_old_library_fails_only_when_probe_is_invoked(hass):
    coordinator, _ = setup_probe(hass)
    coordinator.controller = SimpleNamespace()
    with pytest.raises(ServiceValidationError, match="experimental"):
        await invoke(hass)


async def test_non_admin_cannot_probe_device(hass, hass_owner):
    # Core automatically makes the first normal user the owner; seed that user
    # through the fixture before creating the genuinely unprivileged account.
    assert hass_owner.is_owner
    _, probe = setup_probe(hass)
    user = await hass.auth.async_create_user("Non-admin")
    assert not user.is_admin
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_READ_AIR_PURIFIER_STATUS,
            {"config_entry_id": "test_entry_id"},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )
    probe.assert_not_called()


async def test_arbitrary_command_input_is_rejected(hass):
    _, probe = setup_probe(hass)
    with pytest.raises(vol.Invalid):
        await invoke(hass, command="do-not-send")
    probe.assert_not_called()


@pytest.mark.parametrize(
    "error", [ValueError("private data"), requests.exceptions.Timeout("private data")]
)
async def test_failed_probe_does_not_expose_transport_details(hass, error):
    _, probe = setup_probe(hass)
    probe.side_effect = error
    with pytest.raises(HomeAssistantError) as exc:
        await invoke(hass)
    assert "private data" not in str(exc.value)


async def test_registration_and_unload_with_multiple_entries(hass):
    coordinator, _ = setup_probe(hass)
    async_setup_services(hass)
    hass.data[DOMAIN]["other_entry"] = coordinator
    del hass.data[DOMAIN]["test_entry_id"]
    async_unload_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_READ_AIR_PURIFIER_STATUS)
    del hass.data[DOMAIN]["other_entry"]
    async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_READ_AIR_PURIFIER_STATUS)
