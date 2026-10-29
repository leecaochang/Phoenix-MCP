"""Tests for ESPHome device-YAML credential masking (esphome_yaml.py)."""

from __future__ import annotations

import pytest

from custom_components.phoenix_mcp.esphome_yaml import (
    esphome_rel_path_ok,
    inline_secret_values,
    redact_esphome_text,
    scrub_secret_values,
    secret_keys_from_text,
    secret_values_from_text,
)
from custom_components.phoenix_mcp.yaml_includes import YamlParseError

# A representative device file: inline api key, LIST-form ota (the modern syntax
# a legacy-only path list would miss), a !lambda C++ block, and wifi behind
# !secret.
LIVE_SHAPE = '''esphome:
  name: rf-blaster1

# Enable Home Assistant API
api:
  encryption:
    key: "ndqsa+Shak6xAu4IU1NW=="
  actions:
    - action: transmit_raw
      variables:
        timings: int[]
      then:
        - remote_transmitter.transmit_raw:
            code: !lambda |-
              esphome::remote_base::RawTimings v;
              return v;

ota:
  - platform: esphome
    password: "otapassword123"

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

uart:
  baud_rate: 9600
'''


class TestJail:
    @pytest.mark.parametrize("rel", [
        "rf-blaster1.yaml", "sub/dir/device.yml", "esphome-web-31fca4.yaml",
    ])
    def test_allowed(self, rel):
        assert esphome_rel_path_ok(rel) is True

    @pytest.mark.parametrize("rel", [
        "secrets.yaml", "SECRETS.YAML", "Secrets.Yaml",
        "archive/old.yaml", "ARCHIVE/old.yaml",
        ".device-builder.json", ".git/config", "sub/.hidden/x.yaml",
        "notes.txt", "device.yaml.bak", "", "   ",
    ])
    def test_refused(self, rel):
        assert esphome_rel_path_ok(rel) is False

    def test_non_string_refused(self):
        assert esphome_rel_path_ok(None) is False


class TestLayer1CuratedPaths:
    def test_api_key_and_list_form_ota_redacted(self):
        red, paths = redact_esphome_text(LIVE_SHAPE)
        assert paths == ["api.encryption.key", "ota[0].password"]
        assert "ndqsa" not in red
        assert "otapassword123" not in red
        assert "__PHOENIX_REDACTED__api.encryption.key__" in red
        # The legacy top-level ota.password path alone would have missed this.
        assert "__PHOENIX_REDACTED__ota[0].password__" in red

    def test_legacy_api_password_and_wifi_networks_list(self):
        text = (
            "api:\n  password: legacyapipw\n"
            "wifi:\n  networks:\n    - ssid: A\n      password: netpassword1\n"
            "    - ssid: B\n      password: netpassword2\n"
        )
        _, paths = redact_esphome_text(text)
        assert paths == ["api.password", "wifi.networks[0].password", "wifi.networks[1].password"]

    def test_tagged_scalars_and_comments_survive(self):
        red, _ = redact_esphome_text(LIVE_SHAPE)
        assert "ssid: !secret wifi_ssid" in red
        assert "password: !secret wifi_password" in red
        assert "!lambda" in red
        assert "# Enable Home Assistant API" in red
        assert "baud_rate: 9600" in red

    def test_unparseable_fails_closed(self):
        with pytest.raises(YamlParseError):
            redact_esphome_text("api:\n  key: [unclosed\n")

    def test_bare_api_and_passwordless_ota_walk_cleanly(self):
        # A stock web-flashed device file: no encryption block at all.
        text = "api:\n\n# Allow OTA\nota:\n- platform: esphome\n"
        red, paths = redact_esphome_text(text)
        assert paths == []
        assert red == text


@pytest.mark.parametrize("prefix,dotted", [
    ("ota:\n  encryption:\n    key: ", "ota.encryption.key"),
    ("ota:\n  - platform: esphome\n    encryption:\n      key: ", "ota[0].encryption.key"),
])
def test_ota_encryption_keys_are_masked_and_scrubbed(prefix, dotted):
    import base64

    key = base64.b64encode(bytes(range(32))).decode("ascii")
    disk = prefix + key + "\n"
    masked, paths = redact_esphome_text(disk)
    assert key not in masked
    assert dotted in paths
    assert f"__PHOENIX_REDACTED__{dotted}__" in masked
    assert key in inline_secret_values(disk)
    assert key not in scrub_secret_values("compiler reports " + key, inline_secret_values(disk))


class TestLayer2KeyNameHeuristic:
    def test_credential_key_at_an_uncurated_path(self):
        text = "ble_client:\n  - mac_address: AA\n    bindkey: abcdef0123456789\n"
        red, paths = redact_esphome_text(text)
        assert paths == ["ble_client[0].bindkey"]
        assert "abcdef0123456789" not in red

    def test_token_in_a_custom_component_block(self):
        text = "http_request:\n  headers:\n    auth_token: zzzsecrettokenzzz\n"
        _, paths = redact_esphome_text(text)
        assert paths == ["http_request.headers.auth_token"]

    def test_benign_keys_untouched(self):
        text = "sensor:\n  - platform: dht\n    model: DHT22\n    update_interval: 60s\n"
        red, paths = redact_esphome_text(text)
        assert paths == []
        assert red == text


class TestLayer3SecretsCrossCheck:
    def test_value_matching_secrets_yaml_at_an_unknown_path(self):
        # Most files reference a secret via !secret while another inlines the same
        # value, so the inline copy must be caught even where the path is not a
        # known credential location.
        text = "substitutions:\n  wifi_pass: housepassword1\n"
        red, paths = redact_esphome_text(text, {"housepassword1"})
        assert paths == ["substitutions.wifi_pass"]
        assert "housepassword1" not in red

    def test_short_secret_values_never_trigger(self):
        # A short secrets.yaml value would otherwise mangle pins and intervals.
        text = "uart:\n  tx_pin: GPIO19\n  baud_rate: 9600\n"
        red, paths = redact_esphome_text(text, {"9600", "GPIO19"})
        assert paths == []
        assert red == text

    def test_substring_occurrences_never_trigger(self):
        text = "esphome:\n  name: housepassword1-extended-name\n"
        _, paths = redact_esphome_text(text, {"housepassword1"})
        assert paths == []

    def test_missing_secrets_degrades_to_layers_1_and_2(self):
        red, paths = redact_esphome_text(LIVE_SHAPE, set())
        assert paths == ["api.encryption.key", "ota[0].password"]
        assert "ndqsa" not in red

    def test_secret_parsing_helpers_tolerate_junk(self):
        assert secret_values_from_text("not: [valid") == set()
        assert secret_keys_from_text("not: [valid") == set()
        assert secret_values_from_text("a: short\nb: longenoughvalue\n") == {"longenoughvalue"}
        assert secret_keys_from_text("wifi_ssid: x\nwifi_password: y\n") == {"wifi_ssid", "wifi_password"}


class TestScrubHelpers:
    """The free-text scrub used on Device Builder output.

    Validation errors quote the offending YAML line back, so a file carrying an
    inline credential can print it into a message that has no structure to
    splice; a blind value replace is the only tool that works there.
    """

    FILE = '''esphome:
  name: dev
api:
  encryption:
    key: "INLINEKEY1234567="
wifi:
  password: !secret wifi_password
  ssid: MyNetworkName
'''

    def test_returns_values_not_spans(self):
        values = inline_secret_values(self.FILE)
        # The value itself, unquoted, is what has to be matched in free text.
        assert "INLINEKEY1234567=" in values
        assert '"INLINEKEY1234567="' not in values

    def test_secrets_yaml_values_are_included_when_inlined(self):
        values = inline_secret_values(self.FILE, {"MyNetworkName"})
        assert "MyNetworkName" in values

    def test_short_values_never_scrubbed(self):
        # A blind substring replace over arbitrary output would corrupt it.
        short = 'esphome:\n  name: dev\nsensor:\n  - platform: gpio\n    password: "abc"\n'
        assert inline_secret_values(short) == set()

    def test_secret_refs_are_not_values(self):
        assert not any("!secret" in v for v in inline_secret_values(self.FILE))

    def test_scrub_replaces_every_occurrence(self):
        out = scrub_secret_values("saw pw1234567 twice: pw1234567", {"pw1234567"})
        assert "pw1234567" not in out
        assert out.count("<redacted>") == 2

    def test_scrub_replaces_longest_value_first(self):
        # Replacing the short one first would leave a readable remainder of the
        # long one behind.
        out = scrub_secret_values("value=abcdefgh12345", {"abcdefgh", "abcdefgh12345"})
        assert "abcdefgh" not in out

    def test_scrub_leaves_unrelated_text_alone(self):
        out = scrub_secret_values("ERROR at line 12: bad pin", {"housepassword1"})
        assert out == "ERROR at line 12: bad pin"

    def test_unparseable_file_raises_so_callers_decide(self):
        with pytest.raises(YamlParseError):
            inline_secret_values("esphome:\n  name: [unclosed\n")


class TestSliceMasking:
    """redact_esphome_slice cuts lines from a whole-document mask, never a partial parse."""

    TEXT = (
        "esphome:\n  name: dev\n"
        "ota:\n  - platform: esphome\n    password: \"slicepassword1\"\n"
        "web_server:\n  auth:\n    password: |\n      blockline-one\n      blockline-two\n"
        "uart:\n  baud_rate: 9600\n"
    )

    def test_a_credential_inside_the_slice_is_masked(self):
        from custom_components.phoenix_mcp.esphome_yaml import redact_esphome_slice

        out = redact_esphome_slice(self.TEXT, set(), 3, 5)
        assert "slicepassword1" not in out and "__PHOENIX_REDACTED__ota[0].password__" in out
        assert out.startswith("ota:\n")

    def test_a_block_scalar_that_starts_above_the_slice_leaves_nothing(self):
        from custom_components.phoenix_mcp.esphome_yaml import redact_esphome_slice

        out = redact_esphome_slice(self.TEXT, set(), 9, 10)
        assert "blockline" not in out
        assert "__PHOENIX_REDACTED__web_server.auth.password__" in out

    def test_lines_without_credentials_are_returned_verbatim(self):
        from custom_components.phoenix_mcp.esphome_yaml import redact_esphome_slice

        assert redact_esphome_slice(self.TEXT, set(), 11, 12) == "uart:\n  baud_rate: 9600\n"

    def test_fails_closed_and_refuses_ranges_outside_the_file(self):
        from custom_components.phoenix_mcp.esphome_yaml import redact_esphome_slice

        with pytest.raises(YamlParseError):
            redact_esphome_slice("a: [x\n", set(), 1, 1)
        with pytest.raises(ValueError):
            redact_esphome_slice(self.TEXT, set(), 0, 2)
        with pytest.raises(ValueError):
            redact_esphome_slice(self.TEXT, set(), 12, 13)


class TestDeclaredComponents:
    def test_names_only_with_platforms_and_the_include_flag(self):
        from custom_components.phoenix_mcp.esphome_yaml import declared_components

        text = (
            "esphome:\n  name: dev\npackages:\n  base: !include base.yaml\n"
            "sensor:\n  - platform: dht\n    pin: GPIO4\n  - platform: !lambda x\n"
            "wifi:\n  password: \"declaredpass1\"\n"
        )
        out = declared_components(text)
        assert out == {"components": ["esphome", "packages", "sensor", "wifi"],
                       "platforms": {"sensor": ["dht"]}, "uses_includes": True}
        assert "declaredpass1" not in repr(out) and "GPIO4" not in repr(out)

    def test_a_plain_file_uses_no_includes(self):
        from custom_components.phoenix_mcp.esphome_yaml import declared_components

        assert declared_components("ota:\n  platform: esphome\n")["uses_includes"] is False


class TestLineSplice:
    """apply_line_splice mirrors the add-on's own apply_yaml_diff."""

    def test_replace_insert_and_bounds(self):
        from custom_components.phoenix_mcp.esphome_yaml import apply_line_splice

        text = "a\nb\nc\n"
        assert apply_line_splice(text, 2, 2, "B\n") == "a\nB\nc\n"
        assert apply_line_splice(text, 2, 1, "x\n") == "a\nx\nb\nc\n"
        assert apply_line_splice("a\nb", 2, 2, "B\n") == "a\nB"
        with pytest.raises(ValueError):
            apply_line_splice(text, 3, 1, "x")
        with pytest.raises(ValueError):
            apply_line_splice(text, 1, 4, "x")


def test_masked_diff_keeps_an_unterminated_last_line_apart():
    from custom_components.phoenix_mcp.esphome_yaml import masked_unified_diff

    diff, truncated = masked_unified_diff("x", "a: 1\n", "old", "new", 10_000)
    assert "-x\n+a: 1\n" in diff and truncated is False
