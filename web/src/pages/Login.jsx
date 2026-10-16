import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { DemoBanner } from "../ui";

const QUICK = [
  ["ava.okafor@newtonite.dev", "Ava, admin"],
  ["finn.okafor@newtonite.dev", "Finn, leads Risk"],
  ["dev.mehta@newtonite.dev", "Dev, on Risk"],
  ["ben.okafor@newtonite.dev", "Ben, leads Compliance, views Risk"],
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
      navigate("/inbox");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (config.isPending) return <div className="page-loading">Loading</div>;
  if (config.isError) return <div className="login"><p className="error">Could not reach the server: {config.error.message}</p></div>;
  const { google_client_id: clientId, dev_login: devLogin, demo } = config.data;

  return (
    <div className="login">
      <div className="login-card">
        {demo && <DemoBanner />}
        <h1>Newtonite Ops</h1>
        {clientId ? (
          <>
            <p className="muted">
              {demo
                ? "Sign in with any Google account. In this demo you join the Support team."
                : "Sign in with the Google account your admin invited."}
            </p>
            <GoogleButton
              clientId={clientId}
              onCredential={(credential) =>
                finish(() => api("/auth/google", { method: "POST", body: { credential } }))
              }
              onError={setError}
            />
          </>
        ) : (
          !devLogin && <p className="muted">Sign-in is not configured on this server.</p>
        )}
        {error && <p className="error">{error}</p>}
        {devLogin && <DevSignIn demo={demo} divided={Boolean(clientId)} busy={busy} onSignIn={(email) => finish(() => api("/auth/dev-login", { method: "POST", body: { email } }))} />}
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

function DevSignIn({ demo, divided, busy, onSignIn }) {
  const [email, setEmail] = useState("");
  return (
    <form
      className={divided ? "dev-signin divided" : "dev-signin"}
      onSubmit={(e) => {
        e.preventDefault();
        onSignIn(email.trim());
      }}
    >
      <p className="muted">
        {demo
          ? "Or try it instantly as one of the demo people. No account needed."
          : "Development sign-in: any seeded user, no password. Off in production."}
      </p>
      <label>
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="first.last@newtonite.dev" />
      </label>
      <button className="primary" disabled={busy || !email}>
        Sign in
      </button>
      <div className="quick">
        {QUICK.map(([value, label]) => (
          <button key={value} type="button" className="chip" onClick={() => onSignIn(value)} disabled={busy}>
            {label}
          </button>
        ))}
      </div>
    </form>
  );
}
