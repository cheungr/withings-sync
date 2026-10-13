# Withings → Garmin Connect

Sync Withings scale weight and body composition (fat %, hydration %, muscle, bone, and BMI) to Garmin Connect. Runs as one non-root Docker Compose service; credentials and sync state live in `./data`.

## Ubuntu quickstart

1. Install Docker Engine and the Docker Compose plugin using Docker's [Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/).
2. Create a Withings developer application at [developer.withings.com](https://developer.withings.com/). Set its callback/redirect URL to the exact URL you will enter as `WITHINGS_REDIRECT_URI`, and enable the `user.metrics` scope. You need your app's client ID and client secret; do not use credentials from another app.
3. Prepare configuration and the writable data directory:

   ```sh
   cp .env.example .env
   sed -i "s/^PUID=.*/PUID=$(id -u)/; s/^PGID=.*/PGID=$(id -g)/" .env
   chmod 600 .env
   mkdir -p data
   sudo chown "$(id -u):$(id -g)" data
   ```

   Set `WITHINGS_CLIENT_ID`, `WITHINGS_CLIENT_SECRET`, `WITHINGS_REDIRECT_URI` in `.env`. The default scheduler interval is `1h`; `SYNC_INTERVAL` also accepts seconds, minutes, and days (for example `30m`). If you change `PUID`/`PGID`, make `data/` writable by that UID/GID and rebuild.
4. Build the image and authorize Withings:

   ```sh
   docker compose build
   docker compose run --rm withings-sync auth withings
   ```

   Open the printed URL, approve access, then paste the complete redirect URL (or just its `code`) into the prompt. The code is exchanged immediately. Withings codes are short-lived, so paste the redirect as soon as authorization completes.
5. Authenticate Garmin. The command prompts for your email/password and, when required, an MFA code. Passwords are used for this login only and are not saved:

   ```sh
   docker compose run --rm withings-sync auth garmin
   ```

   If you already have an authenticator code ready, it can be supplied with `--mfa-code CODE`; otherwise, enter it at the prompt. The saved Garmin token is reused and refreshed without asking for your password.
6. Start the hourly sync service and follow its logs:

   ```sh
   docker compose up -d
   docker compose logs -f
   ```

## Commands

```sh
docker compose run --rm withings-sync status
docker compose run --rm withings-sync sync --once
docker compose run --rm withings-sync sync --since 2026-01-01
```

`sync --since` performs a one-time backfill starting on the given UTC date. Sync state records successfully uploaded Withings measurement IDs, so reruns skip them. The service automatically retries at the next interval after a scheduled failure.

If Garmin credentials truly expire or are revoked, logs report the re-auth command:

```sh
docker compose run --rm withings-sync auth garmin
```

For Withings revocation or a missing refresh token, repeat `auth withings`. Keep `./data` private and backed up; it contains long-lived account tokens. `./data` is the only persistent volume.
