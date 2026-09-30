# Routing acceptance baseline

`v1.json` groups the executable acceptance cases by native behavior, conversational context and decision-provider boundaries. The cases use real Home Assistant recognition and registries where applicable, with explicit positive and refusal controls. The native action sentences additionally reference the pinned OHF corpus in [../ohf_native_actions/README.md](../ohf_native_actions/README.md).

Run the test files listed under `profiles` in `v1.json` with `python -m pytest -q <test-files>`. For example, the native profile is `python -m pytest -q tests/test_native_action_routing.py tests/test_numeric_recognition.py tests/test_command_summary.py tests/test_command_display.py`. These tests use the isolated pytest environment and do not contact a live model or server.

This baseline complements the full suite. Mocked decision responses verify adapter safety and do not establish model accuracy. A future model qualification must separately satisfy and record every requirement listed in `model_qualification`, with complete catalogs and unchanged confidence/evidence rules. Unsupported categories remain refusals. Test runtime is not provider latency, and an overall passing percentage cannot substitute for positive controls in each claimed action family.
