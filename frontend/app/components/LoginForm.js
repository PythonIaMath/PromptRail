"use client";

import { authClient } from "../lib/auth-client.js";
import {
  authErrorCallbackPath,
  DEFAULT_AUTH_REDIRECT_PATH,
  DEFAULT_SIGNUP_REDIRECT_PATH,
  resolveAuthRedirect,
} from "../lib/authRedirect.js";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

function GoogleLogo() {
  return (
    <svg className="auth-social-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.1c-.22-.66-.35-1.36-.35-2.1s.13-1.44.35-2.1V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l3.66-2.84z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06L5.84 9.9C6.71 7.3 9.14 5.38 12 5.38z"
      />
    </svg>
  );
}

function GitHubLogo() {
  return (
    <svg className="auth-social-icon auth-social-icon-github" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 .5C5.65.5.5 5.65.5 12c0 5.09 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.16c-3.2.7-3.87-1.36-3.87-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.05-.72.08-.71.08-.71 1.16.08 1.77 1.19 1.77 1.19 1.03 1.76 2.69 1.25 3.35.96.1-.75.4-1.25.73-1.54-2.55-.29-5.23-1.28-5.23-5.68 0-1.25.45-2.28 1.19-3.08-.12-.29-.52-1.46.11-3.04 0 0 .97-.31 3.17 1.18.92-.26 1.9-.38 2.88-.39.98 0 1.96.13 2.88.39 2.2-1.49 3.17-1.18 3.17-1.18.63 1.58.23 2.75.11 3.04.74.8 1.19 1.83 1.19 3.08 0 4.42-2.69 5.39-5.25 5.67.41.36.78 1.07.78 2.15v3.16c0 .31.21.68.79.56A11.5 11.5 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5z"
      />
    </svg>
  );
}

export default function LoginForm() {
  const router = useRouter();
  const [nextPath, setNextPath] = useState(DEFAULT_AUTH_REDIRECT_PATH);
  const [signupPath, setSignupPath] = useState(DEFAULT_SIGNUP_REDIRECT_PATH);
  const [checkoutActivation, setCheckoutActivation] = useState(false);
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [socialProvider, setSocialProvider] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const redirect = resolveAuthRedirect(window.location.search);
    setNextPath(redirect.nextPath);
    setSignupPath(redirect.signupPath);
    setMode(redirect.mode);
    setCheckoutActivation(new URLSearchParams(window.location.search).get("source") === "checkout");
  }, []);

  async function submitLogin(event) {
    event.preventDefault();
    const trimmedEmail = email.trim();

    if (!trimmedEmail) {
      setError("Email is required.");
      return;
    }

    setIsSubmitting(true);
    setError("");

    let response;
    try {
      response = await authClient.signIn.magicLink({
        email: trimmedEmail,
        name: trimmedEmail.split("@")[0],
        callbackURL: nextPath,
        newUserCallbackURL: signupPath,
        errorCallbackURL: authErrorCallbackPath({
          mode,
          nextPath: mode === "signup" ? signupPath : nextPath,
          checkoutActivation,
        }),
      });
    } catch (requestError) {
      setIsSubmitting(false);
      setError(requestError.message || "Authentication request failed.");
      return;
    }

    setIsSubmitting(false);

    if (response.error) {
      const code = response.error.code || response.error.statusText;
      if (code === "EMAIL_DELIVERY_FAILED") {
        setError(response.error.message || "Verification email delivery failed. Please try again.");
        return;
      }
      setError(response.error.message || "Authentication failed.");
      return;
    }

    const params = new URLSearchParams({ email: trimmedEmail });
    router.push(`/check-email?${params.toString()}`);
  }

  async function submitSocial(provider) {
    setIsSubmitting(true);
    setSocialProvider(provider);
    setError("");

    try {
      const response = await authClient.signIn.social({
        provider,
        callbackURL: nextPath,
        newUserCallbackURL: signupPath,
      });

      if (response?.error) {
        if (response.error.code === "PROVIDER_NOT_FOUND") {
          setError(`${provider} OAuth is not configured on this server.`);
          setIsSubmitting(false);
          setSocialProvider("");
          return;
        }
        setError(response.error.message || `${provider} sign-in failed.`);
        setIsSubmitting(false);
        setSocialProvider("");
      }
    } catch (requestError) {
      setError(requestError.message || `${provider} sign-in failed.`);
      setIsSubmitting(false);
      setSocialProvider("");
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-label={mode === "signup" ? "Create a PromptRail account" : "Log in to PromptRail"}>
        <div className="auth-copy">
          <h1>{checkoutActivation ? "Activate your paid plan." : mode === "signup" ? "Create your PromptRail account." : "Continue to your router."}</h1>
          {checkoutActivation ? <p>Use the same email address you entered in Stripe so we can attach your subscription.</p> : null}
        </div>

        <div className="auth-social-actions">
          <button
            className="auth-social-button"
            disabled={isSubmitting}
            type="button"
            onClick={() => submitSocial("google")}
          >
            <GoogleLogo />
            {socialProvider === "google" ? "Opening Google..." : mode === "signup" ? "Create account with Google" : "Continue with Google"}
          </button>
          <button
            className="auth-social-button"
            disabled={isSubmitting}
            type="button"
            onClick={() => submitSocial("github")}
          >
            <GitHubLogo />
            {socialProvider === "github" ? "Opening GitHub..." : mode === "signup" ? "Create account with GitHub" : "Continue with GitHub"}
          </button>
        </div>

        <form className="auth-form" onSubmit={submitLogin}>
          <div className="auth-divider">
            <span>Magic link</span>
          </div>
          <label>
            <span>Email</span>
            <input
              autoComplete="email"
              required
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          {error ? <p className="auth-error">{error}</p> : null}
          <button className="setup-button setup-button-primary" disabled={isSubmitting} type="submit">
            {isSubmitting ? "Sending link..." : mode === "signup" ? "Create account with email" : "Continue with email"}
          </button>
        </form>
      </section>
    </main>
  );
}
