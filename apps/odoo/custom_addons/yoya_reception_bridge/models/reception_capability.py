"""An internal, non-forgeable capability signal for the reception workflow.

hospital.patient, hospital.appointment and hospital.patient.card.issue all
guard create() so a receptionist cannot create these records through the
generic Odoo form or a raw RPC call to <model>.create() -- only
hospital.reception.workflow may do so, because only it opens the patient,
appointment, encounter and card together, atomically, with clearance
persisted and the reception_workflow_managed marker set correctly.

Those guards need a way to tell "this create is mine" apart from "this create
came from anywhere else." A context flag CANNOT be that signal:
self.env.context is fully attacker-controlled. Any RPC caller -- including a
raw XML-RPC/JSON-RPC call to execute_kw, which bypasses our own HTTP
controllers entirely -- can pass an arbitrary context dict alongside the
call, so a check like ``self.env.context.get('from_reception_workflow')``
would be defeated by simply sending that same key.

This uses a contextvars.ContextVar instead: plain server-side process state
that is never part of any RPC payload, never read from self.env.context, and
can only be set by executing the exact Python statement in
``reception_workflow_capability()`` below -- which only
hospital.reception.workflow's own method bodies do. It cannot be set, read or
forged from outside the Python process.

The window is kept as small as possible: a single ``with`` block around one
create() call, never around an entire request. The token is always released
in a ``finally``, so a raised exception during creation cannot leave the
capability active for a later, unrelated call on the same thread.
"""
import contextvars
from contextlib import contextmanager

_capability = contextvars.ContextVar(
    "yoya_reception_workflow_capability", default=False
)


@contextmanager
def reception_workflow_capability():
    """Grant the capability for the duration of the wrapped block only."""
    token = _capability.set(True)
    try:
        yield
    finally:
        _capability.reset(token)


def has_reception_workflow_capability():
    return _capability.get()
