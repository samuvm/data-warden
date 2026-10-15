"""Correction loop: generate, validate, return an actionable message, retry.

A small hand-written loop rather than a framework: `G-RECOVERY` measures whether the
model corrects itself from the rejection message, and a framework's own retry
policy would confound that measurement.

The loop is code and is tested with a recorded provider (retry count, what is fed
to the next attempt, when to stop). What the model writes is measured by evals, not
unit-tested.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from datawarden.domain.types import (
    Position,
    RejectionReason,
    Severity,
    ValidatedQuery,
)
from datawarden.nl2sql.providers import Provider, Request

#: Two retries, three attempts in total. Each retry costs a model call and a full
#: guard pass; if the message has not helped after two, the message needs fixing.
MAX_RETRIES: Final = 2

Validate = Callable[[str], ValidatedQuery | RejectionReason]


@dataclass(frozen=True, slots=True)
class Attempt:
    """One attempt: the generated SQL and the guard's verdict."""

    sql: str
    rejection: RejectionReason | None


@dataclass(frozen=True, slots=True)
class LoopResult:
    """Loop result with the full attempt history.

    The full history is kept because the `G-RECOVERY` report groups by what the
    model recovered from, which the last attempt alone cannot tell.
    """

    query: ValidatedQuery | None
    rejection: RejectionReason | None
    attempts: tuple[Attempt, ...]
    provider: str

    @property
    def accepted(self) -> bool:
        return self.query is not None

    @property
    def recovered(self) -> bool:
        """Accepted after at least one rejection; what `G-RECOVERY` counts.

        A first-attempt success is not a recovery and would inflate the metric.
        """
        return self.accepted and len(self.attempts) > 1

    @property
    def first_rejection(self) -> RejectionReason | None:
        """The first rejection, which is what the metric groups by.

        Grouping by the last one would count a query that recovers from R008 and then
        hits R006 as an R006 failure, while the message under evaluation was R008's.
        """
        for attempt in self.attempts:
            if attempt.rejection is not None:
                return attempt.rejection
        return None


def run_loop(
    question: str,
    *,
    provider: Provider,
    validate: Validate,
    max_retries: int = MAX_RETRIES,
    prompt_id: str = "nl2sql",
    prompt_version: str = "1",
    seed: Attempt | None = None,
) -> LoopResult:
    """Generate, validate and retry with the message. Never raises, never executes.

    `seed` is an attempt that already happened, with its SQL and rejection.
    `G-RECOVERY` measures whether the model corrects itself, not whether it fails in
    the expected way; seeding the rejection fixes the denominator. The seed enters
    the history as the first attempt, so `recovered` and `first_rejection` keep their
    usual meaning.
    """
    name = getattr(provider, "name", "unknown")
    if not question.strip():
        return LoopResult(
            query=None,
            rejection=_internal(
                "the question is empty, so there is nothing to translate into SQL",
                "ask something about the data. The catalog resource lists what there is",
            ),
            attempts=(),
            provider=name,
        )

    attempts: list[Attempt] = []
    rejection: RejectionReason | None = None
    previous_sql = ""

    if seed is not None:
        attempts.append(seed)
        rejection = seed.rejection
        previous_sql = seed.sql
        if rejection is not None and not rejection.retryable:
            # A seeded non-retryable rejection is not retried either, as in the
            # normal path: unfixable cases would pollute the metric.
            return LoopResult(
                query=None, rejection=rejection, attempts=tuple(attempts), provider=name
            )

    for attempt_number in range(len(attempts) + 1, len(attempts) + max_retries + 2):
        request = Request(
            question=question,
            attempt=attempt_number,
            rejection=rejection,
            previous_sql=previous_sql,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
        )
        try:
            sql = provider.generate(request)
        except Exception as exc:
            # Fail-closed, as in the guard: a model failure becomes a verdict, never
            # an exception propagated to the caller.
            rejection = _internal(
                f"the model did not answer: {type(exc).__name__}",
                "retry later, or run with the recorded provider, which needs no model",
            )
            attempts.append(Attempt(sql="", rejection=rejection))
            break

        if not sql.strip():
            rejection = _internal(
                "the model returned nothing",
                "rephrase the question. An empty answer is not a query",
            )
            attempts.append(Attempt(sql=sql, rejection=rejection))
            break

        verdict = validate(sql)
        if isinstance(verdict, ValidatedQuery):
            attempts.append(Attempt(sql=sql, rejection=None))
            return LoopResult(
                query=verdict, rejection=None, attempts=tuple(attempts), provider=name
            )

        rejection = verdict
        previous_sql = sql
        attempts.append(Attempt(sql=sql, rejection=verdict))
        if not verdict.retryable:
            # Non-retryable rejections are not retried: rewording a `DELETE` does
            # not make it a question, and retrying would pollute `G-RECOVERY`.
            break

    return LoopResult(query=None, rejection=rejection, attempts=tuple(attempts), provider=name)


def _internal(message: str, suggestion: str) -> RejectionReason:
    """A rejection raised by the loop itself rather than by a guard rule."""
    return RejectionReason(
        rule_id="INTERNAL",
        code="generation_failed",
        message=message,
        suggestion=suggestion,
        severity=Severity.INTERNAL,
        position=Position.STATEMENT,
        subject="the model",
        retryable=False,
    )
