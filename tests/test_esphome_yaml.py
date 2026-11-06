"""Tests for ESPHome device-YAML credential masking (esphome_yaml.py)."""

from __future__ import annotations

import pytest

from custom_components.phoenix_mcp.esphome_yaml import (
    esphome_rel_path_ok,
    inline_secret_values,
    redact_esphome_slice,
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


# Each document carries credentials spelled SECRETVAL<n> in a shape device files use.
LEAK_SHAPES = {
    "list_under_key": "wifi:\n  ssid: x\n  passwords:\n    - SECRETVAL1\n    - SECRETVAL2\n",
    "flow_list_under_key": "my_component:\n  api_tokens: [SECRETVAL3, SECRETVAL4]\n",
    "mapping_under_key": "foo:\n  password:\n    value: SECRETVAL5\n",
    "url_userinfo": "http_request:\nsomething:\n  url: https://user:SECRETVAL6@example.com/path\n",
    "url_behind_an_escape": 'x:\n  url: "https://user:SECRETVAL6\\x40example.com/path"\n',
    "url_query": "on_boot:\n  - http_request.get:\n      url: https://x.example/api?token=SECRETVAL7\n",
    "package_token": "packages:\n  remote:\n    url: https://ghp_SECRETVAL8@github.com/me/repo\n    ref: main\n",
    "external_component": (
        "external_components:\n  - source:\n      type: git\n      url: https://oauth2:SECRETVAL9@gitlab.com/x/y\n"),
    "external_shorthand": "external_components:\n  - source: github://user:SECRETVAL10@me/repo\n",
    "certificate_key": (
        "mqtt:\n  broker: x\n  client_certificate_key: |\n    -----BEGIN PRIVATE KEY-----\n"
        "    SECRETVAL11\n    -----END PRIVATE KEY-----\n"),
    "eap_key": (
        "wifi:\n  eap:\n    username: u\n    key: |\n      -----BEGIN PRIVATE KEY-----\n      SECRETVAL12\n"
        "    certificate: c\n"),
    "decryption_key": "dsmr:\n  decryption_key: SECRETVAL13\n",
    "bare_key": "wmbus:\n  meters:\n    - key: SECRETVAL14\n",
    "encryption_scalar": "packet_transport:\n  - platform: udp\n    encryption: SECRETVAL15\n",
    "encryption_nested": (
        "packet_transport:\n  - platform: udp\n    encryption:\n      key: SECRETVAL16\n    providers:\n"
        "      - name: a\n        encryption: SECRETVAL17\n"),
    "json_body": (
        "on_boot:\n  - http_request.post:\n      url: https://x\n      json:\n        pwd: SECRETVAL18\n"
        "        apiKey: SECRETVAL19\n        access-key: SECRETVAL20\n        client_secret: SECRETVAL21\n"),
    "lambda_bearer": "on_boot:\n  - lambda: |-\n      const char* k = \"Bearer SECRETVAL22\";\n",
    "lambda_assignment": "on_boot:\n  - lambda: |-\n      std::string password = \"SECRETVAL22\";\n",
    "substitutions": (
        "substitutions:\n  wifi_pw: SECRETVAL23\n  mqttpass: SECRETVAL24\n  admin_pass: SECRETVAL25\n  pw2: SECRETVAL26\n"
        "  wifipassword: SECRETVAL27\n  auth_key: SECRETVAL28\n  key: SECRETVAL29\n  ha_llat: SECRETVAL30\n"
        "  wg_key: SECRETVAL31\n"),
    "wireguard": (
        "wireguard:\n  address: 10.0.0.1\n  private_key: SECRETVAL32\n  peer_public_key: pub\n"
        "  peer_preshared_key: SECRETVAL33\n"),
    "espnow": "espnow:\n  peers:\n    - AA:BB\n  encryption_key: SECRETVAL34\n",
    "other_names": (
        "somedev:\n  aes_key: SECRETVAL35\n  psk_key: SECRETVAL36\n  wep: SECRETVAL37\n  pre_shared: SECRETVAL38\n"
        "  sas: SECRETVAL39\n  credentials: SECRETVAL40\n"),
    "anchor": "wifi:\n  password: &p SECRETVAL41\n  ap:\n    password: *p\n",
    "anchor_elsewhere": "common: &p SECRETVAL42\nwifi:\n  password: *p\n",
    "merge": "base: &b\n  password: SECRETVAL43\nwifi:\n  <<: *b\n",
    "quoted_key": "\"wifi\":\n  \"password\": 'SECRETVAL44'\n",
    "complex_key": "? password\n: SECRETVAL45\n",
    "mapping_as_key": "? {a: SECRETVAL45}\n: SECRETVAL45\n",
    "request_headers": "http_request.post:\n  request_headers:\n    Authorization: Bearer SECRETVAL46\n",
    "headers_capitalized": "x:\n  Headers:\n    X-Thing: SECRETVAL47\n",
    "collect_headers": "x:\n  collect_headers:\n    - SECRETVAL48\n",
    "lambda_tag_on_password": "wifi:\n  password: !lambda 'return \"SECRETVAL49\";'\n",
    "carriage_returns": "wifi:\r\n  password: SECRETVAL50\r\n",
    "folded": "wifi:\n  password: >-\n    SECRETVAL51\n    SECRETVAL52\n",
    "commented_out": "wifi:\n  ssid: a\n  # old password: SECRETVAL53\n  password: x\n",
    "commented_out_block": "wifi:\n  ssid: a\n  # password:\n  #   SECRETVAL53\n",
    "comment_beside_value": "wifi:\n  password: x # was SECRETVAL53\n",
    "block_header_comment": "x:\n  note: |  # token SECRETVAL53\n    fine\n",
    "flow_mapping": "wifi: {ssid: a, password: SECRETVAL54}\n",
    "explicit_str_tag": "x:\n  password: !!str SECRETVAL55\n",
}


class TestLeakShapes:
    @pytest.mark.parametrize("name", LEAK_SHAPES)
    def test_no_shape_returns_a_credential(self, name):
        masked, _ = redact_esphome_text(LEAK_SHAPES[name])
        assert "SECRETVAL" not in masked

    @pytest.mark.parametrize("name", LEAK_SHAPES)
    def test_a_slice_of_the_whole_file_matches_the_whole_read(self, name):
        text = LEAK_SHAPES[name]
        assert redact_esphome_slice(text, set(), 1, len(text.splitlines())) == redact_esphome_text(text)[0]

    def test_a_slice_through_a_partly_masked_scalar_masks_all_of_it(self):
        text = 'on_boot:\n  - lambda: |-\n      a();\n      url = "https://u:p@host";\nnext: 1\n'
        assert "u:p@host" in text and "a();" not in redact_esphome_slice(text, set(), 1, 3)
        assert "u:p@host" not in redact_esphome_slice(text, set(), 4, 4)

    def test_an_aliased_scalar_is_replaced_once(self):
        text = "wifi:\n  password: &p " + "x" * 80 + "\n  ap:\n    password: *p\nnext: 1\n"
        masked, paths = redact_esphome_text(text)
        assert masked == "wifi:\n  password: __PHOENIX_REDACTED__wifi.password__\n  ap:\n    password: *p\nnext: 1\n"
        assert paths == ["wifi.ap.password", "wifi.password"]

    def test_a_block_scalar_leaves_the_next_key_on_its_own_line(self):
        masked, _ = redact_esphome_text("wifi:\n  password: |\n    abc\n  ssid: home\n")
        assert masked == "wifi:\n  password: __PHOENIX_REDACTED__wifi.password__\n  ssid: home\n"

    def test_only_the_credential_in_a_url_is_replaced(self):
        masked, _ = redact_esphome_text("x:\n  url: https://user:hunter2@example.com/path\n")
        assert masked == "x:\n  url: https://<redacted>@example.com/path\n"

    def test_the_credential_in_a_url_is_scrubbed_from_free_text_too(self):
        url = "https://user:hunter2@example.com/path"
        assert url in inline_secret_values(f"x:\n  url: {url}\n")

    @pytest.mark.parametrize("key", ["public_key", "peer_public_key", "bypass", "compass", "keys", "tx_pin"])
    def test_public_names_stay_readable(self, key):
        text = f"x:\n  {key}: visible\n"
        assert redact_esphome_text(text) == (text, [])

    def test_a_flag_below_a_credential_key_stays_readable(self):
        text = "ota:\n  - platform: esphome\n    encryption:\n      allow_plaintext_upload: true\n"
        assert redact_esphome_text(text) == (text, [])

    def test_a_gpio_mapping_stays_readable(self):
        text = "output:\n  - platform: gpio\n    pin:\n      number: GPIO5\n      inverted: true\n"
        assert redact_esphome_text(text) == (text, [])

    def test_ordinary_comments_stay_and_no_path_is_reported_for_a_blanked_one(self):
        text = "# Relay on GPIO5\nwifi:\n  # password: old\n  ssid: a # upstairs\n"
        assert redact_esphome_text(text) == ("# Relay on GPIO5\nwifi:\n  # <redacted>\n  ssid: a # upstairs\n", [])

    def test_adjacent_comment_lines_go_together_but_a_blank_line_ends_them(self):
        text = "a: 1\n# token:\n#   abc\n\n# unrelated\n"
        assert redact_esphome_text(text)[0] == "a: 1\n# <redacted>\n# <redacted>\n\n# unrelated\n"

    @pytest.mark.parametrize("key", ["community", "pincode", "signing_key", "session_id"])
    def test_the_shared_markers_apply_to_device_files(self, key):
        masked, paths = redact_esphome_text(f"x:\n  {key}: hunter2\n")
        assert "hunter2" not in masked and paths == [f"x.{key}"]

    def test_a_parse_error_does_not_quote_the_file(self):
        with pytest.raises(YamlParseError) as caught:
            redact_esphome_text("password: [hunter2\nother: 1\n")
        assert "hunter2" not in str(caught.value) and "line 2" in str(caught.value)


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
