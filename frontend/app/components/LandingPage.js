"use client";

import gsap from "gsap";
import { MotionPathPlugin } from "gsap/MotionPathPlugin";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

gsap.registerPlugin(ScrollTrigger, MotionPathPlugin);

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

function useHeadlineInkScroll(heroRef, inkPathRef, enabled = true) {
  useLayoutEffect(() => {
    if (!enabled) {
      return undefined;
    }

    const hero = heroRef.current;
    const path = inkPathRef.current;

    if (!hero || !path) {
      return undefined;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const length = path.getTotalLength();

    gsap.set(path, {
      strokeDasharray: length,
      strokeDashoffset: reduceMotion.matches ? 0 : length,
      opacity: reduceMotion.matches ? 1 : 0,
    });

    if (reduceMotion.matches) {
      return undefined;
    }

    const context = gsap.context(() => {
      gsap.to(path, {
        strokeDashoffset: 0,
        opacity: 1,
        ease: "none",
        scrollTrigger: {
          trigger: hero,
          start: "top top",
          end: () => `+=${Math.max(560, window.innerHeight * 0.9)}`,
          pin: true,
          anticipatePin: 1,
          scrub: true,
        },
      });
    }, hero);

    return () => context.revert();
  }, [heroRef, inkPathRef, enabled]);
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

const setupHighlights = [
  { label: "Hermes", value: "Native adapter", icon: "/hermes-agent-icon.png" },
  { label: "OpenClaw", value: "Provider harness", icon: "/openclaw-logo.svg" },
];

const routingModelLogos = [
  { label: "OpenAI", icon: "/openai-logo.svg" },
  { label: "Anthropic", icon: "/anthropic-logo.svg" },
  { label: "Gemini", icon: "/gemini-logo.svg" },
  { label: "Mistral AI", icon: "/mistral-logo.svg" },
  { label: "DeepSeek", icon: "/deepseek-logo.svg" },
  { label: "Moonshot", icon: "/moonshot-logo.svg" },
];

const userCostExplosionPieces = [
  { label: "v", x: -118, y: -52, rotate: -34 },
  { label: "a", x: -78, y: 46, rotate: 22 },
  { label: "r", x: -38, y: -74, rotate: -18 },
  { label: "i", x: 4, y: 66, rotate: 42 },
  { label: "a", x: 46, y: -48, rotate: 18 },
  { label: "b", x: 78, y: 48, rotate: -28 },
  { label: "l", x: 114, y: -18, rotate: 34 },
  { label: "e", x: 142, y: 34, rotate: -16 },
];

function shuffledModelOrder(models) {
  const order = models.map((model, index) => ({ model, index }));

  for (let index = order.length - 1; index > 0; index -= 1) {
    const randomIndex = Math.floor(Math.random() * (index + 1));
    [order[index], order[randomIndex]] = [order[randomIndex], order[index]];
  }

  return order;
}

function useRoutingFlowMotion(stageRef) {
  useEffect(() => {
    const stage = stageRef.current;

    if (!stage) {
      return undefined;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const svg = stage.querySelector(".routing-flow-svg");
    const agent = stage.querySelector(".routing-flow-node-agent");
    const router = stage.querySelector(".routing-flow-router");
    const models = gsap.utils.toArray(".routing-flow-model", stage);
    const paths = gsap.utils.toArray(".js-routing-path", stage);
    const pulse = stage.querySelector(".js-routing-pulse");
    let context;
    let resizeObserver;
    let frame = 0;

    const relativeBox = (element) => {
      const stageRect = stage.getBoundingClientRect();
      const rect = element.getBoundingClientRect();

      return {
        left: rect.left - stageRect.left,
        right: rect.right - stageRect.left,
        top: rect.top - stageRect.top,
        bottom: rect.bottom - stageRect.top,
        centerX: rect.left - stageRect.left + rect.width / 2,
        centerY: rect.top - stageRect.top + rect.height / 2,
      };
    };

    const setRoutePath = (path, fromX, fromY, toX, toY) => {
      const distance = Math.abs(toX - fromX);
      const bend = Math.max(8, Math.min(96, distance * 0.46));

      path.setAttribute(
        "d",
        `M ${fromX.toFixed(2)} ${fromY.toFixed(2)} C ${(fromX + bend).toFixed(2)} ${fromY.toFixed(2)} ${(toX - bend).toFixed(2)} ${toY.toFixed(2)} ${toX.toFixed(2)} ${toY.toFixed(2)}`,
      );
    };

    const layoutPaths = () => {
      if (!svg || !agent || !router || !models.length || paths.length !== models.length + 1 || !pulse) {
        return false;
      }

      const stageRect = stage.getBoundingClientRect();
      const agentBox = relativeBox(agent);
      const routerBox = relativeBox(router);

      svg.setAttribute("viewBox", `0 0 ${stageRect.width.toFixed(2)} ${stageRect.height.toFixed(2)}`);
      setRoutePath(paths[0], agentBox.right, agentBox.centerY, routerBox.left, routerBox.centerY);

      models.forEach((model, index) => {
        const modelBox = relativeBox(model);
        setRoutePath(
          paths[index + 1],
          routerBox.right,
          routerBox.centerY,
          modelBox.left,
          modelBox.centerY,
        );
      });

      return true;
    };

    const buildMotion = () => {
      context?.revert();

      if (!layoutPaths()) {
        return;
      }

      if (reduceMotion.matches) {
        gsap.set(pulse, { autoAlpha: 0 });
        return;
      }

      context = gsap.context(() => {
        models.forEach((model) => {
          model.classList.remove("routing-flow-model-active");
        });

        gsap.set(pulse, {
          autoAlpha: 0,
          scale: 0.72,
          transformOrigin: "50% 50%",
        });

        const timeline = gsap.timeline({
          onComplete: requestBuild,
        });
        const entryDuration = 1.05;
        const exitDuration = 1.45;
        const activeLogoHoldDuration = 0.82;

        shuffledModelOrder(models).forEach(({ model, index }) => {
          timeline
            .set(pulse, { autoAlpha: 0, scale: 0.72 })
            .to(pulse, { autoAlpha: 1, scale: 1, duration: 0.12, ease: "power1.out" })
            .to(
              pulse,
              {
                duration: entryDuration,
                ease: "power1.inOut",
                motionPath: {
                  path: paths[0],
                  align: paths[0],
                  alignOrigin: [0.5, 0.5],
                },
              },
              "<",
            )
            .to(pulse, { scale: 1.08, duration: 0.1, ease: "power1.out" })
            .to(pulse, { scale: 1, duration: 0.1, ease: "power1.in" })
            .to(pulse, {
              duration: exitDuration,
              ease: "power1.inOut",
              motionPath: {
                path: paths[index + 1],
                align: paths[index + 1],
                alignOrigin: [0.5, 0.5],
              },
            })
            .call(() => model.classList.add("routing-flow-model-active"), [], ">-0.1")
            .to(pulse, { autoAlpha: 0, scale: 0.62, duration: 0.18, ease: "power1.in" }, "<")
            .call(() => model.classList.remove("routing-flow-model-active"), [], `>+${activeLogoHoldDuration}`)
            .to({}, { duration: 0.28 });
        });
      }, stage);
    };

    const requestBuild = () => {
      if (frame) {
        return;
      }

      frame = window.requestAnimationFrame(() => {
        frame = 0;
        buildMotion();
      });
    };

    requestBuild();
    resizeObserver = new ResizeObserver(requestBuild);
    resizeObserver.observe(stage);

    return () => {
      context?.revert();
      models.forEach((model) => {
        model.classList.remove("routing-flow-model-active");
      });
      resizeObserver?.disconnect();
      if (frame) {
        window.cancelAnimationFrame(frame);
      }
    };
  }, [stageRef]);
}

function useUserCostTransformMotion(stageRef) {
  useEffect(() => {
    const stage = stageRef.current;

    if (!stage) {
      return undefined;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const variable = stage.querySelector(".user-cost-word-variable");
    const parameter = stage.querySelector(".user-cost-word-parameter");
    const shards = gsap.utils.toArray(".user-cost-shard", stage);
    const cursor = stage.querySelector(".user-cost-cursor");
    const equals = stage.querySelector(".user-cost-equals");

    if (!variable || !parameter || !cursor || !equals || !shards.length) {
      return undefined;
    }

    gsap.set(variable, {
      autoAlpha: reduceMotion.matches ? 0 : 1,
      scale: 1,
      transformOrigin: "0% 58%",
    });
    gsap.set(parameter, {
      autoAlpha: reduceMotion.matches ? 1 : 0,
      y: reduceMotion.matches ? 0 : 18,
      scale: reduceMotion.matches ? 1 : 0.86,
      filter: "blur(0px)",
      transformOrigin: "50% 58%",
    });
    gsap.set(shards, {
      autoAlpha: 0,
      x: 0,
      y: 0,
      rotate: 0,
      scale: 0.62,
      transformOrigin: "50% 50%",
    });

    if (reduceMotion.matches) {
      return undefined;
    }

    const context = gsap.context(() => {
      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: stage,
          start: "top 72%",
          end: "bottom 30%",
          scrub: true,
        },
      });

      timeline
        .to(equals, { opacity: 1, duration: 0.1 }, 0)
        .to(
          variable,
          {
            color: "#0b67c2",
            filter: "blur(0px)",
            letterSpacing: "0.08em",
            scale: 2,
            textShadow: "0 20px 42px rgba(11, 103, 194, 0.34)",
            duration: 0.58,
            ease: "power2.in",
          },
          0.12,
        )
        .to(
          variable,
          {
            autoAlpha: 0,
            filter: "blur(4px)",
            scale: 2.22,
            duration: 0.1,
            ease: "power3.in",
          },
          0.68,
        )
        .to(
          shards,
          {
            autoAlpha: 1,
            duration: 0.06,
            stagger: 0.006,
          },
          0.64,
        )
        .to(
          shards,
          {
            x: (_index, element) => Number(element.dataset.x),
            y: (_index, element) => Number(element.dataset.y),
            rotate: (_index, element) => Number(element.dataset.rotate),
            scale: 1,
            duration: 0.22,
            ease: "power2.out",
            stagger: 0.006,
          },
          0.66,
        )
        .to(shards, { autoAlpha: 0, scale: 0.42, duration: 0.22 }, 0.86)
        .to(cursor, { autoAlpha: 0.18, duration: 0.06 }, 0.68)
        .to(
          parameter,
          {
            autoAlpha: 1,
            filter: "blur(0px)",
            scale: 1,
            y: 0,
            duration: 0.3,
            ease: "back.out(1.5)",
          },
          0.76,
        )
        .to(cursor, { autoAlpha: 1, duration: 0.1 }, 0.96);
    }, stage);

    return () => context.revert();
  }, [stageRef]);
}

function UserCostParameterVisual() {
  const stageRef = useRef(null);
  useUserCostTransformMotion(stageRef);

  return (
    <div className="user-cost-transform" ref={stageRef} aria-hidden="true">
      <div className="user-cost-terminal">
        <div className="user-cost-terminal-head">
          <span>cost policy</span>
        </div>
        <div className="user-cost-code-line">
          <span className="user-cost-prompt">&gt;</span>
          <span className="user-cost-key">user_cost</span>
          <span className="user-cost-equals">=</span>
          <span className="user-cost-word-shell">
            <span className="user-cost-word-variable">variable</span>
            <span className="user-cost-word-parameter">parameter</span>
            {userCostExplosionPieces.map((piece, index) => (
              <span
                className="user-cost-shard"
                data-rotate={piece.rotate}
                data-x={piece.x}
                data-y={piece.y}
                key={`${piece.label}-${index}`}
              >
                {piece.label}
              </span>
            ))}
          </span>
          <span className="user-cost-cursor" />
        </div>
      </div>
    </div>
  );
}

function RoutingFlowSection() {
  const stageRef = useRef(null);
  useRoutingFlowMotion(stageRef);

  return (
    <section className="routing-flow-section" aria-label="Dynamic routing overview">
      <div className="routing-flow-shell">
        <div className="routing-flow-copy">
          <h2>
            <span>One request enters.</span>
            <span className="routing-flow-title-accent">The right</span>
            <span className="routing-flow-title-accent">model answers.</span>
          </h2>
          <p>
            Rail-1 evaluates each agent call before it reaches a provider, sends routine work
            through cheaper candidates, and keeps premium fallback available when the task needs it.
          </p>
        </div>

        <div className="routing-flow-visual" aria-hidden="true">
          <div className="routing-flow-stage" ref={stageRef}>
            <svg className="routing-flow-svg" viewBox="0 0 760 420" preserveAspectRatio="none">
              <defs>
                <linearGradient id="routeSoft" x1="0%" x2="100%" y1="0%" y2="0%">
                  <stop offset="0%" stopColor="rgba(255,255,255,0.16)" />
                  <stop offset="55%" stopColor="rgba(255,189,99,0.5)" />
                  <stop offset="100%" stopColor="rgba(255,255,255,0.18)" />
                </linearGradient>
              </defs>

              <path
                className="routing-flow-line routing-flow-line-entry js-routing-path"
                d=""
              />
              {routingModelLogos.map((model) => {
                return (
                  <path
                    className="routing-flow-line routing-flow-line-model js-routing-path"
                    d=""
                    key={model.label}
                  />
                );
              })}
            </svg>

            <div className="routing-flow-node routing-flow-node-agent">
              <span>Agent</span>
            </div>
            <div className="routing-flow-router">
              <span>Rail-1</span>
            </div>
            <div className="routing-flow-model-list">
              {routingModelLogos.map((model) => (
                <div className="routing-flow-model" key={model.label}>
                  <img src={model.icon} alt="" draggable="false" />
                  <span>{model.label}</span>
                </div>
              ))}
            </div>

            <span className="routing-flow-pulse routing-flow-pulse-entry js-routing-pulse" />
          </div>
        </div>
      </div>
    </section>
  );
}

function useBudgetComparisonMotion(stageRef) {
  useEffect(() => {
    const stage = stageRef.current;

    if (!stage) {
      return undefined;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const railFill = stage.querySelector(".budget-comparison-fill-rail");
    const normalFill = stage.querySelector(".budget-comparison-fill-normal");

    if (!railFill || !normalFill) {
      return undefined;
    }

    gsap.set(railFill, { height: reduceMotion.matches ? "62%" : "4%" });
    gsap.set(normalFill, { height: reduceMotion.matches ? "88%" : "4%" });

    if (reduceMotion.matches) {
      return undefined;
    }

    const context = gsap.context(() => {
      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: stage,
          start: "top 58%",
          once: true,
        },
      });

      timeline
        .to(railFill, {
          height: "62%",
          duration: 2.65,
          ease: "none",
        })
        .to(
          normalFill,
          {
            keyframes: [
              { height: "28%", duration: 0.38, ease: "power3.out" },
              { height: "53%", duration: 1.45, ease: "power1.inOut" },
              { height: "88%", duration: 0.5, ease: "power4.out" },
            ],
          },
          0.16,
        )
        .to(normalFill, { x: -2, duration: 0.04, ease: "none" }, 0.2)
        .to(normalFill, { x: 2, duration: 0.05, ease: "none" }, 0.24)
        .to(normalFill, { x: 0, duration: 0.05, ease: "none" }, 0.29)
        .to(normalFill, { x: 2, duration: 0.04, ease: "none" }, 1.28)
        .to(normalFill, { x: 0, duration: 0.06, ease: "none" }, 1.32);
    }, stage);

    return () => context.revert();
  }, [stageRef]);
}

function BudgetComparisonVisual() {
  const stageRef = useRef(null);
  useBudgetComparisonMotion(stageRef);

  return (
    <div className="budget-comparison" ref={stageRef} aria-hidden="true">
      <div className="budget-comparison-line">
        <span>Budget</span>
      </div>

      <div className="budget-comparison-bars">
        <div className="budget-comparison-item budget-comparison-item-rail">
          <div className="budget-comparison-bar">
            <span className="budget-comparison-fill budget-comparison-fill-rail" />
          </div>
          <strong>Rail-1</strong>
        </div>

        <div className="budget-comparison-item budget-comparison-item-normal">
          <div className="budget-comparison-bar">
            <span className="budget-comparison-fill budget-comparison-fill-normal" />
          </div>
          <strong>Normal</strong>
        </div>
      </div>
    </div>
  );
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

function BudgetPromiseSection({ enterprise = false }) {
  if (enterprise) {
    return <EnterpriseSavingsFlow />;
  }

  return (
    <section className="budget-promise-section" aria-label="Budget promise">
      <div className="budget-promise-shell">
        <div className="budget-promise-copy">
          <h2>
            <span>You put a budget</span>
            <span>We enforce it.</span>
          </h2>
          <p>
            Rail-1 treats spend as a constraint, not a suggestion. Routine calls move to
            efficient models, premium routes stay available, and every request is checked
            against the remaining budget before it runs.
          </p>
        </div>

        <div className="budget-promise-visual">
          <BudgetComparisonVisual />
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

function FastSetupSection({ enterprise = false }) {
  if (enterprise) {
    return <EnterprisePrivateModelsSection />;
  }

  const chartBounds = { left: 118, top: 228, width: 942, height: 488, maxCost: 22.5, maxScore: 78 };
  const x = (cost) => chartBounds.left + (cost / chartBounds.maxCost) * chartBounds.width;
  const y = (score) => chartBounds.top + chartBounds.height - (score / chartBounds.maxScore) * chartBounds.height;
  const linePoints = (points) => points.map(([cost, score]) => `${x(cost)},${y(score)}`).join(" ");
  const benchmarkSeries = [
    { label: "GPT-5.6 Sol", color: "#314f8a", points: [[0.7, 45], [1.5, 61], [3.75, 69.5], [5, 70.8], [8.5, 72.6]] },
    { label: "GPT-5.6 Luna", color: "#c7d9fb", points: [[0.08, 2], [0.18, 11.5], [0.38, 14], [0.5, 24], [0.66, 35], [0.88, 44.5], [1.25, 54], [1.6, 57], [2.3, 60], [2.85, 67.5], [5, 69.8]] },
    { label: "Claude Fable 5", color: "#cb6a40", marker: "square", points: [[3.7, 59.5], [6.1, 65.5], [9.2, 68.5], [13.4, 70.1], [21.6, 70]] },
    { label: "Gemini 3.1 Pro Preview", color: "#9bd36a", marker: "diamond", points: [[9.4, 12]] },
    { label: "GPT-5.6 Terra", color: "#98b7f5", points: [[0.06, 2], [0.22, 12], [0.45, 24], [0.6, 35], [1.25, 54], [2.4, 60], [5, 69.8]] },
    { label: "GPT-5.5", color: "#bd4f98", points: [[1.25, 27], [3, 54], [5.25, 64.5], [7.5, 67]] },
    { label: "Claude Opus 4.8", color: "#ff8857", marker: "square", points: [[2.4, 40.5], [3.4, 48.8], [4.2, 52], [8.2, 54.5], [13.2, 59]] },
  ];
  const promptRail = { cost: 0.7, score: 67 };

  return (
    <section className="fast-setup-section" id="setup" aria-label="Fast setup and savings">
      <div className="fast-setup-shell">
        <div className="fast-setup-copy">
          <h2>
            <span className="fast-setup-title-line">
              Same quality.
            </span>
            <span className="fast-setup-title-accent">
              Half the cost.
            </span>
          </h2>
          <p>PromptRail reaches GPT-Sol high quality at the cost of GPT-Luna.</p>
        </div>

        <figure className="benchmark-chart" aria-labelledby="benchmark-chart-title">
          <figcaption id="benchmark-chart-title">DeepSWE v1.1</figcaption>
          <svg className="benchmark-chart-svg" viewBox="0 0 1200 840" role="img" aria-labelledby="benchmark-chart-title">
            <g className="benchmark-chart-legend">
              {[...benchmarkSeries.slice(0, 4), { label: "PromptRail", color: "#e5484d" }, ...benchmarkSeries.slice(4)].map((series, index) => {
                const legendX = index < 4 ? 120 : 440;
                const legendY = 92 + (index % 4) * 31;
                const isSquare = series.marker === "square";
                const isDiamond = series.marker === "diamond";
                return (
                  <g key={series.label} transform={`translate(${legendX} ${legendY})`} className={series.label === "PromptRail" ? "benchmark-chart-legend-prompt-rail" : ""}>
                    {isSquare ? <rect x="0" y="-10" width="20" height="20" fill={series.color} /> : isDiamond ? <rect x="2" y="-8" width="16" height="16" fill={series.color} transform="rotate(45 10 0)" /> : <circle cx="10" cy="0" r="10" fill={series.color} />}
                    <text x="30" y="7">{series.label}</text>
                  </g>
                );
              })}
            </g>
            <g className="benchmark-chart-grid-lines" aria-hidden="true">
              {[10, 20, 30, 40, 50, 60, 70].map((score) => <line key={score} x1={chartBounds.left} y1={y(score)} x2={chartBounds.left + chartBounds.width} y2={y(score)} />)}
            </g>
            <line className="benchmark-chart-axis" x1={chartBounds.left} y1={chartBounds.top} x2={chartBounds.left} y2={chartBounds.top + chartBounds.height} />
            <line className="benchmark-chart-axis" x1={chartBounds.left} y1={chartBounds.top + chartBounds.height} x2={chartBounds.left + chartBounds.width} y2={chartBounds.top + chartBounds.height} />
            {[0, 5, 10, 15, 20].map((cost) => <g className="benchmark-chart-tick" key={cost}><line x1={x(cost)} y1={chartBounds.top + chartBounds.height} x2={x(cost)} y2={chartBounds.top + chartBounds.height + 8} /><text x={x(cost)} y="758" textAnchor="middle">${cost}</text></g>)}
            {[10, 20, 30, 40, 50, 60, 70].map((score) => <g className="benchmark-chart-tick" key={score}><line x1={chartBounds.left - 8} y1={y(score)} x2={chartBounds.left} y2={y(score)} /><text x="94" y={y(score) + 7} textAnchor="end">{score}%</text></g>)}
            <text className="benchmark-chart-axis-title" x="590" y="800" textAnchor="middle">API cost (USD)</text>
            <text className="benchmark-chart-axis-title" x="23" y="475" textAnchor="middle" transform="rotate(-90 23 475)">Score</text>
            {benchmarkSeries.map((series) => (
              <g className="benchmark-chart-series" key={series.label} style={{ "--series-color": series.color }}>
                {series.points.length > 1 ? <polyline points={linePoints(series.points)} fill="none" /> : null}
                {series.points.map(([cost, score]) => series.marker === "square" ? <rect key={`${cost}-${score}`} x={x(cost) - 7} y={y(score) - 7} width="14" height="14" /> : series.marker === "diamond" ? <rect key={`${cost}-${score}`} x={x(cost) - 7} y={y(score) - 7} width="14" height="14" transform={`rotate(45 ${x(cost)} ${y(score)})`} /> : <circle key={`${cost}-${score}`} cx={x(cost)} cy={y(score)} r="7" />)}
              </g>
            ))}
            <g className="benchmark-chart-prompt-rail">
              <circle cx={x(promptRail.cost)} cy={y(promptRail.score)} r="10" />
            </g>
          </svg>
        </figure>
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
      <BudgetPromiseSection enterprise />
      <EnterpriseQuoteSection />
      <FastSetupSection enterprise />
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
