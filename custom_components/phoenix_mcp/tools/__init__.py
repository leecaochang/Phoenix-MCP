"""Per-domain MCP tool modules.

Each module here owns one domain's tools end to end: the _tool_X gate, the
_execute_X side effect, its diff builders and its prechecks. They depend on
tool_common for the shared primitives and are registered by tool_registration.
The dependency runs one way: a domain module never imports tool_dispatch,
tool_registration or the MCP transport.
"""
