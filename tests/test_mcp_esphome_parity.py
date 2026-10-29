"""ESPHome tools added for parity with the Device Builder's own MCP server.

Covers the extended read modes (device firmware fields, component file and
advanced filtering, existing automations, building-block docs, the migration
check on validate), the connectivity check, memory analysis and the job-tool
parity fields. async_builder_command is faked at the seam; what is asserted is
what this layer owns: scope, masking, what is sent to the add-on, and what is
never relayed from it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.phoenix_mcp.const import ASSIST_CLIENT_IP
from custom_components.phoenix_mcp.esphome_builder import (
    BuilderCommandError,
    BuilderResult,
    BuilderUnavailable,
)
from custom_components.phoenix_mcp.tools import esphome
from tests.test_mcp_esphome_firmware_tools import FakeBuilder
from tests.test_mcp_esphome_tools import (
    _call,
    _device_info,
    _esphome_entry,
    _json,
    _runtime,
    _yaml_access_key,
)

_DASHBOARD_PATCH = "custom_components.phoenix_mcp.tools.esphome._esphome_dashboard"
_COMMAND_PATCH = "custom_components.phoenix_mcp.tools.esphome.async_builder_command"
FILE = "rf-blaster1.yaml"
REAL_API_KEY = "REALAPIKEY123456="
REAL_OTA_PASSWORD = "realotapassword"
REAL_WIFI_PASSWORD = "housepassword1"


def _dash():
    from types import SimpleNamespace

    return SimpleNamespace(url="http://127.0.0.1:6052", data={}, last_update_success=True)


async def _run(tool, args, hass, builder, access_key=None):
    with patch(_DASHBOARD_PATCH, return_value=_dash()), patch(_COMMAND_PATCH, builder):
        return await _call(tool, args, access_key or _yaml_access_key(), hass)


def _out_of_scope(hass, esphome_entries):
    """Adopt rf-blaster1 by an entity the test access key cannot see."""
    _esphome_entry(hass, esphome_entries, entity_ids=("light.not_mine",),
                   runtime=_runtime(_device_info(name="rf-blaster1")))


class TestDeviceFirmwareFields:
    async def test_configured_rows_carry_firmware_state_and_never_an_address(self, hass, esphome_dir):
        device = {
            "name": "rf-blaster1", "configuration": FILE, "current_version": "2026.10.1",
            "address": "rf-blaster1.local", "migration_available": True,
            "loaded_integrations": ["api", "wifi"],
            "runtime_state": {"state": "ONLINE", "deployed_version": "2026.7.3",
                              "queued_update": True, "ip_addresses": ["192.168.1.77"]},
        }
        content, outcome, _ = await _run("list_esphome_devices", {}, hass,
                                         FakeBuilder(**{"devices/list": {"configured": [device]}}))
        row = _json(content)["devices"][0]
        assert outcome == "allowed"
        assert row["deployed_version"] == "2026.7.3"
        assert row["update_available"] is True
        assert row["migration_available"] is True
        assert row["update_armed_for_next_boot"] is True
        text = content["content"][0]["text"]
        assert "192.168.1.77" not in text and "rf-blaster1.local" not in text
        assert "loaded_integrations" not in row

    async def test_matching_versions_are_not_an_update(self, hass, esphome_dir):
        device = {"name": "rf-blaster1", "configuration": FILE, "current_version": "2026.10.1",
                  "runtime_state": {"deployed_version": "2026.10.1"}}
        content, _, _ = await _run("list_esphome_devices", {}, hass,
                                   FakeBuilder(**{"devices/list": {"configured": [device]}}))
        row = _json(content)["devices"][0]
        assert row["update_available"] is False
        assert "update_armed_for_next_boot" not in row


class TestComponents:
    BODY = {"dht": {"id": "dht", "config_entries": [
        {"key": "pin"},
        {"key": "update_interval", "advanced": True},
        {"key": "model", "config_entries": [{"key": "raw", "hidden": True}, {"key": "kind"}]},
    ]}}

    async def test_advanced_and_hidden_entries_are_dropped_at_depth_by_default(self, hass, esphome_dir):
        content, _, _ = await _run("get_esphome_component", {"component_ids": ["dht"]}, hass,
                                   FakeBuilder(**{"components/get_component_bodies": self.BODY}))
        entries = _json(content)["components"]["dht"]["config_entries"]
        assert [e["key"] for e in entries] == ["pin", "model"]
        assert [e["key"] for e in entries[1]["config_entries"]] == ["kind"]

    async def test_include_advanced_returns_the_body_verbatim(self, hass, esphome_dir):
        content, _, _ = await _run("get_esphome_component",
                                   {"component_ids": ["dht"], "include_advanced": True}, hass,
                                   FakeBuilder(**{"components/get_component_bodies": self.BODY}))
        assert _json(content)["components"] == self.BODY

    async def test_file_mode_lists_declared_names_and_only_its_own_loaded_set(self, hass, esphome_dir):
        devices = {"configured": [
            {"name": "rf-blaster1", "configuration": FILE,
             "loaded_integrations": ["api", "ota", "uart", "wifi"], "loaded_platforms": ["ota.esphome"]},
            {"name": "other", "configuration": "other.yaml",
             "loaded_integrations": ["bluetooth_proxy"], "loaded_platforms": []},
        ]}
        (esphome_dir / "other.yaml").write_text("esphome:\n  name: other\n")
        builder = FakeBuilder(**{"devices/list": devices})
        content, outcome, resource = await _run("get_esphome_component", {"file": FILE}, hass, builder)
        body = _json(content)
        assert outcome == "allowed" and resource == f"esphome:{FILE}"
        assert body["declared"]["components"] == ["api", "esphome", "ota", "uart", "wifi"]
        assert body["declared"]["platforms"] == {"ota": ["esphome"]}
        assert body["loaded"]["integrations"] == ["api", "ota", "uart", "wifi"]
        text = content["content"][0]["text"]
        assert "bluetooth_proxy" not in text
        assert REAL_API_KEY not in text and REAL_OTA_PASSWORD not in text

    async def test_file_mode_refusals_match_the_other_file_tools(self, hass, esphome_dir, esphome_entries):
        builder = FakeBuilder()
        missing, missing_outcome, _ = await _run("get_esphome_component", {"file": "nope.yaml"}, hass, builder)
        _out_of_scope(hass, esphome_entries)
        hidden, hidden_outcome, _ = await _run("get_esphome_component", {"file": FILE}, hass, builder)
        assert missing["content"][0]["text"] == hidden["content"][0]["text"]
        assert (missing_outcome, hidden_outcome) == ("not_found", "denied")
        assert builder.calls == []

    async def test_file_and_component_ids_together_are_refused(self, hass, esphome_dir):
        builder = FakeBuilder()
        _content, outcome, _ = await _run("get_esphome_component",
                                          {"file": FILE, "component_ids": ["dht"]}, hass, builder)
        assert outcome == "invalid_request"
        assert builder.calls == []


AUTOMATION_YAML = '''esphome:
  name: rf-blaster1
api:
  encryption:
    key: "REALAPIKEY123456="
button:
  - platform: template
    name: Send
    on_press:
      - http_request.post:
          url: http://example.invalid
          password: "inlinepostpass9"
'''


class TestExistingAutomations:
    def _parse_reply(self, raw_yaml):
        return [{"location": {"kind": "component_on", "component_id": "send", "trigger": "on_press"},
                 "label": "Send on_press", "from_line": 9, "to_line": 12,
                 "raw_yaml": raw_yaml, "automation": {"actions": [{"action_id": "x"}]}}]

    async def test_yaml_is_cut_from_the_masked_file_and_never_relayed(self, hass, esphome_dir):
        (esphome_dir / FILE).write_text(AUTOMATION_YAML)
        # The add-on's raw_yaml is its own re-dump of the parsed value: different
        # quoting and indentation, every credential in the clear. Phoenix MCP may use
        # it only to check it read the same data, and answers from its own masked read.
        reported = ("on_press:\n- http_request.post:\n    url: http://example.invalid\n"
                    "    password: inlinepostpass9\n")
        builder = FakeBuilder(**{"automations/parse": self._parse_reply(reported)})
        content, outcome, _ = await _run("get_esphome_automations", {"file": FILE, "existing": True}, hass, builder)
        body = _json(content)
        row = body["automations"][0]
        assert outcome == "allowed"
        assert builder.args_for("automations/parse") == {"configuration": FILE}
        assert "inlinepostpass9" not in content["content"][0]["text"]
        assert "__PHOENIX_REDACTED__" in row["yaml"]
        assert "automation" not in row and "raw_yaml" not in row
        assert row["location"]["trigger"] == "on_press"

    async def test_a_slice_the_file_no_longer_holds_is_withheld(self, hass, esphome_dir):
        (esphome_dir / FILE).write_text(AUTOMATION_YAML)
        builder = FakeBuilder(**{"automations/parse": self._parse_reply("    on_press:\n      - SENTINEL_ONLY_IN_BUILDER\n")})
        content, _, _ = await _run("get_esphome_automations", {"file": FILE, "existing": True}, hass, builder)
        row = _json(content)["automations"][0]
        assert "yaml" not in row and row["yaml_withheld"]
        assert "SENTINEL_ONLY_IN_BUILDER" not in content["content"][0]["text"]

    async def test_an_unparseable_file_shows_no_yaml(self, hass, esphome_dir):
        broken = AUTOMATION_YAML + "bad: [unclosed\n"
        (esphome_dir / FILE).write_text(broken)
        lines = broken.splitlines(keepends=True)
        builder = FakeBuilder(**{"automations/parse": self._parse_reply("".join(lines[8:12]).replace("    ", "", 1))})
        content, _, _ = await _run("get_esphome_automations", {"file": FILE, "existing": True}, hass, builder)
        body = _json(content)
        assert "yaml" not in body["automations"][0]
        assert body["warnings"]
        assert "inlinepostpass9" not in content["content"][0]["text"]


class TestAutomationDocs:
    async def test_refs_page_until_nothing_remains(self, hass, esphome_dir):
        pages = iter([
            {"bodies": {"actions/light.turn_on": {"id": "light.turn_on"}},
             "remaining": [{"type": "conditions", "id": "lambda"}]},
            {"bodies": {"conditions/lambda": {"id": "lambda"}}, "remaining": []},
        ])
        builder = FakeBuilder(**{"automations/get_bodies": lambda _args: next(pages)})
        refs = [{"type": "actions", "id": "light.turn_on"}, {"type": "conditions", "id": "lambda"},
                {"type": "triggers", "id": "on_nothing"}]
        content, outcome, resource = await _run("get_esphome_automations", {"refs": refs}, hass, builder)
        body = _json(content)
        assert outcome == "allowed" and resource == "get_esphome_automations"
        assert set(body["bodies"]) == {"actions/light.turn_on", "conditions/lambda"}
        assert body["not_found"] == ["triggers/on_nothing"]
        assert builder.calls[1][1] == {"refs": [{"type": "conditions", "id": "lambda"}]}

    async def test_a_page_without_progress_ends_the_loop(self, hass, esphome_dir):
        stuck = {"bodies": {}, "remaining": [{"type": "actions", "id": "light.turn_on"}]}
        builder = FakeBuilder(**{"automations/get_bodies": stuck})
        content, _, _ = await _run("get_esphome_automations",
                                   {"refs": [{"type": "actions", "id": "light.turn_on"}]}, hass, builder)
        assert len(builder.calls) == 1
        assert _json(content)["not_listed"] == ["actions/light.turn_on"]

    @pytest.mark.parametrize("refs", [
        [{"type": "scripts", "id": "x"}],
        [{"type": "actions", "id": "has space"}],
        [{"type": "actions", "id": "x", "extra": 1}],
        [{"type": "actions", "id": f"a{i}"} for i in range(21)],
    ])
    async def test_bad_refs_are_refused_before_any_call(self, hass, esphome_dir, refs):
        builder = FakeBuilder()
        _content, outcome, _ = await _run("get_esphome_automations", {"refs": refs}, hass, builder)
        assert outcome == "invalid_request"
        assert builder.calls == []

    async def test_refs_with_a_file_are_refused(self, hass, esphome_dir):
        builder = FakeBuilder()
        _content, outcome, _ = await _run("get_esphome_automations",
                                          {"file": FILE, "refs": [{"type": "actions", "id": "x"}]}, hass, builder)
        assert outcome == "invalid_request" and builder.calls == []


MIGRATABLE_YAML = '''esphome:
  name: rf-blaster1
api:
  services:
    - service: send
      then:
        - logger.log: hi
ota:
  - platform: esphome
    password: "realotapassword"
'''


class TestValidateMigration:
    def _builder(self, migrate):
        return FakeBuilder(**{
            "devices/validate": BuilderResult(output="INFO valid\n", success=True, exit_code=0),
            "editor/migrate_config": migrate,
        })

    async def test_migration_diff_is_masked_and_the_replacement_never_relayed(self, hass, esphome_dir):
        (esphome_dir / FILE).write_text(MIGRATABLE_YAML)
        migrate = {
            "yaml_diff": {"fromLine": 4, "toLine": 5,
                          "replacement": "  actions:\n    - action: send\n"},
            "changes": [{"kind": "key", "scope": "api", "old": "services", "new": "actions",
                         "since": "2024.5.0", "removed_in": None, "required": False},
                        {"kind": "field", "scope": "api", "old": "a value with spaces", "new": "x"}],
        }
        content, outcome, _ = await _run("validate_esphome_yaml", {"file": FILE}, hass, self._builder(migrate))
        body = _json(content)
        migration = body["migration"]
        assert outcome == "allowed" and body["valid"] is True
        assert migration["needed"] is True
        assert "+  actions:" in migration["diff"] and "-  services:" in migration["diff"]
        assert migration["changes"][0] == {"kind": "key", "scope": "api", "old": "services",
                                           "new": "actions", "since": "2024.5.0", "required": False}
        assert "old" not in migration["changes"][1]
        assert REAL_OTA_PASSWORD not in content["content"][0]["text"]

    async def test_a_migration_that_unmasks_a_credential_withholds_its_diff(self, hass, esphome_dir):
        (esphome_dir / FILE).write_text(MIGRATABLE_YAML)
        # Respelling the password key to a name the mask does not know would show
        # its value on the migrated side.
        migrate = {"yaml_diff": {"fromLine": 10, "toLine": 10, "replacement": '    pw: "short1"\n'},
                   "changes": []}
        (esphome_dir / FILE).write_text(MIGRATABLE_YAML.replace("realotapassword", "short1"))
        content, _, _ = await _run("validate_esphome_yaml", {"file": FILE}, hass, self._builder(migrate))
        migration = _json(content)["migration"]
        assert "diff" not in migration and migration["diff_withheld"]
        assert "short1" not in content["content"][0]["text"]

    async def test_nothing_to_migrate(self, hass, esphome_dir):
        content, _, _ = await _run("validate_esphome_yaml", {"file": FILE}, hass,
                                   self._builder({"yaml_diff": None, "changes": []}))
        assert _json(content)["migration"] == {"needed": False}

    async def test_a_failed_check_never_fails_validation(self, hass, esphome_dir):
        content, outcome, _ = await _run("validate_esphome_yaml", {"file": FILE}, hass,
                                         self._builder(BuilderUnavailable("down")))
        body = _json(content)
        assert outcome == "allowed" and body["valid"] is True
        assert "migration_unavailable" in body

    async def test_an_unparseable_file_is_not_sent_for_migration(self, hass, esphome_dir):
        (esphome_dir / FILE).write_text("esphome: [unclosed\n")
        builder = self._builder({"yaml_diff": None})
        content, _, _ = await _run("validate_esphome_yaml", {"file": FILE}, hass, builder)
        assert "editor/migrate_config" not in builder.commands
        assert "migration_unavailable" in _json(content)


TROUBLESHOOT = {
    "configuration": FILE, "address": "192.168.1.77", "icmp_available": True,
    "zeroconf_running": True, "dns_resolved": True, "dns_addresses": ["192.168.1.77"],
    "mdns_addresses": ["192.168.1.77", "fe80::1%eth0"], "ping_attempted": True,
    "ping_target": "192.168.1.77", "ping_target_source": "dns", "ping_rtt_ms": 3.456,
    "future_field": {"nested": "192.168.1.78"},
}


class TestConnectivity:
    async def test_reports_verdicts_and_counts_but_no_address(self, hass, esphome_dir):
        builder = FakeBuilder(**{"devices/troubleshoot": TROUBLESHOOT})
        content, outcome, _ = await _run("check_esphome_connectivity", {"file": FILE}, hass, builder)
        body = _json(content)
        text = content["content"][0]["text"]
        assert outcome == "allowed"
        assert body["verdict"] == "reachable" and body["ping_rtt_ms"] == 3.5
        assert body["dns_address_count"] == 1 and body["mdns_address_count"] == 2
        assert body["address_kind"] == "ip"
        assert "192.168.1." not in text and "fe80" not in text and "future_field" not in text
        assert builder.args_for("devices/troubleshoot") == {"configuration": FILE}

    async def test_read_scope_suffices_and_hidden_files_are_not_found(self, hass, esphome_dir, esphome_entries):
        builder = FakeBuilder(**{"devices/troubleshoot": TROUBLESHOOT})
        _out_of_scope(hass, esphome_entries)
        content, outcome, _ = await _run("check_esphome_connectivity", {"file": FILE}, hass, builder)
        assert outcome == "denied" and builder.calls == []

    async def test_a_silent_device_is_not_found_on_the_network(self, hass, esphome_dir):
        reply = {"address": "porch-light.local", "icmp_available": True, "ping_attempted": False}
        content, _, _ = await _run("check_esphome_connectivity", {"file": FILE}, hass,
                                   FakeBuilder(**{"devices/troubleshoot": reply}))
        body = _json(content)
        assert body["verdict"] == "not_found_on_network" and body["address_kind"] == "mdns"
        assert "porch-light.local" not in content["content"][0]["text"]

    async def test_headless_surfaces_get_the_short_timeout(self, hass, esphome_dir):
        seen = {}

        async def builder(_hass, _url, command, args, **kw):
            seen["timeout"] = kw["timeout_seconds"]
            return BuilderResult(result={})

        with patch(_DASHBOARD_PATCH, return_value=_dash()), patch(_COMMAND_PATCH, builder):
            await esphome._tool_check_esphome_connectivity(
                {"file": FILE}, _yaml_access_key(), hass, client_ip=ASSIST_CLIENT_IP)
        assert seen["timeout"] == esphome.ESPHOME_BUILDER_TROUBLESHOOT_HEADLESS_SECONDS


class TestMemoryAnalysis:
    def _builder(self, follow=None, jobs=None):
        return FakeBuilder(**{
            "firmware/get_jobs": jobs or [],
            "firmware/analyze_memory": {"job_id": "an1", "status": "queued"},
            "firmware/follow_job": follow or BuilderResult(
                result={"status": "completed", "exit_code": 0},
                output=f"Flash: 61% RAM: 12% (key {REAL_API_KEY})\n", exit_code=0),
        })

    async def test_reports_the_scrubbed_report(self, hass, esphome_dir):
        builder = self._builder()
        content, outcome, _ = await _run("analyze_esphome_memory", {"file": FILE}, hass, builder)
        body = _json(content)
        assert outcome == "allowed" and body["status"] == "completed"
        assert "Flash: 61%" in body["report"] and REAL_API_KEY not in content["content"][0]["text"]
        assert builder.commands == ["firmware/get_jobs", "firmware/analyze_memory", "firmware/follow_job"]

    async def test_refused_while_a_job_is_in_flight(self, hass, esphome_dir):
        builder = self._builder(jobs=[{"job_id": "inst", "configuration": FILE, "status": "running"}])
        content, outcome, _ = await _run("analyze_esphome_memory", {"file": FILE}, hass, builder)
        assert outcome == "invalid_request" and "inst" in content["content"][0]["text"]
        assert "firmware/analyze_memory" not in builder.commands

    async def test_headless_surfaces_are_refused_before_any_call(self, hass, esphome_dir):
        builder = self._builder()
        with patch(_DASHBOARD_PATCH, return_value=_dash()), patch(_COMMAND_PATCH, builder):
            _content, outcome, _ = await esphome._tool_analyze_esphome_memory(
                {"file": FILE}, _yaml_access_key(), hass, client_ip=ASSIST_CLIENT_IP)
        assert outcome == "invalid_request" and builder.calls == []

    async def test_needs_write_scope_like_compile(self, hass, esphome_dir, esphome_entries):
        _esphome_entry(hass, esphome_entries, entity_ids=("sensor.rf_blaster1_rssi",),
                       runtime=_runtime(_device_info(name="rf-blaster1")))
        from custom_components.phoenix_mcp.access_key_store import PermissionNode, PermissionTree

        read_only = _yaml_access_key()
        read_only.permissions = PermissionTree(domains={"sensor": PermissionNode(state="YELLOW")})
        builder = self._builder()
        _content, outcome, _ = await _run("analyze_esphome_memory", {"file": FILE}, hass, builder, read_only)
        assert outcome == "denied" and builder.calls == []

    async def test_a_timeout_cancels_the_uncollectable_job(self, hass, esphome_dir):
        builder = self._builder(follow=BuilderUnavailable("slow"))
        _content, outcome, _ = await _run("analyze_esphome_memory", {"file": FILE}, hass, builder)
        assert outcome == "invalid_request"
        assert builder.args_for("firmware/cancel") == {"job_id": "an1"}

    async def test_a_vanished_job_says_run_again(self, hass, esphome_dir):
        builder = self._builder(follow=BuilderCommandError("internal_error", "Job not found"))
        content, outcome, _ = await _run("analyze_esphome_memory", {"file": FILE}, hass, builder)
        assert outcome == "invalid_request" and "Run it again" in content["content"][0]["text"]

    async def test_a_superseded_analysis_says_so(self, hass, esphome_dir):
        follow = BuilderResult(result={"status": "cancelled"}, output="", exit_code=-15)
        content, _, _ = await _run("analyze_esphome_memory", {"file": FILE}, hass, self._builder(follow=follow))
        assert "cancelled the analysis" in _json(content)["note"]

    async def test_job_tools_answer_an_analysis_with_a_note_and_no_replay(self, hass, esphome_dir):
        job = {"job_id": "an1", "configuration": FILE, "status": "running", "job_type": "analyze_memory"}
        builder = FakeBuilder(**{"firmware/get_job": job})
        content, _, _ = await _run("get_esphome_job", {"lookup": {"kind": "job", "id": "an1"}}, hass, builder)
        assert "analyze_esphome_memory" in _json(content)["note"]
        assert "firmware/follow_job" not in builder.commands
        content, _, _ = await _run("wait_for_esphome_job", {"job_id": "an1"}, hass, builder)
        assert "analyze_esphome_memory" in _json(content)["note"]


class TestJobParity:
    async def test_failure_reason_and_an_unavailable_log_are_reported(self, hass, esphome_dir):
        job = {"job_id": "j1", "configuration": FILE, "status": "failed", "job_type": "compile",
               "failure_reason": "provision", "exit_code": 1}
        builder = FakeBuilder(**{"firmware/get_job": job,
                                 "firmware/follow_job": BuilderUnavailable("gone")})
        content, _, _ = await _run("get_esphome_job", {"lookup": {"kind": "job", "id": "j1"}}, hass, builder)
        body = _json(content)
        assert body["failure_reason"] == "provision"
        assert body["output_available"] is False

    async def test_an_odd_failure_reason_is_not_relayed(self, hass, esphome_dir):
        job = {"job_id": "j1", "configuration": FILE, "status": "failed", "failure_reason": "see 192.168.1.9"}
        builder = FakeBuilder(**{"firmware/get_job": job, "firmware/follow_job": BuilderResult(result={})})
        content, _, _ = await _run("get_esphome_job", {"lookup": {"kind": "job", "id": "j1"}}, hass, builder)
        assert "failure_reason" not in _json(content)

    async def test_the_follow_frames_armed_flag_marks_a_deferred_install(self, hass, esphome_dir):
        job = {"job_id": "j1", "configuration": FILE, "status": "completed", "job_type": "compile"}
        builder = FakeBuilder(**{
            "firmware/get_job": job,
            "firmware/follow_job": BuilderResult(result={"status": "completed", "queued_update_armed": True}),
            "firmware/get_jobs": [],
        })
        content, _, _ = await _run("get_esphome_job", {"lookup": {"kind": "job", "id": "j1"}}, hass, builder)
        body = _json(content)
        assert body["armed_for_next_boot"] is True and body["flashed"] is False
