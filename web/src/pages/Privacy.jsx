import { Link } from "react-router-dom";

// Public, no sign-in needed: Google links here from its consent screen.
export default function Privacy() {
  return (
    <div className="login">
      <article className="login-card doc">
        <div className="login-head">
          <Link to="/login" className="brand-mark" aria-label="Newtonite Ops">
            N
          </Link>
          <h1>Privacy</h1>
          <p className="muted">Newtonite Ops is a public demo of an internal work tracker.</p>
        </div>

        <h2>What we keep when you sign in with Google</h2>
        <ul>
          <li>Your name and email address, and the account id Google gives this app.</li>
          <li>A session cookie that keeps you signed in. It holds a random id and nothing else.</li>
          <li>What you do in the app: your teams, the people you add to them by email, items, comments and status changes.</li>
          <li>Your IP address, only as a counter that limits sign-in attempts. It is deleted within an hour.</li>
        </ul>
        <p>We do not ask Google for anything else, and we never see your password.</p>

        <h2>How long we keep it</h2>
        <p>
          Everything is deleted every night at 04:00 Singapore time, when the demo resets. That includes your account,
          your sessions and anything you wrote.
        </p>

        <h2>Who sees it</h2>
        <p>
          Your workspace is private. Only the people you add to your teams see it, and they see your name and what you
          write there. The shared demo accounts, the demo admin included, cannot see your account or your workspace,
          and nobody can sign in as you without your Google account. Nothing is sold or shared for advertising, and
          the app has no analytics or tracking. The site runs on Vercel and stores its data with Neon, who process it
          only to host the app.
        </p>

        <h2>Questions or removal</h2>
        <p>
          Open an issue at{" "}
          <a href="https://github.com/khushi09-oss/newtonite-ops/issues">github.com/khushi09-oss/newtonite-ops</a>, or
          wait for the nightly reset, which removes everything.
        </p>

        <Link to="/login">Back to sign-in</Link>
      </article>
    </div>
  );
}
