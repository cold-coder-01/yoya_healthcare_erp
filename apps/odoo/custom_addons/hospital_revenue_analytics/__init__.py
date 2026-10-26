from . import controllers
from . import models


def uninstall_hook(env):
    """Remove what this module created outside its own tables: the views are
    dropped by the ORM except the unregistered class view, and the index on
    hospital_charge_line is not dropped at all."""
    from .models.revenue_fact import CHARGE_CLASS_VIEW, OPERATIONAL_INDEX

    # The class view backs the two registered fact views; CASCADE takes them
    # with it, and the ORM then finds nothing left to drop.
    env.cr.execute("DROP VIEW IF EXISTS %s CASCADE" % CHARGE_CLASS_VIEW)
    env.cr.execute("DROP INDEX IF EXISTS %s" % OPERATIONAL_INDEX)
