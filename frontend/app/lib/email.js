import fs from "node:fs";
import path from "node:path";
import nodemailer from "nodemailer";
import { serverEnv } from "./serverEnv.js";

const defaultEmailLogPath = path.join(process.cwd(), ".auth-email-links.log");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function sendWithResend({ from, to, subject, text, html }) {
  const apiKey = serverEnv("RESEND_API_KEY");
  if (!apiKey) {
    return null;
  }

  const response = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from,
      to: [to],
      subject,
      text,
      html,
    }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) {
    throw new Error(payload.error?.message || payload.message || "Resend email delivery failed.");
  }

  return payload;
}

function writeVerificationLog({ user, subject, url, deliveredBy, error }) {
  const logPath = serverEnv("LEROUTER_EMAIL_LOG_PATH", defaultEmailLogPath);
  fs.appendFileSync(
    logPath,
    `${JSON.stringify({
      ts: new Date().toISOString(),
      to: user.email,
      subject,
      url,
      deliveredBy,
      error,
    })}\n`,
  );
}

function smtpTransport() {
  const host = serverEnv("SMTP_HOST");
  if (!host) {
    return null;
  }

  return nodemailer.createTransport({
    host,
    port: Number(serverEnv("SMTP_PORT", "587")),
    secure: serverEnv("SMTP_SECURE", "false") === "true",
    auth: serverEnv("SMTP_USER")
      ? {
        user: serverEnv("SMTP_USER"),
        pass: serverEnv("SMTP_PASS"),
      }
      : undefined,
  });
}

export async function sendMagicLinkEmail({ email, url }) {
  if (!url) {
    throw new Error("Magic link URL is missing.");
  }

  const user = { email };
  const from = serverEnv(
    "RESEND_FROM",
    serverEnv("SMTP_FROM", "PromptRail <onboarding@resend.dev>"),
  );
  const subject = "Sign in to PromptRail";
  const text = `Sign in to PromptRail by opening this link:\n\n${url}\n\nThis link expires in 24 hours.`;
  const html = `
    <div style="font-family: Inter, Arial, sans-serif; line-height: 1.55; color: #172331;">
      <h1 style="font-size: 20px; margin: 0 0 12px;">Sign in to PromptRail</h1>
      <p style="margin: 0 0 18px;">Use this secure link to continue to your PromptRail account.</p>
      <p style="margin: 0 0 22px;">
        <a href="${escapeHtml(url)}" style="display: inline-block; padding: 10px 14px; border-radius: 7px; background: #172331; color: #ffffff; text-decoration: none; font-weight: 700;">
          Continue to PromptRail
        </a>
      </p>
      <p style="margin: 0 0 18px; color: #50687a; font-size: 13px;">
        If the button does not work, copy and paste this link into your browser:
      </p>
      <p style="margin: 0 0 22px; word-break: break-all; font-size: 13px;">
        <a href="${escapeHtml(url)}" style="color: #1264a3;">${escapeHtml(url)}</a>
      </p>
      <p style="margin: 0; color: #50687a; font-size: 13px;">This link expires in 24 hours and can only be used once. If you did not request it, you can ignore this email.</p>
    </div>
  `.trim();
  try {
    const resendResult = await sendWithResend({
      from,
      to: user.email,
      subject,
      text,
      html,
    });

    if (resendResult) {
      if (process.env.NODE_ENV !== "production") {
        writeVerificationLog({
          user,
          subject,
          url,
          deliveredBy: "resend",
          error: resendResult.id ? `resend_id:${resendResult.id}` : undefined,
        });
      }
      return;
    }
  } catch (error) {
    writeVerificationLog({
      user,
      subject,
      url,
      deliveredBy: "resend-failed",
      error: error?.message || "Resend email delivery failed.",
    });
    throw error;
  }

  const transport = smtpTransport();

  if (transport) {
    try {
      await transport.sendMail({
        from,
        to: user.email,
        subject,
        text,
        html,
      });
    } catch (error) {
      throw error;
    }
    return;
  }

  if (process.env.NODE_ENV === "production") {
    throw new Error("Email delivery is not configured.");
  }

  writeVerificationLog({
    user,
    subject,
    url,
    deliveredBy: "local-log",
  });
}
