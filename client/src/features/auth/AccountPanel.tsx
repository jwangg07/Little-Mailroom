import type { User } from './types'
import './auth.css'

type AccountPanelProps = {
  user: User | null
  loading: boolean
  signingOut: boolean
  error: string | null
  signOut: () => Promise<void>
}

export default function AccountPanel({
  user,
  loading,
  signingOut,
  error,
  signOut,
}: AccountPanelProps) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <div className="mail-mark" aria-hidden="true">
        ✉
      </div>
      <p className="eyebrow">A little home for your inbox</p>
      <h1 id="welcome-title">Mini Mailroom</h1>
      <p className="intro">
        A quiet place to read your inbox and write your next letter.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">Checking your account…</p>
      ) : user ? (
        <div className="account">
          <span className="status">Signed in</span>
          <h2>Welcome, {user.name}</h2>
          <p className="email">{user.email}</p>
          <button
            className="secondary"
            onClick={() => void signOut()}
            disabled={signingOut}
          >
            {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      ) : (
        <>
          <a className="google-button" href="/auth/google/login">
            <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
              <path
                fill="#4285F4"
                d="M21.6 12.23c0-.71-.06-1.39-.18-2.05H12v3.88h5.38a4.6 4.6 0 0 1-2 3.02v2.51h3.24c1.9-1.75 2.98-4.33 2.98-7.36Z"
              />
              <path
                fill="#34A853"
                d="M12 22c2.7 0 4.96-.9 6.62-2.42l-3.24-2.51c-.9.6-2.05.97-3.38.97-2.6 0-4.81-1.76-5.6-4.12H3.06v2.59A10 10 0 0 0 12 22Z"
              />
              <path
                fill="#FBBC05"
                d="M6.4 13.92a6 6 0 0 1 0-3.84V7.49H3.06a10 10 0 0 0 0 9.02l3.34-2.59Z"
              />
              <path
                fill="#EA4335"
                d="M12 5.96c1.47 0 2.79.5 3.82 1.49l2.87-2.87A9.6 9.6 0 0 0 12 2a10 10 0 0 0-8.94 5.49l3.34 2.59A5.98 5.98 0 0 1 12 5.96Z"
              />
            </svg>
            Sign in with Google
          </a>
          <p className="note">
            Connect your Google account to read and send Gmail messages.
          </p>
        </>
      )}
    </section>
  )
}
