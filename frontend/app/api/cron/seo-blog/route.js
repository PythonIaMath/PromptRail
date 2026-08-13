import { createHash, randomUUID } from "node:crypto";
import { serverEnv } from "../../../lib/serverEnv.js";
import { seedPosts } from "../../../lib/blog.js";
import { requireMongoDatabase } from "../../../lib/mongo.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const outline = ["title", "targetKeyword", "searchIntent", "metaTitle", "metaDescription", "intro", "h1", "h2Headings", "h3Headings", "internalLinks", "externalLinks", "images", "expertInsights", "ctas", "faqs", "conclusion"];
const keywordStrategy = [
  { keyword: "LLM router", cluster: "AI routing", mode: "standard", priority: 100, angle: "A technical buyer's guide to request-level LLM routing." },
  { keyword: "AI model routing vs load balancing", cluster: "AI routing", mode: "comparison", priority: 95, angle: "Separate semantic model selection from infrastructure traffic distribution." },
  { keyword: "best LLM routers for AI agents", cluster: "AI routing", mode: "list", priority: 90, angle: "A criteria-led comparison for agent builders, including when a router is unnecessary." },
  { keyword: "reasoning effort routing", cluster: "Reasoning", mode: "standard", priority: 85, angle: "Explain how to allocate reasoning effort by task difficulty and failure cost." },
  { keyword: "AI API cost management", cluster: "Cost management", mode: "standard", priority: 80, angle: "A budget-aware operating model for long-running agent workloads." },
  { keyword: "PromptRail review", cluster: "PromptRail", mode: "review", priority: 75, angle: "An evidence-based product review covering fit, limitations, privacy boundary, and setup." },
  { keyword: "PromptRail vs OpenRouter Auto", cluster: "PromptRail", mode: "comparison", priority: 70, angle: "Compare reasoning routing with general-purpose provider routing without inventing claims." },
  { keyword: "budget-aware AI routing", cluster: "Cost management", mode: "standard", priority: 65, angle: "Show how remaining budget can influence model selection without hard quality cliffs." },
];

const modeGuidance = {
  standard: "Answer an informational query with a practical framework, examples, implementation details, and FAQs.",
  comparison: "Use explicit evaluation criteria, represent both sides fairly, identify who each option fits, and avoid declaring a winner without evidence.",
  review: "Separate verified product facts, observed limitations, ideal users, non-ideal users, pricing/setup facts, and open questions. Do not invent testimonials.",
  list: "Define inclusion criteria before naming options. Give each option distinct strengths, limitations, and best-fit users. Avoid affiliate-style hype.",
};

function openAiModel() { return serverEnv("SEO_BLOG_MODEL", "gpt-5.6-terra"); }
function normalize(value) { return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(); }
function words(value) { return String(value || "").trim().split(/\s+/).filter(Boolean); }
function topicKey(post) { return normalize(`${post.primaryKeyword || ""} ${post.title || ""}`); }
function postText(post) { return [post.title, post.description, post.excerpt, ...(post.sections || []).flatMap((section) => [section.heading, ...(section.paragraphs || []), ...(section.faqs || []).flatMap((faq) => [faq.question, faq.answer])]), post.conclusion].filter(Boolean).join(" "); }
function overlap(left, right) {
  const a = new Set(normalize(left).split(" ").filter((token) => token.length > 2));
  const b = new Set(normalize(right).split(" ").filter((token) => token.length > 2));
  if (!a.size || !b.size) return 0;
  const intersection = [...a].filter((token) => b.has(token)).length;
  return intersection / new Set([...a, ...b]).size;
}

function responseText(payload) {
  return payload.output_text || payload.output?.flatMap((item) => item.content || []).find((item) => item.type === "output_text")?.text || "";
}

async function callOpenAI({ input, schema, schemaName, tools = [], effort = "medium" }) {
  const apiKey = serverEnv("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY is required for the SEO blog agent.");
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ model: openAiModel(), reasoning: { effort }, tools, store: false, max_output_tokens: 14000, input, text: { format: { type: "json_schema", name: schemaName, strict: true, schema } } }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`OpenAI request failed with ${response.status}: ${payload.error?.message || "unknown error"}`);
  const raw = responseText(payload);
  if (!raw) throw new Error("OpenAI returned no structured output.");
  return { value: JSON.parse(raw), responseId: payload.id || null };
}

const linkSchema = { type: "object", additionalProperties: false, required: ["url", "label", "reason"], properties: { url: { type: "string" }, label: { type: "string" }, reason: { type: "string" } } };
const faqSchema = { type: "object", additionalProperties: false, required: ["question", "answer"], properties: { question: { type: "string" }, answer: { type: "string" } } };
const briefSchema = {
  type: "object", additionalProperties: false,
  required: ["slug", "contentMode", "targetKeyword", "secondaryKeywords", "searchIntent", "workingTitle", "metaTitle", "metaDescription", "angle", "introHook", "serpCompetitors", "answerShape", "contentGaps", "headings", "internalLinks", "externalSources", "imageBriefs", "expertInsightDirections", "ctas", "faqs", "conclusionDirection"],
  properties: {
    slug: { type: "string" }, contentMode: { type: "string", enum: ["standard", "comparison", "review", "list"] }, targetKeyword: { type: "string" }, secondaryKeywords: { type: "array", minItems: 3, items: { type: "string" } }, searchIntent: { type: "string" }, workingTitle: { type: "string" }, metaTitle: { type: "string" }, metaDescription: { type: "string" }, angle: { type: "string" }, introHook: { type: "string" },
    serpCompetitors: { type: "array", minItems: 4, items: { type: "object", additionalProperties: false, required: ["title", "url", "answerShape", "headings", "strengths", "gaps"], properties: { title: { type: "string" }, url: { type: "string" }, answerShape: { type: "string" }, headings: { type: "array", items: { type: "string" } }, strengths: { type: "array", items: { type: "string" } }, gaps: { type: "array", items: { type: "string" } } } } },
    answerShape: { type: "string" }, contentGaps: { type: "array", minItems: 2, items: { type: "string" } },
    headings: { type: "array", minItems: 7, items: { type: "object", additionalProperties: false, required: ["h2", "h3s", "purpose"], properties: { h2: { type: "string" }, h3s: { type: "array", items: { type: "string" } }, purpose: { type: "string" } } } },
    internalLinks: { type: "array", minItems: 2, items: linkSchema }, externalSources: { type: "array", minItems: 3, items: linkSchema }, imageBriefs: { type: "array", minItems: 2, items: { type: "string" } }, expertInsightDirections: { type: "array", minItems: 2, items: { type: "string" } }, ctas: { type: "array", minItems: 2, items: { type: "string" } }, faqs: { type: "array", minItems: 4, items: faqSchema }, conclusionDirection: { type: "string" },
  },
};

const brandPersonaSchema = {
  type: "object", additionalProperties: false,
  required: ["brand", "oneLiner", "audiences", "positioning", "voice", "preferredTerms", "avoidTerms", "verifiedClaims", "forbiddenClaims", "proofPoints", "primaryCtas", "topicClusters"],
  properties: {
    brand: { type: "string" }, oneLiner: { type: "string" }, audiences: { type: "array", minItems: 2, items: { type: "string" } }, positioning: { type: "array", minItems: 2, items: { type: "string" } }, voice: { type: "array", minItems: 3, items: { type: "string" } }, preferredTerms: { type: "array", items: { type: "string" } }, avoidTerms: { type: "array", items: { type: "string" } }, verifiedClaims: { type: "array", items: { type: "string" } }, forbiddenClaims: { type: "array", items: { type: "string" } }, proofPoints: { type: "array", items: { type: "string" } }, primaryCtas: { type: "array", items: { type: "string" } }, topicClusters: { type: "array", items: { type: "string" } },
  },
};

const sectionSchema = { type: "object", additionalProperties: false, required: ["heading", "paragraphs", "faqs"], properties: { heading: { type: "string" }, paragraphs: { type: "array", minItems: 2, items: { type: "string" } }, faqs: { type: "array", items: faqSchema } } };
const articleSchema = {
  type: "object", additionalProperties: false,
  required: ["slug", "title", "metaTitle", "description", "primaryKeyword", "secondaryKeywords", "intent", "category", "eyebrow", "excerpt", "sections", "references", "internalLinks", "imageBriefs", "expertInsights", "conclusion"],
  properties: {
    slug: { type: "string" }, title: { type: "string" }, metaTitle: { type: "string" }, description: { type: "string" }, primaryKeyword: { type: "string" }, secondaryKeywords: { type: "array", items: { type: "string" } }, intent: { type: "string" }, category: { type: "string" }, eyebrow: { type: "string" }, excerpt: { type: "string" },
    sections: { type: "array", minItems: 7, items: sectionSchema }, references: { type: "array", minItems: 3, items: linkSchema }, internalLinks: { type: "array", minItems: 2, items: linkSchema }, imageBriefs: { type: "array", minItems: 2, items: { type: "string" } }, expertInsights: { type: "array", minItems: 2, items: { type: "string" } }, conclusion: { type: "string" },
  },
};

const editedSchema = {
  type: "object", additionalProperties: false, required: ["article", "quality"],
  properties: {
    article: articleSchema,
    quality: { type: "object", additionalProperties: false, required: ["accuracy", "originality", "searchIntent", "structure", "usefulness", "notes"], properties: { accuracy: { type: "integer", minimum: 1, maximum: 10 }, originality: { type: "integer", minimum: 1, maximum: 10 }, searchIntent: { type: "integer", minimum: 1, maximum: 10 }, structure: { type: "integer", minimum: 1, maximum: 10 }, usefulness: { type: "integer", minimum: 1, maximum: 10 }, notes: { type: "array", items: { type: "string" } } } },
  },
};

function historySummary(posts, runs) {
  return [...posts.map((post) => ({ slug: post.slug, title: post.title, targetKeyword: post.primaryKeyword, description: post.description, status: post.status || "published" })), ...runs.filter((run) => run.slug || run.targetKeyword).map((run) => ({ slug: run.slug, title: run.title, targetKeyword: run.targetKeyword, status: run.status }))].slice(-100);
}

function publicSiteUrl() {
  return serverEnv("NEXT_PUBLIC_APP_URL", "https://www.promptrail.ai").replace(/\/$/, "");
}

function pageText(html) {
  return String(html || "")
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

async function ingestBrandPersona(database) {
  const collection = database.collection(serverEnv("LEROUTER_BRAND_PERSONA_COLLECTION", "blog_brand_personas"));
  const existing = await collection.findOne({ brand: "PromptRail" });
  const maxAgeDays = Number(serverEnv("SEO_BRAND_PERSONA_MAX_AGE_DAYS", "30"));
  if (existing?.updatedAt && Date.now() - new Date(existing.updatedAt).getTime() < maxAgeDays * 86400000) return existing.persona;

  const baseUrl = publicSiteUrl();
  const paths = ["/", "/plugins", "/plugins/privacy", "/blog", "/privacy"];
  const pages = await Promise.all(paths.map(async (path) => {
    const response = await fetch(`${baseUrl}${path}`, { headers: { "User-Agent": "PromptRail-SEO-Agent/1.0" }, cache: "no-store" });
    if (!response.ok) throw new Error(`Brand ingestion failed for ${path} with ${response.status}.`);
    return { path, text: pageText(await response.text()).slice(0, 18000) };
  }));
  const generated = await callOpenAI({
    effort: "medium", schema: brandPersonaSchema, schemaName: "promptrail_brand_persona",
    input: [
      { role: "system", content: [{ type: "input_text", text: "Build a factual Brand Persona from the supplied PromptRail pages. Capture the product's actual positioning, audience, vocabulary, tone, proof points, and claims boundary. A verified claim must appear in the supplied site text. Put unsupported performance, customer, ranking, savings, and market-leadership claims in forbiddenClaims. Return only JSON." }] },
      { role: "user", content: [{ type: "input_text", text: JSON.stringify({ pages }) }] },
    ],
  });
  const version = createHash("sha256").update(JSON.stringify(pages)).digest("hex");
  await collection.updateOne({ brand: "PromptRail" }, { $set: { brand: "PromptRail", persona: generated.value, sourcePages: paths, version, model: openAiModel(), responseId: generated.responseId, updatedAt: new Date() }, $setOnInsert: { createdAt: new Date() } }, { upsert: true });
  return generated.value;
}

async function seedKeywordQueue(collection) {
  await collection.createIndex({ keywordKey: 1 }, { unique: true });
  await collection.bulkWrite(keywordStrategy.map((item) => ({ updateOne: { filter: { keywordKey: normalize(item.keyword) }, update: { $setOnInsert: { ...item, keywordKey: normalize(item.keyword), status: "queued", source: "repo_keyword_strategy", createdAt: new Date() } }, upsert: true } })));
}

async function claimKeyword(collection, history, runId) {
  const candidates = await collection.find({ status: "queued" }).sort({ priority: -1, createdAt: 1 }).toArray();
  for (const candidate of candidates) {
    const alreadyCovered = history.some((post) => normalize(post.targetKeyword) === candidate.keywordKey);
    if (alreadyCovered) {
      await collection.updateOne({ _id: candidate._id, status: "queued" }, { $set: { status: "skipped_duplicate", completedAt: new Date(), reason: "The keyword already exists in publication history." } });
      continue;
    }
    const claimed = await collection.findOneAndUpdate({ _id: candidate._id, status: "queued" }, { $set: { status: "researching", claimedAt: new Date(), claimedBy: runId } }, { returnDocument: "after" });
    if (claimed) return claimed;
  }
  throw new Error("Keyword queue is empty. Add or reprioritize keywords before the next cron run.");
}

async function researchBrief(keywordPlan, history, currentLinks, brandPersona) {
  return callOpenAI({
    tools: [{ type: "web_search" }], effort: "medium", schema: briefSchema, schemaName: "seo_research_brief",
    input: [
      { role: "system", content: [{ type: "input_text", text: "You are PromptRail's SERP briefing strategist. Search the exact target keyword and inspect the pages that currently rank. Record at least four real competitors, their semantic heading structure, answer shape, strengths, and gaps. Build the article around the answer shape search engines are rewarding, then add an original PromptRail-relevant angle. Follow the requested content mode. Use only sources you actually opened. Never fabricate headings, quotes, rankings, traffic estimates, product features, or customer results. Return only JSON." }] },
      { role: "user", content: [{ type: "input_text", text: JSON.stringify({ keywordPlan, modeInstructions: modeGuidance[keywordPlan.mode], brandPersona, perfectOutlineSteps: outline, publicationHistory: history, allowedInternalLinks: currentLinks }) }] },
    ],
  });
}

async function writeArticle(brief, history, brandPersona) {
  return callOpenAI({
    effort: "high", schema: articleSchema, schemaName: "seo_blog_draft",
    input: [
      { role: "system", content: [{ type: "input_text", text: "You are PromptRail's senior technical writer. Write a deeply useful 1,600-2,200 word article from the approved research brief. Satisfy the search intent quickly, use concrete examples, explain tradeoffs, and keep one clear H1 represented by title. Do not keyword-stuff. Do not invent quotes or claims. Paraphrase sources and preserve their URLs in references. Every internal link must be selected from the allowed links in the brief. Return only JSON." }] },
      { role: "user", content: [{ type: "input_text", text: JSON.stringify({ brief, brandPersona, publicationHistory: history }) }] },
    ],
  });
}

async function editArticle(brief, draft, history, brandPersona) {
  return callOpenAI({
    effort: "high", schema: editedSchema, schemaName: "seo_blog_final",
    input: [
      { role: "system", content: [{ type: "input_text", text: "You are the final PromptRail editor. Rewrite weak or generic passages. Remove repeated ideas, unsupported claims, fake quotations, keyword stuffing, and filler. Preserve verified sources. Make the article specific enough that an experienced developer learns something useful. Score the final result honestly. Return only JSON." }] },
      { role: "user", content: [{ type: "input_text", text: JSON.stringify({ brief, draft, brandPersona, publicationHistory: history, minimumScores: { accuracy: 8, originality: 8, searchIntent: 8, structure: 8, usefulness: 8 } }) }] },
    ],
  });
}

function assertUnique(article, history) {
  const duplicate = history.find((post) => normalize(post.slug) === normalize(article.slug) || normalize(post.title) === normalize(article.title) || normalize(post.targetKeyword) === normalize(article.primaryKeyword) || overlap(post.title, article.title) >= 0.68);
  if (duplicate) throw new Error(`Duplicate topic rejected: ${duplicate.slug || duplicate.title || duplicate.targetKeyword}.`);
}

function validateQuality(article, quality, allowedInternalLinks) {
  const minimumWords = Number(serverEnv("SEO_BLOG_MIN_WORDS", "1400"));
  const wordCount = words(postText(article)).length;
  const faqCount = article.sections.flatMap((section) => section.faqs || []).length;
  const scores = [quality.accuracy, quality.originality, quality.searchIntent, quality.structure, quality.usefulness];
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(article.slug)) throw new Error("Quality gate failed: invalid slug.");
  if (wordCount < minimumWords) throw new Error(`Quality gate failed: ${wordCount} words is below ${minimumWords}.`);
  if (article.sections.length < 7 || faqCount < 4 || article.references.length < 3 || article.internalLinks.length < 2) throw new Error("Quality gate failed: outline coverage is incomplete.");
  if (scores.some((score) => score < 8)) throw new Error(`Quality gate failed: editor scores ${scores.join("/")}.`);
  if (article.title.length < 35 || article.title.length > 90) throw new Error("Quality gate failed: title length is outside 35-90 characters.");
  if (article.description.length < 110 || article.description.length > 180) throw new Error("Quality gate failed: meta description length is outside 110-180 characters.");
  const referenceUrls = article.references.map((reference) => reference.url);
  if (new Set(referenceUrls).size !== referenceUrls.length || referenceUrls.some((url) => { try { return new URL(url).protocol !== "https:"; } catch { return true; } })) throw new Error("Quality gate failed: external sources must be distinct HTTPS URLs.");
  if (article.internalLinks.some((link) => !allowedInternalLinks.includes(link.url))) throw new Error("Quality gate failed: article invented an internal link outside the site catalog.");
  return { wordCount, faqCount, averageScore: scores.reduce((sum, score) => sum + score, 0) / scores.length };
}

export async function GET(request) {
  const expected = serverEnv("CRON_SECRET");
  if (!expected || request.headers.get("authorization") !== `Bearer ${expected}`) return new Response("Unauthorized", { status: 401 });

  const database = await requireMongoDatabase();
  const posts = database.collection(serverEnv("LEROUTER_BLOG_POST_COLLECTION", "blog_posts"));
  const runs = database.collection(serverEnv("LEROUTER_BLOG_HISTORY_COLLECTION", "blog_agent_history"));
  const keywords = database.collection(serverEnv("LEROUTER_BLOG_KEYWORD_COLLECTION", "blog_keyword_queue"));
  const runId = randomUUID();
  const startedAt = new Date();

  await Promise.all([posts.createIndex({ slug: 1 }, { unique: true }), posts.createIndex({ topicKey: 1 }, { unique: true, sparse: true }), runs.createIndex({ runId: 1 }, { unique: true }), seedKeywordQueue(keywords)]);
  const [storedPosts, previousRuns] = await Promise.all([posts.find({}, { projection: { slug: 1, title: 1, primaryKeyword: 1, description: 1, status: 1 } }).sort({ createdAt: 1 }).toArray(), runs.find({}, { projection: { slug: 1, title: 1, targetKeyword: 1, status: 1 } }).sort({ startedAt: 1 }).toArray()]);
  const history = historySummary([...seedPosts, ...storedPosts], previousRuns);
  const currentLinks = ["/", "/blog", "/plugins", "/plugins/onboarding", "/plugins/privacy", "/privacy", ...history.filter((post) => post.slug).map((post) => `/blog/${post.slug}`)];

  await runs.insertOne({ runId, status: "ingesting_brand", startedAt, model: openAiModel(), historySize: history.length });
  let keywordPlan = null;
  try {
    const brandPersona = await ingestBrandPersona(database);
    keywordPlan = await claimKeyword(keywords, history, runId);
    await runs.updateOne({ runId }, { $set: { status: "researching", keywordPlanId: String(keywordPlan._id), targetKeyword: keywordPlan.keyword, contentMode: keywordPlan.mode } });
    const briefResult = await researchBrief(keywordPlan, history, currentLinks, brandPersona);
    await runs.updateOne({ runId }, { $set: { status: "writing", slug: briefResult.value.slug, targetKeyword: briefResult.value.targetKeyword, researchResponseId: briefResult.responseId, sources: briefResult.value.externalSources, serpCompetitors: briefResult.value.serpCompetitors } });
    assertUnique({ slug: briefResult.value.slug, title: briefResult.value.workingTitle, primaryKeyword: briefResult.value.targetKeyword }, history);

    const draftResult = await writeArticle(briefResult.value, history, brandPersona);
    const editedResult = await editArticle(briefResult.value, draftResult.value, history, brandPersona);
    const article = editedResult.value.article;
    assertUnique(article, history);
    const metrics = validateQuality(article, editedResult.value.quality, currentLinks);
    const publishNow = new URL(request.url).searchParams.get("publish") === "1";
    const status = publishNow || serverEnv("SEO_BLOG_AUTO_PUBLISH", "1") === "1" ? "published" : "draft";
    const now = new Date();
    const document = { ...article, contentMode: briefResult.value.contentMode, status, topicKey: topicKey(article), outline, readTime: `${Math.max(1, Math.ceil(metrics.wordCount / 220))} min read`, generatedAt: now, publishedAt: status === "published" ? now : null, createdAt: now, updatedAt: now, source: "openai-seo-blog-agent", model: openAiModel(), quality: editedResult.value.quality, metrics, agentRunId: runId };
    await posts.insertOne(document);
    await runs.updateOne({ runId }, { $set: { status, completedAt: new Date(), slug: article.slug, title: article.title, targetKeyword: article.primaryKeyword, brief: briefResult.value, quality: editedResult.value.quality, metrics, draftResponseId: draftResult.responseId, editorResponseId: editedResult.responseId } });
    await keywords.updateOne({ _id: keywordPlan._id }, { $set: { status, completedAt: new Date(), runId, slug: article.slug, title: article.title } });
    return Response.json({ ok: true, runId, status, slug: article.slug, title: article.title, model: openAiModel(), metrics });
  } catch (error) {
    await runs.updateOne({ runId }, { $set: { status: "failed", completedAt: new Date(), error: String(error?.message || error).slice(0, 1000) } });
    if (keywordPlan?._id) await keywords.updateOne({ _id: keywordPlan._id }, { $set: { status: "failed", completedAt: new Date(), runId, error: String(error?.message || error).slice(0, 1000) } });
    return Response.json({ ok: false, runId, error: String(error?.message || error) }, { status: 500 });
  }
}
