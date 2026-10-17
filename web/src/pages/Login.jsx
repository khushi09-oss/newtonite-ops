import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

// The four roles worth trying first: each sees the same app differently.
const ROLES = [
  { email: "ava.okafor@newtonite.dev", role: "Admin", person: "Ava Okafor", what: "Every team, and the Admin page" },
  { email: "finn.okafor@newtonite.dev", role: "Risk lead", person: "Finn Okafor", what: "Approves and closes Risk work" },
  { email: "dev.mehta@newtonite.dev", role: "Risk member", person: "Dev Mehta", what: "Claims and works Risk items" },
  { email: "ben.okafor@newtonite.dev", role: "Compliance lead", person: "Ben Okafor", what: "Leads Compliance, views Risk" },
];

const GSI_SRC = "https://accounts.google.com/gsi/client";

// Google's script is loaded only when this deployment offers Google sign-in.
function loadGoogle() {
  if (window.google?.accounts?.id) return Promise.resolve(window.google);
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${GSI_SRC}"]`);
    const script = existing || Object.assign(document.createElement("script"), { src: GSI_SRC, async: true });
    script.addEventListener("load", () => resolve(window.google));
    script.addEventListener("error", () => reject(new Error("Google sign-in could not load. Check your connection.")));
    if (!existing) document.head.appendChild(script);
  });
}

export default function Login() {
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api("/auth/config"), staleTime: Infinity });

  async function finish(request) {
    setBusy(true);
    setError(null);
    try {
      await request();
      qc.clear();
      navigate("/");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (config.isPending) return <div className="page-loading">Loading</div>;
  if (config.isError) return <div className="login"><p className="error">Could not reach the server: {config.error.message}</p></div>;
  const { google_client_id: clientId, dev_login: devLogin, demo } = config.data;

  const devSignIn = devLogin && (
    <DevSignIn busy={busy} onSignIn={(email) => finish(() => api("/auth/dev-login", { method: "POST", body: { email } }))} />
  );
  const privacy = (
    <Link to="/privacy" className="muted small login-foot">
      Privacy
    </Link>
  );

  // The public demo offers two ways in: the shared demo people, or an empty workspace of your own.
  if (demo) {
    return (
      <div className="login">
        <div className="login-wide">
          <div className="login-head">
            <span className="brand-mark">N</span>
            <h1>Newtonite Ops</h1>
            <p className="muted">A tracker for the operational work a company runs on. Try it two ways.</p>
          </div>
          <div className="paths">
            <section className="path">
              <h2>Explore the demo</h2>
              <p className="muted">
                A company's worth of made-up work, shared by every visitor. Pick a seat to see the app from it.
              </p>
              {devSignIn}
            </section>
            <section className="path">
              <h2>Start your own workspace</h2>
              <p className="muted">Sign in with Google and start from empty, the way a real team would.</p>
              <ol className="path-steps">
                <li>Create a team</li>
                <li>Add teammates by email</li>
                <li>Run work through it, from new to closed</li>
              </ol>
              {clientId ? (
                <GoogleButton
                  clientId={clientId}
                  onCredential={(credential) => finish(() => api("/auth/google", { method: "POST", body: { credential } }))}
                  onError={setError}
                />
              ) : (
                <p className="muted">Google sign-in is not set up on this server.</p>
              )}
              <p className="muted small">
                Private to you and the people you add. Cleared every night at 04:00 Singapore time.
              </p>
            </section>
          </div>
          {error && <p className="error">{error}</p>}
          {privacy}
        </div>
      </div>
    );
  }

  return (
    <div className="login">
      <div className="login-card">
        <div className="login-head">
          <span className="brand-mark">N</span>
          <h1>Sign in to Newtonite Ops</h1>
          <p className="muted">
            {clientId ? "Use the Google account your admin invited." : "Operational work for every team, in one place."}
          </p>
        </div>
        {clientId ? (
          <GoogleButton
            clientId={clientId}
            onCredential={(credential) => finish(() => api("/auth/google", { method: "POST", body: { credential } }))}
            onError={setError}
          />
        ) : (
          !devLogin && <p className="muted">Sign-in is not configured on this server.</p>
        )}
        {error && <p className="error">{error}</p>}
        {devLogin && clientId && <div className="or">or sign in as a seeded person</div>}
        {devSignIn}
        {privacy}
      </div>
    </div>
  );
}

function GoogleButton({ clientId, onCredential, onError }) {
  const slot = useRef(null);
  const handler = useRef(onCredential);
  handler.current = onCredential;
  useEffect(() => {
    let cancelled = false;
    loadGoogle()
      .then((google) => {
        if (cancelled || !slot.current) return;
        google.accounts.id.initialize({
          client_id: clientId,
          callback: (response) => handler.current(response.credential),
          ux_mode: "popup",
        });
        google.accounts.id.renderButton(slot.current, { theme: "outline", size: "large", text: "signin_with", width: 320 });
      })
      .catch((e) => onError(e.message));
    return () => {
      cancelled = true;
    };
  }, [clientId, onError]);
  return <div ref={slot} className="google-slot" />;
}

function DevSignIn({ busy, onSignIn }) {
  const [email, setEmail] = useState("");
  return (
    <div className="dev-signin">
      <div className="quick">
        {ROLES.map((r) => (
          <button key={r.email} type="button" className="role-card" onClick={() => onSignIn(r.email)} disabled={busy}>
            <strong>{r.role}</strong>
            <span>{r.person}</span>
            <span>{r.what}</span>
          </button>
        ))}
      </div>
      <details>
        <summary className="muted small">Sign in with a seeded email instead</summary>
        <form
          className="dev-signin"
          style={{ marginTop: 10 }}
          onSubmit={(e) => {
            e.preventDefault();
            onSignIn(email.trim());
          }}
        >
          <label>
            Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="first.last@newtonite.dev" />
          </label>
          <button className="primary" disabled={busy || !email}>
            Sign in
          </button>
        </form>
      </details>
    </div>
  );
}
