"use client";

import { useEffect, useRef } from "react";

export default function PluginHowItWorks() {
  const sectionRef = useRef(null);

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) {
      return undefined;
    }

    const revealItems = section.querySelectorAll("[data-reveal]");
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -10%", threshold: 0.35 },
    );

    revealItems.forEach((item) => observer.observe(item));

    return () => observer.disconnect();
  }, []);

  return (
    <section className="plugin-how-it-works" ref={sectionRef}>
      <h2>How does it work?</h2>

      <div className="plugin-how-flow">
        <article className="plugin-how-step plugin-how-reveal" data-reveal>
          <span className="plugin-how-emoji" aria-hidden="true">🔗</span>
          <strong>Connect your subscription.</strong>
          <p>Connect the plugin to your existing provider subscription.</p>
        </article>

        <span className="plugin-how-arrow plugin-how-reveal" data-reveal aria-hidden="true">
          ↓
        </span>

        <article className="plugin-how-step plugin-how-step-wide plugin-how-reveal" data-reveal>
          <span className="plugin-how-emoji" aria-hidden="true">🧭</span>
          <strong>Use the right model.</strong>
          <p className="plugin-how-routing-copy">We use your subscription only when it&apos;s needed, and a cheaper option otherwise.</p>
        </article>

        <span className="plugin-how-arrow plugin-how-reveal" data-reveal aria-hidden="true">
          ↓
        </span>

        <article className="plugin-how-step plugin-how-reveal" data-reveal>
          <span className="plugin-how-emoji" aria-hidden="true">🤑</span>
          <strong>Save, or get refunded.</strong>
          <p>You save money. If you still hit your usage limit, we refund you.</p>
        </article>
      </div>
    </section>
  );
}
