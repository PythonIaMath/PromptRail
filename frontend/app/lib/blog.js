import { requireMongoDatabase } from "./mongo.js";

const seedPosts = [
  {
    slug: "what-is-promptrail",
    title: "What Is PromptRail? A Practical Guide to AI Reasoning Routing",
    description: "PromptRail routes AI agent prompts to the right reasoning level so simple work stays fast and difficult work gets the thinking it needs.",
    primaryKeyword: "PromptRail",
    intent: "Navigational and informational",
    category: "PromptRail",
    publishedAt: "2026-07-21",
    readTime: "7 min read",
    eyebrow: "PromptRail explained",
    excerpt: "AI agents should not spend maximum reasoning on every prompt. PromptRail makes that tradeoff explicit, automatic, and explainable.",
    sections: [
      { heading: "PromptRail in one sentence", paragraphs: ["PromptRail is an automatic reasoning router for AI coding agents. It classifies the latest prompt, selects an effort grade, and keeps the request on the user's existing first-party subscription path."] },
      { heading: "Why reasoning routing matters", paragraphs: ["A one-line rename and a cross-tenant authorization bug do not need the same amount of reasoning. Sending every request to the highest setting adds latency and cost without adding useful quality.", "A router should make the difference visible: low effort for routine work, higher effort for ambiguity, architecture, debugging, and security-sensitive tasks."] },
      { heading: "How the PromptRail flow works", paragraphs: ["The local plugin observes the current user prompt, sends only the minimum classification input to the configured PromptRail grader, receives a grade, and applies that grade to the outgoing request. Provider credentials and full transcripts stay on the user's machine."] },
      { heading: "PromptRail for Codex and Claude Code", paragraphs: ["PromptRail supports separate integrations for Codex and Claude Code. Each integration maps its grade contract to the native reasoning or effort controls exposed by that agent."] },
      { heading: "Frequently asked questions", faqs: [{ question: "Does PromptRail replace my model subscription?", answer: "No. PromptRail routes requests while preserving the supported first-party subscription flow." }, { question: "Does PromptRail store prompts?", answer: "The published privacy boundary says prompts are processed transiently for routing and are not retained by the proxy or grader." }] },
    ],
  },
  {
    slug: "ai-model-routing-guide",
    title: "AI Model Routing: How to Choose the Right Model for Every Agent Task",
    description: "A hands-on guide to AI model routing, reasoning effort, quality, latency, and budget-aware decisions for agent workflows.",
    primaryKeyword: "AI model routing",
    intent: "Informational",
    category: "Routing",
    publishedAt: "2026-07-21",
    readTime: "10 min read",
    eyebrow: "Routing field guide",
    excerpt: "The best model for an agent is not a permanent choice. It depends on the task, the failure cost, the context, and the budget left in the month.",
    sections: [
      { heading: "What AI model routing actually means", paragraphs: ["AI model routing is the policy layer that chooses which model, provider, or reasoning setting should handle a request. It sits between an agent and the model APIs, turning a vague 'auto' decision into a measurable policy."] },
      { heading: "The four signals a useful router needs", paragraphs: ["A practical router considers task difficulty, expected output length, quality requirements, and current budget. Latency and provider availability matter too, but they should not silently override correctness on high-risk work."] },
      { heading: "Static rules versus adaptive routing", paragraphs: ["Static rules are easy to explain but brittle. A rule such as 'use the cheap model for short prompts' misses short prompts that contain a security incident or a production migration.", "Adaptive routing uses classification and feedback to choose a route per request. The important constraint is visibility: when classification fails, the request should stop visibly instead of quietly selecting an arbitrary fallback."] },
      { heading: "A routing policy you can audit", paragraphs: ["Start with a small number of grades. Measure quality, latency, and spend by grade. Review misroutes. Then expand the policy only when the data says the extra complexity is useful."] },
      { heading: "FAQs about AI routing", faqs: [{ question: "Is model routing the same as load balancing?", answer: "No. Load balancing spreads traffic. Model routing chooses a response strategy based on the request and constraints." }, { question: "Should every request use the strongest model?", answer: "Only if latency and spend do not matter. Most agent workloads contain a mix of routine and difficult tasks." }] },
    ],
  },
  {
    slug: "ai-api-cost-management",
    title: "AI API Cost Management: A Budget-Aware Playbook for Agent Workloads",
    description: "Learn how to manage AI API prices without crippling your agents, using budgets, request weights, routing policies, and transparent measurement.",
    primaryKeyword: "AI API cost management",
    intent: "Informational and commercial investigation",
    category: "Cost management",
    publishedAt: "2026-07-21",
    readTime: "9 min read",
    eyebrow: "Spend without guesswork",
    excerpt: "A hard cap tells you when to stop. A budget-aware router helps you decide how to spend the budget before you get there.",
    sections: [
      { heading: "Why AI API prices are hard to manage", paragraphs: ["Agent requests vary widely. A short tool call, a long code review, and a multi-step debugging session can consume very different amounts of input, output, and reasoning work.", "A single monthly dollar cap is useful protection, but it does not tell an agent how to preserve quality as usage changes."] },
      { heading: "The difference between a cap and a policy", paragraphs: ["A cap is a stop condition. A policy is a set of decisions made before the stop condition: which requests can use a cheaper model, which tasks deserve more reasoning, and how much risk is acceptable."] },
      { heading: "Track weighted requests, not only dollars", paragraphs: ["Cost is necessary but incomplete. Track request weights that reflect expected difficulty and output demand, then compare predicted weight with actual usage. This creates a useful control signal for the rest of the billing cycle."] },
      { heading: "A five-step cost management loop", paragraphs: ["Set a monthly budget. Classify each request. Rank eligible candidates. Apply a shadow price to remaining budget. Review outcomes and adjust the policy. The loop should be explainable enough that a user can see why a request took a particular route."] },
      { heading: "FAQs about AI API pricing", faqs: [{ question: "Can cheaper models always replace expensive models?", answer: "No. Cost management works best when it preserves stronger models for requests where failure is expensive." }, { question: "What is the fastest first step?", answer: "Instrument spend and request metadata before changing routes. You need a baseline to know whether a policy improved quality per dollar." }] },
    ],
  },
];

function normalizePost(post) {
  if (!post) return null;
  return {
    ...post,
    id: post.id || String(post._id || ""),
    publishedAt: new Date(post.publishedAt || post.createdAt || Date.now()).toISOString(),
    sections: Array.isArray(post.sections) ? post.sections : [],
  };
}

export function getSeedPosts() {
  return seedPosts.map(normalizePost);
}

export async function getPublishedPosts() {
  try {
    const database = await requireMongoDatabase();
    const posts = await database.collection("blog_posts")
      .find({ status: "published" })
      .sort({ publishedAt: -1 })
      .toArray();
    const merged = new Map(getSeedPosts().map((post) => [post.slug, post]));
    posts.map(normalizePost).forEach((post) => merged.set(post.slug, post));
    return [...merged.values()].sort((left, right) => new Date(right.publishedAt) - new Date(left.publishedAt));
  } catch {
    return getSeedPosts();
  }
}

export async function getPublishedPost(slug) {
  const posts = await getPublishedPosts();
  return posts.find((post) => post.slug === slug) || null;
}

export { seedPosts };
