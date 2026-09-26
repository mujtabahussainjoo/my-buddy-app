import { useRef, useState, type DragEvent, type RefObject } from 'react';
import {
  MAX_UPLOAD_FILES,
  MAX_UPLOAD_MB,
  api,
  type BulkUploadResult,
  type DocumentSummary,
} from '../lib/api';
import { useAuth } from '../lib/store';

const BATCH_SIZE = 4;

type UploadState = 'uploading' | 'failed';

interface QueuedUpload {
  key: string;
  file: File;
  state: UploadState;
  error?: string;
}

interface DocumentAttachmentsProps {
  docs: DocumentSummary[];
  onUploaded: (docs: DocumentSummary[]) => void;
  onDelete: (doc: DocumentSummary) => void;
  inputRef?: RefObject<HTMLInputElement | null>;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentAttachments({
  docs,
  onUploaded,
  onDelete,
  inputRef,
}: DocumentAttachmentsProps) {
  const { token } = useAuth();
  const [queue, setQueue] = useState<QueuedUpload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const localInputRef = useRef<HTMLInputElement | null>(null);
  const fileInputRef = inputRef ?? localInputRef;

  const addFiles = (incoming: File[], reservedSlots = 0) => {
    if (!token) return;
    setMessage(null);
    // Any file type is welcome; only the size and the per-request count are capped.
    const accepted: File[] = [];
    const rejected: string[] = [];

    for (const file of incoming) {
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) rejected.push(`${file.name}: over ${MAX_UPLOAD_MB} MB`);
      else accepted.push(file);
    }

    if (rejected.length) setMessage(rejected.join(' · '));
    if (!accepted.length) return;

    const slots = Math.max(0, MAX_UPLOAD_FILES - docs.length - queue.length - reservedSlots);
    const batch = accepted.slice(0, slots);
    if (accepted.length > slots) {
      setMessage(
        `Only ${MAX_UPLOAD_FILES} documents can be attached at once; the rest were skipped`,
      );
    }
    if (!batch.length) return;

    const stamp = Date.now();
    const pending: QueuedUpload[] = batch.map((file, index) => ({
      key: `${stamp}-${index}-${file.name}`,
      file,
      state: 'uploading',
    }));
    setQueue((prev) => [...prev, ...pending]);

    void (async () => {
      for (let index = 0; index < pending.length; index += BATCH_SIZE) {
        const slice = pending.slice(index, index + BATCH_SIZE);
        const keys = slice.map((item) => item.key);
        try {
          const result: BulkUploadResult = await api.uploadDocuments(
            slice.map((item) => item.file),
            token,
          );
          if (result.items.length) onUploaded(result.items);
          const byName = new Map(result.failed.map((item) => [item.filename, item.error]));
          setQueue((prev) =>
            prev
              .filter((item) => !keys.includes(item.key))
              .concat(
                slice
                  .filter((item) => byName.has(item.file.name))
                  .map((item) => ({
                    key: item.key,
                    file: item.file,
                    state: 'failed' as const,
                    error: byName.get(item.file.name),
                  })),
              ),
          );
        } catch (err) {
          const detail = err instanceof Error ? err.message : 'Upload failed';
          setQueue((prev) =>
            prev.map((item) =>
              keys.includes(item.key) ? { ...item, state: 'failed', error: detail } : item,
            ),
          );
          break;
        }
      }
    })();
  };

  const retry = (key: string) => {
    const item = queue.find((entry) => entry.key === key);
    if (!item) return;
    setQueue((prev) => prev.filter((entry) => entry.key !== key));
    addFiles([item.file], 1);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files ?? []);
    if (files.length) addFiles(files);
  };

  const readyDocs = docs.filter((doc) => doc.status === 'ready');
  const totalChunks = readyDocs.reduce((sum, doc) => sum + (doc.chunk_count ?? 0), 0);
  const totalBytes = readyDocs.reduce((sum, doc) => sum + (doc.size_bytes ?? 0), 0);
  const busy = queue.some((item) => item.state === 'uploading');

  return (
    <div
      className={`mb-2 rounded-xl border p-2 transition-colors ${
        dragging
          ? 'border-[var(--mab-accent)] bg-[var(--mab-primary-soft)]'
          : 'border-mab-border bg-mab-panel'
      }`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        setDragging(false);
      }}
      onDrop={onDrop}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          addFiles(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />

      <div className="mb-1 flex items-center justify-between gap-2 px-1">
        <span className="mab-subtle text-xs font-medium">
          Documents
          {docs.length > 0 && (
            <span className="font-normal">
              {' '}
              · {readyDocs.length}/{docs.length} ready · {totalChunks} chunks · {formatBytes(totalBytes)}
            </span>
          )}
        </span>
        <button
          type="button"
          className="mab-btn mab-btn-ghost mab-btn-sm text-xs"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
          title="Attach one or more documents"
        >
          {busy ? 'Uploading…' : '+ Add documents'}
        </button>
      </div>

      {docs.length === 0 && queue.length === 0 ? (
        <p className="mab-subtle px-1 pb-1 text-xs">
          Drop any files here or use “Add documents” — code, docs, notes, images — and the
          assistant reads every attached file when it answers.
        </p>
      ) : (
        <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto px-1 pb-1">
          {docs.map((doc) => (
            <span
              key={doc.id}
              title={`${doc.filename} · ${doc.chunk_count} chunks · ${formatBytes(doc.size_bytes)}`}
              className="inline-flex items-center gap-1 rounded-md border border-mab-border bg-[var(--mab-primary-soft)] px-2 py-1 text-xs"
            >
              <span className="max-w-[140px] truncate">{doc.filename}</span>
              <span className="text-mab-muted" title={doc.chunk_count ? 'text chunks' : 'no text extracted'}>
                {doc.chunk_count ? `${doc.chunk_count}▦` : 'no text'}
              </span>
              {doc.status !== 'ready' && <span className="text-[var(--mab-danger)]">⚠</span>}
              <button
                type="button"
                className="ml-1 text-mab-muted hover:text-[var(--mab-danger)]"
                onClick={() => onDelete(doc)}
                title={`Remove ${doc.filename} from the knowledge base`}
              >
                ✕
              </button>
            </span>
          ))}
          {queue.map((item) => (
            <span
              key={item.key}
              className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs ${
                item.state === 'failed'
                  ? 'border-[var(--mab-danger)] text-[var(--mab-danger)]'
                  : 'border-mab-border text-mab-muted'
              }`}
              title={item.error}
            >
              <span className="max-w-[140px] truncate">{item.file.name}</span>
              {item.state === 'uploading' ? (
                <span className="animate-pulse">…</span>
              ) : (
                <>
                  <span className="max-w-[160px] truncate">{item.error ?? 'failed'}</span>
                  <button type="button" onClick={() => retry(item.key)} title="Retry">
                    ↻
                  </button>
                  <button
                    type="button"
                    onClick={() => setQueue((prev) => prev.filter((e) => e.key !== item.key))}
                    title="Dismiss"
                  >
                    ✕
                  </button>
                </>
              )}
            </span>
          ))}
        </div>
      )}

      {message && (
        <p className="mab-error px-1 pb-1 text-xs" role="alert">
          {message}
        </p>
      )}
    </div>
  );
}
