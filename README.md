# Mini-Mailroom

A Gmail client growing into a cozy pixel-art mailroom. The first implemented
step is Google sign-in; inbox access is next.

For a walkthrough of the backend and frontend authentication code, see the
[Google sign-in code guide](docs/OAUTH_CODE_GUIDE.md).

## Run locally

Requirements: Node.js, Python 3.11+ (3.12 is the planned target), and
[uv](https://docs.astral.sh/uv/getting-started/installation/).

1. Create a **Web application** OAuth client in Google Cloud. Register this exact
   authorized redirect URI: `http://localhost:8000/auth/google/callback`.
   Configure the consent screen and add your Google account as a test user if
   the app is in Testing.
2. Copy `server/.env.example` to `server/.env` if you do not already have one.
   Fill in `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI`.
   Existing `server/.env` values are loaded automatically; keep this file private.
3. In one terminal:

   ```powershell
   cd server
   uv sync
   uv run uvicorn main:app --host localhost --port 8000 --no-access-log
   ```

4. In another terminal:

   ```powershell
   cd client
   npm.cmd install
   npm.cmd run dev
   ```

5. Open **http://localhost:5173** and click **Sign in with Google**. After Google
   approval, the page shows your name and email. Sign out ends the local session.

Use `localhost` for both servers and in your browser, rather than mixing it with
`127.0.0.1`: the OAuth state cookie must return on the same hostname. Vite proxies
`/auth` to the backend. Port 5173 is fixed so the return URL stays consistent.
If you change the frontend URL, also set `FRONTEND_URL` in `server/.env`.

## Deploy to AWS without local Docker

For a low-memory laptop, build the Docker image remotely with AWS CodeBuild,
store it in Amazon ECR, and run it with ECS Express Mode on Fargate. Your laptop
only needs Git and a browser for deployment; Docker Desktop is optional.

Follow the [cloud-build deployment guide](private/docs/AWS_DEPLOYMENT.md). The repository
includes `buildspec.yml` to build and smoke-test the image before pushing it to
ECR. The guide covers GitHub access, IAM permissions, runtime secrets, HTTPS,
Google OAuth URLs, and updating the running service.

## Run with Docker locally (optional)

Start Docker Desktop using Linux containers. From the project root, build and run:

```powershell
docker build -t mini-mailroom:dev .
docker run --rm --name mini-mailroom -p 8000:8000 --env-file server/.env -e FRONTEND_URL=http://localhost:8000 -e GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback -e COOKIE_SECURE=false mini-mailroom:dev
```

Open **http://localhost:8000**. The image builds React and serves it through
FastAPI; no separate Vite server or host Python environment is needed. Register
`http://localhost:8000/auth/google/callback` in your Google OAuth client.
Ensure port 8000 is free before starting the container.

The multi-stage Dockerfile installs locked backend dependencies into a Linux
virtual environment and runs the final container as a non-root user. Credentials
are passed at runtime; `.env` and local environments are excluded from the build.
Rebuild the image after changing source files. Sessions and pending sign-in
attempts remain in memory, so restarting the container clears them.

For an HTTPS deployment, set `FRONTEND_URL` and `GOOGLE_REDIRECT_URI` to the public
HTTPS URLs, set `COOKIE_SECURE=true`, and inject the Google secret through your
hosting service's secret manager. Configure the hosting service's health check
to use `/health` on port 8000.

## OAuth scope and current limits

This increment requests only `openid`, email, and profile. It uses Google's
[web-server OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server)
with PKCE, one-time browser-bound state, nonce validation, and verified ID tokens.
The Google secret and tokens remain server-side; tokens are discarded after
sign-in. The browser holds only an opaque HttpOnly, SameSite session cookie.

Sessions last eight hours and live in memory: **restarting the backend signs users
out**. Run one backend worker for now. Persistent encrypted Gmail credentials,
refresh, disconnect/revocation, and mail reading remain subsequent Phase 1 work.
Sign out does not revoke the Google consent grant. This is a local prototype;
deployment will require HTTPS, `COOKIE_SECURE=true`, shared persistent sessions,
and the appropriate production proxy configuration.

Run without access logs so callback query strings containing authorization codes
are not written to logs. No OAuth credentials belong in frontend environment files.

## Checks

```powershell
cd server
uv run pytest
uv run ruff check main.py tests
```

```powershell
cd client
npm.cmd run build
npm.cmd run lint
```
