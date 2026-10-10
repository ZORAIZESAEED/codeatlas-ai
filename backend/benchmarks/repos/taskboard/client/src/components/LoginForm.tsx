import { useState, type FormEvent } from "react";
import { ApiError, apiFetch } from "../api/client";

export function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token } = await apiFetch<{ token: string }>("/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      localStorage.setItem("taskboard.token", token);
      onSuccess();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed, please try again");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="login-form">
      <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      {error && <p role="alert" className="form-error">{error}</p>}
      <button disabled={busy}>Sign in</button>
    </form>
  );
}
