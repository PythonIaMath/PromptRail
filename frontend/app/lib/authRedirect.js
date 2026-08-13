export const DEFAULT_AUTH_REDIRECT_PATH = "/dashboard";
export const DEFAULT_SIGNUP_REDIRECT_PATH = "/onboarding";

export function encodeAuthRedirectPath(value) {
  return Array.from(new TextEncoder().encode(String(value || "")), (byte) => (
    byte.toString(16).padStart(2, "0")
  )).join("");
}

export function decodeAuthRedirectPath(value) {
  if (typeof value !== "string" || !value || value.length % 2 !== 0 || !/^[0-9a-f]+$/i.test(value)) {
    return "";
  }

  try {
    const bytes = value.match(/.{2}/g).map((byte) => Number.parseInt(byte, 16));
    return new TextDecoder().decode(Uint8Array.from(bytes));
  } catch {
    return "";
  }
}

export function safeAuthRedirectPath(value, fallback) {
  if (
    typeof value !== "string"
    || !value.startsWith("/")
    || value.startsWith("//")
    || value.includes("\\")
  ) {
    return fallback;
  }

  try {
    const parsed = new URL(value, "http://localhost");
    return parsed.origin === "http://localhost"
      ? `${parsed.pathname}${parsed.search}${parsed.hash}`
      : fallback;
  } catch {
    return fallback;
  }
}

export function resolveAuthRedirect(search = "") {
  const params = new URLSearchParams(search);
  const requestedPath = params.get("next") || decodeAuthRedirectPath(params.get("next_path"));
  const mode = params.get("mode") === "signup" ? "signup" : "login";
  const defaultPath = mode === "signup"
    ? DEFAULT_SIGNUP_REDIRECT_PATH
    : DEFAULT_AUTH_REDIRECT_PATH;
  const destination = safeAuthRedirectPath(requestedPath, defaultPath);

  return {
    mode,
    nextPath: requestedPath
      ? destination
      : DEFAULT_AUTH_REDIRECT_PATH,
    signupPath: requestedPath
      ? destination
      : DEFAULT_SIGNUP_REDIRECT_PATH,
  };
}

export function authErrorCallbackPath({ mode, nextPath, checkoutActivation = false }) {
  const params = new URLSearchParams({
    error: "magic-link",
    mode,
    next_path: encodeAuthRedirectPath(nextPath),
  });
  if (checkoutActivation) {
    params.set("source", "checkout");
  }
  return `/login?${params.toString()}`;
}

export function authDestination(mode, nextPath, signupPath) {
  return mode === "signup" ? signupPath : nextPath;
}
