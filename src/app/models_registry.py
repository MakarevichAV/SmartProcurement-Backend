"""Imports every ORM model module so ``Base.metadata`` is fully populated.

Alembic's env.py imports this; application code that needs metadata (tests, ``create_all``)
can import it too. Add new model modules here as phases land.
"""

from __future__ import annotations

from app.audit import models as _audit  # noqa: F401
from app.core import models_secret as _secret  # noqa: F401
from app.domain import models as _domain  # noqa: F401
from app.enterprise import models as _enterprise  # noqa: F401
from app.identity import models as _identity  # noqa: F401
from app.integration import models as _integration  # noqa: F401
from app.jobs import models as _jobs  # noqa: F401
from app.lorm import models as _lorm  # noqa: F401
from app.observation import models as _observation  # noqa: F401
