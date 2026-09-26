import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useAuth } from '../lib/store';
import {
  MAX_UPLOAD_MB,
  api,
  type DocumentSummary,
  type Paginated,
  type UploadFailure,
} from '../lib/api';

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB';
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentsView() {
  const { user, token } = useAuth();
  const isAdmin = (user?.roles ?? []).includes('admin');
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failures, setFailures] = useState<UploadFailure[]>([]);
  const [available, setAvailable] = useState<boolean | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .get<Paginated<DocumentSummary>>('/documents?page=1&page_size=100', token)
      .then((page) => {
        setAvailable(true);
        setDocs(page.items);
      })
      .catch(() => setAvailable(false))
      .finally(() => setLoading(false));
  }, [token]);

  const upload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem('files') as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    if (!token || !files.length) return;
    const tooBig = files.filter((file) => file.size > MAX_UPLOAD_MB * 1024 * 1024);
    if (tooBig.length) {
      setError(`${tooBig.map((f) => f.name).join(', ')} exceed the ${MAX_UPLOAD_MB} MB limit`);
      return;
    }
    setUploading(true);
    setError(null);
    setFailures([]);
    try {
      const result = await api.uploadDocuments(files, token);
      setDocs((prev) => [...result.items, ...prev]);
      setFailures(result.failed);
      input.value = '';
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const deleteDoc = async (doc: DocumentSummary) => {
    if (!token) return;
    if (!window.confirm(`Delete "${doc.filename}"? This removes it from the shared knowledge base.`)) {
      return;
    }
    setUploading(true);
    setError(null);
    try {
      await api.del(`/documents/${doc.id}`, token);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setUploading(false);
    }
  };

  if (loading) {
    return <div className="p-8 text-sm text-mab-muted">Loading documents…</div>;
  }

  if (available === false) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="max-w-md text-center">
          <div className="mb-3 text-5xl">📄</div>
          <h2 className="mab-heading mb-2 text-lg">Documents are coming online</h2>
          <p className="mab-subtle text-sm">
            The upload + indexing API (PDF, DOCX, PPTX, XLSX, scanned OCR, text) is the next backend
            increment. As soon as it's up this screen becomes your library — you'll drop files here
            and chat over them in the <strong>Document Q&amp;A</strong> assistant.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-8">
      <div className="mb-6 max-w-3xl">
        <h1 className="mab-heading mb-1 text-xl">Documents</h1>
        <p className="mab-subtle text-sm">
          Upload files to build your knowledge base. Extracted text is chunked and indexed for
          keyword + semantic retrieval.
        </p>
      </div>

      <div className="mb-8 max-w-3xl">
        <form onSubmit={upload} className="mab-panel rounded-xl border border-mab-border p-4">
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileInputRef}
              type="file"
              name="files"
              multiple
              className="block max-w-xs text-sm"
              required
            />
            <button type="submit" className="mab-btn mab-btn-primary mab-btn-md" disabled={uploading}>
              {uploading ? 'Uploading…' : 'Upload'}
            </button>
          </div>
          <p className="mab-hint mt-2">
            Any file type — source code, docs, notes, images. Text-like files (and UTF-8 text of
            any extension) are chunked and searchable; binaries are stored without text. Select
            several files at once, up to 50 MB each.
          </p>
          {error && (
            <p className="mab-error mt-2" role="alert">
              {error}
            </p>
          )}
          {failures.length > 0 && (
            <ul className="mab-error mt-2 text-xs">
              {failures.map((failure) => (
                <li key={failure.filename}>
                  {failure.filename}: {failure.error}
                </li>
              ))}
            </ul>
          )}
        </form>
      </div>

      <div className="max-w-3xl space-y-2">
        {docs.length === 0 ? (
          <p className="mab-subtle text-sm">No documents yet.</p>
        ) : (
          docs.map((doc) => (
            <div key={doc.id} className="mab-panel flex items-center justify-between gap-3 rounded-lg border border-mab-border px-4 py-3">
              <div className="flex items-center gap-3">
                <span className="text-xl">📄</span>
                <div>
                  <div className="text-sm font-medium">{doc.filename}</div>
                  <div className="text-xs text-mab-muted">
                    {doc.content_type} · {formatBytes(doc.size_bytes)} · {doc.chunk_count} chunks
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="mab-badge mab-badge-neutral">{doc.status}</span>
                {(isAdmin || doc.is_owner) && (
                  <button
                    type="button"
                    className="mab-btn mab-btn-danger mab-btn-sm"
                    disabled={uploading}
                    onClick={() => void deleteDoc(doc)}
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))
        )}
        <p className="mab-subtle pt-4 text-xs">
          Signed in as {user?.email}. You can delete the documents you uploaded
          {isAdmin ? '; as an admin, any document.' : '.'}
        </p>
      </div>
    </div>
  );
}
