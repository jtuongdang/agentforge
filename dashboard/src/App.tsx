import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { invoke, isTauri } from '@tauri-apps/api/core'
import { open } from '@tauri-apps/plugin-dialog'
import './App.css'

let apiBasePromise: Promise<string> | null = null
let apiTokenPromise: Promise<string> | null = null

async function getApiBase(): Promise<string> {
  if (!isTauri()) return ''
  if (!apiBasePromise) {
    apiBasePromise = (async () => {
      for (let attempt = 0; attempt < 80; attempt++) {
        try { return await invoke<string>('backend_url') }
        catch { await new Promise(resolve => window.setTimeout(resolve, 250)) }
      }
      throw new Error('Local backend did not become ready')
    })().catch(error => { apiBasePromise = null; throw error })
  }
  return apiBasePromise
}

async function getApiToken(): Promise<string> {
  if (!apiTokenPromise) {
    apiTokenPromise = getApiBase().then(base => fetch(`${base}/api/session`)).then(async response => {
      if (!response.ok) throw new Error('Local API session could not be established')
      const session = await response.json() as { access_token: string }
      return session.access_token
    }).catch(error => { apiTokenPromise = null; throw error })
  }
  return apiTokenPromise
}
type Agent = { id: string; name: string; adapter: string; capabilities: string[]; file_globs: string[]; max_parallel: number; available: boolean; availability_note: string; enabled: boolean; credential_status: string; credential_checked_at?: string | null; credential_note: string; credential_usage: { groups?: Record<string, unknown>[] } }
type Repo = { id: string; name: string; path: string; default_branch: string; remote: string | null; validation_profile: string; validation_command: string[] }
type RepoBranch = { name: string; local: boolean; remote: boolean }
type Task = { id: string; title: string; objective: string; acceptance_criteria: string[]; priority: string; status: string; repository_id: string; repository_name: string; repository_remote?: string | null; repository_default_branch?: string; base_branch?: string; repository_validation_profile?: string; repository_validation_command?: string[]; agent_name?: string; assigned_agent_id?: string; preferred_agent_id?: string; file_globs: string[]; required_capabilities: string[]; dependencies: string[]; contract_id?: string; branch?: string; worktree?: string; retries: number; max_retries: number; validation: Record<string, unknown>; error?: string; pr_url?: string; created_at: string }
type Contract = { id: string; title: string; body: string; state: string; version: number }
type EventRow = { id: number; kind: string; message: string; created_at: string; details: Record<string, unknown> }
type Nav = 'Overview' | 'Tasks' | 'Agents' | 'Contracts' | 'History' | 'Storage'
type StorageMetrics = { measured_at: string; data_bytes: number; categories: Record<string, number>; database_files: Record<string, number>; sqlite_logical_bytes: number; sqlite_unused_bytes: number; disk: { total_bytes: number; free_bytes: number; used_percent: number }; counts: { repositories: number; agents: number; tasks: number; runs: number; events: number; active_tasks: number } }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getApiToken()
  const base = await getApiBase()
  const response = await fetch(`${base}/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...init?.headers } })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail) } catch { /* keep default */ }
    throw new Error(detail)
  }
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return response.json() as Promise<T>
  return response.text() as Promise<T>
}

const date = (value?: string) => value ? new Date(value.replace(' ', 'T')).toLocaleString() : '—'
const statusLabel: Record<string, string> = {
  queued: 'Queued', planning: 'Planning', waiting_for_contract: 'Contract gate', waiting_for_scope: 'Waiting for file scope', assigned: 'Assigned', running: 'Running',
  validating: 'Validating', recovering: 'Retrying', handoff_ready: 'IDE handoff', awaiting_pr_approval: 'Needs PR approval',
  publishing: 'Publishing', draft_pr: 'Draft PR', completed: 'Complete', failed: 'Failed', blocked: 'Blocked', cancelled: 'Cancelled',
}

function App() {
  const [nav, setNav] = useState<Nav>('Overview')
  const [agents, setAgents] = useState<Agent[]>([])
  const [repos, setRepos] = useState<Repo[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [contracts, setContracts] = useState<Contract[]>([])
  const [selected, setSelected] = useState<Task | null>(null)
  const [events, setEvents] = useState<EventRow[]>([])
  const [showTaskForm, setShowTaskForm] = useState(false)
  const [showContractForm, setShowContractForm] = useState(false)
  const [showAgentForm, setShowAgentForm] = useState(false)
  const [showRepoForm, setShowRepoForm] = useState(false)
  const [approval, setApproval] = useState<Task | null>(null)
  const [error, setError] = useState('')
  const [health, setHealth] = useState<{ ok: boolean } | null>(null)
  const [storage, setStorage] = useState<StorageMetrics | null>(null)
  const [storageError, setStorageError] = useState('')
  const [storageBusy, setStorageBusy] = useState(false)
  const [busyAgentId, setBusyAgentId] = useState<string | null>(null)
  const [taskRepositoryId, setTaskRepositoryId] = useState('')
  const [taskBaseBranch, setTaskBaseBranch] = useState('')
  const [branchOptions, setBranchOptions] = useState<RepoBranch[]>([])

  const pickRepositoryPath = async () => {
    try {
      const selectedPath = await open({ directory: true, multiple: false, title: 'Choose a Git repository folder' })
      if (selectedPath && typeof selectedPath === 'string') {
        const pathInput = document.querySelector<HTMLInputElement>('input[name="path"]')
        if (pathInput) pathInput.value = selectedPath
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Folder picker could not be opened') }
  }

  const refresh = useCallback(async () => {
    try {
      const [a, r, t, c, h] = await Promise.all([
        api<Agent[]>('/agents'), api<Repo[]>('/repositories'), api<Task[]>('/tasks'), api<Contract[]>('/contracts'), api<{ok:boolean}>('/health'),
      ])
      setAgents(a); setRepos(r); setTasks(t); setContracts(c); setHealth(h)
      setSelected(current => current ? t.find(task => task.id === current.id) ?? current : null)
      setError('')
    } catch (e) { setError(e instanceof Error ? e.message : 'API connection failed') }
  }, [])

  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 3500); return () => window.clearInterval(timer) }, [refresh])
  useEffect(() => {
    if (!showTaskForm || !taskRepositoryId) return
    let active = true
    setBranchOptions([])
    api<{branches: RepoBranch[]}>(`/repositories/${taskRepositoryId}/branches`)
      .then(result => { if (active) setBranchOptions(result.branches) })
      .catch(e => { if (active) { setBranchOptions([]); setError(e instanceof Error ? e.message : 'Could not load repository branches') } })
    return () => { active = false }
  }, [showTaskForm, taskRepositoryId])
  const refreshStorage = useCallback(async () => {
    setStorageBusy(true)
    try { setStorage(await api<StorageMetrics>('/storage')); setStorageError('') }
    catch (e) { setStorageError(e instanceof Error ? e.message : 'Could not read storage metrics') }
    finally { setStorageBusy(false) }
  }, [])
  useEffect(() => {
    if (nav !== 'Storage') return
    void refreshStorage()
    const timer = window.setInterval(() => void refreshStorage(), 30000)
    return () => window.clearInterval(timer)
  }, [nav, refreshStorage])
  useEffect(() => {
    if (!selected) { setEvents([]); return }
    let socket: WebSocket | undefined
    const load = async () => {
      let existing: EventRow[] = []
      try { existing = await api<EventRow[]>(`/tasks/${selected.id}/events`); setEvents(existing) } catch { /* task may have just been removed */ }
      const lastId = Math.max(0, ...existing.map(row => row.id))
      const socketTicket = await api<{ticket:string}>('/ws-ticket')
      const base = await getApiBase()
      const socketUrl = new URL(`/ws/tasks/${selected.id}/events?after=${lastId}&ticket=${encodeURIComponent(socketTicket.ticket)}`, base || location.origin)
      socketUrl.protocol = socketUrl.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(socketUrl)
      socket.onmessage = event => { const row = JSON.parse(event.data) as EventRow; setEvents(current => [...current, row]) }
    }
    void load()
    return () => socket?.close()
  }, [selected?.id])

  const counts = useMemo(() => ({ active: tasks.filter(t => ['planning', 'assigned', 'running', 'validating', 'recovering'].includes(t.status)).length,
    approval: tasks.filter(t => t.status === 'awaiting_pr_approval').length, handoff: tasks.filter(t => t.status === 'handoff_ready').length,
    failed: tasks.filter(t => ['failed', 'blocked'].includes(t.status)).length }), [tasks])

  const createTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget)
    try {
      const task = await api<Task>('/tasks', { method: 'POST', body: JSON.stringify({
        title: form.get('title'), objective: form.get('objective'), repository_id: form.get('repository_id'),
        base_branch: form.get('base_branch'),
        acceptance_criteria: String(form.get('acceptance_criteria') || '').split('\n').map(s => s.trim()).filter(Boolean),
        priority: form.get('priority'),
        preferred_agent_id: form.get('agent_id') || null,
        required_capabilities: String(form.get('capabilities') || '').split(',').map(s => s.trim()).filter(Boolean),
        file_globs: String(form.get('globs')).split('\n').map(s => s.trim()).filter(Boolean),
        dependencies: form.getAll('dependencies'), contract_id: form.get('contract_id') || null,
        max_retries: Number(form.get('retries') || 1),
      }) })
      setSelected(task); setShowTaskForm(false); setNav('Tasks'); await refresh()
    } catch (e) { setError(e instanceof Error ? e.message : 'Task creation failed') }
  }

  const addContract = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget)
    try { await api('/contracts', { method: 'POST', body: JSON.stringify({ title: form.get('title'), body: form.get('body') }) }); setShowContractForm(false); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : 'Contract creation failed') }
  }

  const addAgent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget)
    try {
      await api('/agents', { method: 'POST', body: JSON.stringify({ name: form.get('name'), adapter: form.get('adapter'),
        command: String(form.get('command') || '').split('\n').filter(Boolean),
        capabilities: String(form.get('capabilities') || '').split(',').map(s => s.trim()).filter(Boolean),
        file_globs: String(form.get('globs')).split('\n').map(s => s.trim()).filter(Boolean), max_parallel: Number(form.get('parallel') || 1) }) })
      setShowAgentForm(false); await refresh()
    } catch (e) { setError(e instanceof Error ? e.message : 'Agent registration failed') }
  }

  const addRepository = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    try {
      const validationProfile = String(form.get('validation_profile'))
      const validationCommand = String(form.get('validation_command') || '').split('\n').map(s => s.trim()).filter(Boolean)
      const preflight = await api<{base_error: string | null; warnings: string[]}>('/repositories/preflight', { method: 'POST', body: JSON.stringify({
        path: form.get('path'), default_branch: form.get('default_branch'), validation_profile: validationProfile,
        validation_command: validationCommand,
      }) })
      if (preflight.base_error) throw new Error(preflight.base_error)
      await api<Repo>('/repositories', { method: 'POST', body: JSON.stringify({
        name: form.get('name'), path: form.get('path'), default_branch: form.get('default_branch'),
        validation_profile: validationProfile, validation_command: validationCommand,
      }) })
      setShowRepoForm(false)
      await refresh()
      if (preflight.warnings.length) setError(`Repository connected. Preflight: ${preflight.warnings.join(' ')}`)
    } catch (e) { setError(e instanceof Error ? e.message : 'Repository registration failed') }
  }

  const checkAgentToken = async (agent: Agent) => {
    if (agent.adapter === 'copilot' && !window.confirm('Kiểm tra Copilot sẽ gửi một prompt ngắn và tiêu thụ một request AI. Tiếp tục?')) return
    setBusyAgentId(agent.id)
    try { await api(`/agents/${encodeURIComponent(agent.id)}/check-token`, { method: 'POST' }); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusyAgentId(null) }
  }

  const removeAgent = async (agent: Agent) => {
    if (!window.confirm(`Gỡ ${agent.name} khỏi lịch phân công? Lịch sử được giữ lại; CLI và đăng nhập trên máy không bị gỡ.`)) return
    setBusyAgentId(agent.id)
    try { await api(`/agents/${encodeURIComponent(agent.id)}`, { method: 'DELETE' }); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusyAgentId(null) }
  }

  const restoreAgent = async (agent: Agent) => {
    setBusyAgentId(agent.id)
    try { await api(`/agents/${encodeURIComponent(agent.id)}/restore`, { method: 'POST' }); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusyAgentId(null) }
  }

  const approveContract = async (id: string) => { try { await api(`/contracts/${id}/approve`, { method: 'POST' }); await refresh() } catch (e) { setError(String(e)) } }
  const cancel = async (task: Task) => { try { await api(`/tasks/${task.id}/cancel`, { method: 'POST' }); await refresh() } catch (e) { setError(String(e)) } }
  const handoff = async (task: Task) => {
    const report = window.prompt('Paste the IDE agent completion summary (diff stays in its assigned worktree):')
    if (!report) return
    try { await api(`/tasks/${task.id}/handoff-complete`, { method: 'POST', body: JSON.stringify({ report }) }); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  const approvePr = async (task: Task, confirmation: string) => {
    try { await api(`/tasks/${task.id}/approve-draft-pr?confirmation=${encodeURIComponent(confirmation)}`, { method: 'POST' }); setApproval(null); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  const completeLocally = async (task: Task) => {
    if (!window.confirm('Hoàn tất task trên máy này? Commit và branch sẽ được giữ local; hệ thống không push hoặc tạo Pull Request.')) return
    try { await api(`/tasks/${task.id}/complete-local`, { method: 'POST' }); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">D</div><div><strong>DevAgent Orchestrator</strong><small>Multi-agent engineering</small></div></div>
      <div className="workspace"><span className="pulse"/><div><small>WORKSPACE</small><strong>Registered projects</strong></div><span className="chevron">⌄</span></div>
      <div className="nav-label">WORKSPACE</div>
      <nav>{(['Overview', 'Tasks', 'Agents', 'Contracts', 'History', 'Storage'] as Nav[]).map((item, i) => <button key={item} className={nav === item ? 'nav-item active' : 'nav-item'} onClick={() => setNav(item)}><span className="nav-icon">{['◫', '☷', '◇', '⌘', '◷', '▤'][i]}</span>{item}{item === 'Tasks' && tasks.length > 0 && <span className="nav-count">{tasks.length}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="connection"><span className={health?.ok ? 'status-dot online' : 'status-dot'}/><span>{health?.ok ? 'Local engine connected' : 'Connecting to API…'}</span></div><small>v0.1.0 · localhost only</small></div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="breadcrumbs">AI Engineering <span>/</span> <b>{nav}</b></div><div className="top-actions"><span className="local-tag"><span/>LOCAL</span><button className="icon-btn" title="Refresh" onClick={() => void refresh()}>⟳</button><div className="avatar">JT</div></div></header>
      {error && <div className="toast-error"><span>!</span>{error}<button onClick={() => setError('')}>×</button></div>}
      <div className="page">
        <div className="page-heading"><div><div className="eyebrow">ENGINEERING CONTROL PLANE</div><h1>{nav === 'Overview' ? 'AI engineering workspace' : nav}</h1><p>{nav === 'Overview' ? 'Coordinate independent agents across your registered Git projects.' : pageDescription(nav)}</p></div>
          {nav !== 'Contracts' && nav !== 'History' && nav !== 'Storage' && repos.length > 0 && <button className="button primary" onClick={() => { const first = repos[0]; setTaskRepositoryId(first?.id || ''); setTaskBaseBranch(first?.default_branch || ''); setShowTaskForm(true) }}><span>＋</span> New task</button>}
          {nav === 'Contracts' && <button className="button primary" onClick={() => setShowContractForm(true)}><span>＋</span> Propose contract</button>}
        </div>
        {nav === 'Overview' && <>
          {repos.length === 0 && <section className="setup-wizard panel"><div><div className="eyebrow">FIRST RUN · STEP 1 OF 3</div><h2>Connect a project repository</h2><p>Choose a local Git checkout, review its base branch and validation setup, then add an Agent before scheduling your first task.</p></div><div className="setup-steps"><span className="current">1 · Repository</span><span>2 · Validation</span><span>3 · Agent</span></div><div className="setup-actions"><button className="button primary" onClick={() => setShowRepoForm(true)}>Choose repository</button><button className="button secondary" onClick={async () => { try { await api('/agents/refresh', { method: 'POST' }); await refresh() } catch (e) { setError(String(e)) } }}>Detect Agent CLIs</button></div></section>}
          <section className="stat-grid">
            <StatCard label="Active tasks" value={counts.active} note="Executing in isolated worktrees" icon="◷" tone="blue"/>
            <StatCard label="Need your review" value={counts.approval} note="Diff and validation ready" icon="◎" tone="amber"/>
            <StatCard label="IDE handoffs" value={counts.handoff} note="Waiting for a manual agent run" icon="↗" tone="violet"/>
            <StatCard label="Needs attention" value={counts.failed} note="Blocked or failed tasks" icon="!" tone="red"/>
          </section>
          <div className="section-head"><div><h2>Recent tasks</h2><p>Live lifecycle across registered repositories</p></div><button className="text-button" onClick={() => setNav('Tasks')}>View all tasks <span>→</span></button></div>
          <TaskTable tasks={tasks.slice(0, 6)} onSelect={setSelected} selectedId={selected?.id}/>
          <section className="two-col"><div className="panel"><div className="panel-heading"><div><h2>Agent registry</h2><p>Capability matching · no fixed roles</p></div><button className="text-button" onClick={() => setNav('Agents')}>Manage →</button></div><AgentList agents={agents.filter(a => a.enabled).slice(0, 3)}/></div>
            <div className="panel"><div className="panel-heading"><div><h2>Registered repositories</h2><p>Isolated worktree roots</p></div><div className="panel-buttons"><span className="count-pill">{repos.length}</span><button className="button secondary" onClick={() => setShowRepoForm(true)}>＋ Add repository</button></div></div>{repos.map(repo => <div className="repo-row" key={repo.id}><div className="repo-icon">⌘</div><div className="repo-info"><b>{repo.name}</b><small>{repo.path}</small></div><span className="branch-pill">{repo.default_branch}</span><span className="branch-pill">{repo.validation_profile.toUpperCase()} CHECK</span></div>)}</div></section>
        </>}
        {nav === 'Tasks' && <><div className="filter-row"><span className="filter-summary">{tasks.length} tasks total</span><span className="spacer"/><span className="hint">Select a task to inspect logs and validation.</span></div><TaskTable tasks={tasks} onSelect={setSelected} selectedId={selected?.id}/>{selected && <TaskInspector task={selected} events={events} onApprove={() => setApproval(selected)} onCompleteLocal={() => void completeLocally(selected)} onCancel={() => void cancel(selected)} onHandoff={() => void handoff(selected)}/>}</>}
        {nav === 'Agents' && <div className="panel"><div className="panel-heading"><div><h2>Registered agents</h2><p>Kiểm tra trạng thái xác thực và gỡ Agent khỏi lịch phân công.</p></div><div className="panel-buttons"><button className="button secondary" onClick={async () => { try { await api('/agents/refresh', { method: 'POST' }); await refresh() } catch (e) { setError(String(e)) } }}>Refresh detection</button><button className="button secondary" onClick={() => setShowAgentForm(true)}>＋ Register adapter</button></div></div><AgentList agents={agents} detailed busyAgentId={busyAgentId} onCheckToken={checkAgentToken} onRemove={removeAgent} onRestore={restoreAgent}/></div>}
        {nav === 'Contracts' && <div className="panel"><div className="panel-heading"><div><h2>Shared interfaces</h2><p>Approve contracts before dependent tasks can execute.</p></div></div>{contracts.length ? contracts.map(c => <div className="contract-row" key={c.id}><div className="contract-icon">⌘</div><div className="contract-content"><div><b>{c.title}</b><span className={`badge ${c.state === 'approved' ? 'success' : 'warning'}`}>{c.state}</span></div><p>{c.body}</p><small>v{c.version} · {c.id}</small></div>{c.state === 'proposed' && <button className="button secondary" onClick={() => void approveContract(c.id)}>Approve contract</button>}</div>) : <Empty title="No interface contracts" text="Propose shared API or data decisions before scheduling dependent work."/>}</div>}
        {nav === 'History' && <History tasks={tasks.filter(t => ['draft_pr', 'completed', 'failed', 'blocked', 'cancelled'].includes(t.status))} onSelect={setSelected}/>}
        {nav === 'Storage' && <StoragePage metrics={storage} error={storageError} busy={storageBusy} onRefresh={() => void refreshStorage()}/>}
      </div>
    </main>
    {selected && nav !== 'Tasks' && <TaskDrawer task={selected} events={events} onClose={() => setSelected(null)} onApprove={() => setApproval(selected)} onCompleteLocal={() => void completeLocally(selected)} onCancel={() => void cancel(selected)} onHandoff={() => void handoff(selected)}/>}
    {showTaskForm && <Modal title="Create a task" subtitle="Define the scope before any agent starts." onClose={() => setShowTaskForm(false)}><form className="form" onSubmit={createTask}>
      <label>Task title<input name="title" required maxLength={180} placeholder="Add repository implementation for…"/></label>
      <label>Objective<textarea name="objective" required minLength={10} rows={4} placeholder="Describe expected behavior, constraints, and acceptance criteria."/></label>
      <label>Acceptance criteria<textarea name="acceptance_criteria" rows={3} placeholder="One observable result per line"/><small>Agent reports these criteria; automated validation still checks the configured project build/test profile.</small></label>
      <div className="form-grid"><label>Repository<select name="repository_id" required value={taskRepositoryId} onChange={event => { const id = event.target.value; const repo = repos.find(item => item.id === id); setTaskRepositoryId(id); setTaskBaseBranch(repo?.default_branch || '') }}>{repos.map(repo => <option value={repo.id} key={repo.id}>{repo.name}</option>)}</select></label>
        <label>Preferred agent<select name="agent_id"><option value="">Auto match capabilities</option>{agents.filter(agent => agent.enabled).map(agent => <option key={agent.id} value={agent.id}>{agent.name}{!agent.available ? ' · handoff' : ''}</option>)}</select></label></div>
      <label>Base branch<select name="base_branch" required value={taskBaseBranch} onChange={event => setTaskBaseBranch(event.target.value)}>{branchOptions.map(branch => <option value={branch.name} key={branch.name}>{branch.name}{branch.local && branch.remote ? ' · local + origin' : branch.local ? ' · local only' : ' · origin'}</option>)}</select><small>Task worktree starts from this branch. Uncommitted files in the source checkout are not copied. A Draft PR requires this base branch to exist on GitHub; local-only branches can be completed locally, or push the base branch separately first.</small></label>
      <label>Required capabilities<input name="capabilities" defaultValue="implementation" placeholder="implementation, java, testing"/><small>Comma-separated capability tags. Registry matching is not role-based.</small></label>
      <label>Allowed file globs<textarea name="globs" rows={3} required defaultValue="**/*"/><small>One relative glob per line. Narrow this to the files the task should change; changes outside the list block commit.</small></label>
      <div className="form-grid"><label>Approved interface contract<select name="contract_id"><option value="">No shared contract</option>{contracts.filter(c => c.state === 'approved').map(contract => <option value={contract.id} key={contract.id}>{contract.title} · v{contract.version}</option>)}</select></label>
        <label>Depends on completed tasks<select name="dependencies" multiple size={3}>{tasks.filter(t => ['completed','draft_pr','awaiting_pr_approval','handoff_complete'].includes(t.status)).map(task => <option value={task.id} key={task.id}>{task.title} · {task.id.slice(0,8)}</option>)}</select><small>Use a shared approved contract for dependent tasks.</small></label></div>
      <div className="form-grid"><label>Priority<select name="priority" defaultValue="normal"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select><small>Higher priority tasks are dispatched first when an Agent becomes available.</small></label><label>Maximum recovery retries<select name="retries" defaultValue="1"><option value="0">0</option><option value="1">1</option><option value="2">2</option></select></label></div>
      <div className="form-note">Agents use a separate Git worktree from the selected base branch. Before editing, Orchestrator supplies its naming/architecture conventions and reads repository guidance such as AGENTS.md, README.md, and docs/PROJECT_CONVENTIONS.md. Push and Draft PR always require separate approval.</div><div className="modal-actions"><button type="button" className="button secondary" onClick={() => setShowTaskForm(false)}>Cancel</button><button className="button primary">Queue task</button></div>
    </form></Modal>}
    {showContractForm && <Modal title="Propose an interface contract" subtitle="A reviewer must approve it before dependent work begins." onClose={() => setShowContractForm(false)}><form className="form" onSubmit={addContract}><label>Contract title<input name="title" required/></label><label>Interface and invariants<textarea name="body" minLength={10} rows={8} required placeholder="DTO/API shape, ownership, compatibility rules…"/></label><div className="modal-actions"><button type="button" className="button secondary" onClick={() => setShowContractForm(false)}>Cancel</button><button className="button primary">Save proposal</button></div></form></Modal>}
    {showAgentForm && <Modal title="Register an agent adapter" subtitle="Provide an executable command array or choose manual handoff." onClose={() => setShowAgentForm(false)}><form className="form" onSubmit={addAgent}><label>Agent name<input name="name" required/></label><label>Adapter<select name="adapter"><option value="exec">Generic CLI</option><option value="handoff">IDE manual handoff</option></select></label><label>Command tokens<textarea name="command" rows={3} placeholder={'For CLI, one argv token per line. No shell expansion.\nExample: /path/to/agent\n--workspace\n{worktree}'} /></label><label>Capabilities<input name="capabilities" placeholder="java, implementation, testing" required/></label><label>Allowed file globs<textarea name="globs" rows={2} defaultValue="**/*" required/></label><label>Concurrent tasks<input type="number" min="1" max="8" name="parallel" defaultValue="1"/></label><div className="modal-actions"><button type="button" className="button secondary" onClick={() => setShowAgentForm(false)}>Cancel</button><button className="button primary">Register</button></div></form></Modal>}
    {showRepoForm && <Modal title="Add a project repository" subtitle="Register a local Git checkout so tasks can target it." onClose={() => setShowRepoForm(false)}><form className="form" onSubmit={addRepository}><label>Project name<input name="name" required maxLength={120} placeholder="My project"/></label><label>Local project path<input name="path" required placeholder="/Users/you/Documents/my-project"/>{isTauri() && <button type="button" className="button secondary" onClick={() => void pickRepositoryPath()}>Browse folders…</button>}<small>Choose a folder inside a Git repository. Preflight checks the selected base, dirty state, disk space and configured tools.</small></label><label>Default branch<input name="default_branch" defaultValue="main" required placeholder="main"/><small>Branch must already exist locally or as a fetched origin branch.</small></label><label>Validation profile<select name="validation_profile" defaultValue="auto"><option value="auto">Auto detect Maven</option><option value="maven">Maven</option><option value="python">Python (argv required)</option><option value="node">Node (argv required)</option><option value="go">Go (argv required)</option><option value="custom">Custom argv command</option></select><small>Auto mode detects only a root Maven project. Other profiles run the command shown below after task changes are reviewed.</small></label><label>Validator command arguments<textarea name="validation_command" rows={3} placeholder={'One argv item per line. Example for Python:\npython3\n-m\npytest'} /><small>Required for Python, Node, Go, and custom profiles. No shell expansion or dependency installation. Do not put secrets in command arguments.</small></label><div className="form-note">The selected checkout stays unchanged; task worktrees use only committed base content.</div><div className="modal-actions"><button type="button" className="button secondary" onClick={() => setShowRepoForm(false)}>Cancel</button><button className="button primary">Add repository</button></div></form></Modal>}
    {approval && <ApprovalModal task={approval} onClose={() => setApproval(null)} onApprove={text => void approvePr(approval, text)}/>}
  </div>
}

function formatBytes(value:number) { if (value < 1024) return `${value} B`; const units = ['KB','MB','GB','TB']; let size = value / 1024; let unit = units[0]; for (let i = 1; size >= 1024 && i < units.length; i++) { size /= 1024; unit = units[i] } return `${size.toFixed(size >= 100 ? 0 : 1)} ${unit}` }
function StoragePage({metrics,error,busy,onRefresh}:{metrics:StorageMetrics|null;error:string;busy:boolean;onRefresh:()=>void}) {
  if (error && !metrics) return <div className="panel"><div className="panel-heading"><div><h2>Không đọc được dung lượng</h2><p>{error}</p></div><button className="button secondary" onClick={onRefresh}>Thử lại</button></div></div>
  if (!metrics) return <div className="panel storage-loading">Đang đọc thống kê lưu trữ…</div>
  const categories = [['database','Cơ sở dữ liệu SQLite','File .sqlite3, WAL và shared memory'],['worktrees','Worktree & build','Mã nguồn task, thư mục .git và file build'],['handoffs','IDE handoff','Gói bàn giao cho Agent chạy thủ công'],['other','Dữ liệu khác','Các file/thư mục khác dưới data/']] as const
  const volumeWarning = metrics.disk.used_percent >= 90 ? 'critical' : metrics.disk.used_percent >= 80 ? 'warning' : 'ok'
  return <div className="storage-page">
    <div className="storage-toolbar"><div><b>Thống kê trên máy này</b><small>Cập nhật {date(metrics.measured_at)}</small></div><button className="button secondary" onClick={onRefresh}>{busy ? 'Đang cập nhật…' : '⟳ Cập nhật'}</button></div>
    {volumeWarning !== 'ok' && <div className={`storage-alert ${volumeWarning}`}><b>{volumeWarning === 'critical' ? 'Ổ đĩa gần đầy' : 'Dung lượng ổ đĩa đang cao'}</b><span>Ổ đĩa đã dùng {metrics.disk.used_percent}%. Kiểm tra worktree và các file build trước khi tạo thêm task.</span></div>}
    <section className="storage-summary">
      <div className="storage-card"><small>DEVAGENT DATA</small><strong>{formatBytes(metrics.data_bytes)}</strong><span>Tổng dữ liệu Orchestrator đang quản lý</span></div>
      <div className="storage-card"><small>SQLITE</small><strong>{formatBytes(metrics.categories.database || 0)}</strong><span>{metrics.counts.tasks} task · {metrics.counts.events} sự kiện</span></div>
      <div className="storage-card"><small>WORKTREE</small><strong>{formatBytes(metrics.categories.worktrees || 0)}</strong><span>{metrics.counts.active_tasks} task đang chạy · gồm file build</span></div>
      <div className="storage-card"><small>CÒN TRỐNG TRÊN Ổ ĐĨA</small><strong>{formatBytes(metrics.disk.free_bytes)}</strong><span>Đã sử dụng {metrics.disk.used_percent}% dung lượng ổ đĩa</span></div>
    </section>
    <section className="panel storage-panel"><div className="panel-heading"><div><h2>Phân bổ dữ liệu</h2><p>Dung lượng tính từ kích thước file; không bao gồm block filesystem.</p></div></div>
      {categories.map(([key,label,description]) => { const bytes = metrics.categories[key] || 0; const percent = metrics.data_bytes ? Math.min(100, bytes / metrics.data_bytes * 100) : 0; return <div className="storage-category" key={key}><div className="storage-category-head"><div><b>{label}</b><small>{description}</small></div><strong>{formatBytes(bytes)} <span>{percent.toFixed(1)}%</span></strong></div><div className="storage-track"><i style={{width:`${percent}%`}}/></div></div> })}
    </section>
    <section className="two-col storage-details">
      <div className="panel"><div className="panel-heading"><div><h2>Chi tiết SQLite</h2><p>WAL có thể biến động khi ứng dụng hoạt động.</p></div></div><div className="storage-line"><span>File database</span><b>{formatBytes(metrics.database_files.main || 0)}</b></div><div className="storage-line"><span>WAL</span><b>{formatBytes(metrics.database_files.wal || 0)}</b></div><div className="storage-line"><span>Shared memory</span><b>{formatBytes(metrics.database_files.shared_memory || 0)}</b></div><div className="storage-line"><span>SQLite pages</span><b>{formatBytes(metrics.sqlite_logical_bytes)}</b></div><div className="storage-line"><span>Page trống có thể tái sử dụng</span><b>{formatBytes(metrics.sqlite_unused_bytes)}</b></div></div>
      <div className="panel"><div className="panel-heading"><div><h2>Số liệu lưu trữ</h2><p>Được đọc trực tiếp từ SQLite.</p></div></div><div className="storage-line"><span>Repositories</span><b>{metrics.counts.repositories}</b></div><div className="storage-line"><span>Agents</span><b>{metrics.counts.agents}</b></div><div className="storage-line"><span>Tasks / runs</span><b>{metrics.counts.tasks} / {metrics.counts.runs}</b></div><div className="storage-line"><span>Events</span><b>{metrics.counts.events}</b></div><div className="storage-note">Bản hiện tại chỉ theo dõi và cảnh báo. Xóa lịch sử/worktree cần thao tác dọn dẹp có kiểm tra riêng; không tự xóa dữ liệu.</div></div>
    </section>
  </div>
}
function pageDescription(nav: Nav) { switch (nav) { case 'Tasks': return 'Plan, assign, validate and review isolated work.'; case 'Agents': return 'Register executors by capability and allowed file scope.'; case 'Contracts': return 'Coordinate shared interfaces before dependent tasks run.'; case 'History': return 'Completed work, blocked runs and Draft PR records.'; case 'Storage': return 'Theo dõi SQLite, worktree và dung lượng ổ đĩa trên máy.'; default: return 'Coordinate independent agents across your registered Git projects.' } }
function StatCard({label,value,note,icon,tone}:{label:string;value:number;note:string;icon:string;tone:string}) { return <div className="stat-card"><div className={`stat-icon ${tone}`}>{icon}</div><div className="stat-value">{value}</div><div className="stat-label">{label}</div><div className="stat-note">{note}</div></div> }
function TaskTable({tasks,onSelect,selectedId}:{tasks:Task[];onSelect:(task:Task)=>void;selectedId?:string}) { return tasks.length ? <div className="table-wrap"><table><thead><tr><th>TASK</th><th>STATUS</th><th>AGENT</th><th>REPOSITORY</th><th>UPDATED</th><th/></tr></thead><tbody>{tasks.map(task => <tr key={task.id} className={selectedId === task.id ? 'selected-row' : ''} onClick={() => onSelect(task)}><td><div className="task-name"><span className="task-dot" /><b>{task.title}</b><span className={`priority-label ${task.priority || 'normal'}`}>{task.priority || 'normal'}</span></div><small className="task-id">{task.id.slice(0,8)} · {task.file_globs.join(', ')}</small></td><td><Status status={task.status}/></td><td>{task.agent_name || <span className="muted">Unassigned</span>}</td><td>{task.repository_name}</td><td>{date(task.created_at)}</td><td className="arrow">›</td></tr>)}</tbody></table></div> : <Empty title="Nothing in the queue yet" text="Create a bounded task to start an isolated agent run."/> }
function Status({status}:{status:string}) { const tone = status === 'draft_pr' || status === 'completed' ? 'success' : status === 'failed' || status === 'blocked' ? 'danger' : status === 'awaiting_pr_approval' || status === 'handoff_ready' || status === 'waiting_for_contract' || status === 'waiting_for_scope' ? 'warning' : 'blue'; return <span className={`badge ${tone}`}><span/> {statusLabel[status] || status}</span> }
function AgentList({agents,detailed=false,busyAgentId,onCheckToken,onRemove,onRestore}:{agents:Agent[];detailed?:boolean;busyAgentId?:string|null;onCheckToken?:(agent:Agent)=>void;onRemove?:(agent:Agent)=>void;onRestore?:(agent:Agent)=>void}) { return agents.length ? <div className="agent-list">{agents.map(agent => <div className={`agent-row ${agent.enabled ? '' : 'agent-disabled'}`} key={agent.id}><div className={`agent-glyph ${agent.adapter}`}>{agent.adapter === 'codex' ? '✳' : agent.adapter === 'handoff' ? '↗' : '⌘'}</div><div className="agent-main"><b>{agent.name}</b><small>{agent.availability_note}</small>{detailed && <><div className="chips">{agent.capabilities.map(cap => <span key={cap}>{cap}</span>)}</div><small className="credential-note">Token: <b>{credentialLabel(agent.credential_status)}</b>{agent.credential_checked_at ? ` · kiểm tra ${date(agent.credential_checked_at)}` : ''}{agent.credential_note ? ` · ${agent.credential_note}` : ''}</small>{agent.credential_usage?.groups?.map((group, index) => <small className="credential-note" key={index}>{String(group.label ?? group.name ?? `Quota ${index + 1}`)}: {group.remaining_percent === 'disabled' ? 'đang tắt' : group.remaining_percent != null ? `còn ${String(group.remaining_percent)}%` : ''}{group.reset_at ? ` · làm mới ${String(group.reset_at)}` : ''}</small>)}</>}</div><div className="agent-right"><span className={`agent-state ${!agent.enabled ? 'disabled' : agent.available ? 'available' : 'manual'}`}>{!agent.enabled ? 'REMOVED' : agent.available ? 'EXECUTABLE' : 'HANDOFF'}</span>{detailed && <><small>{agent.max_parallel} parallel · {agent.file_globs.join(', ')}</small><div className="agent-actions">{onCheckToken && agent.adapter !== 'handoff' && <button className="button secondary" disabled={busyAgentId === agent.id} onClick={() => onCheckToken(agent)}>{busyAgentId === agent.id ? 'Checking…' : 'Kiểm tra token'}</button>}{agent.enabled ? onRemove && <button className="button danger-outline" disabled={busyAgentId === agent.id} onClick={() => onRemove(agent)}>Gỡ khỏi hệ thống</button> : onRestore && <button className="button secondary" disabled={busyAgentId === agent.id} onClick={() => onRestore(agent)}>Khôi phục</button>}</div></>}</div></div>)}</div> : <Empty title="No agents detected" text="Register a CLI adapter or an IDE handoff target."/> }
function credentialLabel(status:string) { return ({unchecked:'chưa kiểm tra',valid:'hợp lệ',invalid:'không hợp lệ',error:'lỗi khi kiểm tra',unsupported:'không hỗ trợ',not_applicable:'không áp dụng'} as Record<string,string>)[status] || status }
function TaskInspector({task,events,onApprove,onCompleteLocal,onCancel,onHandoff}:{task:Task;events:EventRow[];onApprove:()=>void;onCompleteLocal:()=>void;onCancel:()=>void;onHandoff:()=>void}) { return <div className="panel inspector"><TaskDetails task={task} events={events}/><TaskActions task={task} onApprove={onApprove} onCompleteLocal={onCompleteLocal} onCancel={onCancel} onHandoff={onHandoff}/></div> }
function TaskDrawer({task,events,onClose,onApprove,onCompleteLocal,onCancel,onHandoff}:{task:Task;events:EventRow[];onClose:()=>void;onApprove:()=>void;onCompleteLocal:()=>void;onCancel:()=>void;onHandoff:()=>void}) { return <><div className="drawer-shade" onClick={onClose}/><aside className="drawer"><div className="drawer-head"><div><small>Task details</small><h2>{task.title}</h2></div><button className="icon-btn" onClick={onClose}>×</button></div><TaskDetails task={task} events={events}/><TaskActions task={task} onApprove={onApprove} onCompleteLocal={onCompleteLocal} onCancel={onCancel} onHandoff={onHandoff}/></aside></> }
function TaskDetails({task,events}:{task:Task;events:EventRow[]}) { return <div className="task-details"><div className="detail-status"><Status status={task.status}/><span className="muted">{task.id}</span><span className={`priority-label ${task.priority || 'normal'}`}>{task.priority || 'normal'} priority</span></div><p className="objective">{task.objective}</p>{task.acceptance_criteria?.length > 0 && <div className="detail-section"><h3>Acceptance criteria</h3><ul className="criteria-list">{task.acceptance_criteria.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul></div>}<div className="detail-grid"><div><small>AGENT</small><b>{task.agent_name || 'Not assigned'}</b></div><div><small>BASE BRANCH</small><b>{task.base_branch || task.repository_default_branch || '—'}</b></div><div><small>TASK BRANCH</small><b>{task.branch || '—'}</b></div><div><small>RETRIES</small><b>{task.retries}/{task.max_retries}</b></div><div><small>WORKTREE</small><b className="path-value">{task.worktree || '—'}</b></div></div>
  <div className="detail-section"><h3>File boundary</h3><div className="chips">{task.file_globs.map(g => <span key={g}>{g}</span>)}</div></div>
  {Boolean(task.validation?.commit) && <div className="detail-section"><h3>Code diff</h3><TaskDiff taskId={task.id}/></div>}
  {Object.keys(task.validation || {}).length > 0 && <div className="detail-section"><h3>Validation</h3><pre className="validation">{JSON.stringify(task.validation, null, 2)}</pre></div>}
  {task.error && <div className="detail-section"><h3>Recovery note</h3><pre className="error-box">{task.error}</pre></div>}
  <div className="detail-section"><h3>Live activity <span className="live-indicator"/></h3><div className="timeline">{events.slice(-30).map(event => <div className="event-row" key={event.id}><span className="event-dot"/><div><b>{event.kind.replaceAll('_',' ')}</b><p>{event.message}</p><small>{date(event.created_at)}</small></div></div>)}</div></div>
  </div> }
function isGitHubRemote(remote?:string|null) { if (!remote) return false; try { return new URL(remote).hostname.toLowerCase() === 'github.com' } catch { return /^(?:[^@/]+@)?github\.com:/i.test(remote) } }
function TaskActions({task,onApprove,onCompleteLocal,onCancel,onHandoff}:{task:Task;onApprove:()=>void;onCompleteLocal:()=>void;onCancel:()=>void;onHandoff:()=>void}) { return <div className="task-actions">{task.status === 'awaiting_pr_approval' && isGitHubRemote(task.repository_remote) && <button className="button primary" onClick={onApprove}>Review & approve Draft PR</button>}{task.status === 'awaiting_pr_approval' && <button className="button secondary" onClick={onCompleteLocal}>Hoàn tất cục bộ</button>}{task.status === 'awaiting_pr_approval' && !isGitHubRemote(task.repository_remote) && <span className="hint">Repository không có remote GitHub; có thể hoàn tất cục bộ.</span>}{task.status === 'handoff_ready' && <><button className="button secondary" onClick={async () => { try { const text = await api<string>(`/tasks/${task.id}/handoff`); const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' })); const link = document.createElement('a'); link.href = url; link.download = `handoff-${task.id}.md`; link.click(); URL.revokeObjectURL(url) } catch (e) { window.alert(e instanceof Error ? e.message : 'Could not download handoff package') } }}>Download handoff package</button><button className="button primary" onClick={onHandoff}>Submit IDE completion</button></>}{['queued','waiting_for_contract','waiting_for_scope','running','planning','assigned','validating','recovering'].includes(task.status) && <button className="button danger-outline" onClick={onCancel}>Cancel run</button>}{task.pr_url && <a className="button secondary" href={task.pr_url} target="_blank" rel="noreferrer">Open Draft PR ↗</a>}</div> }
function History({tasks,onSelect}:{tasks:Task[];onSelect:(task:Task)=>void}) { return <div className="panel"><div className="panel-heading"><div><h2>Task history</h2><p>Committed work and execution outcomes.</p></div></div><TaskTable tasks={tasks} onSelect={onSelect}/></div> }
function Empty({title,text}:{title:string;text:string}) { return <div className="empty"><div className="empty-icon">⌁</div><b>{title}</b><p>{text}</p></div> }
function Modal({title,subtitle,onClose,children}:{title:string;subtitle:string;onClose:()=>void;children:ReactNode}) { return <div className="modal-shade" onMouseDown={e => e.target === e.currentTarget && onClose()}><section className="modal"><div className="modal-head"><div><h2>{title}</h2><p>{subtitle}</p></div><button className="icon-btn" onClick={onClose}>×</button></div>{children}</section></div> }
function TaskDiff({taskId}:{taskId:string}) { const [result,setResult] = useState<{diff:string;truncated:boolean}|null>(null); const [message,setMessage] = useState('Loading diff…'); useEffect(() => { let current = true; api<{diff:string;truncated:boolean}>(`/tasks/${taskId}/diff`).then(value => { if (current) { setResult(value); setMessage('') } }).catch(e => { if (current) setMessage(e instanceof Error ? e.message : 'Could not load diff') }); return () => { current = false } }, [taskId]); return result ? <><pre className="diff-preview">{result.diff || 'No textual diff is available.'}</pre>{result.truncated && <small className="muted">Diff preview truncated at 250 KB.</small>}</> : <small className="muted">{message}</small> }
function ApprovalModal({task,onClose,onApprove}:{task:Task;onClose:()=>void;onApprove:(text:string)=>void}) { const [value,setValue] = useState(''); const phrase = `CREATE DRAFT PR ${task.id.slice(0,8)}`; const changed = Array.isArray(task.validation?.changed_paths) ? task.validation.changed_paths as string[] : []; return <Modal title="Approve external publication" subtitle="Review the patch below. This pushes the task branch and opens a Draft Pull Request; it does not merge." onClose={onClose}><div className="approval-summary"><Status status={task.status}/><p><b>{task.title}</b></p><small>{task.branch} → {task.repository_name} (base: {task.base_branch || task.repository_default_branch || 'default branch'})</small><div className="chips">{changed.map(file => <span key={file}>{file}</span>)}</div><TaskDiff taskId={task.id}/></div><label className="approval-label">Type <code>{phrase}</code> to confirm<input value={value} onChange={e => setValue(e.target.value)} autoFocus/></label><div className="modal-actions"><button className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={value !== phrase} onClick={() => onApprove(value)}>Push & create Draft PR</button></div></Modal> }

export default App
