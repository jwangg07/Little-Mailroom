# Following a feature through Mini Mailroom

The frontend groups code by feature. Components render UI, hooks own loading and
cancellation, and named API functions call the shared JSON helper. The backend
uses routers for HTTP/authentication, services for user actions, and integration
code for Google/AWS. Dependencies are assembled explicitly in
[`create_app()`](../server/app/application.py).

## Sign in and sign out

1. [`App`](../client/src/App.tsx) calls
   [`useSession`](../client/src/features/auth/useSession.ts), which loads `/auth/me`
   through [`getSession()`](../client/src/features/auth/api.ts).
   [`AccountPanel`](../client/src/features/auth/AccountPanel.tsx) renders loading,
   sign-in, and account states. Cleanup aborts account requests on unmount.
2. The sign-in link opens `/auth/google/login` in the
   [auth router](../server/app/auth/router.py). The router generates browser-bound
   state, a PKCE verifier, and a nonce. The
   [`GoogleOAuth`](../server/app/auth/google_oauth.py) provider constructs the
   Google authorization URL. The store saves the short-lived pending attempt.
3. The callback validates and consumes state once, then calls
   `GoogleOAuth.exchange()` to exchange the code and verify the ID token and nonce.
   [`GmailCredentials.capture()`](../server/app/mail/credentials.py) encrypts tokens
   with the [vault](../server/integrations/google/credential_vault.py) and preserves
   an existing refresh token when Google does not return a new one.
4. The router saves a new session, revokes any previous browser session, sets the
   HttpOnly cookie, and redirects to the frontend. `useSession.signOut()` posts to
   `/auth/logout`; the router checks the request origin and deletes session/state.
   Sign out retains the encrypted Google grant.

## Load the inbox and open a message

1. [`Mailbox`](../client/src/features/mail/Mailbox.tsx) coordinates
   [`Inbox`](../client/src/features/mail/Inbox.tsx),
   [`MessageDetail`](../client/src/features/mail/MessageDetail.tsx), and compose.
2. [`useInbox`](../client/src/features/mail/useInbox.ts) owns the current page,
   pagination, refresh, loading, and errors. It aborts old requests when the page
   changes or the component unmounts. The
   [mail API functions](../client/src/features/mail/api.ts) call
   `/api/mail/messages?page_token=...`.
3. The [mail router](../server/app/mail/router.py) authenticates the session and
   calls [`GmailService.inbox()`](../server/app/mail/service.py). The service loads
   a page and fetches metadata with a bounded thread pool. The
   [formatters](../server/app/mail/formatting.py) convert payloads for display.
4. [`GmailClient.request()`](../server/app/mail/gmail_client.py) checks granted
   scopes and asks `GmailCredentials` to load/refresh tokens. Refresh saves with a
   conditional update so it cannot overwrite a newer login or refresh. HTTP errors
   become safe messages; a rejected 401 can refresh and retry once.
5. `Mailbox.openMessage()` aborts any previous message request before loading
   `/api/mail/messages/{id}`. The backend parses raw MIME and returns plain text;
   `MessageDetail` displays it as text without injecting email HTML.

## Send email

1. [`ComposeForm`](../client/src/features/mail/ComposeForm.tsx) owns the draft,
   sending flag, errors, and success notice. A synchronous ref blocks duplicate
   submissions before React can disable the form. A successful send clears the
   draft; failures retain it.
2. `sendMail()` posts JSON and the `X-Mailroom-Request` header to `/api/mail/send`.
   The router authenticates the user, checks origin, and validates the
   [`MailDraft`](../server/app/mail/schemas.py), including single-line headers.
3. `GmailService.send()` uses `encode_draft()` to create a plain-text MIME message
   and sends it through `GmailClient`. Transport errors and server errors do not
   trigger automatic retries: delivery may already have occurred. Check Sent in
   Gmail before manually retrying an uncertain result.

## Paths, storage, and Docker

- Settings load `server/.env` regardless of the current working directory.
  Built frontend assets remain at `client/dist` relative to the repository root.
- Authentication records and encrypted credentials share the existing store;
  records keep their existing keys and formats. Only sessions and pending logins
  expire; encrypted Gmail credentials have no session TTL.
- The client secret is retrieved lazily by
  [`GoogleClientSecret`](../server/integrations/google/client_secret.py), so
  anonymous health/account checks do not contact AWS.
- The [Dockerfile](../Dockerfile) copies `main.py`, `app/`, and `integrations/` in
  both backend test and runtime stages. The runtime serves frontend assets from
  `/app/client/dist` and still starts `uvicorn main:app` as a non-root user.
- CodeBuild uses the same Docker test target, then builds and smoke-tests the
  runtime image. Tests use fake Google/AWS boundaries; they never send real email.
