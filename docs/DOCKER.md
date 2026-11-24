> Combined MVP: read ../README.md and COMBINED_ARCHITECTURE.md for current defaults, release gates and cost scope. The optional/inherited details below do not override them.

# Start both services with Docker Compose

The management recommendation proposes Docker for consistent local startup. The
package contains Python/FastAPI and Next.js Dockerfiles plus `compose.yaml`.
Requires an available Docker installation with Compose v2.

From the extracted project directory:

```bash
python3 scripts/bootstrap.py
docker compose build backend
docker compose run --rm backend python scripts/bootstrap_admin.py --email sam@example.org
docker compose up --build -d
```

Run the first bootstrap command with project dependencies installed. It creates
`.env` with an encryption key and preserves an existing `.env`. Replace the
example address with Sam's. The second bootstrap command uses the persistent
Docker volume and shows Sam's one-time temporary password.

Open http://127.0.0.1:3000 and sign in with email, password, and an authenticator code.
The API is available at http://127.0.0.1:8000. The interface receives no server-side
API secrets and reaches the backend through the fixed proxy. Dockerfiles use
unprivileged users. Compose binds both ports only to loopback.

The first image build needs internet. Offline mode is the default. A later live
run needs credentials in `.env`; then recreate the services:

```bash
docker compose up -d --force-recreate
```

For GSC in a container, use a configured access token. To use a service-account
file, add a read-only mount in your own Compose override and set
`GSC_SERVICE_ACCOUNT_FILE` to the container path. `.dockerignore` excludes secrets
from builds.

SQLite checkpoints, audits, and budgets live in the named volume `seo_state`;
HTML output lives in `seo_outputs`. Download bundles through the review app.
Included examples remain separately available in the extracted directory.

```bash
docker compose logs --tail=100 backend
docker compose down
```

`down` stops services and preserves volumes. `down --volumes` deletes run data and
is not part of the normal stop instructions.

Compose structure was checked. Image building and container startup were not
performed because Docker was unavailable in the verification environment. The
local Python pipeline and Next.js build were checked separately.

Original documentation references: [Compose services](https://docs.docker.com/reference/compose-file/services/)
and [healthcheck startup order](https://docs.docker.com/compose/how-tos/startup-order/).
