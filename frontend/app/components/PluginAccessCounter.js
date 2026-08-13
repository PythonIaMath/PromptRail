"use client";

import { useEffect, useState } from "react";
import styles from "./PluginAccessCounter.module.css";

export default function PluginAccessCounter({ compact = false }) {
  const [access, setAccess] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetch("/api/plugins/access", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || "Plugin access counter failed to load.");
        }
        if (active) {
          setAccess(payload.access);
        }
      })
      .catch((requestError) => {
        if (active) {
          setError(requestError.message);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (error) {
    return <p className={styles.error}>{error}</p>;
  }
  if (!access) {
    return compact ? null : <div className={`${styles.counter} ${styles.loading}`} />;
  }

  if (compact) return null;
  return null;
}
