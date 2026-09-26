import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useAuth } from '../lib/store';
import {
  api,
  ApiError,
  type AdminUser,
  type LogEntry,
  type ProviderStatus,
  type ThirdPartyApi,
} from '../lib/api';

const LOG_META_FIELDS = new Set(['ts', 'level', 'logger', 'message', 'request_id', 'exc_info']);

/** Everything the backend attached to a record (code, status, detail, path…). */
function logExtras(entry: LogEntry): [string, string][] {
  return Object.entries(entry)
    .filter(([key, value]) => !LOG_META_FIELDS.has(key) && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => [
      key,
      typeof value === 'string' ? value : JSON.stringify(value),
    ]);
}

interface ProviderForm {
  api_key: string;
  base_url: string;
  default_model: string;
  model_chat: string;
}

interface IntegrationForm {
  name: string;
  description: string;
  base_url: string;
  method: string;
  api_key: string;
  code: string;
  enabled: boolean;
}

interface AdminUserForm {
  email: string;
  password: string;
  display_name: string;
  roles: string[];
}

interface AdminEditForm {
  display_name: string;
  password: string;
  roles: string[];
  is_active: boolean;
}

function emptyForm(): ProviderForm {
  return { api_key: '', base_url: '', default_model: '', model_chat: '' };
}

function emptyIntegrationForm(): IntegrationForm {
  return {
    name: '',
    description: '',
    base_url: '',
    method: 'GET',
    api_key: '',
    code: '',
    enabled: true,
  };
}

function emptyUserForm(): AdminUserForm {
  return { email: '', password: '', display_name: '', roles: ['user'] };
}

function emptyEditForm(item?: AdminUser): AdminEditForm {
  return {
    display_name: item?.display_name ?? '',
    password: '',
    roles: item?.roles?.length ? [...item.roles] : ['admin'],
    is_active: item?.is_active ?? true,
  };
}

export default function SettingsView() {
  const { user, token } = useAuth();
  const [statuses, setStatuses] = useState<ProviderStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<ProviderForm>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logLevel, setLogLevel] = useState('ERROR');
  const [logsLoading, setLogsLoading] = useState(false);

  const [integrations, setIntegrations] = useState<ThirdPartyApi[]>([]);
  const [integrationsLoading, setIntegrationsLoading] = useState(false);
  const [editingIntegration, setEditingIntegration] = useState<string | 'new' | null>(null);
  const [integrationForm, setIntegrationForm] = useState<IntegrationForm>(emptyIntegrationForm());
  const [integrSaving, setIntegrSaving] = useState(false);
  const [integrBusy, setIntegrBusy] = useState<string | null>(null);
  const [integrSaved, setIntegrSaved] = useState<string | null>(null);

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [userForm, setUserForm] = useState<AdminUserForm>(emptyUserForm());
  const [userSaving, setUserSaving] = useState(false);
  const [userSaved, setUserSaved] = useState<string | null>(null);
  const [userBusy, setUserBusy] = useState<string | null>(null);

  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<AdminEditForm>(() => emptyEditForm());
  const [editSaving, setEditSaving] = useState(false);

  const isAdmin = (user?.roles ?? []).includes('admin');

  const refresh = async () => {
    const list = await api.get<ProviderStatus[]>('/providers', token);
    setStatuses(list);
  };

  const refreshIntegrations = async () => {
    if (!token) return;
    setIntegrationsLoading(true);
    try {
      const list = await api.get<ThirdPartyApi[]>('/integrations', token);
      setIntegrations(list);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load API integrations');
    } finally {
      setIntegrationsLoading(false);
    }
  };

  const refreshUsers = async () => {
    if (!token || !isAdmin) return;
    setUsersLoading(true);
    try {
      const list = await api.get<AdminUser[]>('/admin/users', token);
      setUsers(list);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load users');
    } finally {
      setUsersLoading(false);
    }
  };

  const submitUser = async (event: FormEvent) => {
    event.preventDefault();
    if (!token || !isAdmin) return;
    setUserSaving(true);
    setError(null);
    setUserSaved(null);
    try {
      const body: Record<string, unknown> = {
        email: userForm.email.trim(),
        password: userForm.password,
        roles: userForm.roles,
      };
      if (userForm.display_name.trim()) body.display_name = userForm.display_name.trim();
      await api.post<AdminUser>('/admin/users', body, token);
      setUserSaved('Created');
      setUserForm(emptyUserForm());
      await refreshUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create user');
    } finally {
      setUserSaving(false);
    }
  };

  const deactivateUser = async (item: AdminUser) => {
    if (!token || !isAdmin) return;
    if (!window.confirm(`Deactivate ${item.email}? They will no longer be able to log in.`)) return;
    setUserBusy(item.id);
    setError(null);
    try {
      await api.del(`/admin/users/${item.id}`, token);
      await refreshUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not deactivate user');
    } finally {
      setUserBusy(null);
    }
  };

  const startEditUser = (item: AdminUser) => {
    setEditForm(emptyEditForm(item));
    setEditingUserId(item.id);
    setError(null);
  };

  const cancelEditUser = () => {
    setEditingUserId(null);
    setError(null);
  };

  const saveEditUser = async (event: FormEvent) => {
    event.preventDefault();
    if (!token || !isAdmin || !editingUserId) return;
    setEditSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        display_name: editForm.display_name.trim(),
        roles: editForm.roles,
        is_active: editForm.is_active,
      };
      if (editForm.password.trim()) body.password = editForm.password.trim();
      await api.patch<AdminUser>(`/admin/users/${editingUserId}`, body, token);
      setEditingUserId(null);
      await refreshUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update user');
      setEditSaving(false);
      return;
    }
    setEditSaving(false);
  };

  const reactivateUser = async (item: AdminUser) => {
    if (!token || !isAdmin) return;
    setUserBusy(item.id);
    setError(null);
    try {
      await api.patch<AdminUser>(`/admin/users/${item.id}`, { is_active: true }, token);
      await refreshUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reactivate user');
    } finally {
      setUserBusy(null);
    }
  };

  const loadLogs = async (level = logLevel) => {
    if (!token) return;
    setLogsLoading(true);
    try {
      const entries = await api.get<LogEntry[]>(`/admin/logs?min_level=${level}&limit=100`, token);
      setLogs(entries);
    } catch (err) {
      setLogs((prev) => prev);
      setError(err instanceof ApiError ? err.message : 'Could not load logs');
    } finally {
      setLogsLoading(false);
    }
  };

  useEffect(() => {
    if (!token) return;
    api
      .get<ProviderStatus[]>('/providers', token)
      .then(setStatuses)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load providers'))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (token && isAdmin) void loadLogs();
  }, [token, isAdmin]);

  useEffect(() => {
    if (token && isAdmin) void refreshUsers();
  }, [token, isAdmin]);

  useEffect(() => {
    if (token) void refreshIntegrations();
  }, [token]);

  const startEdit = (provider: ProviderStatus) => {
    setEditing(provider.name);
    setForm({
      api_key: '',
      base_url: provider.base_url ?? '',
      default_model: provider.default_model,
      model_chat: provider.model_chat ?? provider.default_model,
    });
    setSaved(null);
  };

  const pokeProvider = async (name: string, body: Record<string, unknown>) => {
    if (!token || !isAdmin) return;
    setBusy(name);
    setError(null);
    try {
      await api.put(`/providers/${name}`, body, token);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update provider');
    } finally {
      setBusy(null);
    }
  };

  const toggleEnabled = (provider: ProviderStatus) => {
    void pokeProvider(provider.name, { enabled: !provider.enabled });
  };

  const setDefault = (provider: ProviderStatus) => {
    void pokeProvider(provider.name, { set_default: true, enabled: true });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing || !token || !isAdmin) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const body: Record<string, string | boolean | number | null> = {
        enabled: true,
      };
      if (form.api_key.trim()) body.api_key = form.api_key.trim();
      if (form.base_url.trim()) body.base_url = form.base_url.trim();
      if (form.default_model.trim()) body.default_model = form.default_model.trim();
      if (form.model_chat.trim()) body.model_chat = form.model_chat.trim();
      await api.put(`/providers/${editing}`, body, token);
      setSaved('Saved');
      await refresh();
      setEditing(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save provider');
    } finally {
      setSaving(false);
    }
  };

  const sorted = useMemo(
    () => [...statuses].sort((a, b) => Number(a.name === 'mock') - Number(b.name === 'mock')),
    [statuses],
  );

  const startAddIntegration = () => {
    setEditingIntegration('new');
    setIntegrationForm(emptyIntegrationForm());
    setIntegrSaved(null);
  };

  const startEditIntegration = (item: ThirdPartyApi) => {
    setEditingIntegration(item.id);
    setIntegrationForm({
      name: item.name,
      description: item.description ?? '',
      base_url: item.base_url,
      method: item.method ?? 'GET',
      api_key: '',
      code: item.code ?? '',
      enabled: item.enabled,
    });
    setIntegrSaved(null);
  };

  const submitIntegration = async (event: FormEvent) => {
    event.preventDefault();
    if (!editingIntegration || !token || !isAdmin) return;
    setIntegrSaving(true);
    setError(null);
    setIntegrSaved(null);
    const body: Record<string, unknown> = {
      name: integrationForm.name.trim(),
      base_url: integrationForm.base_url.trim(),
      method: integrationForm.method,
      enabled: integrationForm.enabled,
    };
    if (integrationForm.description.trim()) body.description = integrationForm.description.trim();
    if (integrationForm.api_key) body.api_key = integrationForm.api_key;
    if (integrationForm.code.trim()) body.code = integrationForm.code;
    try {
      if (editingIntegration === 'new') {
        await api.post('/integrations', body, token);
      } else {
        await api.put(`/integrations/${editingIntegration}`, body, token);
      }
      setIntegrSaved('Saved');
      await refreshIntegrations();
      setEditingIntegration(null);
      setIntegrationForm(emptyIntegrationForm());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save API integration');
    } finally {
      setIntegrSaving(false);
    }
  };

  const deleteIntegration = async (item: ThirdPartyApi) => {
    if (!token || !isAdmin) return;
    setIntegrBusy(item.id);
    setError(null);
    try {
      await api.del(`/integrations/${item.id}`, token);
      await refreshIntegrations();
      if (editingIntegration === item.id) {
        setEditingIntegration(null);
        setIntegrationForm(emptyIntegrationForm());
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete API integration');
    } finally {
      setIntegrBusy(null);
    }
  };

  if (loading) {
    return <div className="p-8 text-sm text-mab-muted">Loading settings…</div>;
  }

  return (
    <div className="h-full overflow-y-auto p-8">
      <div className="mb-6 max-w-3xl">
        <h1 className="mab-heading mb-1 text-xl">Settings</h1>
        <p className="mab-subtle text-sm">
          Manage AI providers. API keys are encrypted at rest (Fernet) and never returned to the
          browser. You can enable several providers at once — each keeps its own models — and pick
          one as the default (used when no specific provider is requested).
        </p>
        {!isAdmin && (
          <p className="mab-hint mt-2">
            You're viewing as <strong>{user?.email}</strong>. Only admins can toggle, change the
            default, or save provider config.
          </p>
        )}
      </div>

      {error && (
        <p className="mab-error mb-4 max-w-3xl" role="alert">
          {error}
        </p>
      )}

      <div className="max-w-3xl space-y-3">
        {sorted.map((provider) => (
          <div key={provider.name} className="mab-panel rounded-xl border border-mab-border p-4">
            {editing === provider.name ? (
              <form onSubmit={submit} className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold">{provider.display_name}</div>
                  <button
                    type="button"
                    className="mab-btn mab-btn-ghost mab-btn-sm"
                    onClick={() => setEditing(null)}
                  >
                    Cancel
                  </button>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="mab-field">
                    <label className="mab-label">API key</label>
                    <input
                      type="password"
                      className="mab-input"
                      placeholder={
                        provider.configured ? '•••••••• (keep to leave unchanged)' : 'New API key'
                      }
                      value={form.api_key}
                      onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                    />
                  </div>
                  <div className="mab-field">
                    <label className="mab-label">Base URL</label>
                    <input
                      type="text"
                      className="mab-input"
                      placeholder="https://…"
                      value={form.base_url}
                      onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    />
                  </div>
                  <div className="mab-field">
                    <label className="mab-label">Default model</label>
                    <input
                      type="text"
                      className="mab-input"
                      value={form.default_model}
                      onChange={(e) => setForm({ ...form, default_model: e.target.value })}
                    />
                  </div>
                  <div className="mab-field">
                    <label className="mab-label">Chat model</label>
                    <input
                      type="text"
                      className="mab-input"
                      value={form.model_chat}
                      onChange={(e) => setForm({ ...form, model_chat: e.target.value })}
                    />
                    {provider.model_suggestions?.chat && (
                      <p className="mab-hint mt-1">
                        Suggested: {provider.model_suggestions.chat.join(', ')}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="submit"
                    className="mab-btn mab-btn-primary mab-btn-md"
                    disabled={saving}
                  >
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  {saved && <span className="text-sm text-green-400">{saved}</span>}
                </div>
              </form>
            ) : (
              <div>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span
                      className={`h-2 w-2 rounded-full ${provider.enabled ? 'bg-green-400' : 'bg-gray-500'}`}
                    />
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold">{provider.display_name}</span>
                        {provider.is_default && (
                          <span className="rounded bg-[var(--mab-primary)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
                            Default
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-mab-muted">
                        {provider.is_default
                          ? 'Used by default when no provider is requested'
                          : provider.enabled
                            ? 'Enabled · used as a fallback option'
                            : 'Disabled'}
                        {provider.key_fingerprint ? ' · key set' : provider.configured ? ' · configured' : ' · no key'}
                        {provider.key_fingerprint ? ` · ${provider.key_fingerprint}` : ''}
                      </div>
                    </div>
                  </div>
                  {isAdmin && (
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        className={`mab-btn mab-btn-sm ${provider.is_default ? 'mab-btn-secondary' : 'mab-btn-ghost'}`}
                        disabled={busy === provider.name}
                        onClick={() => setDefault(provider)}
                      >
                        {provider.is_default ? 'Default' : 'Set default'}
                      </button>
                      <button
                        type="button"
                        className={`mab-btn mab-btn-sm ${provider.enabled ? 'mab-btn-danger' : 'mab-btn-secondary'}`}
                        disabled={busy === provider.name}
                        onClick={() => toggleEnabled(provider)}
                      >
                        {provider.enabled ? 'Disable' : 'Enable'}
                      </button>
                      <button
                        type="button"
                        className="mab-btn mab-btn-secondary mab-btn-sm"
                        onClick={() => startEdit(provider)}
                      >
                        Configure
                      </button>
                    </div>
                  )}
                </div>
                {provider.capabilities.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {provider.capabilities.map((cap) => (
                      <span
                        key={cap}
                        className="rounded border border-mab-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-mab-muted"
                      >
                        {cap}
                      </span>
                    ))}
                  </div>
                )}
                {provider.description && (
                  <p className="mt-2 text-xs text-mab-muted">{provider.description}</p>
                )}
                {provider.chat_unsupported && (
                  <p className="mt-1 text-xs font-medium text-amber-400">
                    Not usable for chat — enable only if you need embeddings or other features.
                  </p>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <section className="mt-10 max-w-3xl">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="mab-heading mb-1 text-lg">3rd party APIs</h2>
            <p className="mab-subtle text-sm">
              Add external API endpoints and their credentials (keys, auth headers) so tools and
              agents can call third-party services. Keys are encrypted at rest and never returned to
              the browser.
            </p>
          </div>
          {isAdmin && (
            <button
              type="button"
              className="mab-btn mab-btn-primary mab-btn-md"
              disabled={editingIntegration === 'new' || integrSaving}
              onClick={startAddIntegration}
            >
              + Add API
            </button>
          )}
        </div>

        <div className="space-y-3">
          {integrationsLoading && integrations.length === 0 ? (
            <p className="mab-subtle text-sm">Loading integrations…</p>
          ) : editingIntegration === 'new' ? (
            <form onSubmit={submitIntegration} className="mab-panel rounded-xl border border-mab-border p-4">
              <div className="mb-3 flex items-center justify-between">
                <div className="text-sm font-semibold">New API integration</div>
                <button
                  type="button"
                  className="mab-btn mab-btn-ghost mab-btn-sm"
                  onClick={() => setEditingIntegration(null)}
                >
                  Cancel
                </button>
              </div>
              <IntegrationFields
                form={integrationForm}
                onChange={(patch) => setIntegrationForm((prev) => ({ ...prev, ...patch }))}
              />
              <div className="flex items-center gap-2">
                <button type="submit" className="mab-btn mab-btn-primary mab-btn-md" disabled={integrSaving}>
                  {integrSaving ? 'Saving…' : 'Save'}
                </button>
                {integrSaved && <span className="text-sm text-green-400">{integrSaved}</span>}
              </div>
            </form>
          ) : integrations.length === 0 ? (
            <p className="mab-subtle text-sm">No third-party APIs configured yet.</p>
          ) : (
            integrations.map((item) =>
              editingIntegration === item.id ? (
                <form
                  key={item.id}
                  onSubmit={submitIntegration}
                  className="mab-panel rounded-xl border border-mab-border p-4"
                >
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-sm font-semibold">{item.name}</div>
                    <button
                      type="button"
                      className="mab-btn mab-btn-ghost mab-btn-sm"
                      onClick={() => setEditingIntegration(null)}
                    >
                      Cancel
                    </button>
                  </div>
                  <IntegrationFields
                    form={integrationForm}
                    onChange={(patch) => setIntegrationForm((prev) => ({ ...prev, ...patch }))}
                  />
                  <div className="flex items-center gap-2">
                    <button type="submit" className="mab-btn mab-btn-primary mab-btn-md" disabled={integrSaving}>
                      {integrSaving ? 'Saving…' : 'Save'}
                    </button>
                    {isAdmin && (
                      <button
                        type="button"
                        className="mab-btn mab-btn-danger mab-btn-md"
                        disabled={integrBusy === item.id}
                        onClick={() => deleteIntegration(item)}
                      >
                        Delete
                      </button>
                    )}
                    {integrSaved && <span className="text-sm text-green-400">{integrSaved}</span>}
                  </div>
                </form>
              ) : (
                <div key={item.id} className="mab-panel rounded-xl border border-mab-border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span
                        className={`h-2 w-2 rounded-full ${item.enabled ? 'bg-green-400' : 'bg-gray-500'}`}
                      />
                      <div>
                        <div className="block text-sm font-semibold">{item.name}</div>
                        <div className="text-xs break-all text-mab-muted">{item.base_url}</div>
                      </div>
                    </div>
                    {isAdmin && (
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          className="mab-btn mab-btn-ghost mab-btn-sm"
                          disabled={integrBusy === item.id}
                          onClick={() => startEditIntegration(item)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="mab-btn mab-btn-danger mab-btn-sm"
                          disabled={integrBusy === item.id}
                          onClick={() => deleteIntegration(item)}
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-mab-muted">
                    <span>method: {item.method ?? 'GET'}</span>
                    {item.key_fingerprint && <span>key: {item.key_fingerprint}</span>}
                  </div>
                  {item.description && <p className="mt-1 text-xs text-mab-muted">{item.description}</p>}
                  {item.code && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs opacity-70">Code / body</summary>
                      <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-black/30 p-2 font-mono text-[11px] text-mab-muted">
                        {item.code}
                      </pre>
                    </details>
                  )}
                </div>
              ),
            )
          )}
        </div>
      </section>

      {isAdmin && (
        <section className="mt-10 max-w-3xl">
          <div className="mb-3">
            <h2 className="mab-heading mb-1 text-lg">Users &amp; roles</h2>
            <p className="mab-subtle text-sm">
              Create accounts and assign roles. A user without the{' '}
              <code className="font-mono text-xs">admin</code> role can view settings and documents
              but can not change anything. Passwords must be at least 8 characters with an uppercase
              letter and a digit.
            </p>
          </div>

          <form
            onSubmit={submitUser}
            className="mab-panel mb-4 rounded-xl border border-mab-border p-4"
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="mab-field">
                <label className="mab-label">Email *</label>
                <input
                  type="email"
                  className="mab-input"
                  placeholder="name@example.com"
                  value={userForm.email}
                  onChange={(e) => setUserForm({ ...userForm, email: e.target.value })}
                  required
                />
              </div>
              <div className="mab-field">
                <label className="mab-label">Display name</label>
                <input
                  type="text"
                  className="mab-input"
                  placeholder="Jane Doe"
                  value={userForm.display_name}
                  onChange={(e) => setUserForm({ ...userForm, display_name: e.target.value })}
                />
              </div>
              <div className="mab-field">
                <label className="mab-label">Password *</label>
                <input
                  type="password"
                  className="mab-input"
                  placeholder="e.g. Admin@1234"
                  value={userForm.password}
                  onChange={(e) => setUserForm({ ...userForm, password: e.target.value })}
                  required
                />
              </div>
              <div className="mab-field">
                <label className="mab-label">Roles</label>
                <div className="flex gap-4 pt-2">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mab-checkbox"
                      checked={userForm.roles.includes('admin')}
                      onChange={(e) =>
                        setUserForm({
                          ...userForm,
                          roles: e.target.checked
                            ? [...userForm.roles, 'admin']
                            : userForm.roles.filter((r) => r !== 'admin'),
                        })
                      }
                    />
                    admin
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mab-checkbox"
                      checked={userForm.roles.includes('user')}
                      onChange={(e) =>
                        setUserForm({
                          ...userForm,
                          roles: e.target.checked
                            ? [...userForm.roles, 'user']
                            : userForm.roles.filter((r) => r !== 'user'),
                        })
                      }
                    />
                    user
                  </label>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="submit"
                className="mab-btn mab-btn-primary mab-btn-md"
                disabled={userSaving}
              >
                {userSaving ? 'Creating…' : '+ Create user'}
              </button>
              {userSaved && <span className="text-sm text-green-400">{userSaved}</span>}
            </div>
          </form>

          <div className="space-y-2">
            {usersLoading && users.length === 0 ? (
              <p className="mab-subtle text-sm">Loading users…</p>
            ) : users.length === 0 ? (
              <p className="mab-subtle text-sm">No users found.</p>
            ) : (
              users.map((u) =>
                editingUserId === u.id ? (
                  <form
                    key={u.id}
                    onSubmit={saveEditUser}
                    className="mab-panel rounded-lg border border-mab-border px-4 py-3"
                  >
                    <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                      <div className="text-sm font-medium">{u.display_name || u.email}</div>
                      <div className="text-xs text-mab-muted">{u.email}</div>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="mab-field">
                        <label className="mab-label">Display name</label>
                        <input
                          type="text"
                          className="mab-input"
                          value={editForm.display_name}
                          onChange={(e) =>
                            setEditForm({ ...editForm, display_name: e.target.value })
                          }
                        />
                      </div>
                      <div className="mab-field">
                        <label className="mab-label">
                          New password (leave blank to keep current)
                        </label>
                        <input
                          type="password"
                          className="mab-input"
                          placeholder="Password@123"
                          value={editForm.password}
                          onChange={(e) => setEditForm({ ...editForm, password: e.target.value })}
                        />
                        <p className="mab-subtle mt-1 text-xs">
                          At least 8 chars, one uppercase letter, one digit.
                        </p>
                      </div>
                      <div className="mab-field">
                        <label className="mab-label">Roles</label>
                        <div className="flex gap-4 pt-2">
                          <label className="flex items-center gap-2 text-sm">
                            <input
                              type="checkbox"
                              className="mab-checkbox"
                              checked={editForm.roles.includes('admin')}
                              onChange={(e) =>
                                setEditForm({
                                  ...editForm,
                                  roles: e.target.checked
                                    ? [...editForm.roles, 'admin']
                                    : editForm.roles.filter((r) => r !== 'admin'),
                                })
                              }
                            />
                            admin
                          </label>
                          <label className="flex items-center gap-2 text-sm">
                            <input
                              type="checkbox"
                              className="mab-checkbox"
                              checked={editForm.roles.includes('user')}
                              onChange={(e) =>
                                setEditForm({
                                  ...editForm,
                                  roles: e.target.checked
                                    ? [...editForm.roles, 'user']
                                    : editForm.roles.filter((r) => r !== 'user'),
                                })
                              }
                            />
                            user
                          </label>
                        </div>
                        <p className="mab-subtle mt-1 text-xs">
                          {u.email === user?.email
                            ? 'Your own admin role cannot be removed.'
                            : 'Roles take effect on their next request.'}
                        </p>
                      </div>
                      <div className="mab-field">
                        <label className="mab-label">Account status</label>
                        <div className="pt-2">
                          <select
                            className="mab-input"
                            value={editForm.is_active ? 'active' : 'inactive'}
                            onChange={(e) =>
                              setEditForm({ ...editForm, is_active: e.target.value === 'active' })
                            }
                            disabled={u.email === user?.email}
                          >
                            <option value="active">Active</option>
                            <option value="inactive">Inactive</option>
                          </select>
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 flex items-center gap-2">
                      <button
                        type="submit"
                        className="mab-btn mab-btn-primary mab-btn-sm"
                        disabled={editSaving}
                      >
                        {editSaving ? 'Saving…' : 'Save changes'}
                      </button>
                      <button
                        type="button"
                        className="mab-btn mab-btn-sm"
                        onClick={cancelEditUser}
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : (
                  <div
                    key={u.id}
                    className={`mab-panel flex flex-wrap items-center justify-between gap-3 rounded-lg border border-mab-border px-4 py-3 ${
                      !u.is_active ? 'opacity-50' : ''
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <div>
                        <div className="text-sm font-medium">
                          {u.display_name || u.email}
                          {!u.is_active && (
                            <span className="ml-2 rounded bg-red-900/40 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-red-200">
                              inactive
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-mab-muted">{u.email}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex flex-wrap gap-1">
                        {u.roles.map((role) => (
                          <span
                            key={role}
                            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                              role === 'admin'
                                ? 'bg-[var(--mab-primary)] text-white'
                                : 'border border-mab-border text-mab-muted'
                            }`}
                          >
                            {role}
                          </span>
                        ))}
                      </div>
                      <button
                        type="button"
                        className="mab-btn mab-btn-sm"
                        disabled={userBusy === u.id}
                        onClick={() => startEditUser(u)}
                      >
                        Edit
                      </button>
                      {u.email !== user?.email && u.is_active && (
                        <button
                          type="button"
                          className="mab-btn mab-btn-danger mab-btn-sm"
                          disabled={userBusy === u.id}
                          onClick={() => deactivateUser(u)}
                        >
                          {userBusy === u.id ? '…' : 'Deactivate'}
                        </button>
                      )}
                      {u.email !== user?.email && !u.is_active && (
                        <button
                          type="button"
                          className="mab-btn mab-btn-sm"
                          disabled={userBusy === u.id}
                          onClick={() => reactivateUser(u)}
                        >
                          Reactivate
                        </button>
                      )}
                    </div>
                  </div>
                )
              )
            )}
          </div>
        </section>
      )}

      {isAdmin && (
        <section className="mt-10 max-w-3xl">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="mab-heading mb-1 text-lg">System logs</h2>
              <p className="mab-subtle text-sm">
                Recent backend log records (errors, warnings, and lower-level events as captured
                in-memory). Use this to diagnose failures — e.g. a chat request going to a
                provider that can't handle it.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <label className="mab-label">Level</label>
              <select
                className="mab-input w-auto"
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value)}
              >
                {(['ERROR', 'WARNING', 'INFO', 'DEBUG'] as const).map((lv) => (
                  <option key={lv} value={lv}>
                    {lv}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="mab-btn mab-btn-secondary mab-btn-sm"
                onClick={() => loadLogs(logLevel)}
                disabled={logsLoading}
              >
                {logsLoading ? 'Loading…' : 'Refresh'}
              </button>
            </div>
          </div>

          <div className="mab-panel rounded-xl border border-mab-border p-4">
            <div className="max-h-[420px] overflow-y-auto font-mono text-xs">
              {logs.length === 0 ? (
                <p className="p-2 text-mab-muted">
                  No {logLevel.toLowerCase()}-level records in the buffer yet.
                </p>
              ) : (
                logs.map((entry, idx) => {
                  const extras = logExtras(entry);
                  return (
                    <div
                      key={`${entry.ts}-${idx}`}
                      className={`border-b border-mab-border/50 p-2 ${
                        entry.level === 'ERROR' || entry.level === 'CRITICAL'
                          ? 'text-red-300'
                          : entry.level === 'WARNING'
                            ? 'text-amber-300'
                            : 'text-mab-muted'
                      }`}
                    >
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                        <span className="text-[10px] opacity-70">{entry.ts}</span>
                        <span className="font-semibold">{String(entry.level ?? '').toUpperCase()}</span>
                        <span className="opacity-70">{String(entry.logger ?? '')}</span>
                        {entry.request_id && <span className="opacity-50">req: {entry.request_id}</span>}
                      </div>
                      <div className="mt-0.5 whitespace-pre-wrap break-words">{entry.message}</div>
                      {extras.length > 0 && (
                        <dl className="mt-1 space-y-0.5 text-[11px] opacity-80">
                          {extras.map(([key, value]) => (
                            <div key={key} className="flex gap-1.5">
                              <dt className="shrink-0 font-semibold opacity-70">{key}:</dt>
                              <dd className="min-w-0 break-words">{value}</dd>
                            </div>
                          ))}
                        </dl>
                      )}
                      {typeof entry.exc_info === 'string' && entry.exc_info && (
                        <details className="mt-1">
                          <summary className="cursor-pointer opacity-70">traceback</summary>
                          <pre className="mt-1 whitespace-pre-wrap break-words bg-black/30 p-2 text-[10px] text-red-200">
                            {entry.exc_info}
                          </pre>
                        </details>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

interface IntegrationFieldsProps {
  form: IntegrationForm;
  onChange: (patch: Partial<IntegrationForm>) => void;
}

function IntegrationFields({ form, onChange }: IntegrationFieldsProps) {
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="mab-field">
          <label className="mab-label">Name *</label>
          <input
            type="text"
            className="mab-input"
            placeholder="e.g. Google Flights"
            value={form.name}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </div>
        <div className="mab-field">
          <label className="mab-label">Base URL / endpoint *</label>
          <input
            type="text"
            className="mab-input"
            placeholder="https://serpapi.com/search.json"
            value={form.base_url}
            onChange={(e) => onChange({ base_url: e.target.value })}
          />
        </div>
        <div className="mab-field">
          <label className="mab-label">Method</label>
          <select
            className="mab-input"
            value={form.method}
            onChange={(e) => onChange({ method: e.target.value })}
          >
            <option value="GET">GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
            <option value="DELETE">DELETE</option>
          </select>
        </div>
        <div className="mab-field">
          <label className="mab-label">API key / secret</label>
          <input
            type="password"
            className="mab-input"
            placeholder="••••••••"
            value={form.api_key}
            onChange={(e) => onChange({ api_key: e.target.value })}
          />
        </div>
        <div className="mab-field sm:col-span-2">
          <label className="mab-label">Description</label>
          <input
            type="text"
            className="mab-input"
            placeholder="What is this API for?"
            value={form.description}
            onChange={(e) => onChange({ description: e.target.value })}
          />
        </div>
      </div>
      <div className="mab-field">
        <label className="mab-label">Code / request body (paste cURL, Python, JSON payload)</label>
        <textarea
          className="mab-input font-mono text-xs"
          rows={6}
          placeholder={
            'e.g. curl "https://serpapi.com/search.json?engine=google_flights&departure_id=CDG&arrival_id=AUS&currency=USD&type=2&outbound_date=2026-09-16&api_key=..."\n\nor:\nimport serpapi\nclient = serpapi.Client(api_key="...")\nresults = client.search({"engine": "google_flights", ...})'
          }
          value={form.code}
          onChange={(e) => onChange({ code: e.target.value })}
        />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="mab-checkbox"
          checked={form.enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
        />
        Enable this API integration
      </label>
    </div>
  );
}