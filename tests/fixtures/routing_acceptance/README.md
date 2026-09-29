# Routing acceptance baseline

`v1.json` groups the executable acceptance cases by native behavior, conversational context and decision-provider boundaries. The cases use real Home Assistant recognition and registries where applicable, with explicit positive and refusal controls. The native action sentences additionally reference the pinned OHF corpus in [../ohf_native_actions/README.md](../ohf_native_actions/README.md).

Run `venv/bin/python scripts/run_routing_acceptance.py --output /tmp/phoenix-routing-acceptance`. Add `--profile native`, `conversation` or `decision` to run one group. The runner saves the full pytest log, JUnit results and a compact JSON record containing the manifest hash, Git revision and dirty status, Python/HA versions, duration and pass/failure/skip totals. It uses the existing isolated pytest environment and never contacts a live model or server.

This baseline complements the full suite. Mocked decision responses verify adapter safety and do not establish model accuracy. A future model qualification must separately satisfy and record every requirement listed in `model_qualification`, with complete catalogs and unchanged confidence/evidence rules. Unsupported categories remain refusals. Test runtime is not provider latency, and an overall passing percentage cannot substitute for positive controls in each claimed action family.
