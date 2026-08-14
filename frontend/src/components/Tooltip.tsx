interface TooltipProps {
  text: string;
}

/**
 * Small "ⓘ" info marker. Hover or focus (keyboard-accessible) reveals
 * the explanation. Used next to every major metric per spec section 17.
 */
export function Tooltip({ text }: TooltipProps) {
  return (
    <span className="info-tip" tabIndex={0}>
      <span className="info-tip-icon" aria-hidden="true">
        ⓘ
      </span>
      <span className="info-tip-text" role="tooltip">
        {text}
      </span>
    </span>
  );
}
