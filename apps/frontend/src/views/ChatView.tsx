import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useAuth } from '../lib/store';
import { api, API_BASE, type ConversationSummary, type DocumentSummary, type MessageOut } from '../lib/api';
import DocumentAttachments from '../components/DocumentAttachments';
import MessageActions from '../components/MessageActions';

const AGENT_KINDS: { value: string; label: string }[] = [
  { value: 'chat', label: 'General chat' },
  { value: 'rag', label: 'Document Q&A (RAG)' },
  { value: 'web_research', label: 'Web research' },
  { value: 'all_rounder', label: 'All-rounder agent' },
  { value: 'recommendation', label: 'Finance · News · Trip' },
  { value: 'coding', label: 'Coding assistant' },
  { value: 'image', label: 'Image chat' },
];

type StreamEvent =
  | { type: 'chat_start'; user_message_id: string }
  | { type: 'delta'; content: string }
  | { type: 'chat_end'; message: MessageOut }
  | { type: 'error'; detail: string };

async function streamChat(
  conversationId: string,
  content: string,
  token: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
  documentIds?: string[],
): Promise<void> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ content, stream: true, ...(documentIds?.length ? { document_ids: documentIds } : {}) }),
    signal,
  });

  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error?.message ?? `Stream failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() ?? '';
    for (const block of blocks) {
      for (const line of block.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            const raw = JSON.parse(line.slice(6)) as Record<string, unknown>;
            onEvent({
              type: (raw.type ?? raw.event) as StreamEvent['type'],
              ...raw,
            } as unknown as StreamEvent);
          } catch {
            // skip malformed frames
          }
        }
      }
    }
    void blocks;
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** Marker model used by the in-progress assistant bubble. */
const STREAM_MODEL = '__stream__';
/** The thinking dots must stay up briefly, otherwise they flash and vanish. */
const MIN_LOADING_MS = 1200;
/** Temporary id for the user's own message until the server confirms it. */
const PENDING_USER_ID = '__pending_user__';

function userMessage(content: string, id = PENDING_USER_ID): MessageOut {
  return {
    id,
    role: 'user',
    content,
    model: null,
    created_at: new Date().toISOString(),
  } as MessageOut;
}

function streamMessage(content: string): MessageOut {
  return {
    id: `${STREAM_MODEL}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: 'assistant',
    content,
    model: STREAM_MODEL,
    created_at: new Date().toISOString(),
  } as MessageOut;
}

/** Give the user's bubble its saved id so it stops looking temporary. */
function confirmUserMessage(previous: MessageOut[], savedId: string): MessageOut[] {
  const index = previous.findIndex((m) => m.id === PENDING_USER_ID);
  if (index < 0) return previous;
  const next = previous.slice();
  next[index] = { ...(next[index] as MessageOut), id: savedId };
  return next;
}

function isStreamMessage(message: MessageOut | undefined): boolean {
  return message?.model === STREAM_MODEL;
}

/** Append text to the streaming bubble, creating it on first use. */
function appendToStream(previous: MessageOut[], text: string): MessageOut[] {
  const index = previous.findIndex(isStreamMessage);
  if (index < 0) return [...previous, streamMessage(text)];
  const next = previous.slice();
  next[index] = {
    ...(next[index] as MessageOut),
    content: (next[index]?.content ?? '') + text,
  };
  return next;
}

/**
 * Swap the streaming bubble for the saved message, keeping any text that was
 * already shown so the answer can never disappear. The bubble is found by marker
 * instead of by position, so real messages are never removed by accident.
 */
function settleStream(previous: MessageOut[], final: MessageOut | null): MessageOut[] {
  const index = previous.findIndex(isStreamMessage);
  const streamed = index >= 0 ? (previous[index]?.content ?? '') : '';
  const settled = final
    ? { ...final, content: final.content || streamed }
    : { ...streamMessage(streamed), content: streamed, model: null };
  if (index < 0) return [...previous, settled];
  const next = previous.slice();
  next[index] = settled;
  return next;
}

/** Throw away a still-empty placeholder, e.g. when the request failed. */
function dropEmptyStream(previous: MessageOut[]): MessageOut[] {
  const index = previous.findIndex(isStreamMessage);
  if (index >= 0 && (previous[index]?.content ?? '') === '') {
    return previous.filter((_, i) => i !== index);
  }
  return previous;
}

/** End the stream after a failure: keep any text, but stop the blinking cursor. */
function closeStream(previous: MessageOut[]): MessageOut[] {
  return settleStream(dropEmptyStream(previous), null);
}

function RobotAvatar() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 3.5V2M8.5 4.5h7"
        stroke="var(--mab-accent)"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <circle cx="12" cy="2" r="1.4" fill="var(--mab-accent)" />
      <rect x="3.5" y="6" width="17" height="13" rx="3.5" fill="var(--mab-surface-2)" stroke="var(--mab-accent)" strokeWidth="1.4" />
      <circle cx="9.2" cy="12.5" r="1.6" fill="var(--mab-accent)" />
      <circle cx="14.8" cy="12.5" r="1.6" fill="var(--mab-accent)" />
      <path d="M8.8 16.5a3.4 3.4 0 0 0 6.4 0" stroke="var(--mab-accent)" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function ThinkingLoader({ text = 'Thinking…' }: { text?: string }) {
  return (
    <span className="mab-thinking" role="status" aria-live="polite">
      <span className="mab-thinking-avatar">
        <RobotAvatar />
      </span>
      <span className="mab-thinking-dots" aria-hidden="true">
        <span className="mab-thinking-dot" />
        <span className="mab-thinking-dot" />
        <span className="mab-thinking-dot" />
      </span>
      <span className="mab-subtle text-sm">{text}</span>
    </span>
  );
}

export default function ChatView() {
  const { token, user } = useAuth();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [draft, setDraft] = useState('');
  const [loadingList, setLoadingList] = useState(true);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showKinds, setShowKinds] = useState(false);
  const [creating, setCreating] = useState(false);
  const [attachedDocs, setAttachedDocs] = useState<DocumentSummary[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // A conversation keeps its null title; the signed-in user names it for now.
  const defaultChatTitle = (user?.display_name || user?.email || 'New chat').trim();

  // Settled, non-empty turns — the transcript offered by the Export menu.
  const exportableMessages = useMemo(
    () => messages.filter((msg) => !isStreamMessage(msg) && msg.content.trim()),
    [messages],
  );

  const refreshList = useCallback(async () => {
    if (!token) return;
    try {
      const page = await api.get<{
        items: ConversationSummary[];
        total: number;
      }>('/conversations?page=1&page_size=50', token);
      setConversations(page.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load conversations');
    } finally {
      setLoadingList(false);
    }
  }, [token]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const loadDocuments = useCallback(async () => {
    if (!token) return;
    try {
      const page = await api.get<{ items: DocumentSummary[]; total: number }>(
        '/documents?page=1&page_size=200',
        token,
      );
      setAttachedDocs(page.items);
    } catch {
      setAttachedDocs([]);
    }
  }, [token]);

  const openConversation = useCallback(
    async (id: string) => {
      if (!token) return;
      abortRef.current?.abort();
      abortRef.current = null;
      setStreaming(false);
      setActiveId(id);
      setLoadingMsgs(true);
      setError(null);
      try {
        const detail = await api.get<{ messages: MessageOut[] }>(`/conversations/${id}`, token);
        setMessages(detail.messages ?? []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not load messages');
        setMessages([]);
      } finally {
        setLoadingMsgs(false);
      }
      void loadDocuments();
    },
    [token, loadDocuments],
  );

  const createConversation = useCallback(
    async (chosenKind: string) => {
      if (!token) return;
      setCreating(true);
      try {
        const created = await api.post<ConversationSummary>('/conversations', {
          title: null,
          agent_kind: chosenKind,
        }, token);
        setConversations((prev) => [created, ...prev]);
        void openConversation(created.id);
        setShowKinds(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not create conversation');
      } finally {
        setCreating(false);
      }
    },
    [token, openConversation],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      if (!token) return;
      await api.del(`/conversations/${id}`, token);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeId === id) {
        setActiveId(null);
        setMessages([]);
        setAttachedDocs([]);
      }
    },
    [token, activeId],
  );

  const handleUploaded = useCallback((uploaded: DocumentSummary[]) => {
    setAttachedDocs((prev) => {
      const known = new Set(prev.map((doc) => doc.id));
      return [...uploaded.filter((doc) => !known.has(doc.id)), ...prev];
    });
  }, []);

  const handleDeleteDoc = useCallback(
    async (doc: DocumentSummary) => {
      if (!token) return;
      if (!window.confirm(`Delete "${doc.filename}"? It is removed from the shared knowledge base.`)) {
        return;
      }
      try {
        await api.del(`/documents/${doc.id}`, token);
        setAttachedDocs((prev) => prev.filter((d) => d.id !== doc.id));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to delete document');
      }
    },
    [token],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, streaming]);

  const readyDocIds = attachedDocs.filter((doc) => doc.status === 'ready').map((doc) => doc.id);

  const send = useCallback(
    async (override?: string) => {
      const content = (override ?? draft).trim();
      if (!token || !activeId || !content || streaming) return;
      if (!override) setDraft('');
      setError(null);

      // Show the question and the thinking dots straight away, whatever the reply speed is.
      setMessages((prev) => [...prev, userMessage(content), streamMessage('')]);
      setStreaming(true);

      const startedAt = Date.now();
      let buffered = '';
      let revealed = false;
      let aborted = false;
      let settled = false;
      let finalMessage: MessageOut | null = null;
      let timer: ReturnType<typeof setTimeout> | null = null;

      const stopTimer = () => {
        if (timer) clearTimeout(timer);
        timer = null;
      };

      const settle = () => {
        if (settled) return;
        settled = true;
        stopTimer();
        setMessages((prev) => settleStream(prev, finalMessage));
        setStreaming(false);
        void refreshList();
      };

      // Hold the dots for a beat so they never flash and vanish, then show the text.
      const reveal = () => {
        if (aborted || settled) return;
        const remaining = MIN_LOADING_MS - (Date.now() - startedAt);
        if (!revealed && remaining > 0) {
          if (!timer) timer = setTimeout(reveal, remaining);
          return;
        }
        if (!revealed) {
          revealed = true;
          if (buffered) {
            setMessages((prev) => appendToStream(prev, buffered));
            buffered = '';
          }
        }
        if (finalMessage) settle();
      };

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamChat(
          activeId,
          content,
          token,
          (event) => {
            if (event.type === 'chat_start') {
              setMessages((prev) => confirmUserMessage(prev, event.user_message_id));
            }
            if (event.type === 'delta') {
              if (revealed) setMessages((prev) => appendToStream(prev, event.content));
              else {
                buffered += event.content;
                reveal();
              }
            }
            if (event.type === 'chat_end') {
              finalMessage = event.message;
              reveal();
            }
            if (event.type === 'error') {
              setError(event.detail);
              stopTimer();
              setMessages((prev) => closeStream(prev));
              setStreaming(false);
              controller.abort();
            }
          },
          controller.signal,
          readyDocIds.length ? readyDocIds : undefined,
        );
        reveal();
      } catch (err) {
        aborted = err instanceof DOMException && err.name === 'AbortError';
        stopTimer();
        if (aborted) return;
        setError(err instanceof Error ? err.message : 'Stream failed');
        setMessages((prev) => closeStream(prev));
        setStreaming(false);
        void refreshList();
      } finally {
        abortRef.current = null;
        stopTimer();
        if (!settled && !aborted) {
          // The stream ended without a saved reply: keep the text, stop the blinking cursor.
          settle();
        }
        setStreaming(false);
      }
    },
    [draft, token, activeId, streaming, readyDocIds, refreshList],
  );

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  };

  return (
    <div className="flex h-full">
      <div className="flex w-72 shrink-0 flex-col border-r border-mab-border">
        <div className="p-3">
          <button
            type="button"
            className="mab-btn mab-btn-primary mab-btn-md w-full"
            disabled={creating}
            onClick={() => setShowKinds(true)}
          >
            + New chat
          </button>
          {showKinds && (
            <div className="mab-panel mt-2 space-y-1 rounded-md border border-mab-border p-2">
              <p className="mab-subtle px-1 pb-1 text-xs">Pick an assistant:</p>
              {AGENT_KINDS.map((k) => (
                <button
                  key={k.value}
                  type="button"
                  className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-[var(--mab-primary-soft)]"
                  onClick={() => void createConversation(k.value)}
                >
                  {k.label}
                </button>
              ))}
              <button
                type="button"
                className="mab-btn mab-btn-ghost mab-btn-sm w-full"
                onClick={() => setShowKinds(false)}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {loadingList ? (
            <p className="mab-subtle p-3 text-sm">Loading…</p>
          ) : conversations.length === 0 ? (
            <p className="mab-subtle p-3 text-sm">No conversations yet. Start a new chat.</p>
          ) : (
            conversations.map((conv) => (
              <div
                key={conv.id}
                className={`group mb-1 cursor-pointer rounded-md border px-3 py-2 ${
                  activeId === conv.id
                    ? 'border-[var(--mab-primary)] bg-[var(--mab-primary-soft)]'
                    : 'border-transparent hover:border-mab-border'
                }`}
                onClick={() => void openConversation(conv.id)}
              >
                <div className="flex items-center justify-between">
                  <div className="truncate text-sm font-medium">
                    {conv.title ?? defaultChatTitle}
                  </div>
                  <button
                    type="button"
                    className="mab-btn mab-btn-ghost mab-btn-sm text-xs opacity-0 group-hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      void deleteConversation(conv.id);
                    }}
                    title="Delete"
                  >
                    ✕
                  </button>
                </div>
                <div className="mab-subtle text-xs">
                  {conv.agent_kind ?? 'chat'} · {formatTime(conv.last_message_at)}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="flex flex-1 flex-col">
        {!activeId ? (
          <div className="grid flex-1 place-items-center">
            <div className="max-w-sm text-center">
              <div className="mb-3 text-5xl">💬</div>
              <h2 className="mab-heading mb-2 text-lg">Start a conversation</h2>
              <p className="mab-subtle text-sm">
                Create a new chat and pick the kind of assistant you want — general chat, document
                Q&A, web research, coding, or the all-rounder agent.
              </p>
            </div>
          </div>
        ) : (
          <>
            <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-6">
              {loadingMsgs ? (
                <p className="mab-subtle text-sm">Loading messages…</p>
              ) : messages.length === 0 ? (
                <p className="mab-subtle text-sm">Say hello to get started.</p>
              ) : (
                messages.map((msg) =>
                  msg.role === 'user' ? (
                    <div key={msg.id} className="flex justify-end">
                      <div className="max-w-[80%] rounded-2xl bg-[var(--mab-primary)] px-4 py-2 text-white">
                        <div className="whitespace-pre-wrap break-words text-sm">{msg.content}</div>
                        <div className="mt-1 flex items-end justify-between gap-2">
                          <MessageActions message={msg} conversation={exportableMessages} tone="dark" />
                          <span className="shrink-0 text-[10px] text-white/60">
                            {formatTime(msg.created_at)}
                          </span>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div key={msg.id} className="flex items-start justify-start gap-2">
                      <span className="mab-msg-avatar mt-1">
                        <RobotAvatar />
                      </span>
                      <div className="max-w-[85%] rounded-2xl border border-mab-border bg-mab-panel px-4 py-2">
                        {msg.model !== STREAM_MODEL && (
                          <div className="mb-0.5 text-[10px] uppercase tracking-wide text-mab-muted">
                            {msg.model ?? 'assistant'}
                          </div>
                        )}
                        {isStreamMessage(msg) && !msg.content ? (
                          <ThinkingLoader text="Thinking…" />
                        ) : isStreamMessage(msg) ? (
                          <div className="whitespace-pre-wrap break-words text-sm">
                            {msg.content}
                            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse align-middle bg-[var(--mab-accent)]" />
                          </div>
                        ) : (
                          <div className="whitespace-pre-wrap break-words text-sm">{msg.content}</div>
                        )}
                        {!isStreamMessage(msg) && (
                          <MessageActions message={msg} conversation={exportableMessages} />
                        )}
                      </div>
                    </div>
                  ),
                )
              )}
              {error && <p className="mab-error text-sm">{error}</p>}
            </div>

            <div className="border-t border-mab-border p-4">
              <DocumentAttachments
                docs={attachedDocs}
                onUploaded={handleUploaded}
                onDelete={(doc) => void handleDeleteDoc(doc)}
                inputRef={fileInputRef}
              />
              <div className="mab-panel flex items-end gap-2 rounded-xl border border-mab-border p-2">
                <button
                  type="button"
                  className="mab-btn mab-btn-ghost mab-btn-sm shrink-0 px-2"
                  onClick={() => fileInputRef.current?.click()}
                  title="Attach documents"
                  aria-label="Attach documents"
                >
                  📎
                </button>
                <textarea
                  className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none"
                  placeholder="Ask anything…  (Enter to send, Shift+Enter for a new line)"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={onKeyDown}
                  rows={Math.min(4, Math.max(1, draft.split('\n').length))}
                  disabled={streaming}
                />
                <button
                  type="button"
                  className="mab-btn mab-btn-primary mab-btn-md"
                  disabled={streaming || !draft.trim()}
                  onClick={() => void send()}
                >
                  Send
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}