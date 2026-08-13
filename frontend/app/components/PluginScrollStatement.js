"use client";

import { useEffect, useRef, useState } from "react";

const lines = [
  "No quality drop.",
  "The optimal pipeline.",
  "Feel the infinity.",
];

const characterCount = lines.join("").replaceAll(" ", "").length;
const START_COLOR = [89, 89, 89];
const END_COLOR = [255, 179, 77];

function clamp(value) {
  return Math.min(1, Math.max(0, value));
}

function characterColor(progress, index) {
  const revealSpan = 0.1;
  const start = (index / (characterCount - 1)) * 0.82;
  const reveal = clamp((progress - start) / revealSpan);
  const eased = reveal * reveal * (3 - (2 * reveal));
  const channels = START_COLOR.map(
    (channel, channelIndex) => Math.round(
      channel + ((END_COLOR[channelIndex] - channel) * eased),
    ),
  );
  return `rgb(${channels.join(", ")})`;
}

export default function PluginScrollStatement() {
  const sectionRef = useRef(null);
  const frameRef = useRef(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    function measure() {
      frameRef.current = null;
      if (reducedMotion.matches) {
        setProgress(1);
        return;
      }

      const section = sectionRef.current;
      if (!section) {
        return;
      }

      const rect = section.getBoundingClientRect();
      const viewportHeight = window.innerHeight;
      const pinnedDistance = Math.max(rect.height - viewportHeight, 1);
      const nextProgress = clamp(-rect.top / pinnedDistance);
      setProgress((current) => (
        Math.abs(current - nextProgress) >= 0.001 ? nextProgress : current
      ));
    }

    function requestMeasure() {
      if (frameRef.current === null) {
        frameRef.current = window.requestAnimationFrame(measure);
      }
    }

    measure();
    window.addEventListener("scroll", requestMeasure, { passive: true });
    window.addEventListener("resize", requestMeasure);
    reducedMotion.addEventListener("change", requestMeasure);

    return () => {
      window.removeEventListener("scroll", requestMeasure);
      window.removeEventListener("resize", requestMeasure);
      reducedMotion.removeEventListener("change", requestMeasure);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
    };
  }, []);

  let characterIndex = 0;

  return (
    <section
      className="plugin-onboarding-preview"
      aria-labelledby="plugin-onboarding-title"
      ref={sectionRef}
    >
      <div className="plugin-onboarding-preview-copy">
        <h2
          aria-label={lines.join(" ")}
          className="plugin-scroll-statement"
          id="plugin-onboarding-title"
        >
          {lines.map((line, lineIndex) => (
            <span aria-hidden="true" className="plugin-scroll-line" key={lineIndex}>
              {[...line].map((character, lineCharacterIndex) => {
                if (character === " ") {
                  return "\u00a0";
                }

                const index = characterIndex;
                characterIndex += 1;
                return (
                  <i
                    className="plugin-scroll-character"
                    key={`${lineIndex}-${lineCharacterIndex}`}
                    style={{ color: characterColor(progress, index) }}
                  >
                    {character}
                  </i>
                );
              })}
            </span>
          ))}
        </h2>
      </div>
    </section>
  );
}
