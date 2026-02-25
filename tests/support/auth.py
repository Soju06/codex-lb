from __future__ import annotations

import os
from uuid import uuid4

BOOTSTRAP_ADMIN_PASSWORD_ENV = "CODEX_LB_BOOTSTRAP_ADMIN_PASSWORD"

if BOOTSTRAP_ADMIN_PASSWORD_ENV not in os.environ:
    os.environ[BOOTSTRAP_ADMIN_PASSWORD_ENV] = f"test-admin-{uuid4().hex}"

BOOTSTRAP_ADMIN_PASSWORD = os.environ[BOOTSTRAP_ADMIN_PASSWORD_ENV]
