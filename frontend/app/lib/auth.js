import { betterAuth } from "better-auth";
import { mongodbAdapter } from "better-auth/adapters/mongodb";
import { nextCookies } from "better-auth/next-js";
import { magicLink } from "better-auth/plugins";
import { sendMagicLinkEmail } from "./email.js";
import { requireMongoClientInstance, requireMongoDatabaseHandle } from "./mongo.js";
import { userAdditionalFields } from "./routeSchemas.js";
import { serverEnv } from "./serverEnv.js";

function originFromUrl(value) {
  if (!value) {
    return null;
  }

  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

function addOrigin(origins, value) {
  const origin = originFromUrl(value);
  if (origin) {
    origins.add(origin);
  }
}

function addWwwPair(origins, origin) {
  const parsed = new URL(origin);
  if (parsed.protocol !== "https:" || parsed.hostname === "localhost") {
    return;
  }

  if (parsed.hostname.startsWith("www.")) {
    parsed.hostname = parsed.hostname.slice(4);
    origins.add(parsed.origin);
    return;
  }

  parsed.hostname = `www.${parsed.hostname}`;
  origins.add(parsed.origin);
}

function trustedOrigins() {
  const origins = new Set();
  addOrigin(origins, serverEnv("BETTER_AUTH_URL"));
  addOrigin(origins, serverEnv("NEXT_PUBLIC_APP_URL"));

  for (const value of [
    ...serverEnv("BETTER_AUTH_TRUSTED_ORIGINS").split(","),
    ...serverEnv("LEROUTER_TRUSTED_ORIGINS").split(","),
  ]) {
    addOrigin(origins, value.trim());
  }

  for (const origin of [...origins]) {
    addWwwPair(origins, origin);
  }

  return [...origins];
}

const authTrustedOrigins = trustedOrigins();
const configuredBaseURL = serverEnv("BETTER_AUTH_URL", serverEnv("NEXT_PUBLIC_APP_URL", "http://localhost:3000"));

function authBaseURL() {
  const fallbackOrigin = originFromUrl(configuredBaseURL) || "http://localhost:3000";
  const allowedHosts = authTrustedOrigins
    .map((origin) => {
      try {
        return new URL(origin).host;
      } catch {
        return null;
      }
    })
    .filter(Boolean);

  if (process.env.NODE_ENV !== "production") {
    for (const host of ["localhost:3000", "localhost:3001", "localhost:3002"]) {
      if (!allowedHosts.includes(host)) {
        allowedHosts.push(host);
      }
    }
  }

  if (allowedHosts.length <= 1) {
    return configuredBaseURL;
  }

  return {
    allowedHosts,
    protocol: new URL(fallbackOrigin).protocol.replace(":", ""),
    fallback: configuredBaseURL,
  };
}

const socialProviders = {};

const googleClientId = serverEnv("GOOGLE_CLIENT_ID");
const googleClientSecret = serverEnv("GOOGLE_CLIENT_SECRET");
if (googleClientId && googleClientSecret) {
  socialProviders.google = {
    clientId: googleClientId,
    clientSecret: googleClientSecret,
  };
}

const githubClientId = serverEnv("GITHUB_CLIENT_ID");
const githubClientSecret = serverEnv("GITHUB_CLIENT_SECRET");
if (githubClientId && githubClientSecret) {
  socialProviders.github = {
    clientId: githubClientId,
    clientSecret: githubClientSecret,
  };
}

export const auth = betterAuth({
  database: mongodbAdapter(requireMongoDatabaseHandle(), {
    client: requireMongoClientInstance(),
  }),
  secret: serverEnv("BETTER_AUTH_SECRET", "lerouter-dev-secret-change-before-production"),
  baseURL: authBaseURL(),
  basePath: "/api/auth",
  trustedOrigins: authTrustedOrigins,
  socialProviders,
  emailAndPassword: {
    enabled: false,
  },
  user: {
    additionalFields: userAdditionalFields,
  },
  plugins: [
    magicLink({
      expiresIn: 60 * 60 * 24,
      sendMagicLink: sendMagicLinkEmail,
    }),
    nextCookies(),
  ],
});
