# Azizam on the server

Pushes to `main` run **Deploy Azizam** in GitHub Actions. It checks the Mini App,
then connects to `arian@178.104.74.99` with a dedicated deployment key. That key
can request an Azizam deployment or its status; it cannot open a shell.
Pull requests only run validation and do not receive deployment credentials.
The existing GitHub Pages publishing continues separately.

## What is ready

- Server directory: `/home/arian/azizam`.
- Private settings: `/home/arian/azizam/shared/bot.env` (mode 600).
- Persistent bot files: Docker volume `azizam_bot-data`, mounted at `/data`.
- API address on the server: `http://127.0.0.1:8093` (container port 8080).
- Runtime: 512 MiB RAM, one CPU, bounded logs, automatic restart after reboot.
- Observer's database, settings, containers and model volume are preserved.

There is no bot code in the repository yet. The workflow reports this as
**waiting for bot code**, without interrupting Observer. If code is present but
the first handover is not ready, it builds the image and reports **staged**.
This does not mean the Telegram bot is running on the server.

## What the bot developer needs to add

1. Push the actual bot and API source into this repository.
2. Add a **root `Dockerfile`** that installs dependencies and starts the bot/API
   in the foreground. The build context is the repository root. Never put a
   bot token or `.env` file in an image or in Git.
3. Have the API listen on `0.0.0.0:8080` (`HOST` and `PORT` are supplied).
   Store all persistent databases and uploads under `/data`; configure their
   paths through the private environment file.
4. Add a Docker `HEALTHCHECK` that succeeds only after startup is ready.
   For example, a Python API could check an unauthenticated `/health` endpoint
   with `urllib.request`. The server requires a real healthcheck before launch.
5. Document the required environment variable **names** in a token-free
   `.env.example`. Copy the existing laptop values directly to the private
   server file over SSH, not to GitHub or chat. Keep the existing Azizam bot
   token; Observer's token belongs to a different bot.
6. Migrate the existing laptop database and any necessary files privately into
   the persistent volume before the first launch. Do not replace live data with
   the public `js/snapshot.json` export.

The deployment uses one container. If the bot needs separate services,
model access, another startup port or additional storage, adapt the deployment
after the source has been reviewed. Repository changes to `deploy/compose.yml`
do not automatically change the installed server runtime.

## First handover

The first launch is deliberately disabled until all of these are complete:

- Bot source, Dockerfile and healthcheck have been checked.
- `/home/arian/azizam/shared/bot.env` contains the required private settings.
- Existing bot data has been migrated.
- A permanent HTTPS API address has been configured, Telegram Mini App request
  authentication has been checked, and `js/config.js` points to that address.
- The laptop bot and its tunnel/config/snapshot watchdog have been stopped.
  Two processes must not poll Telegram with the same bot token.

Then the server administrator enables deployment and reruns **Deploy Azizam**:

```sh
touch /home/arian/azizam/shared/activation-ready
```

The server launches Azizam, waits for its Docker healthcheck, then stops only
`bbh-observer-observer-1` and `bbh-observer-chat-brain-1` and disables their
automatic restart. It takes a fresh consistent Observer database backup first.
Their data and images remain available for rollback. Other server apps are not
part of this deployment.

## Later updates and recovery

Every later push to `main` deploys automatically. GitHub queues deployments and
the server also locks deployment work. An outdated commit cannot replace a newer
`main` commit. The previous image remains available; a failed startup restores
that image. Persistent bot data is never deleted or automatically restored, so
database migrations must remain compatible with the previous image.

Private build/startup logs and data backups are under
`/home/arian/azizam/backups/`. Inspect the current service with:

```sh
docker compose -p azizam -f /home/arian/azizam/compose.yml \
  --env-file /home/arian/azizam/current.env ps
```

To pause future releases without stopping the running bot:

```sh
touch /home/arian/azizam/shared/pause-deployments
```

Remove that file to resume. Manual workflow runs use the same readiness gates.
The installed deployment program is maintained separately from application
commits at `/home/arian/azizam/bin/deploy.py`.
