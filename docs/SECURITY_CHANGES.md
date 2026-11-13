# Mini Mailroom security guide

Updated November 12, 2026. Changes are uncommitted and have not been deployed.

## History review

No live secret was found in the available history: 12 reachable commits through
`9c0a9dc`, 104 historical paths, and 147 distinct file contents, including deleted
files. Secret scans and inspection found placeholders in environment examples and
fake credentials in tests. Real AWS resource identifiers were committed, but those
are addresses, not passwords. They remain to avoid unnecessary deployment changes.

This does not cover inaccessible branches, forks, cloud settings, logs, or old
images. No active compromise was established. There is no evidence here requiring
emergency credential rotation or a Git-history rewrite.

| Commit | Security-relevant history |
| --- | --- |
| `5b1653c`–`bb4a15c` | Scaffold, ignore rules, dependencies, local Python pin. |
| `5d812c8` | Google sign-in with OAuth checks, but no request quotas. |
| `f4227c9` | Gmail access added to consent. |
| `1a43022` | Docker/CodeBuild, non-root runtime, disabled access logs. |
| `c2cbdc2` | DynamoDB stored usable session tokens in record keys; memory cleanup regressed. |
| `6274e7c` | Lambda adapter and policy examples with real resource identifiers. |
| `aa42fb6` | Persistent encrypted Gmail credentials; KMS policy example incomplete. |
| `1c78525`, `9c0a9dc` | Build fixes, tests, reorganization; the issues below remained. |

## Problems, alternatives, and retained solutions

### Browser session tokens

**Problem:** Reading an active database session record exposed a usable cookie.
**Options:** Rely only on database restrictions, encrypt tokens, or store fingerprints.
**Solution:** `server/app/auth/store.py` uses a one-way fingerprint:

```python
"session-v2#" + hashlib.sha256(session_id.encode()).hexdigest()
```

**Why this fits:** The app needs to compare random tokens, never recover them.
A copied backup no longer supplies usable browser cookies. This does not protect
against database writes. It is for random tokens, not human passwords. The new
format intentionally requires existing users to sign in again.

### Combined Sign out and Gmail disconnection

**Problem:** Old sign-out left Google access saved and other devices signed in.
**Options:** Separate controls or a combined action.
**Solution:** One Sign out button calls `/auth/logout`. It checks the request and
session, revokes Google access, invalidates existing sessions across devices,
deletes the expected credential version, and clears browser cookies.

**Why this fits:** It matches your expectation that leaving also ends Gmail access.
Emails stay in Gmail. Users reconnect next time; other devices sign in again.
It does not sign you out of Google's website. Google revocation can affect related
grants in the same Google project; a dedicated project avoids coupling other apps.

If Google fails, the app reports sign-out as **incomplete** and preserves the
session/credential for retry. You may still be signed in; do not treat that error
as successful sign-out on a shared computer. Immediate local logout plus a reliable
background revocation queue is another option, but adds infrastructure.

If the session or saved credential expired, the app cannot always revoke Google
access. It clears this browser's cookies and directs you to **Google Account →
Third-party connections → Mini Mailroom**. If it can identify the user, it also
invalidates their existing app sessions. Concurrent credential changes return a
conflict rather than deleting newer access. Already-running requests cannot be
recalled, and sign-out cannot undo mail already sent.

**What `generation: str = ""` means:** Each session copies the account's current
session label. Sign-out changes that label, so all older sessions stop matching.
The empty string is the initial label, not a password. This avoids scanning the
table for every device. Do not delete generation markers while old sessions exist:
resetting a label could make an older generation valid again.

### Gmail scope and credential expiry

**Problem:** Gmail permissions exceeded today's features; saved access had no expiry.
**Options:** Read/send only, expand later, or retain `gmail.modify` now.
**Solution:** Keep `gmail.modify` for your planned features. Saved credentials
expire 30 days after the latest login capture or token refresh.

**Why this fits:** Future mailbox actions can use the existing grant. This accepts
broader permissions; it is not a permission-related security improvement. Narrower
scopes would reduce the impact of a stolen token. Expiry limits abandoned access,
not continuously used accounts. Deleting a token does not revoke Google's grant;
use Sign out while access exists or Google Account settings afterward.

### Excessive requests and large email

**Problem:** Repeated logins wrote unlimited state; inbox calls multiplied Google
requests; raw email included unnecessary attachments; local expired state accumulated.
**Options:** Provider quotas, edge controls, and application limits.
**Solution:** Atomic shared counters allow 10 login starts per minute per direct
source, 30 mailbox reads per minute per user, 5 sends per minute per user, and
10 sign-out attempts per minute per user. Excess requests receive HTTP 429.
Local storage prunes expired entries and has capacity caps.

Mail uses structured content, skips attachments/HTML, and limits complexity.
Inbox pages contain at most 20 messages with five workers. Google responses are
capped at 2 MiB; incoming POST bodies at 512 KiB. Large mail can be opened in Gmail.

**Why this fits:** It limits work where the app knows the user/operation, across
servers. Frontend buttons cannot stop scripts. AWS edge/concurrency limits and
cost alarms remain necessary: app limits cannot prevent every charged invocation.
Users behind a proxy may share the login quota because untrusted forwarded IPs
are ignored.

**What the 15-second timeout means:** `partial(GoogleRequest(), timeout=15)` bounds
network waits when identity-token verification fetches Google's signing certificates.
It does not shorten the eight-hour session or skip verification. Connect/read
timeouts are not a strict deadline for the entire sign-in flow. POST body reading
has its own 15-second deadline.

### Production settings and browser protections

**Problem:** Startup checks depended on Lambda; browser security headers were missing.
**Options:** Deployment instructions/proxies alone, or enforce settings in code.
**Solution:** Production requires HTTPS, matching frontend/callback origins,
Secure cookies, DynamoDB, Secrets Manager, and KMS. Lambda enforces production;
Docker defaults to it. Local development explicitly uses development mode.
Production cookies use `__Host-` protections. Headers restrict scripts, prevent
framing, discourage content-type guessing, and enforce HTTPS in production.
Private auth/mail responses remain non-cacheable; email HTML remains unrendered.

**Why this fits:** Configuration mistakes fail at startup. Browser headers provide
another layer of protection without extra dependencies.

### Secret files and AWS permissions

**Problem:** Ignore rules missed secret-file variants; the policy omitted KMS
permissions already required by the credential vault.
**Options:** Manual care or preventive ignores; broad or specific AWS permissions.
**Solution:** Git/Docker ignore common secret files while preserving `.env.example`.
The policy adds `dynamodb:UpdateItem` for quotas and `kms:GenerateDataKey`/`kms:Decrypt`
for one key. Context conditions require app/user labels.

**Why this fits:** It prevents common accidents and enables the actual code without
access to every key. GenerateDataKey supplies a data-encryption key and a protected
copy; Decrypt recovers it when reading saved credentials. Context labels bind data
to app/user but do not replace authentication. The KMS key policy must also allow
the role; a separate Secrets Manager encryption key may need its own permission.

### Python and container maintenance

Local Python remains **3.11.1**, as requested. Docker retains its separate Python
3.12 runtime, updated to a patched 3.12.15/Debian 13 base. Node/Python/uv images
use verified digest pins; OS updates run during builds. Pins need regular updates.
This keeps your installation compatible without downgrading production. Local
3.11.1 still misses subsequent security fixes.

## Optional additions removed

Removed the pre-commit hook, scanner download runner `scripts/security_check.py`,
its `security-tools.json` manifest, and related CodeBuild scanning/SBOM steps.
The GitHub workflow and Dependabot files were already removed. Documentation is
consolidated here. The request-body limiter also lost an unnecessary helper and
custom exception while preserving its limits. Duplicate Lambda/production startup
checks now share one validation path.

These tools added setup and maintenance. CodeBuild keeps its existing tests, lint,
build, and smoke checks. **The added automated secret/dependency/image security
scans are no longer part of CodeBuild.** Run them separately and review dependencies
regularly. Ignore rules cannot catch a secret pasted into tracked source code.

The credential migration helper remains because it preserves existing saved
access under the expiry rule. Tests covering the retained fixes also remain.

## Reproduce the checks

From the repository root, review changes and run tests. Open new untracked files
separately: ordinary `git diff` does not display their contents.

```sh
git status --short
git diff --stat
git diff
cd server
uv sync --locked
uv run python --version
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Python should report 3.11.1. Tests fake Google/AWS and do not send real email.
From the repository root, check the frontend:

```sh
cd client
npm ci
npm run build
npm run lint
```

Use `npm.cmd` in PowerShell if its execution policy blocks `npm.ps1`.
Check the separate Docker runtime from the repository root:

```sh
docker build --platform linux/amd64 --target test -t mini-mailroom-tests .
docker run --rm mini-mailroom-tests
```

## Verification after trimming

95 backend tests passed on local Python 3.11.1 and in the Docker test target.
Lint, formatting, and Git whitespace checks passed. The frontend was unchanged
by this trimming pass; its earlier build/lint and browser sign-out checks passed.

## Before deployment

- Verify the existing table/secret ARNs; replace the KMS key placeholder in
  `deploy/lambda-runtime-policy.json`. Check the role and key policy agree.
- Enable DynamoDB TTL on `expires_at`. To preserve legacy saved credentials, preview
  migration from the repository root using an operator role:

  ```sh
  uv run --project server python scripts/expire_legacy_credentials.py --table YOUR_TABLE
  ```

  Review the count, then repeat with `--apply` to assign a 30-day expiry. The operator
  needs Scan/UpdateItem; the runtime does not need Scan. Without migration, legacy
  credentials are rejected/deleted on access; untouched rows still need cleanup.
  The helper has not been run against AWS.
- Configure production settings and deploy without overlapping old/new versions
  that accept different session formats. Existing sessions require fresh sign-in.
  Preserve generation markers. Verify real HTTPS/OAuth and combined sign-out.
- Review live IAM, backups, edge limits, cost alarms, and log handling. Add an
  account allowlist if the app is private; Google's testing mode is not permanent
  access control.
- Scan the final runtime image before release. The earlier test-image scan had
  47 HIGH findings across 11 advisories, no CRITICAL findings, and no reported fixes.
  These counts do not establish exploitability or cover the final Lambda image.
  This environment could not fetch the adapter from `public.ecr.aws`; its tag needs
  a verified digest pin and the final Lambda image needs building/scanning elsewhere.
