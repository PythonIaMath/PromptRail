# PromptRail frontend

This directory is the canonical home of the PromptRail website and web application. The implementation was migrated from the ZRouter repository and reconciled with the latest ZRouter `origin/main` frontend fixes plus the active landing-page work. New frontend work should be made here, not in ZRouter.

## Stack

- Next.js 16 App Router
- React 19
- Better Auth with MongoDB
- Stripe billing and subscriptions
- GSAP, Three.js, and Recharts
- Vercel route handlers and scheduled SEO publishing

## Local development

Node.js 20 or newer is required.

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

The development server listens on <http://localhost:3001>. Authentication and account-backed routes require MongoDB. Public landing, documentation, blog seed content, legal, and support pages can be developed without configuring every optional service.

## Validation

```bash
npm test
npm run build
npm run audit:production
```

`npm run check` runs the test suite followed by a production build. The test suite covers authentication redirects, bounded request bodies, installation flows, plugin access and checkout, Infinite routing authority, credential envelopes, server environment loading, privacy copy, and the bundled PromptRail CLI.

## Application areas

| Area | Routes |
| --- | --- |
| Marketing | `/`, `/plugins`, `/blog`, `/blog/[slug]` |
| Documentation | `/docs`, `/docs/sdk`, `/connect` |
| Account | `/login`, `/check-email`, `/onboarding`, `/device` |
| Product | `/dashboard`, `/dashboard/analytics`, `/dashboard/api-keys`, `/dashboard/credit`, `/dashboard/settings`, `/dashboard/setup` |
| Plugin setup | `/plugins/onboarding`, `/plugins/privacy`, `/install` |
| Public information | `/privacy`, `/terms`, `/support` |
| Internal tooling | `/barred-dashboard`, `/training-dashboard`, `/sample-dashboard`, `/internal/infinite-routing` |

The complete set of web APIs lives under `app/api`. It includes authentication, billing, API keys, setup links, trace-source connection, plugin access, Infinite routing, usage, waitlist, and SEO automation endpoints.

## Documentation ownership

The designed in-product SDK guide is served at `/docs/sdk`. The full SDK reference remains in the repository-level `docs/` directory and is deployed by MkDocs to <https://pythoniamath.github.io/PromptRail/>. The website links to that canonical reference rather than copying every long-form guide into React components.

## Bundled CLI

The install endpoint packages the CLI from `packages/promptrail-cli` during `predev` and `prebuild`:

```text
packages/promptrail-cli
        ↓
scripts/package-promptrail-cli.mjs
        ↓
app/lib/promptrail-cli  # generated and ignored
        ↓
/api/cli/package and /install
```

Edit the package source under `packages/promptrail-cli`. Do not edit the generated `app/lib/promptrail-cli` directory.

## Environment variables

Copy `.env.example` to `.env.local` and replace only the services needed for the flow you are developing. The main groups are:

- application URLs and Better Auth
- MongoDB application state
- email and OAuth providers
- PromptRail routing service URLs
- Stripe billing
- Infinite routing control-plane settings
- blog and SEO automation
- optional model-provider credentials

The existing `LEROUTER_*` names are retained where they are part of the deployed router API and data contracts. Their presence does not mean ZRouter still owns this frontend.

Never commit `.env.local`, local databases, generated CLI output, `.next`, or `node_modules`.

## Deployment

For Vercel, configure the project Root Directory as `frontend`. `vercel.json` contains the SEO cron schedule. Production deployments should provide MongoDB, auth, email, Stripe, routing-service, and cron secrets through the deployment platform.

The repository-level GitHub Pages workflow continues to deploy the MkDocs SDK reference independently from the Next.js application.
