"""Task 31G — reconcile laboratory requests that are already clinically finished.

Evidence-based and narrow: each candidate is completed ONLY if it passes the
exact same ``_completion_blockers()`` rule the live workflow uses. Nothing is
inferred from charge state, and no broad "update all in_progress" is performed.

The transition is applied through the ORM (not SQL), so the model's own
transition guard re-validates the rule — a request that does not qualify simply
cannot be completed here either.

Idempotent: already-completed requests are skipped; requests that do not qualify
(e.g. legacy result lines with no request_line link) are reported and left
untouched rather than guessed at.
"""

import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Request = env["hospital.laboratory.request"].with_context(active_test=False)

    # Only requests that are open and could plausibly be finished.
    candidates = Request.search([("state", "=", "in_progress")])
    completed, skipped = [], []
    for request in candidates:
        blockers = request._completion_blockers()
        if blockers:
            skipped.append((request.name, blockers))
            continue
        request._evaluate_completion()
        if request.state == "completed":
            completed.append(request.name)
        else:  # pragma: no cover - defensive
            skipped.append((request.name, ["completion did not take effect"]))

    _logger.info(
        "31G: examined %s in-progress laboratory request(s); completed %s.",
        len(candidates), completed or "none",
    )
    for name, blockers in skipped:
        _logger.info("31G: left %s unchanged — %s", name, "; ".join(blockers))
