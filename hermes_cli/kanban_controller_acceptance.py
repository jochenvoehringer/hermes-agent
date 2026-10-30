"""Explicit plugin controller contracts; model metadata cannot supply evidence."""
from __future__ import annotations
import re
from hermes_constants import set_hermes_home_override, reset_hermes_home_override


def collect_controller_acceptance(contract, task_id, assignee):
    from hermes_cli.kanban_pr_acceptance import _assignee_profile_home
    from hermes_cli.plugins import invoke_hook
    receipt = {"ok": False, "classification": "missing", "contract": contract,
               "head_sha": None, "detail": "One trusted controller receipt is required.",
               "recovery": "Repair the named controller provider and retry completion."}
    token = None
    try:
        home = _assignee_profile_home(assignee)
        if home:
            token = set_hermes_home_override(home)
        results = invoke_hook("kanban_completion_acceptance", contract=contract,
                              task_id=task_id, assignee=assignee)
        matching = [r for r in results if isinstance(r, dict) and r.get("contract") == contract]
        if len(matching) != 1:
            return receipt
        proof = matching[0]
        if proof.get("ok") is True and re.fullmatch(r"[0-9a-f]{40}", str(proof.get("head_sha", ""))):
            receipt.update(ok=True, classification="success", head_sha=proof["head_sha"],
                           detail="Trusted controller verified installation and publication.")
        return receipt
    except Exception:
        # Plugin exceptions may contain operational data; never persist them.
        return receipt
    finally:
        if token is not None:
            reset_hermes_home_override(token)
