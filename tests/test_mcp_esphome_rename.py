"""Tests for rename_esphome_device.

The add-on's devices/rename compiles and OTA-flashes a renamed image, so the tool
is gated on the flash capability: allowlisting the command under cap_esphome_yaml
would let an access key flash a device without the capability that governs flashing.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from custom_components.phoenix_mcp import mcp_view
from custom_components.phoenix_mcp.tools import esphome
from custom_components.phoenix_mcp.esphome_builder import ESPHOME_BUILDER_ALLOWED_COMMANDS
from custom_components.phoenix_mcp.mcp_view import _call_tool

from tests.test_mcp_esphome_firmware_tools import (
    FILE,
    FakeBuilder,
    _dash,
    _esphome_entry,
    _flash_access_key,
    _job,
    _runtime,
    _yaml_access_key,
)

_DASHBOARD_PATCH = "custom_components.phoenix_mcp.tools.esphome._esphome_dashboard"
_COMMAND_PATCH = "custom_components.phoenix_mcp.tools.esphome.async_builder_command"

RENAMED = {"configuration": FILE, "job": "j-compile", "tail_job": "j-upload"}


def _json_body(content: dict) -> dict:
    return json.loads(content["content"][0]["text"])


async def _run(args, access_key, hass, builder, data=None):
    with patch(_DASHBOARD_PATCH, return_value=_dash()), patch(_COMMAND_PATCH, builder):
        return await _call_tool(
            "rename_esphome_device", args, access_key, hass, data or MagicMock())


class TestCapability:
    async def test_the_authoring_capability_alone_cannot_rename(self, hass, esphome_dir):
        # devices/rename flashes, so an access key that may author but is denied
        # flashing must not reach it.
        builder = FakeBuilder(**{"devices/rename": RENAMED})
        content, outcome, _ = await _run(
            {"file": FILE, "new_name": "new-name"},
            _yaml_access_key(cap_esphome_flash="deny"), hass, builder)

        assert outcome == "denied"
        assert builder.calls == []
        assert "new-name" not in content["content"][0]["text"]

    async def test_the_flash_capability_may_rename(self, hass, esphome_dir):
        builder = FakeBuilder(**{
            "firmware/get_jobs": [],
            "devices/rename": RENAMED,
        })
        _c, outcome, _ = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)
        assert outcome == "allowed"

    def test_the_command_is_reachable_but_only_through_this_tool(self):
        # The allowlist carries the command (so the tool works) and the tool
        # carries the flash capability (so the allowlist entry is not a way
        # around the capability split).
        assert "devices/rename" in ESPHOME_BUILDER_ALLOWED_COMMANDS
        every = (mcp_view._ENTITY_TOOL_DEFS + mcp_view._SYSTEM_TOOL_DEFS
                 + mcp_view._NATIVE_TOOL_DEFS)
        defs = [d for d in every if d["name"] == "rename_esphome_device"]
        assert defs and defs[0]["cap"] == "cap_esphome_flash"


class TestValidation:
    @pytest.mark.parametrize("bad", [
        "Has-Capitals", "under_scores", "-leading", "trailing-", "", "a" * 40, 7, None,
        "spaces here", "dots.in.name",
    ])
    async def test_an_invalid_name_is_refused_before_anything_is_enqueued(
        self, hass, esphome_dir, bad
    ):
        builder = FakeBuilder(**{"firmware/get_jobs": []})
        _c, outcome, _ = await _run(
            {"file": FILE, "new_name": bad}, _flash_access_key(), hass, builder)
        assert outcome == "invalid_request"
        assert "devices/rename" not in builder.commands

    async def test_the_current_name_comes_from_the_yaml_not_the_filename(
        self, hass, esphome_dir
    ):
        # A config's esphome name and its filename stem diverge routinely (an
        # imported config keeps its raw name while the file is slugified; an
        # adopted device is renamed later). Comparing the rename target against
        # the stem would refuse a legitimate rename and blame a name the device
        # does not have.
        (esphome_dir / "imported.yaml").write_text(
            "esphome:\n  name: real-device-name\n")
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})

        content, outcome, _ = await _run(
            {"file": "imported.yaml", "new_name": "real-device-name"},
            _flash_access_key(), hass, builder)
        assert outcome == "invalid_request"
        assert "already has that name" in content["content"][0]["text"]
        assert builder.calls == []

        # ...and renaming it to match its own filename is a real rename.
        content, outcome, _ = await _run(
            {"file": "imported.yaml", "new_name": "imported"}, _flash_access_key(), hass, builder)
        assert outcome == "allowed"

    async def test_a_substituted_name_is_resolved(self, hass, esphome_dir):
        # Generated bluetooth-proxy configs carry esphome.name: ${name}. An
        # unresolved read would compare against the literal "${name}" and never
        # match, silently disabling the same-name refusal for those configs.
        (esphome_dir / "proxy.yaml").write_text(
            "substitutions:\n  name: bt-proxy-aabbcc\nesphome:\n  name: ${name}\n")
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})

        content, outcome, _ = await _run(
            {"file": "proxy.yaml", "new_name": "bt-proxy-aabbcc"},
            _flash_access_key(), hass, builder)
        assert outcome == "invalid_request"
        assert "already has that name" in content["content"][0]["text"]
        assert builder.calls == []

    async def test_an_in_place_rename_is_allowed_not_refused_as_a_collision(
        self, hass, esphome_dir
    ):
        # new_name slugifies to this device's OWN file, so only esphome.name
        # changes. The add-on supports this and takes its config-only path (the
        # OTA chain needs a distinct filename to build against), so the
        # existing-file check must not fire on the file being renamed.
        (esphome_dir / "imported.yaml").write_text(
            "esphome:\n  name: real-device-name\n")
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})

        content, outcome, _ = await _run(
            {"file": "imported.yaml", "new_name": "imported"}, _flash_access_key(), hass, builder)

        assert outcome == "allowed"
        assert "already exists" not in content["content"][0]["text"]
        assert builder.args_for("devices/rename")["new_name"] == "imported"

    async def test_an_unreadable_file_falls_back_to_the_filename_stem(
        self, hass, esphome_dir
    ):
        # Fails soft, like the add-on: a config that will not parse must not
        # become un-renameable, and the stem is the same fallback upstream uses.
        (esphome_dir / "broken.yaml").write_text("esphome: [oops\n  name: ??\n")
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})

        content, outcome, _ = await _run(
            {"file": "broken.yaml", "new_name": "broken"}, _flash_access_key(), hass, builder)
        assert outcome == "invalid_request"
        assert "already has that name" in content["content"][0]["text"]

    async def test_renaming_to_the_current_name_is_refused(self, hass, esphome_dir):
        builder = FakeBuilder(**{"firmware/get_jobs": []})
        _c, outcome, _ = await _run(
            {"file": FILE, "new_name": FILE.removesuffix(".yaml")}, _flash_access_key(), hass, builder)
        assert outcome == "invalid_request"
        assert "devices/rename" not in builder.commands

    async def test_an_existing_target_file_is_never_overwritten(self, hass, esphome_dir):
        # The add-on writes the renamed YAML up front, so landing on an existing
        # file would silently clobber another device's configuration.
        (esphome_dir / "taken.yaml").write_text("esphome:\n  name: taken\n")
        builder = FakeBuilder(**{"firmware/get_jobs": []})
        content, outcome, _ = await _run(
            {"file": FILE, "new_name": "taken"}, _flash_access_key(), hass, builder)

        assert outcome == "invalid_request"
        assert "already exists" in content["content"][0]["text"]
        assert "devices/rename" not in builder.commands
        assert (esphome_dir / "taken.yaml").read_text() == "esphome:\n  name: taken\n"

    async def test_a_running_build_is_never_destroyed_by_a_rename(self, hass, esphome_dir):
        builder = FakeBuilder(**{
            "firmware/get_jobs": [_job(job_id="running-1", status="running")],
        })
        content, outcome, _ = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)

        assert outcome == "invalid_request"
        assert "running-1" in content["content"][0]["text"]
        assert "devices/rename" not in builder.commands


class TestResult:
    async def test_reports_both_jobs_of_the_chain(self, hass, esphome_dir):
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})
        content, outcome, _ = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)

        assert outcome == "allowed"
        body = _json_body(content)
        assert body["job_id"] == "j-compile"
        assert body["tail_job_id"] == "j-upload"
        assert body["new_file"] == "new-name.yaml"
        assert builder.args_for("devices/rename") == {
            "configuration": FILE, "new_name": "new-name"}

    async def test_the_addon_returns_whole_job_records_not_bare_ids(self, hass, esphome_dir):
        # The add-on replies with whole job records, not the bare id its docs
        # imply; passing the record through verbatim would give the agent a
        # dict get_esphome_job cannot accept.
        builder = FakeBuilder(**{
            "firmware/get_jobs": [],
            "devices/rename": {
                "configuration": FILE,
                "job": {"job_id": "j-compile", "status": "running"},
                "tail_job": {"job_id": "j-upload", "status": "queued"},
            },
        })
        content, outcome, _ = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)

        assert outcome == "allowed"
        body = _json_body(content)
        assert body["job_id"] == "j-compile"
        assert body["tail_job_id"] == "j-upload"

    async def test_a_bare_id_still_works_if_the_addon_ever_sends_one(self, hass, esphome_dir):
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})
        content, _o, _r = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)
        assert _json_body(content)["job_id"] == "j-compile"

    async def test_a_reply_carrying_no_job_is_a_config_only_rename(
        self, hass, esphome_dir
    ):
        # The add-on's config-only rename returns {"configuration": ..., "job":
        # None} for an explicit config_only argument (which Phoenix never sends)
        # or for an IN-PLACE rename, where the new name slugifies to the
        # device's own file.
        #
        # Unlike a FAILED flash (config reverted, installing anything is wrong),
        # the file really does carry the new name here, and installing it is how
        # the caller finishes.
        builder = FakeBuilder(**{
            "firmware/get_jobs": [],
            "devices/rename": {"configuration": FILE, "job": None},
        })
        content, outcome, _ = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)

        assert outcome == "allowed"
        body = _json_body(content)
        assert body["config_only"] is True
        assert body["flashed"] is False
        assert "still answers to its OLD name" in body["note"]
        assert "NOT as a completed device rename" in body["note"]
        assert "install the firmware from this file" in body["note"]
        assert "job_id" not in body

    async def test_a_job_it_cannot_read_fails_loud_instead_of_reading_as_config_only(
        self, hass, esphome_dir
    ):
        # The success branch keys on "is there a job id", so an unreadable job
        # would fall through to the config-only branch and report "nothing was
        # compiled or flashed" while a compile-and-flash chain runs on the
        # device, telling the operator the opposite of what is happening.
        #
        # A missing or null "job" is the documented config-only reply and stays
        # quiet; a job PRESENT but unreadable is a third shape and must not be
        # guessed at.
        builder = FakeBuilder(**{
            "firmware/get_jobs": [],
            "devices/rename": {"configuration": FILE, "job": {"identifier": "j-new-shape"}},
        })
        content, outcome, _ = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)
        text = content["content"][0]["text"]

        assert outcome == "invalid_request"
        assert "cannot read" in text
        assert "Check the ESPHome dashboard" in text
        # Must NOT claim either outcome, since it does not know which happened.
        assert "config_only" not in text
        assert "nothing was compiled" not in text

    async def test_it_says_the_action_services_need_a_reconnect(self, hass, esphome_dir):
        # After the flash the device is back, but esphome.<name>_<action> stays
        # UNREGISTERED until the config entry reconnects, because Home Assistant
        # registers those services on connect and never removes the old ones. A
        # note saying only "renamed" leaves a silently uncallable action.
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})
        content, _o, _r = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)

        note = _json_body(content)["note"]
        assert "reload" in note.lower()
        assert "config entry" in note

    async def test_config_only_is_never_taken_from_the_caller(self, hass, esphome_dir):
        # As with install, which always sends port OTA, the caller cannot choose
        # the quiet path.
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})
        await _run(
            {"file": FILE, "new_name": "new-name", "config_only": True},
            _flash_access_key(), hass, builder)
        assert "config_only" not in builder.args_for("devices/rename")

    async def test_it_says_a_failed_rename_reverts_itself(self, hass, esphome_dir):
        # If the OTA flash fails the add-on reverts the configuration to the old
        # name itself. A note covering only the success path lets an agent read
        # that revert as a half-finished rename and "complete" it by installing
        # firmware on the OLD file, which only reflashes what the device already
        # runs; the note denies that misreading by name.
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})
        content, outcome, _r = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)
        note = _json_body(content)["note"]

        assert outcome == "allowed"
        assert "NOTHING is left half-applied" in note
        assert "Do NOT try to finish it by installing firmware" in note
        assert "rename_esphome_device again" in note

    async def test_it_warns_that_both_configurations_are_listed_meanwhile(
        self, hass, esphome_dir
    ):
        # The add-on COPIES the file and drops one only at the end, so
        # mid-rename both configurations are listed. Unwarned, that reads as a
        # half-applied rename or debris to tidy up, and tidying would destroy
        # the rename in flight.
        builder = FakeBuilder(**{"firmware/get_jobs": [], "devices/rename": RENAMED})
        content, _o, _r = await _run(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass, builder)
        note = _json_body(content)["note"]

        assert "both the old and the new" in note
        assert "neither copy is yours to clean up" in note
        assert "wait_for_esphome_job" in note


class TestApprovalDiff:
    async def test_it_names_the_action_services_that_will_break(
        self, hass, esphome_dir, esphome_entries
    ):
        # Entities survive a rename (HA keys them on the MAC); the device's
        # user-defined action SERVICES do not, and every automation calling one
        # stops resolving. That is the fact the card exists to surface.
        _esphome_entry(hass, esphome_entries)
        diff = esphome._build_diff_rename_esphome_device(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass)

        assert diff["kind"] == "system_action"
        assert "compiles and flashes" in diff["summary"]
        assert diff["preview"]["compiles_and_flashes"] is True
        assert "MAC" in diff["preview"]["retained"]

    async def test_it_survives_a_device_it_cannot_resolve(self, hass, esphome_dir):
        diff = esphome._build_diff_rename_esphome_device(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass)
        assert diff["preview"]["new_file"] == "new-name.yaml"
        assert diff["preview"]["device_online"] is False
        assert "cannot reach" in diff["preview"]["offline_note"]

    async def test_a_loaded_entry_does_not_mean_a_reachable_device(
        self, hass, esphome_dir, esphome_entries
    ):
        # entry.state describes the CONFIG ENTRY, not the device: a powered-off
        # or unreachable device keeps a LOADED entry. Reading it as liveness
        # would drop the offline note exactly where it applies. Only
        # runtime.available answers it, as the install diff does.
        _esphome_entry(hass, esphome_entries, runtime=_runtime(available=False))
        diff = esphome._build_diff_rename_esphome_device(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass)

        assert diff["preview"]["device_online"] is False
        assert "cannot reach" in diff["preview"]["offline_note"]

    async def test_the_offline_note_never_promises_a_file_only_rename(
        self, hass, esphome_dir, esphome_entries
    ):
        # device_online is Home Assistant's API connection and the add-on never
        # consults it, so a device reported unavailable can still answer on the
        # OTA port and be flashed. The note must describe that uncertainty
        # rather than promise a file-only rename.
        _esphome_entry(hass, esphome_entries, runtime=_runtime(available=False))
        note = esphome._build_diff_rename_esphome_device(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass
        )["preview"]["offline_note"]

        assert "only rewrite" not in note
        assert "may still compile" in note
        assert "reverted in full" in note

    async def test_a_reachable_device_carries_no_offline_note(
        self, hass, esphome_dir, esphome_entries
    ):
        # The note is specific to an unreachable device, so an always-offline
        # implementation cannot pass.
        _esphome_entry(hass, esphome_entries, runtime=_runtime(available=True))
        diff = esphome._build_diff_rename_esphome_device(
            {"file": FILE, "new_name": "new-name"}, _flash_access_key(), hass)

        assert diff["preview"]["device_online"] is True
        assert "offline_note" not in diff["preview"]
