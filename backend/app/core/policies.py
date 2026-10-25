"""Deterministic, versioned policy evaluation and scoped exception matching."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.models import Policy, PolicyEvaluation, PolicyException, User
from app.schemas.core import PolicyRule

EVALUATOR_VERSION = "deployforge-json-policy-v1"
POLICY_ADMIN_ROLES = {"workspace_admin", "platform_engineer"}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _read_field(context: dict[str, Any], dotted_field: str) -> tuple[bool, Any]:
    current: Any = context
    for segment in dotted_field.split("."):
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return current is not None, current


def _same_json_value(first: Any, second: Any) -> bool:
    if isinstance(first, bool) != isinstance(second, bool):
        return False
    if isinstance(first, (int, float)) and not isinstance(first, bool):
        return isinstance(second, (int, float)) and not isinstance(second, bool) and first == second
    return type(first) is type(second) and first == second


def _condition_matches(rule: PolicyRule, context: dict[str, Any]) -> bool:
    exists, actual = _read_field(context, rule.field)
    operator = rule.operator
    expected = rule.expected
    if operator == "exists":
        return exists
    if operator == "not_exists":
        return not exists
    if not exists:
        return False
    if operator == "equals":
        return _same_json_value(actual, expected)
    if operator == "not_equals":
        return not _same_json_value(actual, expected)
    if operator == "in":
        return any(_same_json_value(actual, candidate) for candidate in expected)
    if operator == "not_in":
        return not any(_same_json_value(actual, candidate) for candidate in expected)
    if operator in {
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
    }:
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or isinstance(expected, bool)
            or not isinstance(expected, (int, float))
        ):
            return False
        if operator == "greater_than":
            return actual > expected
        if operator == "greater_than_or_equal":
            return actual >= expected
        if operator == "less_than":
            return actual < expected
        return actual <= expected
    if operator in {"contains", "not_contains"}:
        if isinstance(actual, (list, str, dict)):
            try:
                contains = expected in actual
            except TypeError:
                contains = False
        else:
            contains = False
        return contains if operator == "contains" else not contains
    return False


def _exception_applies(
    exception: PolicyException,
    *,
    policy_id: UUID,
    policy_version: int,
    rule_id: str,
    service_id: UUID | None,
    environment_id: UUID | None,
    now: datetime,
) -> bool:
    if (
        exception.policy_id != policy_id
        or exception.policy_version != policy_version
        or exception.rule_id != rule_id
        or exception.status != "approved"
        or _utc(exception.expires_at) <= now
    ):
        return False
    if exception.service_id is not None and exception.service_id != service_id:
        return False
    if exception.environment_id is not None and exception.environment_id != environment_id:
        return False
    return exception.service_id is not None or exception.environment_id is not None


def evaluate_policy_set(
    db: Session,
    *,
    workspace_id: UUID,
    action: str,
    context: dict[str, Any],
    subject_type: str,
    subject_id: str | UUID,
    service_id: UUID | None = None,
    environment_id: UUID | None = None,
    actor: User | None = None,
    defer_artifact_rules: bool = False,
) -> PolicyEvaluation:
    """Evaluate active workspace policies and persist a replayable decision."""
    policies = list(
        db.scalars(
            select(Policy)
            .where(Policy.workspace_id == workspace_id, Policy.enabled.is_(True))
            .order_by(Policy.slug, Policy.id)
        ).all()
    )
    applicable = [policy for policy in policies if action in policy.actions]
    snapshot = [
        {
            "policy_id": str(policy.id),
            "slug": policy.slug,
            "version": policy.version,
            "enforcement_mode": policy.enforcement_mode,
            "actions": policy.actions,
            "rules": policy.rules,
        }
        for policy in applicable
    ]
    canonical_snapshot = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    snapshot_digest = hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    exceptions = list(
        db.scalars(
            select(PolicyException)
            .where(
                PolicyException.workspace_id == workspace_id,
                PolicyException.status == "approved",
                PolicyException.expires_at > now,
            )
            .order_by(PolicyException.created_at, PolicyException.id)
        ).all()
    )

    results: list[dict[str, Any]] = []
    denied = False
    requires_approval = False
    active_enforcement_policy = False
    for policy in applicable:
        enforcing = policy.enforcement_mode != "monitor"
        for document in policy.rules:
            try:
                rule = PolicyRule.model_validate(document)
            except Exception:
                effective = "deny" if enforcing else "warn"
                results.append(
                    {
                        "policy_id": str(policy.id),
                        "policy_slug": policy.slug,
                        "policy_version": policy.version,
                        "rule_id": str(document.get("id", "invalid"))
                        if isinstance(document, dict)
                        else "invalid",
                        "outcome": "invalid_policy",
                        "effect": effective,
                        "message": (
                            "Stored rule failed schema validation; enforcement is fail-closed."
                        ),
                    }
                )
                denied |= effective == "deny"
                continue
            if enforcing and rule.effect in {"deny", "require_approval"}:
                active_enforcement_policy = True
            if defer_artifact_rules and rule.field.startswith("artifact."):
                results.append(
                    {
                        "policy_id": str(policy.id),
                        "policy_slug": policy.slug,
                        "policy_version": policy.version,
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "outcome": "deferred",
                        "effect": rule.effect,
                        "message": "Artifact rule is evaluated after CI reports build evidence.",
                    }
                )
                continue
            if not _condition_matches(rule, context):
                results.append(
                    {
                        "policy_id": str(policy.id),
                        "policy_slug": policy.slug,
                        "policy_version": policy.version,
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "outcome": "not_matched",
                        "effect": rule.effect,
                    }
                )
                continue
            matched_exception = next(
                (
                    item
                    for item in exceptions
                    if _exception_applies(
                        item,
                        policy_id=policy.id,
                        policy_version=policy.version,
                        rule_id=rule.id,
                        service_id=service_id,
                        environment_id=environment_id,
                        now=now,
                    )
                ),
                None,
            )
            if matched_exception is not None:
                results.append(
                    {
                        "policy_id": str(policy.id),
                        "policy_slug": policy.slug,
                        "policy_version": policy.version,
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "outcome": "excepted",
                        "effect": rule.effect,
                        "exception_id": str(matched_exception.id),
                        "exception_expires_at": _utc(matched_exception.expires_at).isoformat(),
                    }
                )
                continue

            effective = rule.effect if enforcing else "warn"
            results.append(
                {
                    "policy_id": str(policy.id),
                    "policy_slug": policy.slug,
                    "policy_version": policy.version,
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "outcome": "matched",
                    "configured_effect": rule.effect,
                    "effect": effective,
                    "message": rule.description or rule.name,
                }
            )
            denied |= effective == "deny"
            requires_approval |= effective == "require_approval"

    environment = context.get("environment") or {}
    production_release = action == "deployment" and (
        environment.get("protected") is True
        or str(environment.get("type", "")).casefold() in {"production", "prod"}
    )
    if production_release and not active_enforcement_policy:
        denied = True
        results.append(
            {
                "policy_id": None,
                "policy_slug": "deployforge-production-baseline",
                "policy_version": 1,
                "rule_id": "production-requires-enforced-policy",
                "rule_name": "Production requires an enforced release policy",
                "outcome": "matched",
                "configured_effect": "deny",
                "effect": "deny",
                "message": (
                    "An enabled enforce-mode deployment policy with a blocking or approval rule "
                    "is required."
                ),
                "built_in": True,
            }
        )

    evaluation = PolicyEvaluation(
        workspace_id=workspace_id,
        action=action,
        subject_type=subject_type,
        subject_id=str(subject_id),
        decision="deny" if denied else "allow",
        requires_approval=requires_approval,
        context=context,
        policy_snapshot=snapshot,
        policy_snapshot_sha256=snapshot_digest,
        results=results,
    )
    db.add(evaluation)
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="policy.evaluated",
        resource_type=subject_type,
        resource_id=str(subject_id),
        workspace_id=workspace_id,
        outcome=evaluation.decision,
        details={
            "evaluation_id": str(evaluation.id),
            "action": action,
            "decision": evaluation.decision,
            "requires_approval": requires_approval,
            "policy_snapshot_sha256": snapshot_digest,
            "matched_rule_count": sum(
                result["outcome"] in {"matched", "excepted"} for result in results
            ),
            "evaluator_version": EVALUATOR_VERSION,
        },
    )
    return evaluation
