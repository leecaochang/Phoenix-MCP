"""Measure hosted tool choices against labeled cases without executing any tools."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aiohttp import ClientSession
from custom_components.phoenix_mcp.agentcli import ProviderConfig
from custom_components.phoenix_mcp.decision_provider import DecisionError, DecisionProvider


class MeasuredProvider(DecisionProvider):
    """Keep synthetic qualification distributions separately from runtime telemetry."""

    attempts = 0
    response: dict | None = None

    async def _request(self, session, method, path, timeout_ms, payload=None):
        if method == 'POST':
            self.attempts += 1
        result = await super()._request(session, method, path, timeout_ms, payload)
        if method == 'POST':
            if self.cfg.api_key and self.cfg.api_key in json.dumps(result):
                raise DecisionError('malformed')
            self.response = {key: result[key] for key in ('model', 'answers', 'usage') if key in result}
        return result


async def measure(args) -> dict:
    catalog_bytes = args.catalog.read_bytes()
    tools = json.loads(catalog_bytes)
    cases_bytes = args.cases.read_bytes()
    baseline = json.loads(cases_bytes)
    if not isinstance(tools, list) or len({tool['name'] for tool in tools}) != len(tools):
        raise ValueError('A complete announced catalog is required')
    if any(case['proposed'] not in {tool['name'] for tool in tools} for case in baseline['cases']):
        raise ValueError('The supplied catalog does not cover the baseline')
    provider = MeasuredProvider(ProviderConfig(args.kind, args.model, args.base_url,
                                              os.environ.get(args.api_key_env)), args.protocol)
    rows = []
    async with ClientSession() as session:
        for case in baseline['cases']:
            pending = {'id': 'review', 'name': case['proposed'], 'input': case.get('arguments', {})}
            state = {'constraints': baseline['constraints'], 'language': baseline['language'],
                     'history': [{'role': 'user', 'content': case['request']},
                                 {'role': 'assistant', 'content': [{'type': 'tool_use', **pending}]}],
                     'pending': [pending], 'catalog': {f't{i}': tool for i, tool in enumerate(tools)}}
            start = monotonic()
            provider.response = None
            before = provider.attempts
            try:
                async with asyncio.timeout(args.timeout_ms / 1000):
                    choice = await provider.select_tool(session, state, args.timeout_ms)
                row = {'selected': choice['tool'], 'matched': choice['tool'] in case['expected'], 'reason': 'answered'}
            except (DecisionError, TimeoutError) as error:
                row = {'selected': None, 'matched': False, 'reason': str(error) or 'timeout'}
            rows.append({'id': case['id'], 'kind': case['kind'], **row,
                         'elapsed_ms': round((monotonic() - start) * 1000, 2),
                         'http_attempts': provider.attempts - before, 'response': provider.response})
            if row['reason'] in ('provider_context_limit', 'authentication', 'catalog_unavailable'):
                break
    return {'baseline_version': baseline['version'], 'catalog_count': len(tools),
            'catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
            'baseline_sha256': hashlib.sha256(cases_bytes).hexdigest(),
            'provider': args.kind, 'endpoint': args.base_url, 'configured_model': args.model,
            'language': baseline['language'], 'cases': rows, 'http_attempts': provider.attempts,
            'decision_gate_passed': len(rows) == len(baseline['cases']) and all(row['matched'] for row in rows),
            'advice_qualified': False,
            'remaining_gate': 'Independent paired Off/Advise task success, safety/refusal, latency and usage evaluation is required.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parent.parent / 'tests/fixtures/tool_routing_acceptance/v1.json')
    parser.add_argument('--kind', default='typesafe')
    parser.add_argument('--base-url', default='https://api.typesafe.ai/v1')
    parser.add_argument('--protocol', choices=['systemone', 'classifier'], default='systemone')
    parser.add_argument('--model', required=True)
    parser.add_argument('--api-key-env', default='PHOENIX_QUALIFICATION_API_KEY')
    parser.add_argument('--timeout-ms', type=int, default=5000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a fresh output file to preserve qualification evidence')
    result = asyncio.run(measure(args))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('catalog_count', 'http_attempts', 'decision_gate_passed', 'advice_qualified')}))
    return 0 if result['decision_gate_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
