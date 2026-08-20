import type { ReactNode } from "react";

/**
 * Progressive-disclosure educational aid (spec section 21): "what is
 * this / why does it matter" content, collapsed by default so it never
 * becomes a wall of text in the main UI. Plain <details>/<summary> --
 * no JS state needed, keyboard/screen-reader accessible for free.
 */
export function InfoDisclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="info-disclosure">
      <summary>{title}</summary>
      <div className="info-disclosure-body">{children}</div>
    </details>
  );
}
