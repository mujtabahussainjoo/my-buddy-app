import { useEffect, useMemo, useRef, useState } from 'react';
import {
  copyText,
  downloadMarkdown,
  downloadPdf,
  unsupportedForPdf,
  type ExportMessage,
} from '../lib/export';

type Feedback =
  | 'copied'
  | 'copy-failed'
  | 'pdf-busy'
  | 'pdf-failed'
  | 'pdf-partial'
  | null;

interface MessageActionsProps {
  message: ExportMessage;
  /** Export the whole conversation instead of just this message. */
  conversation?: ExportMessage[];
  tone?: 'light' | 'dark';
}

const FEEDBACK_LABELS: Record<Exclude<Feedback, null>, string> = {
  copied: 'Copied',
  'copy-failed': 'Copy failed',
  'pdf-busy': 'Making PDF…',
  'pdf-failed': 'PDF failed',
  'pdf-partial': 'Saved (non-Latin text dropped)',
};

export default function MessageActions({
  message,
  conversation,
  tone = 'light',
}: MessageActionsProps) {
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scope = useMemo(
    () => (conversation?.length ? conversation : [message]),
    [conversation, message],
  );

  // PDF fonts are Latin-only. Scanning every message is wasted work on each
  // streaming frame, so we only count characters the first time the menu opens.
  const [unrenderable, setUnrenderable] = useState<number | null>(null);

  // Recount lazily whenever the transcript changes, never during streaming.
  useEffect(() => setUnrenderable(null), [scope]);

  const openMenu = () => {
    setUnrenderable((known) => known ?? scope.reduce(
      (total, item) => total + unsupportedForPdf(item.content),
      0,
    ));
    setOpen((prev) => !prev);
  };

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const flash = (next: Feedback) => {
    setFeedback(next);
    if (timerRef.current) clearTimeout(timerRef.current);
    if (next) timerRef.current = setTimeout(() => setFeedback(null), 2200);
  };

  const onCopy = async () => {
    const ok = await copyText(message.content);
    flash(ok ? 'copied' : 'copy-failed');
  };

  const onMarkdown = () => {
    setOpen(false);
    downloadMarkdown(scope);
  };

  const onPdf = async () => {
    setOpen(false);
    flash('pdf-busy');
    try {
      await downloadPdf(scope);
      flash(unrenderable ? 'pdf-partial' : null);
    } catch {
      flash('pdf-failed');
    }
  };

  const ghost = tone === 'dark' ? 'text-white/70 hover:text-white' : 'text-mab-muted hover:text-[var(--mab-primary)]';
  const label = feedback ? FEEDBACK_LABELS[feedback] : null;

  return (
    <div ref={rootRef} className="relative mt-1 flex items-center gap-1">
      <button
        type="button"
        className={`mab-btn mab-btn-ghost mab-btn-sm px-2 py-0.5 text-[11px] ${ghost}`}
        onClick={() => void onCopy()}
        title="Copy this message"
        aria-label="Copy this message"
      >
        {label ?? 'Copy'}
      </button>

      <button
        type="button"
        className={`mab-btn mab-btn-ghost mab-btn-sm px-2 py-0.5 text-[11px] ${ghost}`}
        onClick={openMenu}
        title="Download as a file"
        aria-label="Download as a file"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        Export ▾
      </button>

      {open && (
        <div
          role="menu"
          className="absolute bottom-full left-0 z-20 mb-1 w-44 overflow-hidden rounded-lg border border-mab-border bg-mab-panel py-1 shadow-lg"
        >
          <button
            type="button"
            role="menuitem"
            className="block w-full px-3 py-1.5 text-left text-xs hover:bg-[var(--mab-primary-soft)]"
            onClick={onMarkdown}
          >
            Markdown (.md)
            <span className="block text-[10px] text-mab-muted">
              {scope.length > 1 ? 'Whole conversation' : 'This message'} · full Unicode
            </span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="block w-full px-3 py-1.5 text-left text-xs hover:bg-[var(--mab-primary-soft)]"
            onClick={() => void onPdf()}
          >
            PDF (.pdf)
            <span className="block text-[10px] text-mab-muted">
              {unrenderable
                ? `Latin text only — ${unrenderable.toLocaleString()} character${unrenderable === 1 ? '' : 's'} (CJK, emoji…) cannot be drawn`
                : scope.length > 1
                  ? 'Whole conversation'
                  : 'This message'}
            </span>
          </button>
        </div>
      )}
    </div>
  );
}
