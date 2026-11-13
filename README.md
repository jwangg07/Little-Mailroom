# Mini-Mailroom

A Gmail client growing into a cozy pixel-art mailroom. Sign in with Google,
browse and paginate your inbox, read plain-text messages, and send email.

See the [security guide](docs/SECURITY_CHANGES.md) for the changes and rollout steps.

See the [architecture guide](docs/ARCHITECTURE.md) to follow sign-in, inbox
loading, and sending from the screen through the backend.

## Run locally

Requirements: Node.js 24, Python 3.11.1, and
[uv](https://docs.astral.sh/uv/getting-started/installation/).

1. Create a **Web application** OAuth client in Google Cloud. Register this exact
   authorized redirect URI: `http://localhost:8000/auth/google/callback`.
   Configure the consent screen and add your Google account as a test user if
   the app is in Testing.
2. Copy `server/.env.example` to `server/.env` if you do not already have one.
   Fill in `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI`.
   Existing `server/.env` values are loaded automatically; keep this file private.
   For local development without AWS, explicitly set `AUTH_STORE=memory` in it.
3. In one terminal:

   ```powershell
   cd server
   uv sync
   uv run python -m uvicorn main:app --host localhost --port 8000 --no-access-log
   ```

4. In another terminal:

   ```powershell
   cd client
   npm.cmd install
   npm.cmd run dev
   ```

5. Open **http://localhost:5173** and click **Sign in with Google**. After Google
   approval, the page shows your account and inbox. Enable the Gmail API in your
   Google Cloud project. Use **Reconnect Gmail** if an older grant lacks mail
   access or a refresh token. Sign out disconnects Gmail and ends your app sessions on all devices.

Use `localhost` for both servers and in your browser, rather than mixing it with
`127.0.0.1`: the OAuth state cookie must return on the same hostname. Vite proxies
`/auth` and `/api` to the backend. Port 5173 is fixed so the return URL stays consistent.
If you change the frontend URL, also set `FRONTEND_URL` in `server/.env`.

Local Python remains pinned to 3.11.1 for compatibility with your installation.
Docker uses its own patched Python 3.12.15 runtime; it does not change your local
installation. Python 3.11.1 lacks later security fixes and should stay a local
development choice, not the production runtime.

## Deploy to AWS without local Docker

For a low-memory laptop, build the Docker image remotely with AWS CodeBuild,
store it in Amazon ECR, and run it on AWS Lambda with Lambda Web Adapter. Your laptop
only needs Git and a browser for deployment; Docker Desktop is optional.

Follow the [security rollout notes](docs/SECURITY_CHANGES.md#before-deployment) and your private
Lambda deployment checklist.
The existing `Dockerfile` and `buildspec.yml` are the single Lambda build path;
no alternate Dockerfile or buildspec is needed. CodeBuild runs backend tests and
lint, builds React and the production image, smoke-tests frontend assets and API
routes, then pushes a unique `lambda-<commit>-<build>` image to ECR. Updating
Lambda to that image is a separate manual deployment step.

Use a Lambda container-image function in the same region as ECR, with x86_64
architecture, the scoped runtime role in `deploy/lambda-runtime-policy.json`,
and a public Function URL for this same-origin prototype. No Application Load
Balancer, VPC attachment, provisioned concurrency, or API Gateway is needed.
The function requires `DYNAMODB_TABLE`, `GOOGLE_CLIENT_SECRET_ARN`,
`CREDENTIAL_KMS_KEY_ID`, `APP_ENV=production`, and `COOKIE_SECURE=true`. Grant its role
`kms:GenerateDataKey` and `kms:Decrypt` for that key, in addition to access to
the authentication table and Google client secret. Set `GOOGLE_CLIENT_ID`, `FRONTEND_URL`, and
`GOOGLE_REDIRECT_URI` to your matching Google client and Function URL.

Keep secrets out of CodeBuild and Git. Lambda loads the `GOOGLE_CLIENT_SECRET`
key from the Secrets Manager JSON using its execution role. The ARN is the bare
secret ARN without the ECS `:GOOGLE_CLIENT_SECRET::` suffix. Lookups are lazy,
cached for five minutes, and fail safely without logging provider messages.

After deploying, an optional unsigned HTTP check from the project root is:

```powershell
uv run --project server python deploy/smoke_test.py https://YOUR_FUNCTION_ID.lambda-url.us-east-2.on.aws
```

This checks React assets, health, and anonymous authentication; manually test
Google sign-in, sign-out, and session persistence as described in the checklist.

## Run with Docker locally (optional)

Start Docker Desktop using Linux containers. From the project root, build and run:

```powershell
docker build -t mini-mailroom:dev .
docker run --rm --name mini-mailroom -p 8000:8000 --env-file server/.env -e APP_ENV=development -e AUTH_STORE=memory -e FRONTEND_URL=http://localhost:8000 -e GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback -e COOKIE_SECURE=false mini-mailroom:dev
```

Open **http://localhost:8000**. The image builds React and serves it through
FastAPI; no separate Vite server or host Python environment is needed. Register
`http://localhost:8000/auth/google/callback` in your Google OAuth client.
Ensure port 8000 is free before starting the container.

The multi-stage Dockerfile installs locked backend dependencies into a Linux
virtual environment and runs the final container as a non-root user. Credentials
are passed at runtime; `.env` and local environments are excluded from the build.
Both test and runtime stages copy `server/app` and `server/integrations`;
`main:app` remains the entry point and assets remain at `/app/client/dist`.
For builders behind an HTTPS inspection proxy, optionally pass
`--secret id=proxy_ca,src=/path/to/trusted-ca-bundle.pem` to `docker build`.
The bundle is mounted only while installing dependencies.
Rebuild the image after changing source files. In explicit local memory mode,
restarting the container clears sessions and pending sign-in attempts.

In Lambda the adapter forwards requests to port 8000 and checks `/health` for
readiness. Ordinary `docker run` still starts Uvicorn for local smoke testing;
Lambda launches the adapter extension in its execution environment. Local Docker
checks do not prove the Function URL and Google callback are configured correctly.

## Shared authentication storage

Authentication defaults to DynamoDB. Set `AUTH_STORE=dynamodb` and
`DYNAMODB_TABLE=mini-mailroom-auth` in the deployed service. Create the table in
the runtime's AWS region with a String partition key `pk`, and enable TTL on
`expires_at`. Grant the runtime role `dynamodb:PutItem`, `dynamodb:GetItem`, and
`dynamodb:DeleteItem`, and `dynamodb:UpdateItem` on that table. Replace the
KMS placeholder with your actual key ARN and verify the existing table/secret ARNs.
Use role credentials rather than static keys.

OAuth attempts expire after ten minutes and are consumed with one conditional
delete; sessions expire after eight hours and use consistent reads. The app
checks expiration without waiting for DynamoDB TTL cleanup. Database failures
return a safe HTTP 503 and never fall back to local memory.

Local development can explicitly set `AUTH_STORE=memory`. Lambda rejects memory
mode or a missing table at startup. Creating the app, `/health`, and an unsigned
`/auth/me` request do not initialize AWS resources, allowing ordinary container
smoke checks without credentials. `/health` is a liveness check, not an AWS
connectivity test. Tests inject an isolated store through `create_app(..., store=...)`.

Lambda also requires the secret ARN, KMS key ID, and Secure cookies at startup.
Gmail credentials use AES-GCM with a per-user encryption context; Lambda wraps
data keys with KMS. Credentials expire 30 days after their last login capture or token refresh.
Expired credentials cannot be used even while DynamoDB TTL deletion is pending. For local DynamoDB use,
configure a KMS key or a stable `CREDENTIAL_ENCRYPTION_KEY` encoding 32 bytes.
Memory mode can use an ephemeral key; restarting clears sessions and credentials.
Locally, a plain `GOOGLE_CLIENT_SECRET` from `.env` still works when no ARN is configured.

## OAuth scope and current limits

The app requests `openid`, email, profile, and `gmail.modify`. The broader Gmail
permission is retained for planned mailbox features. It uses Google's
[web-server OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server)
with PKCE, one-time browser-bound state, nonce validation, and verified ID tokens.
The Google secret and tokens remain server-side. Gmail credentials are encrypted
per user and stored separately from browser sessions; access tokens refresh when
needed. The browser holds only an opaque HttpOnly, SameSite session cookie.

Sessions last eight hours and persist across backend instances when DynamoDB is
configured. Inbox lists use 20 messages per page; opening a message displays its
plain-text body without rendering email HTML. Sending supports one recipient and
a plain-text body. Attachments and search remain future work. The single Sign out
action revokes the Google grant,
removes stored access, and invalidates all of that user's sessions. Failures are
reported as incomplete, with the session and credentials preserved for retry.
You must reconnect Gmail next time. If the session or credentials already expired,
the UI directs the user to Google Account connections to remove any remaining grant.

Production mode requires same-origin HTTPS URLs, Secure cookies, DynamoDB, Secrets
Manager, and KMS on every host. Lambda always enforces production rules. Docker
defaults to production; use APP_ENV=development explicitly for local/smoke runs.
Production cookies use the __Host- prefix, and session tokens are hashed before
storage. The security rollout notes describe the intentional session reset.

Request quotas are shared through DynamoDB: 10 login starts per minute per direct
peer, 30 mail reads per minute per user, and 5 sends per minute per user by default.
Untrusted forwarding headers are ignored; an ingress proxy may therefore share a
login quota across users. Add trusted edge/IP throttling and cost controls in AWS.
Mail responses are capped at 2 MiB, POST bodies at 512 KiB, and message traversal
is bounded. Large or complex mail should be opened in Gmail.

Run without access logs so callback query strings containing authorization codes
are not written to logs. No OAuth credentials belong in frontend environment files.

## Checks

```powershell
cd server
uv run pytest
uv run ruff check main.py app integrations tests
uv run ruff format --check main.py app integrations tests
```

```powershell
cd client
npm.cmd run build
npm.cmd run lint
```

Security scanning remains a separate maintenance task; CodeBuild runs tests, lint,
and smoke checks. See the security guide for known image findings before release.
