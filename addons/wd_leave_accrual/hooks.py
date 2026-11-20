# -*- coding: utf-8 -*-


def post_init_assign_accrual(env):
    """Rattache le plan d'accrual R1 aux employés éligibles."""
    env["hr.employee"].search(
        [("active", "=", True)]
    )._wd_ensure_cp_accrual()
