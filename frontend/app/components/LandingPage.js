"use client";

import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

gsap.registerPlugin(ScrollTrigger);

const BASE_SIGNUP_BUTTON_PATH = [
  "M 20 6",
  "C 68 6 164 6 212 6",
  "C 219.73 6 226 12.27 226 20",
  "C 226 25 226 39 226 44",
  "C 226 51.73 219.73 58 212 58",
  "C 164 58 68 58 20 58",
  "C 12.27 58 6 51.73 6 44",
  "C 6 39 6 25 6 20",
  "C 6 12.27 12.27 6 20 6",
].join(" ");

function useHeroBackgroundMotion() {
  useEffect(() => {
    const page = document.querySelector(".landing-page");
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    if (!page || reduceMotion.matches) {
      return undefined;
    }

    let frame = 0;

    const update = () => {
      frame = 0;
      const progress = Math.min(window.scrollY / window.innerHeight, 1.4);
      page.style.setProperty("--hero-bg-scale", String(1.12 + progress * 0.24));
      page.style.setProperty("--hero-bg-rotate", `${-14 + progress * 18}deg`);
    };

    const requestUpdate = () => {
      if (frame) {
        return;
      }

      frame = window.requestAnimationFrame(update);
    };

    update();
    window.addEventListener("scroll", requestUpdate, { passive: true });
    window.addEventListener("resize", requestUpdate);

    return () => {
      window.removeEventListener("scroll", requestUpdate);
      window.removeEventListener("resize", requestUpdate);
      page.style.removeProperty("--hero-bg-scale");
      page.style.removeProperty("--hero-bg-rotate");
      if (frame) {
        window.cancelAnimationFrame(frame);
      }
    };
  }, []);
}

function HeroBackgroundImage() {
  return (
    <img
      className="hero-bg-image"
      src="/Adams_Hand.png"
      alt=""
      aria-hidden="true"
      draggable="false"
    />
  );
}

function makeSignupButtonPath(shape) {
  const isAtRest = ["top", "right", "bottom", "left"].every((key) => {
    return Math.abs(shape[key]) < 0.001;
  });

  if (isAtRest) {
    return `${BASE_SIGNUP_BUTTON_PATH} Z`;
  }

  const top = 6;
  const right = 226;
  const bottom = 58;
  const left = 6;
  const radius = 14;
  const handle = radius * 0.55228475;

  const topY = top + shape.top;
  const rightX = right + shape.right;
  const bottomY = bottom + shape.bottom;
  const leftX = left + shape.left;
  const topLeft = {
    x: 20 + shape.left * 0.9,
    y: top + shape.top * 0.96,
  };
  const topRight = {
    x: 212 + shape.right * 0.9,
    y: top + shape.top * 0.96,
  };
  const rightTop = {
    x: right + shape.right * 0.96,
    y: 20 + shape.top * 0.9,
  };
  const rightBottom = {
    x: right + shape.right * 0.96,
    y: 44 + shape.bottom * 0.9,
  };
  const bottomRight = {
    x: 212 + shape.right * 0.9,
    y: bottom + shape.bottom * 0.96,
  };
  const bottomLeft = {
    x: 20 + shape.left * 0.9,
    y: bottom + shape.bottom * 0.96,
  };
  const leftBottom = {
    x: left + shape.left * 0.96,
    y: 44 + shape.bottom * 0.9,
  };
  const leftTop = {
    x: left + shape.left * 0.96,
    y: 20 + shape.top * 0.9,
  };

  const p = (value) => value.toFixed(2);

  return [
    `M ${p(topLeft.x)} ${p(topLeft.y)}`,
    `C ${p(68 + shape.left * 0.18)} ${p(topY)} ${p(164 + shape.right * 0.18)} ${p(topY)} ${p(topRight.x)} ${p(topRight.y)}`,
    `C ${p(topRight.x + handle)} ${p(topRight.y)} ${p(rightX)} ${p(rightTop.y - handle)} ${p(rightTop.x)} ${p(rightTop.y)}`,
    `C ${p(rightX)} ${p(25 + shape.top * 0.22)} ${p(rightX)} ${p(39 + shape.bottom * 0.22)} ${p(rightBottom.x)} ${p(rightBottom.y)}`,
    `C ${p(rightX)} ${p(rightBottom.y + handle)} ${p(bottomRight.x + handle)} ${p(bottomRight.y)} ${p(bottomRight.x)} ${p(bottomRight.y)}`,
    `C ${p(164 + shape.right * 0.18)} ${p(bottomY)} ${p(68 + shape.left * 0.18)} ${p(bottomY)} ${p(bottomLeft.x)} ${p(bottomLeft.y)}`,
    `C ${p(bottomLeft.x - handle)} ${p(bottomLeft.y)} ${p(leftX)} ${p(leftBottom.y + handle)} ${p(leftBottom.x)} ${p(leftBottom.y)}`,
    `C ${p(leftX)} ${p(39 + shape.bottom * 0.22)} ${p(leftX)} ${p(25 + shape.top * 0.22)} ${p(leftTop.x)} ${p(leftTop.y)}`,
    `C ${p(leftX)} ${p(leftTop.y - handle)} ${p(topLeft.x - handle)} ${p(topLeft.y)} ${p(topLeft.x)} ${p(topLeft.y)}`,
    "Z",
  ].join(" ");
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function useLiquidSignupButton(buttonRef, pathRef) {
  useEffect(() => {
    const button = buttonRef.current;
    const path = pathRef.current;

    if (!button || !path) {
      return undefined;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const shape = { top: 0, right: 0, bottom: 0, left: 0 };
    let isHovering = false;

    const render = () => {
      path.setAttribute("d", makeSignupButtonPath(shape));
    };

    render();

    if (reduceMotion.matches) {
      return undefined;
    }

    const onPointerEnter = (event) => {
      isHovering = true;
      onPointerMove(event);
    };

    const onPointerMove = (event) => {
      if (!isHovering) {
        return;
      }

      const rect = button.getBoundingClientRect();
      const x = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      const y = clamp((event.clientY - rect.top) / rect.height, 0, 1);
      const centerPull = Math.max(0, 1 - Math.hypot(x - 0.5, y - 0.5) * 1.55);
      const edgePull = 1 + centerPull * 0.76;

      gsap.to(shape, {
        top: -clamp((1 - y) * 7.2 * edgePull, 1.8, 10.5),
        right: clamp(x * 6.2 * edgePull, 1.5, 9.2),
        bottom: clamp(y * 7.2 * edgePull, 1.8, 10.5),
        left: -clamp((1 - x) * 6.2 * edgePull, 1.5, 9.2),
        duration: 0.24,
        ease: "power3.out",
        overwrite: "auto",
      });
    };

    const onPointerLeave = () => {
      isHovering = false;
      gsap.to(shape, {
        top: 0,
        right: 0,
        bottom: 0,
        left: 0,
        duration: 0.32,
        ease: "power3.out",
        onComplete: render,
        overwrite: "auto",
      });
    };

    gsap.ticker.add(render);
    button.addEventListener("pointerenter", onPointerEnter);
    button.addEventListener("pointermove", onPointerMove);
    button.addEventListener("pointerleave", onPointerLeave);

    return () => {
      button.removeEventListener("pointerenter", onPointerEnter);
      button.removeEventListener("pointermove", onPointerMove);
      button.removeEventListener("pointerleave", onPointerLeave);
      gsap.ticker.remove(render);
      gsap.killTweensOf(shape);
    };
  }, [buttonRef, pathRef]);
}

function useSavingsCounter(sectionRef, counterRef) {
  useLayoutEffect(() => {
    const section = sectionRef.current;
    const counter = counterRef.current;

    if (!section || !counter) {
      return undefined;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const value = { amount: 47 };
    counter.textContent = reduceMotion.matches ? "72%" : "47%";

    if (reduceMotion.matches) {
      return undefined;
    }

    const context = gsap.context(() => {
      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: section,
          start: "top top",
          end: () => `+=${Math.round(window.innerHeight * 3.5)}`,
          scrub: 1.2,
          pin: true,
          pinSpacing: true,
          anticipatePin: 1,
          invalidateOnRefresh: true,
        },
      });

      timeline.to(value, {
        amount: 72,
        duration: 0.72,
        ease: "power4.in",
        onUpdate: () => {
          counter.textContent = `${Math.round(value.amount)}%`;
        },
      }).to({}, { duration: 0.28 });
    }, section);

    return () => context.revert();
  }, [sectionRef, counterRef]);
}

function EnterpriseSavingsFlow() {
  const sectionRef = useRef(null);
  const counterRef = useRef(null);
  useSavingsCounter(sectionRef, counterRef);

  return (
    <section
      className="budget-promise-section enterprise-savings-section"
      aria-label="How PromptRail creates savings"
      ref={sectionRef}
    >
      <div className="enterprise-savings-flow">
        <div className="savings-flow-sources">
          <div className="savings-flow-node">
            <span>Signal 01</span>
            <strong>User data analytics</strong>
          </div>
          <span className="savings-flow-plus" aria-hidden="true">+</span>
          <div className="savings-flow-node">
            <span>Signal 02</span>
            <strong>User input</strong>
          </div>
        </div>

        <span className="savings-flow-arrow" aria-hidden="true"><i /></span>

        <div className="savings-flow-node savings-flow-policy">
          <span>Adaptive layer</span>
          <strong>Inference policy</strong>
          <small>Personalized per user and request</small>
        </div>

        <span className="savings-flow-arrow" aria-hidden="true"><i /></span>

        <div className="savings-flow-result" aria-label="72% average savings">
          <strong ref={counterRef} aria-hidden="true">47%</strong>
          <span>average savings</span>
        </div>
      </div>
    </section>
  );
}

function EnterpriseQuoteSection() {
  return (
    <section className="enterprise-quote-section" aria-label="Industry perspective">
      <blockquote>
        <p>“Every enterprise now is thinking about spend.”</p>
        <footer>Sam Altman</footer>
      </blockquote>
    </section>
  );
}

function EnterprisePrivateModelsSection() {
  return (
    <section
      className="fast-setup-section enterprise-private-models-section"
      id="setup"
      aria-labelledby="private-models-title"
    >
      <div className="enterprise-private-models-shell">
        <div className="enterprise-private-models-copy">
          <p>Proprietary model support</p>
          <h2 id="private-models-title">
            <span>Works with the models you own.</span>
            <span>Optimizes how they run.</span>
          </h2>
          <p>
            PromptRail plugs into your proprietary and fine-tuned model fleet. For every
            request, it selects the cheapest model configuration, context, and compute that
            can meet your quality bar.
          </p>
        </div>

        <div className="private-model-policy" aria-label="Internal inference optimization flow">
          <div className="private-model-policy-head">
            <span>Your internal inference</span>
            <strong>Per-request policy</strong>
          </div>
          <ol>
            <li><span>01</span><strong>Model configuration</strong><small>Cheapest capable model</small></li>
            <li><span>02</span><strong>Context</strong><small>Only what the request needs</small></li>
            <li><span>03</span><strong>Compute</strong><small>Right-sized execution</small></li>
          </ol>
          <div className="private-model-policy-result">
            <span>Constraint</span>
            <strong>Preserve output quality</strong>
          </div>
        </div>
      </div>
    </section>
  );
}

export default function LandingPage() {
  const signupButtonRef = useRef(null);
  const signupPathRef = useRef(null);
  const [isWaitlistOpen, setIsWaitlistOpen] = useState(false);
  const [waitlistEmail, setWaitlistEmail] = useState("");
  const [waitlistStatus, setWaitlistStatus] = useState("idle");
  const [waitlistMessage, setWaitlistMessage] = useState("");
  useHeroBackgroundMotion();
  useLiquidSignupButton(signupButtonRef, signupPathRef);

  function openWaitlist() {
    setIsWaitlistOpen(true);
    setWaitlistStatus("idle");
    setWaitlistMessage("");
  }

  function closeWaitlist() {
    if (waitlistStatus === "submitting") {
      return;
    }

    setIsWaitlistOpen(false);
  }

  async function submitWaitlist(event) {
    event.preventDefault();

    const email = waitlistEmail.trim();

    if (!email) {
      setWaitlistStatus("error");
      setWaitlistMessage("Enter your email to join the waitlist.");
      return;
    }

    setWaitlistStatus("submitting");
    setWaitlistMessage("");

    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email }),
      });
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload?.error || "Unable to join the waitlist.");
      }

      setWaitlistStatus("success");
      setWaitlistMessage("You're on the waitlist.");
      setWaitlistEmail("");
    } catch (error) {
      setWaitlistStatus("error");
      setWaitlistMessage(error.message || "Unable to join the waitlist.");
    }
  }

  return (
    <main className="landing-page landing-page-enterprise">
      <a className="landing-brand-mark" href="/" aria-label="PromptRail home">
        <img src="/PromptRail-logo.png" alt="" aria-hidden="true" />
        <span>PromptRail</span>
      </a>
      <HeroBackgroundImage />
      <section className="hero">
        <div className="enterprise-hero-grid">
          <div className="enterprise-hero-copy">
            <h1>
              <span>Save 70% on token costs</span>
              <span>without your users noticing.</span>
            </h1>
            <div className="hero-actions" aria-label="Primary actions">
              <button
                className="liquid-signup-button"
                type="button"
                ref={signupButtonRef}
                onClick={openWaitlist}
              >
                <svg className="liquid-signup-bg" viewBox="0 0 232 64" aria-hidden="true">
                  <path ref={signupPathRef} d={`${BASE_SIGNUP_BUTTON_PATH} Z`} />
                </svg>
                <span>Join The Waitlist</span>
              </button>
            </div>
          </div>
        </div>
      </section>
      {isWaitlistOpen && typeof document !== "undefined"
        ? createPortal(
            <div
              className="waitlist-modal waitlist-modal-enterprise"
              role="dialog"
              aria-modal="true"
              aria-labelledby="waitlist-title"
            >
              <button
                className="waitlist-modal-backdrop"
                type="button"
                aria-label="Close waitlist form"
                onClick={closeWaitlist}
              />
              <div className="waitlist-modal-panel">
            <button
              className="waitlist-modal-close"
              type="button"
              aria-label="Close waitlist form"
              onClick={closeWaitlist}
              disabled={waitlistStatus === "submitting"}
            >
              ×
            </button>
            <h2 id="waitlist-title">Join the waitlist</h2>
            <form className="waitlist-form" onSubmit={submitWaitlist}>
              <label htmlFor="waitlist-email">Email</label>
              <div className="waitlist-input-row">
                <input
                  id="waitlist-email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@company.com"
                  value={waitlistEmail}
                  onChange={(event) => setWaitlistEmail(event.target.value)}
                  disabled={waitlistStatus === "submitting"}
                  required
                />
                <button type="submit" disabled={waitlistStatus === "submitting"}>
                  {waitlistStatus === "submitting" ? "Joining..." : "Join"}
                </button>
              </div>
              {waitlistMessage ? (
                <p
                  className={
                    waitlistStatus === "success"
                      ? "waitlist-message waitlist-message-success"
                      : "waitlist-message waitlist-message-error"
                  }
                >
                  {waitlistMessage}
                </p>
              ) : null}
            </form>
              </div>
            </div>,
            document.body,
          )
        : null}
      <EnterpriseSavingsFlow />
      <EnterpriseQuoteSection />
      <EnterprisePrivateModelsSection />
      <section className="final-production-cta" aria-label="Join the waitlist">
        <strong>
          <span>Protect the experience.</span>
          <span>Expand the margin.</span>
        </strong>
        <button className="liquid-signup-button" type="button" onClick={openWaitlist}>
          <svg className="liquid-signup-bg" viewBox="0 0 232 64" aria-hidden="true">
            <path d={`${BASE_SIGNUP_BUTTON_PATH} Z`} />
          </svg>
          <span>Join The Waitlist</span>
        </button>
        <div className="final-production-legal">
          <span>© 2026 PromptRail</span>
          <a href="/plugins">Plugins</a>
          <a href="/privacy">Privacy</a>
          <a href="/terms">Terms</a>
          <a href="/support">Support</a>
        </div>
      </section>
    </main>
  );
}
