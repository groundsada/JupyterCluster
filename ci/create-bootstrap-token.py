"""Create a bootstrap API token for the E2E test suite.

Run inside the JupyterCluster pod via kubectl exec:

    kubectl exec -n jupytercluster deploy/jupytercluster -- \
        python3 /app/ci/create-bootstrap-token.py admin e2e-bootstrap

Prints the raw token to stdout — capture it as the JUPYTERCLUSTER_TOKEN
environment variable for the pytest E2E suite.
"""

import sys

from jupytercluster import orm
from jupytercluster.dbutil import new_session_factory

DB_URL = "sqlite:////data/jupytercluster.db"
USERNAME = sys.argv[1] if len(sys.argv) > 1 else "admin"
TOKEN_NAME = sys.argv[2] if len(sys.argv) > 2 else "e2e-bootstrap"

_, factory = new_session_factory(DB_URL)
db = factory()

user = db.query(orm.User).filter_by(name=USERNAME).first()
if user is None:
    print(f"ERROR: user {USERNAME!r} not found", file=sys.stderr)
    sys.exit(1)

# Revoke any existing bootstrap token with the same name to keep things tidy
existing = db.query(orm.APIToken).filter_by(user_id=user.id, name=TOKEN_NAME).first()
if existing:
    db.delete(existing)
    db.commit()

token_orm, raw = orm.APIToken.new(user_id=user.id, name=TOKEN_NAME)
db.add(token_orm)
db.commit()

# Only the raw value goes to stdout — everything else to stderr
print(f"Created token {TOKEN_NAME!r} for {USERNAME!r} (id={token_orm.id})", file=sys.stderr)
print(raw)   # captured by the workflow
