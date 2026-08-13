"use client";

import { cloneElement, isValidElement } from "react";

function cn(...classes) {
  return classes.filter(Boolean).join(" ");
}

export function Button({ asChild = false, variant = "default", size = "default", className = "", children, ...props }) {
  const classes = cn("ui-button", `ui-button-${variant}`, `ui-button-${size}`, className);

  if (asChild && isValidElement(children)) {
    return cloneElement(children, {
      ...props,
      className: cn(children.props.className, classes),
    });
  }

  return <button className={classes} {...props}>{children}</button>;
}

export function Card({ className = "", ...props }) {
  return <section className={cn("ui-card", className)} {...props} />;
}

export function CardHeader({ className = "", ...props }) {
  return <div className={cn("ui-card-header", className)} {...props} />;
}

export function CardTitle({ className = "", ...props }) {
  return <h2 className={cn("ui-card-title", className)} {...props} />;
}

export function CardDescription({ className = "", ...props }) {
  return <p className={cn("ui-card-description", className)} {...props} />;
}

export function CardContent({ className = "", ...props }) {
  return <div className={cn("ui-card-content", className)} {...props} />;
}

export function Badge({ variant = "default", className = "", ...props }) {
  return <span className={cn("ui-badge", `ui-badge-${variant}`, className)} {...props} />;
}

export function Progress({ value = 0, className = "", ...props }) {
  const normalizedValue = Math.max(0, Math.min(100, Number(value || 0)));

  return (
    <div
      className={cn("ui-progress", className)}
      role="progressbar"
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={normalizedValue}
      {...props}
    >
      <span style={{ transform: `translateX(-${100 - normalizedValue}%)` }} />
    </div>
  );
}

export function Table({ className = "", ...props }) {
  return (
    <div className={cn("ui-table-wrap", className)}>
      <table className="ui-table" {...props} />
    </div>
  );
}

export function TableHeader(props) {
  return <thead {...props} />;
}

export function TableBody(props) {
  return <tbody {...props} />;
}

export function TableRow({ className = "", ...props }) {
  return <tr className={cn("ui-table-row", className)} {...props} />;
}

export function TableHead({ className = "", ...props }) {
  return <th className={cn("ui-table-head", className)} {...props} />;
}

export function TableCell({ className = "", ...props }) {
  return <td className={cn("ui-table-cell", className)} {...props} />;
}
