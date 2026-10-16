import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

const QUICK = [
  ["ava.okafor@newtonite.dev", "Ava, admin"],
  ["finn.okafor@newtonite.dev", "Finn, leads Risk"],
  ["dev.mehta@newtonite.dev", "Dev, on Risk"],
  ["ben.okafor@newtonite.dev", "Ben, leads Compliance, views Risk"],
];

export default function Login() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();

  async function signIn(value) {
    setBusy(true);
    setError(null);
    try {
      await api("/auth/dev-login", { method: "POST", body: { email: value } });
      qc.clear();
      navigate("/inbox");
    } catch (e) {
      setError(e.status === 404 ? "No seeded user has that email." : e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form
        className="login-card"
        onSubmit={(e) => {
          e.preventDefault();
          signIn(email.trim());
        }}
      >
        <h1>Newtonite Ops</h1>
        <p className="muted">Sign in as any seeded user. There is no password in this build.</p>
        <label>
          Email
          <input
            autoFocus
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="first.last@newtonite.dev"
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy || !email}>
          Sign in
        </button>
        <div className="quick">
          {QUICK.map(([value, label]) => (
            <button key={value} type="button" className="chip" onClick={() => signIn(value)} disabled={busy}>
              {label}
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
