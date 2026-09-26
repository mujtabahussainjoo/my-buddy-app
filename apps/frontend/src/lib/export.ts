/** Copy a message to the clipboard, with a fallback for non-secure contexts. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the manual path below
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
/**
 * The bundled PDF fonts only cover WinAnsi (Latin-1) plus a little punctuation.
 * Anything else — CJK, Arabic, Cyrillic, emoji, even some dashes and arrows —
 * would be written out as unreadable mojibake, so we detect it and tell the user
 * instead of silently corrupting their text.
 *
 * This set is the verified WinAnsi range above U+00FF; everything else is
 * replaced with "?" so the file stays readable.
 */
const PDF_SAFE_ABOVE_LATIN1 = new Set([
  0x2013, 0x2014, // – —
  0x2018, 0x2019, 0x201a, 0x201c, 0x201d, 0x201e, // ' ' ‚ " " „
  0x2020, 0x2021, 0x2022, 0x2026, 0x2030, // † ‡ • … ‰
  0x2039, 0x203a, 0x20ac, 0x2122, // ‹ › € ™
]);

export function unsupportedForPdf(text: string): number {
  let count = 0;
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (code <= 0xff || PDF_SAFE_ABOVE_LATIN1.has(code)) continue;
    count += 1;
  }
  return count;
}

/** Replace characters the PDF fonts cannot draw, so nothing turns to mojibake. */
function toPdfSafeText(text: string): string {
  let result = '';
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    result += code <= 0xff || PDF_SAFE_ABOVE_LATIN1.has(code) ? char : '?';
  }
  return result;
}


function safeFileName(name: string, extension: string): string {
  const cleaned = name
    .replace(/[\\/:*?"<>|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 60);
  return `${cleaned || 'response'}.${extension}`;
}

function triggerDownload(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser a moment to start the download before cleaning up.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface ExportMessage {
  role: string;
  content: string;
  model?: string | null;
  created_at?: string | null;
}

function titleOf(text: string, role: string): string {
  const firstLine = text
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.length > 0);
  if (!firstLine) return role === 'user' ? 'Question' : 'Response';
  return firstLine.replace(/^[#>*\-\s]+/, '').slice(0, 70);
}

/** Name the transcript after the question that started it. */
function transcriptTitle(messages: ExportMessage[]): string {
  const first = messages.find((message) => message.role === 'user') ?? messages[0];
  return first ? titleOf(first.content, first.role) : 'Chat';
}

function stampOf(message: ExportMessage): string {
  if (!message.created_at) return '';
  const date = new Date(message.created_at);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

/** Build a readable Markdown transcript. */
export function toMarkdown(messages: ExportMessage[], title?: string): string {
  const heading = (title ?? transcriptTitle(messages)).trim();
  const lines: string[] = [`# ${heading || 'Chat'}`, ''];

  for (const message of messages) {
    if (!message.content.trim()) continue;
    const who = message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Assistant' : message.role;
    const meta = [message.model, stampOf(message)].filter(Boolean).join(' · ');
    lines.push(`## ${who}${meta ? ` — ${meta}` : ''}`, '', message.content.trim(), '');
  }

  lines.push('---', '', '_Exported from MyAIBuddy_');
  return lines.join('\n');
}

export function downloadMarkdown(messages: ExportMessage[], title?: string): void {
  const markdown = toMarkdown(messages, title);
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
  triggerDownload(blob, safeFileName(title ?? transcriptTitle(messages), 'md'));
}

/**
 * Lay out a chat transcript as a PDF document.
 *
 * The PDF library is imported on demand so it never slows down the first paint.
 */
export async function buildPdf(
  messages: ExportMessage[],
  title?: string,
): Promise<import('jspdf').jsPDF> {
  const { jsPDF } = await import('jspdf');
  const doc = new jsPDF({ unit: 'pt', format: 'a4' });
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 56;
  const maxWidth = pageWidth - margin * 2;
  const lineHeight = 14;
  const bottom = pageHeight - margin;

  const heading = toPdfSafeText((title ?? transcriptTitle(messages)).trim() || 'Chat');
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(16);
  doc.text(doc.splitTextToSize(heading, maxWidth), margin, margin);
  let y = margin + 26;

  for (const message of messages) {
    if (!message.content.trim()) continue;

    if (y > bottom - 60) {
      doc.addPage();
      y = margin;
    }

    const who = message.role === 'user' ? 'You' : 'Assistant';
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(11);
    const meta = [message.model, stampOf(message)].filter(Boolean).join(' · ');
    doc.text(toPdfSafeText(`${who}${meta ? ` — ${meta}` : ''}`), margin, y);
    y += 16;

    doc.setFont('times', 'normal');
    doc.setFontSize(11);
    for (const paragraph of toPdfSafeText(message.content).split('\n')) {
      const lines = doc.splitTextToSize(paragraph || ' ', maxWidth) as string[];
      for (const line of lines) {
        if (y > bottom) {
          doc.addPage();
          y = margin;
        }
        doc.text(line, margin, y);
        y += lineHeight;
      }
    }
    y += 12;
  }

  return doc;
}

/** Save one message (or the whole chat) as a PDF file. */
export async function downloadPdf(
  messages: ExportMessage[],
  title?: string,
): Promise<void> {
  const doc = await buildPdf(messages, title);
  doc.save(safeFileName((title ?? transcriptTitle(messages)).trim() || 'Chat', 'pdf'));
}
