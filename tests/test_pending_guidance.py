"""The surfaces that tell an agent what to do about a pending approval agree.

Approvals STAGE: they queue and the operator clears them in one action, so after
a gate the agent keeps going and collects the outcomes together with a single
`wait_for_approval` call over a list of `approval_ids`. A surface that points at
a singular `approval_id` makes an agent serialise on the first id, which is the
stall staging exists to remove.

Wording is the only mechanism here: nothing enforces what an agent does next, so
the phrases are pinned.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from custom_components.phoenix_mcp.skill_view import PHOENIX_SKILL_MARKDOWN
from custom_components.phoenix_mcp.tool_common import _tool_pending


def _message(**kwargs) -> str:
    approval = SimpleNamespace(id="appr-1", tool_name="create_scene", expires_at=None)
    return json.loads(_tool_pending(approval, **kwargs)["content"][0]["text"])["message"]


class TestPendingToolResult:
    def test_it_uses_the_custom_panel_route_for_review(self):
        approval = SimpleNamespace(id="appr-1", tool_name="create_scene", expires_at=None)
        payload = json.loads(_tool_pending(approval)["content"][0]["text"])
        assert payload["review_url"] == "/phoenix-mcp/approvals/appr-1"

    def test_it_points_at_the_plural_wait(self):
        """The message names `wait_for_approval` over `approval_ids`, not a singular id."""
        message = _message()
        assert "approval_ids" in message
        assert "wait_for_approval ONCE" in message

    def test_it_tells_the_agent_to_continue_rather_than_stop(self):
        """The message tells the agent to keep working.

        "You may finish now" reads as an instruction to stop, an early return
        read as success for a whole batch.
        """
        message = _message()
        assert "Continue with your remaining steps" in message
        assert "queue alongside" in message
        assert "do NOT stop or wait after each one" in message

    def test_it_still_forbids_a_retry(self):
        assert "Do not retry" in _message()

    def test_a_plain_pending_does_not_claim_a_wait_happened(self):
        message = _message()
        assert "already held" not in message

    def test_the_timeout_form_states_what_it_already_spent(self):
        message = _message(waited_seconds=60)
        assert "already held for 60 seconds" in message
        # ...and gives the SAME advice, since the agent cannot see the setting.
        assert "approval_ids" in message
        assert "Continue with your remaining steps" in message

    def test_zero_is_not_a_wait(self):
        """A falsy waited_seconds must not render "already held for 0 seconds"."""
        assert "already held" not in _message(waited_seconds=0)


class TestSurfacesAgree:
    """A primer, a skill guide and a tool result describing one event must agree.

    They are read at different moments by the same agent; whichever it weights
    decides whether a twenty-write run takes one approval round or twenty.
    """

    @pytest.fixture
    def primer(self, hass):
        from unittest.mock import MagicMock

        from custom_components.phoenix_mcp.mcp_view import _build_instructions
        from custom_components.phoenix_mcp.access_key_store import PermissionTree, AccessKeyRecord
        from homeassistant.util.dt import utcnow

        access_key = AccessKeyRecord(
            id="t", name="t", access_key_hash="x", created_at=utcnow(),
            created_by="u", permissions=PermissionTree(),
        )
        data = MagicMock()
        data.store.get_settings.return_value = SimpleNamespace(mesa_mode="off")
        return _build_instructions(access_key, data, hass)

    def test_the_primer_names_the_plural_wait(self, primer):
        assert "wait_for_approval ONCE with approval_ids" in primer

    def test_the_primer_says_keep_going(self, primer):
        assert "do not stop after one" in primer
        assert "queue alongside" in primer

    def test_the_skill_guide_names_the_plural_wait(self):
        assert "`approval_ids` (a list)" in PHOENIX_SKILL_MARKDOWN
        assert "call `wait_for_approval` ONCE" in PHOENIX_SKILL_MARKDOWN

    def test_the_skill_guide_says_keep_going(self):
        assert "Do not stop after one, and do not wait after each one" in PHOENIX_SKILL_MARKDOWN

    def test_every_surface_still_forbids_a_retry(self, primer):
        assert "Do not retry" in _message()
        assert "Do not retry" in primer
        assert "Do not retry" in PHOENIX_SKILL_MARKDOWN
