"""Bounded decision transport measurements without prompts, targets or account IDs."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
from typing import Any

from .const import DECISION_METRIC_SCOPES, DECISION_METRIC_OPERATIONS, DECISION_METRIC_SAMPLES, DECISION_PROVIDERS


@dataclass
class RequestMetrics:
    """Totals cover this runtime; percentiles cover only the retained timing window."""

    requests: int = 0
    responses: int = 0
    request_bytes: int = 0
    elapsed_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_reports: int = 0
    timings: deque[float] = field(default_factory=lambda: deque(maxlen=DECISION_METRIC_SAMPLES), repr=False)

    def record(self, size: int, elapsed: float, body: Any) -> None:
        self.requests += 1
        self.request_bytes += size
        self.elapsed_ms += elapsed
        self.timings.append(elapsed)
        if isinstance(body, dict):
            self.responses += 1
            usage = body.get("usage")
            if isinstance(usage, dict) and all(
                type(usage.get(key)) is int and 0 <= usage[key] <= 2**53 - 1
                for key in ("input_tokens", "output_tokens")
            ):
                self.usage_reports += 1
                self.input_tokens += usage["input_tokens"]
                self.output_tokens += usage["output_tokens"]

    def summary(self) -> dict:
        ordered = sorted(self.timings)
        def percentile(fraction: float) -> float | None:
            return round(ordered[math.ceil(len(ordered) * fraction) - 1], 2) if ordered else None
        return {
            "requests": self.requests, "responses": self.responses,
            "request_bytes": self.request_bytes,
            "mean_elapsed_ms": round(self.elapsed_ms / self.requests, 2) if self.requests else None,
            "timing_samples": len(ordered), "p50_elapsed_ms": percentile(.50), "p95_elapsed_ms": percentile(.95),
            "usage_reports": self.usage_reports, "usage_unknown": self.requests - self.usage_reports,
            "reported_input_tokens": self.input_tokens if self.usage_reports else None,
            "reported_output_tokens": self.output_tokens if self.usage_reports else None,
        }


@dataclass
class DecisionMetrics:
    """Fixed internal dimensions prevent model text from becoming telemetry keys."""

    groups: dict[str, dict[str, dict[str, RequestMetrics]]] = field(default_factory=dict)

    def record(self, scope: str, operation: str, size: int, elapsed: float, body: Any, *, provider_kind: str) -> None:
        if (provider_kind not in DECISION_PROVIDERS or scope not in DECISION_METRIC_SCOPES
                or operation not in DECISION_METRIC_OPERATIONS):
            return
        group = self.groups.setdefault(provider_kind, {}).setdefault(scope, {}).setdefault(operation, RequestMetrics())
        group.record(size, elapsed, body)

    def summary(self) -> dict:
        return {kind: {scope: {operation: group.summary() for operation, group in groups.items()}
                       for scope, groups in scopes.items()} for kind, scopes in self.groups.items()}
