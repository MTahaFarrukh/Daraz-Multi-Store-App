import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { ErrorBanner, LoadingState } from "@/components/ui/Primitives";

export function LoginPage() {
  const { authenticated, loading, signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from || "/app";

  if (loading) return <LoadingState label="Checking session…" />;
  if (authenticated) return <Navigate to={from} replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div style={{ textAlign: "center", marginBottom: "0.75rem", fontFamily: "var(--font-display)", fontWeight: 800, color: "var(--teal-deep)" }}>
          MultiStore
        </div>
        <h1>Sign in</h1>
        <p className="sub">Access your vendor workspace</p>
        <ErrorBanner message={error} />
        <form onSubmit={onSubmit}>
          <label className="field">
            <span>Email</span>
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          <label className="field">
            <span>Password</span>
            <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          <button className="btn btn-primary" style={{ width: "100%" }} disabled={busy} type="submit">
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="auth-toggle">
          Need an account? <Link to="/signup">Create one</Link>
        </p>
      </div>
    </div>
  );
}

export function SignupPage() {
  const { authenticated, loading, signUp } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  if (loading) return <LoadingState label="Checking session…" />;
  if (authenticated) return <Navigate to="/app" replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signUp(email.trim(), password);
      navigate("/app", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign up failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div style={{ textAlign: "center", marginBottom: "0.75rem", fontFamily: "var(--font-display)", fontWeight: 800, color: "var(--teal-deep)" }}>
          MultiStore
        </div>
        <h1>Create account</h1>
        <p className="sub">We'll create your vendor workspace automatically</p>
        <ErrorBanner message={error} />
        <form onSubmit={onSubmit}>
          <label className="field">
            <span>Email</span>
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          <label className="field">
            <span>Password</span>
            <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </label>
          <button className="btn btn-primary" style={{ width: "100%" }} disabled={busy} type="submit">
            {busy ? "Creating…" : "Sign up"}
          </button>
        </form>
        <p className="auth-toggle">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
