import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { invoke, isTauri } from '@tauri-apps/api/core'
import { open } from '@tauri-apps/plugin-dialog'
import { QRCodeSVG } from 'qrcode.react'
import './App.css'
import './Fleet.css'

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
type Agent = { id: string; name: string; adapter: string; provider?: string | null; capabilities: string[]; file_globs: string[]; max_parallel: number; available: boolean; availability_note: string; enabled: boolean; credential_status: string; credential_checked_at?: string | null; credential_note: string; credential_usage: { groups?: Record<string, unknown>[] } }
type ProviderConnection = { id:string; agent_id:string; provider:'openai'|'anthropic'|'gemini'|'ollama'; name:string; model:string; endpoint:string; state:string; models:string[]; checked_at:string|null; has_credential:boolean }
type Repo = { id: string; name: string; path: string; default_branch: string; remote: string | null; validation_profile: string; validation_command: string[] }
type RepoBranch = { name: string; local: boolean; remote: boolean }
type Task = { id: string; title: string; objective: string; acceptance_criteria: string[]; priority: string; status: string; repository_id: string; repository_name: string; repository_remote?: string | null; repository_default_branch?: string; base_branch?: string; repository_validation_profile?: string; repository_validation_command?: string[]; agent_name?: string; assigned_agent_id?: string; preferred_agent_id?: string; writer_lease_state?: 'active'|'orphaned'|null; writer_lease_run_id?: string | null; writer_lease_agent_id?: string | null; file_globs: string[]; required_capabilities: string[]; dependencies: string[]; contract_id?: string; branch?: string; worktree?: string; retries: number; max_retries: number; validation: Record<string, unknown>; error?: string; pr_url?: string; created_at: string }
type Contract = { id: string; title: string; body: string; state: string; version: number }
type EventRow = { id: number; kind: string; message: string; created_at: string; details: Record<string, unknown> }
type Nav = 'Overview' | 'Tasks' | 'Agents' | 'Contracts' | 'History' | 'Storage' | 'Fleet' | 'Remote'
type StorageMetrics = { measured_at: string; data_bytes: number; categories: Record<string, number>; database_files: Record<string, number>; sqlite_logical_bytes: number; sqlite_unused_bytes: number; disk: { total_bytes: number; free_bytes: number; used_percent: number }; counts: { repositories: number; agents: number; tasks: number; runs: number; events: number; active_tasks: number } }
type CheckpointWorktree = { task_id: string; available: boolean; dirty_paths: string[]; secret_paths?: string[]; secret_scan_incomplete_paths?: string[]; scan_error?: string }
type CheckpointRepository = { id: string; name: string; default_branch: string; remote: string | null; available: boolean; dirty_paths: string[]; secret_paths?: string[]; secret_scan_incomplete_paths?: string[]; scan_error?: string; worktrees: CheckpointWorktree[] }
type CheckpointPreview = { active_tasks: number; repositories: CheckpointRepository[]; uncommitted_included_by_default: boolean; credential_policy: string }
type DataProfileStatus = { active: boolean; available: boolean; path: string | null }
type FleetSettings = { max_active_runs: number; max_parallel_builds: number; max_per_credential: number; min_free_disk_bytes: number; disk_reserve_per_run_bytes: number }
type FleetSnapshot = { settings: FleetSettings; active_runs: { task_id: string; title: string; status: string; assigned_agent_id?: string; agent_name?: string; repository_name?: string; run_id?: string }[]; credential_cooldowns: { adapter: string; executable: string; reason: string; expires_at: string }[]; active_run_count: number; queued_tasks: number; draining: boolean; builds_active: number; disk: { free_bytes: number; required_reserve_bytes: number; can_admit_run: boolean } }
type LeadStatus = { connected: boolean; session: { session_id: string; client_id: string; client_name: string; scopes: string[]; lease_expires_at: string; session_expires_at: string } | null }
type LeadPairing = { pairing_code: string; expires_at: string; contract_version: number; scopes: string[] }
type LeadContract = { version: number; lease_ttl_seconds: number; session_ttl_seconds: number; client_endpoints: Record<string,string>; publication: string }
type RemoteDevice = { id:string; client_id:string; client_name:string; state:string; created_at:string; last_seen:string; expires_at:string; diff_grants?:{task_id:string;revision_sha:string;granted_at:string}[] }
type RemoteRequest = { id:string; client_id:string; client_name:string; created_at:string; expires_at:string; state:string }
type RemoteStatus = { enabled:boolean; origin:string|null; gateway:string|null; transport:string; devices:RemoteDevice[]; pending_requests:RemoteRequest[] }
type RemoteInvite = { invite_id:string; pairing_code:string; expires_at:string; scopes:string[]; transport:string }

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

async function downloadCheckpoint(checkpointId: string, filename: string) {
  const [base, token] = await Promise.all([getApiBase(), getApiToken()])
  const response = await fetch(`${base}/api/storage/checkpoints/${encodeURIComponent(checkpointId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail) } catch { /* keep default */ }
    throw new Error(detail)
  }
  const objectUrl = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
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
  const [fleet, setFleet] = useState<FleetSnapshot | null>(null)
  const [fleetBusy, setFleetBusy] = useState(false)
  const [leadStatus, setLeadStatus] = useState<LeadStatus|null>(null)
  const [leadPairing, setLeadPairing] = useState<LeadPairing|null>(null)
  const [leadPrompt, setLeadPrompt] = useState('')
  const [leadBusy, setLeadBusy] = useState(false)
  const [remoteStatus, setRemoteStatus] = useState<RemoteStatus|null>(null)
  const [remoteInvite, setRemoteInvite] = useState<RemoteInvite|null>(null)
  const [remoteBusy, setRemoteBusy] = useState(false)
  const [busyAgentId, setBusyAgentId] = useState<string | null>(null)
  const [taskRepositoryId, setTaskRepositoryId] = useState('')
  const [taskAgentId, setTaskAgentId] = useState('')
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
      const [a, r, t, c, h, f, lead, remote] = await Promise.all([
        api<Agent[]>('/agents'), api<Repo[]>('/repositories'), api<Task[]>('/tasks'), api<Contract[]>('/contracts'), api<{ok:boolean}>('/health'), api<FleetSnapshot>('/fleet'), api<LeadStatus>('/lead/status'), api<RemoteStatus>('/remote/status'),
      ])
      setAgents(a); setRepos(r); setTasks(t); setContracts(c); setHealth(h); setFleet(f); setLeadStatus(lead); setRemoteStatus(remote)
      if (lead.connected) { setLeadPairing(null); setLeadPrompt('') }
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
  const selectedTaskId = selected?.id
  const selectedTaskAgent = agents.find(agent => agent.id === taskAgentId)
  useEffect(() => {
    const activeTaskId = selectedTaskId
    if (!activeTaskId) { setEvents([]); return }
    const control: { active: boolean; retryTimer: number; retryDelay: number; socket?: WebSocket } = { active: true, retryTimer: 0, retryDelay: 250 }
    const cursor = { value: 0 }
    const connect = async (initial = false) => {
      try {
        const existing = await api<EventRow[]>(`/tasks/${activeTaskId}/events?after=${cursor.value}`)
        if (!control.active) return
        if (existing.length) {
          cursor.value = Math.max(cursor.value, ...existing.map(row => row.id))
          setEvents(current => (initial ? existing : [...current, ...existing.filter(row => !current.some(item => item.id === row.id))]).slice(-1000))
        }
        const socketTicket = await api<{ticket:string}>('/ws-ticket')
        const base = await getApiBase()
        const socketUrl = new URL(`/ws/tasks/${activeTaskId}/events?after=${cursor.value}&ticket=${encodeURIComponent(socketTicket.ticket)}`, base || location.origin)
        socketUrl.protocol = socketUrl.protocol === 'https:' ? 'wss:' : 'ws:'
        control.socket = new WebSocket(socketUrl)
        control.socket.onopen = () => { control.retryDelay = 250 }
        control.socket.onmessage = event => {
          const row = JSON.parse(event.data) as EventRow
          cursor.value = Math.max(cursor.value, row.id)
          setEvents(current => (current.some(item => item.id === row.id) ? current : [...current, row]).slice(-1000))
        }
        control.socket.onclose = () => {
          if (!control.active) return
          control.retryTimer = window.setTimeout(() => void connect(false), control.retryDelay)
          control.retryDelay = Math.min(5000, control.retryDelay * 2)
        }
        control.socket.onerror = () => control.socket?.close()
      } catch {
        if (!control.active) return
        control.retryTimer = window.setTimeout(() => void connect(false), control.retryDelay)
        control.retryDelay = Math.min(5000, control.retryDelay * 2)
      }
    }
    void connect(true)
    return () => { control.active = false; window.clearTimeout(control.retryTimer); control.socket?.close() }
  }, [selectedTaskId])

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
        provider_data_consent: selectedTaskAgent?.adapter === 'provider' && selectedTaskAgent.provider !== 'ollama'
          && form.get('provider_data_consent') === 'on',
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

  const saveFleetSettings = async (settings: FleetSettings) => {
    setFleetBusy(true)
    try {
      const updated = await api<FleetSnapshot>('/fleet', { method: 'PATCH', body: JSON.stringify(settings) })
      setFleet(updated); setError('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Fleet settings could not be saved') }
    finally { setFleetBusy(false) }
  }

  const createLeadPairing = async () => {
    setLeadBusy(true)
    try {
      const [pairing,contract,base] = await Promise.all([
        api<LeadPairing>('/lead/pairing',{method:'POST',body:JSON.stringify({scopes:['handover:read','task:create','task:review']})}),
        api<LeadContract>('/lead/contract'), getApiBase().then(base=>base||location.origin),
      ])
      setLeadPairing(pairing)
      setLeadPrompt(`Connect as the External Tech Lead using API contract v${contract.version}. Base URL: ${base}.\n\nUse the one-time pairing code shown separately in the Orchestrator UI. The included scripts/tech_lead_bridge.py CLI can perform pairing and keep its session token in memory; run it with this base URL and enter the code at its hidden prompt. For a custom client, send POST ${contract.client_endpoints.handshake} with client_id, client_name, pairing_code, and scopes ${JSON.stringify(pairing.scopes)}. POST ${contract.client_endpoints.heartbeat} every 30 seconds to maintain the 90-second control lease. Read current work from ${contract.client_endpoints.handover}. You may create scoped tasks with ${contract.client_endpoints.create_task} and record auditable review decisions with ${contract.client_endpoints.review}. Do not publish, push, merge, or request broader scopes; ${contract.publication}`)
      setError('')
    } catch(e) { setError(e instanceof Error?e.message:'Could not create Tech Lead pairing') }
    finally { setLeadBusy(false) }
  }

  const revokeLead = async () => {
    const sessionId=leadStatus?.session?.session_id
    if (!sessionId) return
    setLeadBusy(true)
    try { await api(`/lead/sessions/${sessionId}/revoke`,{method:'POST'}); setLeadPairing(null); setLeadPrompt(''); await refresh() }
    catch(e) { setError(e instanceof Error?e.message:'Could not revoke Tech Lead') }
    finally { setLeadBusy(false) }
  }

  const createRemoteInvite = async () => {
    setRemoteBusy(true)
    try { setRemoteInvite(await api<RemoteInvite>('/remote/invites', { method:'POST' })); setError('') }
    catch(e) { setError(e instanceof Error ? e.message : 'Could not create remote invitation') }
    finally { setRemoteBusy(false) }
  }
  const reviewRemoteRequest = async (id:string, decision:'approve'|'deny') => {
    setRemoteBusy(true)
    try { await api(`/remote/pairings/${encodeURIComponent(id)}/${decision}`, { method:'POST' }); await refresh() }
    catch(e) { setError(e instanceof Error ? e.message : 'Could not update remote pairing') }
    finally { setRemoteBusy(false) }
  }
  const revokeRemoteDevice = async (device:RemoteDevice) => {
    if (!window.confirm(`Revoke ${device.client_name}? Its remote REST and WebSocket sessions will stop.`)) return
    setRemoteBusy(true)
    try { await api(`/remote/devices/${encodeURIComponent(device.id)}/revoke`, { method:'POST' }); await refresh() }
    catch(e) { setError(e instanceof Error ? e.message : 'Could not revoke remote device') }
    finally { setRemoteBusy(false) }
  }
  const grantRemoteDiff = async (device:RemoteDevice, task:Task) => {
    const revision = typeof task.validation?.commit === 'string' ? task.validation.commit : ''
    if (!revision) return
    const phrase = `GRANT FULL DIFF ${task.id.slice(0,8)} ${revision.slice(0,12)}`
    const confirmation = window.prompt(`Grant ${device.client_name} full diff access for task ${task.id.slice(0,8)} at validated revision ${revision.slice(0,12)}?\n\nType exactly: ${phrase}`)
    if (confirmation !== phrase) return
    setRemoteBusy(true)
    try { await api(`/remote/devices/${encodeURIComponent(device.id)}/tasks/${encodeURIComponent(task.id)}/diff-grant`, { method:'POST', body:JSON.stringify({revision_sha:revision,confirmation}) }); await refresh() }
    catch(e) { setError(e instanceof Error ? e.message : 'Could not grant task diff access') }
    finally { setRemoteBusy(false) }
  }
  const revokeRemoteDiff = async (device:RemoteDevice, task:Task) => {
    setRemoteBusy(true)
    try { await api(`/remote/devices/${encodeURIComponent(device.id)}/tasks/${encodeURIComponent(task.id)}/diff-grant`, { method:'DELETE' }); await refresh() }
    catch(e) { setError(e instanceof Error ? e.message : 'Could not revoke task diff access') }
    finally { setRemoteBusy(false) }
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
      <nav>{(['Overview', 'Tasks', 'Agents', 'Contracts', 'History', 'Storage', 'Fleet', 'Remote'] as Nav[]).map((item, i) => <button key={item} className={nav === item ? 'nav-item active' : 'nav-item'} onClick={() => setNav(item)}><span className="nav-icon">{['◫', '☷', '◇', '⌘', '◷', '▤', '▥','⇄'][i]}</span>{item}{item === 'Tasks' && tasks.length > 0 && <span className="nav-count">{tasks.length}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="connection"><span className={health?.ok ? 'status-dot online' : 'status-dot'}/><span>{health?.ok ? 'Local engine connected' : 'Connecting to API…'}</span></div><small>v0.1.0 · localhost only</small></div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="breadcrumbs">AI Engineering <span>/</span> <b>{nav}</b></div><div className="top-actions"><span className="local-tag"><span/>LOCAL</span><button className="icon-btn" title="Refresh" onClick={() => void refresh()}>⟳</button><div className="avatar">JT</div></div></header>
      {error && <div className="toast-error"><span>!</span>{error}<button onClick={() => setError('')}>×</button></div>}
      <div className="page">
        <div className="page-heading"><div><div className="eyebrow">ENGINEERING CONTROL PLANE</div><h1>{nav === 'Overview' ? 'AI engineering workspace' : nav}</h1><p>{nav === 'Overview' ? 'Coordinate independent agents across your registered Git projects.' : pageDescription(nav)}</p></div>
          {nav !== 'Contracts' && nav !== 'History' && nav !== 'Storage' && repos.length > 0 && <button className="button primary" onClick={() => { const first = repos[0]; setTaskRepositoryId(first?.id || ''); setTaskBaseBranch(first?.default_branch || ''); setTaskAgentId(''); setShowTaskForm(true) }}><span>＋</span> New task</button>}
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
        {nav === 'Tasks' && <><div className="filter-row"><span className="filter-summary">{tasks.length} tasks total</span><span className="spacer"/><span className="hint">Select a task to inspect logs and validation.</span></div><TaskTable tasks={tasks} onSelect={setSelected} selectedId={selected?.id}/>{selected && <TaskInspector task={selected} tasks={tasks} events={events} onApprove={() => setApproval(selected)} onCompleteLocal={() => void completeLocally(selected)} onCancel={() => void cancel(selected)} onHandoff={() => void handoff(selected)}/>}</>}
      {nav === 'Agents' && <><div className="panel"><div className="panel-heading"><div><h2>Registered agents</h2><p>Kiểm tra trạng thái xác thực và gỡ Agent khỏi lịch phân công.</p></div><div className="panel-buttons"><button className="button secondary" onClick={async () => { try { await api('/agents/refresh', { method: 'POST' }); await refresh() } catch (e) { setError(String(e)) } }}>Refresh detection</button><button className="button secondary" onClick={() => setShowAgentForm(true)}>＋ Register adapter</button></div></div><AgentList agents={agents} detailed busyAgentId={busyAgentId} onCheckToken={checkAgentToken} onRemove={removeAgent} onRestore={restoreAgent}/></div><ProviderConnections/></>}
        {nav === 'Contracts' && <div className="panel"><div className="panel-heading"><div><h2>Shared interfaces</h2><p>Approve contracts before dependent tasks can execute.</p></div></div>{contracts.length ? contracts.map(c => <div className="contract-row" key={c.id}><div className="contract-icon">⌘</div><div className="contract-content"><div><b>{c.title}</b><span className={`badge ${c.state === 'approved' ? 'success' : 'warning'}`}>{c.state}</span></div><p>{c.body}</p><small>v{c.version} · {c.id}</small></div>{c.state === 'proposed' && <button className="button secondary" onClick={() => void approveContract(c.id)}>Approve contract</button>}</div>) : <Empty title="No interface contracts" text="Propose shared API or data decisions before scheduling dependent work."/>}</div>}
        {nav === 'History' && <History tasks={tasks.filter(t => ['draft_pr', 'completed', 'failed', 'blocked', 'cancelled'].includes(t.status))} onSelect={setSelected}/>}
        {nav === 'Storage' && <StoragePage metrics={storage} error={storageError} busy={storageBusy} onRefresh={() => void refreshStorage()}/>}
        {nav === 'Fleet' && <FleetPage fleet={fleet} busy={fleetBusy} onSave={saveFleetSettings} leadStatus={leadStatus} pairing={leadPairing} prompt={leadPrompt} leadBusy={leadBusy} onCreatePairing={createLeadPairing} onRevokeLead={revokeLead}/>}
        {nav === 'Remote' && <RemotePage status={remoteStatus} invite={remoteInvite} tasks={tasks} busy={remoteBusy} onCreateInvite={createRemoteInvite} onApprove={id=>void reviewRemoteRequest(id,'approve')} onDeny={id=>void reviewRemoteRequest(id,'deny')} onRevoke={device=>void revokeRemoteDevice(device)} onGrant={grantRemoteDiff} onRevokeDiff={revokeRemoteDiff} onDismissInvite={()=>setRemoteInvite(null)}/>}
      </div>
    </main>
    {selected && nav !== 'Tasks' && <TaskDrawer key={selected.id} task={selected} tasks={tasks} events={events} onClose={() => setSelected(null)} onApprove={() => setApproval(selected)} onCompleteLocal={() => void completeLocally(selected)} onCancel={() => void cancel(selected)} onHandoff={() => void handoff(selected)}/>}
    {showTaskForm && <Modal title="Create a task" subtitle="Define the scope before any agent starts." onClose={() => setShowTaskForm(false)}><form className="form" onSubmit={createTask}>
      <label>Task title<input name="title" required maxLength={180} placeholder="Add repository implementation for…"/></label>
      <label>Objective<textarea name="objective" required minLength={10} rows={4} placeholder="Describe expected behavior, constraints, and acceptance criteria."/></label>
      <label>Acceptance criteria<textarea name="acceptance_criteria" rows={3} placeholder="One observable result per line"/><small>Agent reports these criteria; automated validation still checks the configured project build/test profile.</small></label>
      <div className="form-grid"><label>Repository<select name="repository_id" required value={taskRepositoryId} onChange={event => { const id = event.target.value; const repo = repos.find(item => item.id === id); setTaskRepositoryId(id); setTaskBaseBranch(repo?.default_branch || '') }}>{repos.map(repo => <option value={repo.id} key={repo.id}>{repo.name}</option>)}</select></label>
        <label>Preferred agent<select name="agent_id" value={taskAgentId} onChange={event => setTaskAgentId(event.target.value)}><option value="">Auto match capabilities</option>{agents.filter(agent => agent.enabled).map(agent => <option key={agent.id} value={agent.id}>{agent.name}{agent.adapter === 'provider' ? ' · provider (manual)' : !agent.available ? ' · handoff' : ''}</option>)}</select></label></div>
      {selectedTaskAgent?.adapter === 'provider' && selectedTaskAgent.provider === 'ollama' && <div className="form-note">Ollama runs on this computer. It will run only when selected for this task and may use substantial CPU and memory. No source is sent to a cloud provider.</div>}
      {selectedTaskAgent?.adapter === 'provider' && selectedTaskAgent.provider !== 'ollama' && <label className="form-note"><input type="checkbox" name="provider_data_consent" required/> I authorize sending this task's prompt, project guidance, retrieved source excerpts, and contents of files the worker reads to {selectedTaskAgent.name}. The worker can read and edit only within the declared file scopes; the local worktree path is withheld, shell access is unavailable, and edits are validated locally.</label>}
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
  const [checkpointPreview,setCheckpointPreview] = useState<CheckpointPreview|null>(null)
  const [checkpointBusy,setCheckpointBusy] = useState(false)
  const [includeUncommitted,setIncludeUncommitted] = useState(false)
  const [checkpointError,setCheckpointError] = useState('')
  const [checkpointNotice,setCheckpointNotice] = useState('')
  const [restoredDataDir,setRestoredDataDir] = useState('')
  const [profileStatus,setProfileStatus] = useState<DataProfileStatus|null>(null)
  useEffect(() => { void api<CheckpointPreview>('/storage/checkpoint-preview').then(setCheckpointPreview).catch(e => setCheckpointError(e instanceof Error ? e.message : 'Could not inspect checkpoint state')) }, [])
  useEffect(() => { if (isTauri()) void invoke<DataProfileStatus>('data_profile_status').then(setProfileStatus).catch(() => setProfileStatus(null)) }, [])
  const checkpointDirtyCount = checkpointPreview?.repositories.reduce((count, repo) => count + repo.dirty_paths.length + repo.worktrees.reduce((n, worktree) => n + worktree.dirty_paths.length, 0), 0) || 0
  const checkpointBlocked = Boolean(checkpointPreview && (checkpointPreview.repositories.some(repo => !repo.available || (includeUncommitted && (repo.scan_error || repo.secret_paths?.length || repo.secret_scan_incomplete_paths?.length || repo.worktrees.some(worktree => !worktree.available || worktree.scan_error || worktree.secret_paths?.length || worktree.secret_scan_incomplete_paths?.length))))))
  const exportCheckpoint = async () => {
    if (!checkpointPreview || checkpointBusy) return
    if (includeUncommitted && !window.confirm('Gói checkpoint sẽ chứa nội dung các file thay đổi chưa commit. Hãy kiểm tra danh sách file; tiếp tục chỉ khi bạn muốn sao lưu các thay đổi này.')) return
    setCheckpointBusy(true); setCheckpointError(''); setCheckpointNotice('')
    let checkpointId = ''
    try {
      const checkpoint = await api<{id:string;filename:string;size_bytes:number;sha256:string;omitted_uncommitted_paths:string[]}>('/storage/checkpoints', {
        method: 'POST', body: JSON.stringify({ include_uncommitted: includeUncommitted }),
      })
      checkpointId = checkpoint.id
      if (isTauri()) {
        const saved = await invoke<string|null>('save_checkpoint', { fileName: checkpoint.filename })
        if (!saved) { await api(`/storage/checkpoints/${checkpoint.id}`, { method: 'DELETE' }); setCheckpointNotice('Đã hủy lưu file checkpoint.'); return }
        await api(`/storage/checkpoints/${checkpoint.id}`, { method: 'DELETE' })
        setCheckpointNotice(`Đã lưu ${formatBytes(checkpoint.size_bytes)} tại ${saved}`)
      } else {
        await downloadCheckpoint(checkpoint.id, checkpoint.filename)
        setCheckpointNotice(`Đã tạo checkpoint ${formatBytes(checkpoint.size_bytes)}. Trình duyệt sẽ lưu file .devagent.`)
      }
    } catch (e) {
      setCheckpointError(e instanceof Error ? e.message : 'Could not create project checkpoint')
      if (checkpointId) { try { await api(`/storage/checkpoints/${checkpointId}`, { method: 'DELETE' }) } catch { /* stale checkpoints are removed after two days */ } }
    } finally { setCheckpointBusy(false) }
  }
  const restoreCheckpoint = async () => {
    if (!isTauri() || checkpointBusy) return
    setCheckpointBusy(true); setCheckpointError(''); setCheckpointNotice('')
    try {
      const selected = await invoke<[string,string]|null>('choose_checkpoint_restore_paths')
      if (!selected) return
      if (!window.confirm('Checkpoint sẽ được khôi phục thành một profile mới trong thư mục đã chọn. Dữ liệu Orchestrator hiện tại không bị thay thế; agent credentials vẫn phải cấu hình lại. Tiếp tục?')) return
      const restored = await api<{path:string;repository_count:number;worktree_count:number}>('/storage/checkpoints/restore', {
        method: 'POST', body: JSON.stringify({ checkpoint_path: selected[0], destination_parent: selected[1] }),
      })
      setRestoredDataDir(`${restored.path}/data`)
      setCheckpointNotice(`Đã khôi phục ${restored.repository_count} repositories và ${restored.worktree_count} worktrees tại ${restored.path}. Profile hiện tại chưa bị đổi; các task cần được kiểm tra trước khi chạy lại.`)
    } catch (e) {
      setCheckpointError(e instanceof Error ? e.message : 'Could not restore project checkpoint')
    } finally { setCheckpointBusy(false) }
  }
  const activateRestoredProfile = async () => {
    if (!restoredDataDir || checkpointBusy) return
    if (!window.confirm('Đổi profile sẽ đóng ứng dụng. Dữ liệu profile hiện tại được giữ nguyên và có thể quay lại từ Storage sau khi mở lại ứng dụng. Tiếp tục?')) return
    try { await invoke('activate_restored_profile', { dataDir: restoredDataDir }) }
    catch (e) { setCheckpointError(e instanceof Error ? e.message : 'Could not activate restored profile') }
  }
  const resetDataProfile = async () => {
    if (!profileStatus?.active) return
    if (!window.confirm('Ứng dụng sẽ đóng và lần mở tiếp theo sẽ dùng lại dữ liệu ban đầu. Profile đã restore vẫn được giữ nguyên trên đĩa. Tiếp tục?')) return
    try { await invoke('reset_data_profile') }
    catch (e) { setCheckpointError(e instanceof Error ? e.message : 'Could not return to the original profile') }
  }
  if (error && !metrics) return <div className="panel"><div className="panel-heading"><div><h2>Không đọc được dung lượng</h2><p>{error}</p></div><button className="button secondary" onClick={onRefresh}>Thử lại</button></div></div>
  if (!metrics) return <div className="panel storage-loading">Đang đọc thống kê lưu trữ…</div>
  const categories = [['database','Cơ sở dữ liệu SQLite','File .sqlite3, WAL và shared memory'],['worktrees','Worktree & build','Mã nguồn task, thư mục .git và file build'],['handoffs','IDE handoff','Gói bàn giao cho Agent chạy thủ công'],['other','Dữ liệu khác','Các file/thư mục khác dưới data/']] as const
  const volumeWarning = metrics.disk.used_percent >= 90 ? 'critical' : metrics.disk.used_percent >= 80 ? 'warning' : 'ok'
  return <div className="storage-page">
    <div className="storage-toolbar"><div><b>Thống kê trên máy này</b><small>Cập nhật {date(metrics.measured_at)}</small></div><button className="button secondary" onClick={onRefresh}>{busy ? 'Đang cập nhật…' : '⟳ Cập nhật'}</button></div>
    {volumeWarning !== 'ok' && <div className={`storage-alert ${volumeWarning}`}><b>{volumeWarning === 'critical' ? 'Ổ đĩa gần đầy' : 'Dung lượng ổ đĩa đang cao'}</b><span>Ổ đĩa đã dùng {metrics.disk.used_percent}%. Kiểm tra worktree và các file build trước khi tạo thêm task.</span></div>}
    <section className="panel checkpoint-panel"><div className="panel-heading"><div><h2>Sao lưu và di chuyển dự án</h2><p>Xuất lịch sử, contracts, Git refs và repository bundles thành một file .devagent có kiểm tra SHA-256.</p></div><div className="checkpoint-actions">{isTauri() && <button className="button secondary" disabled={checkpointBusy} onClick={() => void restoreCheckpoint()}>{checkpointBusy ? 'Đang xử lý…' : 'Restore checkpoint'}</button>}<button className="button primary" disabled={checkpointBusy || !checkpointPreview || checkpointBlocked || (checkpointPreview?.active_tasks || 0) > 0} onClick={() => void exportCheckpoint()}>{checkpointBusy ? 'Đang tạo…' : 'Export checkpoint'}</button></div></div>
      <div className="checkpoint-disclosure">Checkpoint chứa Git history và dữ liệu dự án riêng tư. API/provider credentials, executable argv và đường dẫn máy này được loại bỏ; repositories/agents cần cấu hình lại sau restore. Restore tạo profile mới cạnh dữ liệu hiện tại; task cũ ở trạng thái blocked và cần kiểm tra trước khi chạy tiếp. RAG indexes sẽ cần dựng lại từ repositories. Git history được quét secret trước khi đóng gói.</div>
      {profileStatus?.active && <div className="storage-alert warning"><b>{profileStatus.available ? 'Đang dùng restored profile' : 'Không tìm thấy restored profile đã chọn'}</b><span>{profileStatus.path || 'Profile path chưa đọc được'}</span><button className="button secondary" onClick={() => void resetDataProfile()}>Quay về dữ liệu ban đầu</button></div>}
      {restoredDataDir && <div className="storage-alert warning"><b>Profile mới sẵn sàng</b><span>Kích hoạt sẽ đóng ứng dụng; lần mở lại tiếp theo dùng profile này. Dữ liệu hiện tại vẫn được giữ lại.</span><button className="button secondary" disabled={checkpointBusy} onClick={() => void activateRestoredProfile()}>Kích hoạt profile sau khi mở lại</button></div>}
      {checkpointPreview && <><div className="checkpoint-summary"><b>{checkpointPreview.repositories.length} repositories</b><span>{checkpointPreview.active_tasks} active tasks</span><span>{checkpointDirtyCount} uncommitted paths</span></div>
        {checkpointPreview.active_tasks > 0 && <div className="storage-alert warning"><b>Cần dừng task đang chạy</b><span>Checkpoint nhất quán chỉ được tạo khi không có task đang hoạt động.</span></div>}
        {checkpointPreview.repositories.length > 0 && <div className="checkpoint-repositories">{checkpointPreview.repositories.map(repo => <div className="checkpoint-repo" key={repo.id}><b>{repo.name}</b><small>{repo.default_branch}{repo.remote ? ` · ${repo.remote}` : ''}</small>{repo.dirty_paths.length > 0 && <small>Thay đổi chưa commit: {repo.dirty_paths.join(', ')}</small>}{repo.worktrees.map(worktree => worktree.dirty_paths.length > 0 && <small key={worktree.task_id}>Task {worktree.task_id.slice(0,8)}: {worktree.dirty_paths.join(', ')}</small>)}{repo.secret_paths?.length ? <small className="checkpoint-danger">Có thể có secret trong file thay đổi: {repo.secret_paths.join(', ')}</small> : null}{repo.scan_error && <small className="checkpoint-danger">{repo.scan_error}</small>}</div>)}</div>}
        <label className="checkpoint-option"><input type="checkbox" checked={includeUncommitted} onChange={event => setIncludeUncommitted(event.target.checked)}/><span><b>Include uncommitted files</b><small>Mặc định bỏ qua thay đổi chưa commit. Khi bật, file trong status Git sẽ được quét secret, symlink và file lớn sẽ bị từ chối.</small></span></label>
        {!includeUncommitted && checkpointDirtyCount > 0 && <small className="checkpoint-muted">Các file thay đổi chưa commit sẽ không nằm trong checkpoint.</small>}
      </>}
      {checkpointError && <div className="storage-alert critical"><b>Không tạo được checkpoint</b><span>{checkpointError}</span></div>}{checkpointNotice && <div className="storage-alert warning"><b>Checkpoint</b><span>{checkpointNotice}</span></div>}
    </section>
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
function RemotePage({status,invite,tasks,busy,onCreateInvite,onApprove,onDeny,onRevoke,onGrant,onRevokeDiff,onDismissInvite}:{status:RemoteStatus|null;invite:RemoteInvite|null;tasks:Task[];busy:boolean;onCreateInvite:()=>void;onApprove:(id:string)=>void;onDeny:(id:string)=>void;onRevoke:(device:RemoteDevice)=>void;onGrant:(device:RemoteDevice,task:Task)=>void;onRevokeDiff:(device:RemoteDevice,task:Task)=>void;onDismissInvite:()=>void}) {
  const copyInvite = async () => {
    if (!invite || !status?.origin) return
    try { await navigator.clipboard.writeText(`Open ${status.origin}/remote and enter this one-time code:\n${invite.pairing_code}`) }
    catch { window.prompt('Copy this one-time pairing code and send it to the intended device:', invite.pairing_code) }
  }
  return <div className="remote-owner-page">
    <section className="panel remote-owner-panel"><div className="panel-heading"><div><h2>Remote review access</h2><p>Browser client · private VPN + HTTPS ingress · read-only by default. The owner API remains loopback-only.</p></div><span className={`agent-state ${status?.enabled?'available':'disabled'}`}>{status?.enabled?'INGRESS READY':'DISABLED'}</span></div>
      {status?.enabled ? <><div className="remote-owner-info"><b>Configured origin</b><span>{status.origin}</span><b>Loopback gateway</b><span>{status.gateway}</span><small>Expose only <code>/remote</code>, <code>/remote/assets/*</code>, <code>/remote/api/*</code> and <code>/remote/ws/*</code> through the ingress. Terminate HTTPS, restrict ingress to the private VPN, overwrite Host / X-Forwarded-Proto, preserve WebSocket upgrade, and do not log pairing/session headers. The owner API stays on its separate ephemeral loopback port.</small></div>
        {!invite ? <button className="button primary" disabled={busy} onClick={onCreateInvite}>Create one-time invite (5 minutes)</button> : <div className="remote-invite"><QRCodeSVG value={`${(status.origin ?? '').replace(/\/$/, '')}/remote#pairing=${encodeURIComponent(invite.pairing_code)}`} size={132} level="M" title="One-time remote pairing link"/><div><small>ONE-TIME INVITE · EXPIRES {date(invite.expires_at)}</small><code>{invite.pairing_code}</code><span>Scan to open the HTTPS viewer with this code prefilled. The code is carried in the URL fragment and removed from the address bar after opening. Scope: {invite.scopes.join(', ')} · only its SHA-256 hash is stored.</span></div><div className="panel-buttons"><button className="button secondary" onClick={() => void copyInvite()}>Copy remote URL + code</button><button className="button secondary" onClick={onDismissInvite}>Dismiss</button></div></div>}
      </> : <div className="remote-owner-disabled"><b>Remote routes are disabled.</b><p>Set <code>CLINIC_ORCHESTRATOR_REMOTE_ORIGIN</code> to the exact HTTPS origin and <code>CLINIC_ORCHESTRATOR_REMOTE_PROXY_IPS</code> to the ingress peer IPs before creating an invite. This does not change the local bind address; no remote listener is opened.</p></div>}
    </section>
    <section className="panel remote-owner-panel"><div className="panel-heading"><div><h2>Pending device approvals</h2><p>Review the requested device name before issuing its separate read-only credential.</p></div><span className="count-pill">{status?.pending_requests.length || 0}</span></div>
      {status?.pending_requests.length ? status.pending_requests.map(item=><div className="remote-owner-row" key={item.id}><div><b>{item.client_name}</b><small>{item.client_id} · requested {date(item.created_at)} · expires {date(item.expires_at)}</small><span className="chips"><span>summary:read</span></span></div><div className="panel-buttons"><button className="button secondary" disabled={busy} onClick={()=>onDeny(item.id)}>Deny</button><button className="button primary" disabled={busy} onClick={()=>onApprove(item.id)}>Approve device</button></div></div>) : <Empty title="No pending requests" text="A device appears here after it submits an unexpired invitation code."/>}
    </section>
    <section className="panel remote-owner-panel"><div className="panel-heading"><div><h2>Paired devices</h2><p>Revoke a device to deny future REST access and close its live event stream.</p></div><span className="count-pill">{status?.devices.filter(item=>item.state==='active').length || 0} active</span></div>
      {status?.devices.length ? status.devices.map(device=><div className="remote-owner-device" key={device.id}><div className="remote-owner-row"><div><b>{device.client_name}</b><small>{device.client_id} · last seen {date(device.last_seen)} · expires {date(device.expires_at)}</small><span className={`agent-state ${device.state==='active'?'available':'disabled'}`}>{device.state.toUpperCase()}</span></div>{device.state==='active' && <button className="button danger-outline" disabled={busy} onClick={()=>onRevoke(device)}>Revoke device</button>}</div>
        {device.state==='active' && <div className="remote-grants"><b>Task-specific full diff grants</b>{tasks.filter(task=>typeof task.validation?.commit==='string' && (task.validation.commit as string).length>=40).map(task=>{
          const revision=task.validation.commit as string
          const grant=device.diff_grants?.find(item=>item.task_id===task.id && item.revision_sha===revision)
          return <div className="remote-grant-row" key={task.id}><span>{task.title}<small>{task.id.slice(0,8)} · validated {revision.slice(0,12)}</small></span>{grant ? <button className="button danger-outline" disabled={busy} onClick={()=>onRevokeDiff(device,task)}>Revoke diff</button> : <button className="button secondary" disabled={busy} onClick={()=>onGrant(device,task)}>Grant full diff</button>}</div>
        })}</div>}</div>) : <Empty title="No paired devices" text="Approved browser devices will be listed here."/>}
    </section>
  </div>
}
function pageDescription(nav: Nav) { switch (nav) { case 'Tasks': return 'Plan, assign, validate and review isolated work.'; case 'Agents': return 'Register executors by capability and allowed file scope.'; case 'Contracts': return 'Coordinate shared interfaces before dependent tasks run.'; case 'History': return 'Completed work, blocked runs and Draft PR records.'; case 'Storage': return 'Theo dõi SQLite, worktree và dung lượng ổ đĩa trên máy.'; case 'Remote': return 'Manage VPN browser pairing, device approvals and per-task diff access.'; default: return 'Coordinate independent agents across your registered Git projects.' } }
function StatCard({label,value,note,icon,tone}:{label:string;value:number;note:string;icon:string;tone:string}) { return <div className="stat-card"><div className={`stat-icon ${tone}`}>{icon}</div><div className="stat-value">{value}</div><div className="stat-label">{label}</div><div className="stat-note">{note}</div></div> }
function TaskTable({tasks,onSelect,selectedId}:{tasks:Task[];onSelect:(task:Task)=>void;selectedId?:string}) { return tasks.length ? <div className="table-wrap"><table><thead><tr><th>TASK</th><th>STATUS</th><th>AGENT</th><th>REPOSITORY</th><th>UPDATED</th><th/></tr></thead><tbody>{tasks.map(task => <tr key={task.id} className={selectedId === task.id ? 'selected-row' : ''} onClick={() => onSelect(task)}><td><div className="task-name"><span className="task-dot" /><b>{task.title}</b><span className={`priority-label ${task.priority || 'normal'}`}>{task.priority || 'normal'}</span></div><small className="task-id">{task.id.slice(0,8)} · {task.file_globs.join(', ')}</small></td><td><Status status={task.status}/></td><td>{task.agent_name || <span className="muted">Unassigned</span>}</td><td>{task.repository_name}</td><td>{date(task.created_at)}</td><td className="arrow">›</td></tr>)}</tbody></table></div> : <Empty title="Nothing in the queue yet" text="Create a bounded task to start an isolated agent run."/> }
function Status({status}:{status:string}) { const tone = status === 'draft_pr' || status === 'completed' ? 'success' : status === 'failed' || status === 'blocked' ? 'danger' : status === 'awaiting_pr_approval' || status === 'handoff_ready' || status === 'waiting_for_contract' || status === 'waiting_for_scope' ? 'warning' : 'blue'; return <span className={`badge ${tone}`}><span/> {statusLabel[status] || status}</span> }
function ProviderConnections() {
  const [items, setItems] = useState<ProviderConnection[]>([])
  const [credentialStore, setCredentialStore] = useState<{available:boolean;store:string;backend?:string;reason?:string}|null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const refresh = useCallback(async () => {
    const [connections,store] = await Promise.all([api<ProviderConnection[]>('/providers'),api<{available:boolean;store:string;backend?:string;reason?:string}>('/providers/credential-store')])
    setItems(connections); setCredentialStore(store)
  }, [])
  useEffect(() => { void refresh().catch(e => setMessage(e instanceof Error ? e.message : 'Could not load providers')) }, [refresh])
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    setBusy(true); setMessage('')
    try {
      await api('/providers', { method: 'POST', body: JSON.stringify({
        provider: values.get('provider'), name: values.get('name'), model: values.get('model'),
        api_key: values.get('api_key') || null, endpoint: values.get('endpoint') || null,
      }) })
      form.reset(); await refresh(); setMessage('Provider connection saved. The API key is held in the OS credential store.')
    } catch (e) { setMessage(e instanceof Error ? e.message : 'Could not save provider') }
    finally { setBusy(false) }
  }
  const check = async (id: string) => {
    setBusy(true); setMessage('')
    try { await api(`/providers/${encodeURIComponent(id)}/check`, { method: 'POST' }); await refresh(); setMessage('Connection checked. Choose an available model below.') }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Provider check failed'); await refresh() }
    finally { setBusy(false) }
  }
  const chooseModel = async (item: ProviderConnection, model: string) => {
    try { await api(`/providers/${encodeURIComponent(item.id)}`, { method: 'PATCH', body: JSON.stringify({ model }) }); await refresh() }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Could not select model') }
  }
  const remove = async (item: ProviderConnection) => {
    if (!window.confirm(`Remove ${item.name} and its saved credential?`)) return
    try { await api(`/providers/${encodeURIComponent(item.id)}`, { method: 'DELETE' }); await refresh() }
    catch (e) { setMessage(e instanceof Error ? e.message : 'Could not remove provider') }
  }
  return <section className="panel" style={{marginTop:16}}>
    <div className="panel-heading"><div><h2>Provider connections</h2><p>Verified connections with a selected model appear as manual workers. Cloud workers require per-task consent; Ollama runs locally and is never auto-scheduled.</p><small className="credential-note">Credential store: <b>{credentialStore?.available ? credentialStore.backend : credentialStore?.reason || 'checking…'}</b></small></div></div>
    <form className="form" onSubmit={create}>
      <div className="form-grid">
        <label>Provider<select name="provider" defaultValue="openai"><option value="openai">OpenAI</option><option value="anthropic">Anthropic Claude</option><option value="gemini">Google Gemini</option><option value="ollama">Ollama (local)</option></select></label>
        <label>Connection name<input name="name" required maxLength={120} placeholder="Personal OpenAI"/></label>
        <label>Preferred model<input name="model" maxLength={160} placeholder="Select after checking models"/></label>
        <label>API key<input name="api_key" type="password" autoComplete="new-password" placeholder="Required except for Ollama"/></label>
        <label>Ollama endpoint<input name="endpoint" defaultValue="http://127.0.0.1:11434" placeholder="http://127.0.0.1:11434"/><small>Loopback only; no remote endpoint.</small></label>
      </div>
      <div className="form-actions"><span className="hint">Model checks make one bounded metadata request. No prompt is sent.</span><button className="button primary" disabled={busy}>Save connection</button></div>
    </form>
    {message && <p className="muted" role="status">{message}</p>}
    {items.length ? <div className="agent-list">{items.map(item => <div className="agent-row" key={item.id}>
      <div className="agent-glyph">{item.provider==='ollama'?'◉':'✳'}</div><div className="agent-main"><b>{item.name}</b><small>{item.provider} · {item.has_credential?'OS credential stored':'no API key'} · {item.model||'no preferred model'} · {item.state}</small>
        {item.models.length>0 && <div className="chips">{item.models.slice(0,12).map(model=><button className="button secondary" type="button" key={model} onClick={()=>void chooseModel(item,model)}>{model===item.model?`✓ ${model}`:model}</button>)}</div>}
      </div><div className="agent-actions"><button className="button secondary" type="button" disabled={busy} onClick={()=>void check(item.id)}>Check models</button><button className="button danger-outline" type="button" disabled={busy} onClick={()=>void remove(item)}>Remove</button></div>
    </div>)}</div> : <Empty title="No provider connections" text="Add an API provider or a local Ollama service to check available models."/>}
  </section>
}

function AgentList({agents,detailed=false,busyAgentId,onCheckToken,onRemove,onRestore}:{agents:Agent[];detailed?:boolean;busyAgentId?:string|null;onCheckToken?:(agent:Agent)=>void;onRemove?:(agent:Agent)=>void;onRestore?:(agent:Agent)=>void}) { return agents.length ? <div className="agent-list">{agents.map(agent => <div className={`agent-row ${agent.enabled ? '' : 'agent-disabled'}`} key={agent.id}><div className={`agent-glyph ${agent.adapter}`}>{agent.adapter === 'codex' ? '✳' : agent.adapter === 'handoff' ? '↗' : '⌘'}</div><div className="agent-main"><b>{agent.name}</b><small>{agent.availability_note}</small>{detailed && <><div className="chips">{agent.capabilities.map(cap => <span key={cap}>{cap}</span>)}</div>{agent.adapter !== 'provider' && <><small className="credential-note">Token: <b>{credentialLabel(agent.credential_status)}</b>{agent.credential_checked_at ? ` · kiểm tra ${date(agent.credential_checked_at)}` : ''}{agent.credential_note ? ` · ${agent.credential_note}` : ''}</small>{agent.credential_usage?.groups?.map((group, index) => <small className="credential-note" key={index}>{String(group.label ?? group.name ?? `Quota ${index + 1}`)}: {group.remaining_percent === 'disabled' ? 'đang tắt' : group.remaining_percent != null ? `còn ${String(group.remaining_percent)}%` : ''}{group.reset_at ? ` · làm mới ${date(String(group.reset_at))}` : ''}</small>)}</>}</>}</div><div className="agent-right"><span className={`agent-state ${!agent.enabled ? 'disabled' : agent.available ? 'available' : 'manual'}`}>{!agent.enabled ? 'REMOVED' : agent.available ? 'EXECUTABLE' : 'HANDOFF'}</span>{detailed && <><small>{agent.max_parallel} parallel · {agent.file_globs.join(', ')}</small><div className="agent-actions">{onCheckToken && agent.adapter !== 'handoff' && agent.adapter !== 'provider' && <button className="button secondary" disabled={busyAgentId === agent.id} onClick={() => onCheckToken(agent)}>{busyAgentId === agent.id ? 'Checking…' : 'Kiểm tra token'}</button>}{agent.enabled ? onRemove && <button className="button danger-outline" disabled={busyAgentId === agent.id} onClick={() => onRemove(agent)}>Gỡ khỏi hệ thống</button> : onRestore && <button className="button secondary" disabled={busyAgentId === agent.id} onClick={() => onRestore(agent)}>Khôi phục</button>}</div></>}</div></div>)}</div> : <Empty title="No agents detected" text="Register a CLI adapter or an IDE handoff target."/> }
function credentialLabel(status:string) { return ({unchecked:'chưa kiểm tra',valid:'hợp lệ',invalid:'không hợp lệ',error:'lỗi khi kiểm tra',unsupported:'không hỗ trợ',not_applicable:'không áp dụng'} as Record<string,string>)[status] || status }
function TaskInspector({task,tasks,events,onApprove,onCompleteLocal,onCancel,onHandoff}:{task:Task;tasks:Task[];events:EventRow[];onApprove:()=>void;onCompleteLocal:()=>void;onCancel:()=>void;onHandoff:()=>void}) { return <div className="panel inspector"><TaskSummary task={task} tasks={tasks}/><TaskActivity events={events}/><TaskActions task={task} onApprove={onApprove} onCompleteLocal={onCompleteLocal} onCancel={onCancel} onHandoff={onHandoff}/></div> }
function TaskDrawer({task,tasks,events,onClose,onApprove,onCompleteLocal,onCancel,onHandoff}:{task:Task;tasks:Task[];events:EventRow[];onClose:()=>void;onApprove:()=>void;onCompleteLocal:()=>void;onCancel:()=>void;onHandoff:()=>void}) {
  const [tab,setTab] = useState<'Overview'|'Activity'|'Diff'>('Overview')
  return <><div className="drawer-shade" onClick={onClose}/><aside className="drawer"><div className="drawer-head"><div><small>Task details</small><h2>{task.title}</h2></div><button className="icon-btn" onClick={onClose}>×</button></div>
    <div className="task-tabs">{(['Overview','Activity','Diff'] as const).map(item => <button key={item} className={tab===item?'active':''} onClick={() => setTab(item)}>{item}{item==='Activity' && events.length>0 ? ` · ${events.length}` : ''}</button>)}</div>
    {tab==='Overview' && <TaskSummary task={task} tasks={tasks}/>}
    {tab==='Activity' && <TaskActivity events={events}/>}
    {tab==='Diff' && (task.validation?.commit ? <TaskDiff taskId={task.id}/> : <Empty title="Diff is not ready" text="A validated commit is required before reviewing the complete patch."/>)}
    <TaskActions task={task} onApprove={onApprove} onCompleteLocal={onCompleteLocal} onCancel={onCancel} onHandoff={onHandoff}/></aside></> }

function isGitHubRemote(remote?:string|null) { if (!remote) return false; try { return new URL(remote).hostname.toLowerCase() === 'github.com' } catch { return /^(?:[^@/]+@)?github\.com:/i.test(remote) } }
function TaskActions({task,onApprove,onCompleteLocal,onCancel,onHandoff}:{task:Task;onApprove:()=>void;onCompleteLocal:()=>void;onCancel:()=>void;onHandoff:()=>void}) { return <div className="task-actions">{task.status === 'awaiting_pr_approval' && isGitHubRemote(task.repository_remote) && <button className="button primary" onClick={onApprove}>Review & approve Draft PR</button>}{task.status === 'awaiting_pr_approval' && <button className="button secondary" onClick={onCompleteLocal}>Hoàn tất cục bộ</button>}{task.status === 'awaiting_pr_approval' && !isGitHubRemote(task.repository_remote) && <span className="hint">Repository không có remote GitHub; có thể hoàn tất cục bộ.</span>}{task.status === 'handoff_ready' && <><button className="button secondary" onClick={async () => { try { const text = await api<string>(`/tasks/${task.id}/handoff`); const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' })); const link = document.createElement('a'); link.href = url; link.download = `handoff-${task.id}.md`; link.click(); URL.revokeObjectURL(url) } catch (e) { window.alert(e instanceof Error ? e.message : 'Could not download handoff package') } }}>Download handoff package</button><button className="button primary" onClick={onHandoff}>Submit IDE completion</button></>}{['queued','waiting_for_contract','waiting_for_scope','running','planning','assigned','validating','recovering'].includes(task.status) && <button className="button danger-outline" onClick={onCancel}>Cancel run</button>}{task.pr_url && <a className="button secondary" href={task.pr_url} target="_blank" rel="noreferrer">Open Draft PR ↗</a>}</div> }
function History({tasks,onSelect}:{tasks:Task[];onSelect:(task:Task)=>void}) { return <div className="panel"><div className="panel-heading"><div><h2>Task history</h2><p>Committed work and execution outcomes.</p></div></div><TaskTable tasks={tasks} onSelect={onSelect}/></div> }
function Empty({title,text}:{title:string;text:string}) { return <div className="empty"><div className="empty-icon">⌁</div><b>{title}</b><p>{text}</p></div> }
function Modal({title,subtitle,onClose,children}:{title:string;subtitle:string;onClose:()=>void;children:ReactNode}) { return <div className="modal-shade" onMouseDown={e => e.target === e.currentTarget && onClose()}><section className="modal"><div className="modal-head"><div><h2>{title}</h2><p>{subtitle}</p></div><button className="icon-btn" onClick={onClose}>×</button></div>{children}</section></div> }
function TaskDiff({taskId}:{taskId:string}) {
  const [diff,setDiff] = useState('')
  const [nextOffset,setNextOffset] = useState<number|null>(0)
  const [message,setMessage] = useState('Loading diff…')
  const [busy,setBusy] = useState(false)
  const load = useCallback(async (offset:number,replace=false) => {
    setBusy(true)
    if (replace) { setDiff(''); setNextOffset(0) }
    try {
      const page=await api<{diff:string;next_offset:number|null;total_chars:number}>(`/tasks/${taskId}/diff?offset=${offset}&limit=250000`)
      setDiff(current=>replace?page.diff:current+page.diff)
      setNextOffset(page.next_offset)
      setMessage(page.total_chars===0?'No textual diff is available.':'')
    } catch(e) { setMessage(e instanceof Error?e.message:'Could not load diff') }
    finally { setBusy(false) }
  }, [taskId])
  useEffect(() => { void load(0,true) }, [load])
  return <>{diff && <pre className="diff-preview">{diff}</pre>}{message && <small className="muted">{message}</small>}{nextOffset!==null && !message && <button className="button secondary" disabled={busy} onClick={()=>void load(nextOffset)}>{busy?'Loading…':'Load next diff section'}</button>}</>
}
function ApprovalModal({task,onClose,onApprove}:{task:Task;onClose:()=>void;onApprove:(text:string)=>void}) { const [value,setValue] = useState(''); const phrase = `CREATE DRAFT PR ${task.id.slice(0,8)}`; const changed = Array.isArray(task.validation?.changed_paths) ? task.validation.changed_paths as string[] : []; return <Modal title="Approve external publication" subtitle="Review the patch below. This pushes the task branch and opens a Draft Pull Request; it does not merge." onClose={onClose}><div className="approval-summary"><Status status={task.status}/><p><b>{task.title}</b></p><small>{task.branch} → {task.repository_name} (base: {task.base_branch || task.repository_default_branch || 'default branch'})</small><div className="chips">{changed.map(file => <span key={file}>{file}</span>)}</div><TaskDiff taskId={task.id}/></div><label className="approval-label">Type <code>{phrase}</code> to confirm<input value={value} onChange={e => setValue(e.target.value)} autoFocus/></label><div className="modal-actions"><button className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={value !== phrase} onClick={() => onApprove(value)}>Push & create Draft PR</button></div></Modal> }

export default App

function TaskSummary({task,tasks}:{task:Task;tasks:Task[]}) {
  const parents=task.dependencies.map(id=>tasks.find(item=>item.id===id))
  const children=tasks.filter(item=>item.dependencies.includes(task.id))
  return <div className="task-details">
    <div className="detail-status"><Status status={task.status}/><span className="muted">{task.id}</span><span className={`priority-label ${task.priority||'normal'}`}>{task.priority||'normal'} priority</span></div>
    {task.writer_lease_state==='orphaned' && <div className="storage-alert warning"><b>Worker lease cần kiểm tra</b><span>Ứng dụng đã dừng khi task còn chạy. Worktree được giữ lại; hãy kiểm tra process và thay đổi trước khi khởi chạy lại task.</span></div>}
    {task.writer_lease_state==='active' && <div className="storage-alert warning"><b>Worker đang giữ quyền ghi</b><span>Run {task.writer_lease_run_id?.slice(0,8)||'—'} · agent {task.writer_lease_agent_id||'—'}.</span></div>}
    <p className="objective">{task.objective}</p>
    {task.acceptance_criteria?.length>0 && <div className="detail-section"><h3>Acceptance criteria</h3><ul className="criteria-list">{task.acceptance_criteria.map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
    <div className="detail-grid"><div><small>AGENT</small><b>{task.agent_name||'Not assigned'}</b></div><div><small>BASE BRANCH</small><b>{task.base_branch||task.repository_default_branch||'—'}</b></div><div><small>TASK BRANCH</small><b>{task.branch||'—'}</b></div><div><small>RETRIES</small><b>{task.retries}/{task.max_retries}</b></div><div><small>WORKTREE</small><b className="path-value">{task.worktree||'—'}</b></div></div>
    <div className="detail-section"><h3>Dependency flow</h3><div className="dependency-flow"><div><small>Depends on</small>{parents.length ? parents.map((item,index)=><span key={task.dependencies[index]}>{item?.title||task.dependencies[index].slice(0,8)} <Status status={item?.status||'missing'}/></span>) : <span>Entry task · no prerequisites</span>}</div><b>→</b><div><small>Unlocks</small>{children.length ? children.map(item=><span key={item.id}>{item.title} <Status status={item.status}/></span>) : <span>No downstream task</span>}</div></div></div>
    <div className="detail-section"><h3>File boundary</h3><div className="chips">{task.file_globs.map(glob=><span key={glob}>{glob}</span>)}</div></div>
    {Object.keys(task.validation||{}).length>0 && <div className="detail-section"><h3>Validation</h3><pre className="validation">{JSON.stringify(task.validation,null,2)}</pre></div>}
    {task.error && <div className="detail-section"><h3>Recovery note</h3><pre className="error-box">{task.error}</pre></div>}
  </div>
}

function TaskActivity({events}:{events:EventRow[]}) {
  return <div className="task-details"><div className="detail-section"><h3>Live activity <span className="live-indicator"/></h3><div className="timeline">{events.slice(-200).map(event=><div className="event-row" key={event.id}><span className="event-dot"/><div><b>{event.kind.replaceAll('_',' ')}</b><p>{event.message}</p><small>{date(event.created_at)}</small></div></div>)}</div></div></div>
}

function FleetPage({fleet,busy,onSave,leadStatus,pairing,prompt,leadBusy,onCreatePairing,onRevokeLead}:{fleet:FleetSnapshot|null;busy:boolean;onSave:(settings:FleetSettings)=>void;leadStatus:LeadStatus|null;pairing:LeadPairing|null;prompt:string;leadBusy:boolean;onCreatePairing:()=>void;onRevokeLead:()=>void}) {
  if (!fleet) return <div className="panel storage-loading">Loading fleet status…</div>
  const submit = (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    onSave({max_active_runs:Number(form.get('max_active_runs')), max_parallel_builds:Number(form.get('max_parallel_builds')),
      max_per_credential:Number(form.get('max_per_credential')), min_free_disk_bytes:Number(form.get('min_free_disk_gb'))*1024**3,
      disk_reserve_per_run_bytes:Number(form.get('disk_reserve_gb'))*1024**3})
  }
  return <div className="storage-page">
    <section className="stat-grid">
      <StatCard label="Active runs" value={fleet.active_run_count} note={`Capacity ${fleet.settings.max_active_runs}${fleet.draining ? ' · draining' : ''}`} icon="◷" tone={fleet.draining ? 'amber' : 'blue'}/>
      <StatCard label="Queued tasks" value={fleet.queued_tasks} note="Waiting for a safe slot" icon="☷" tone="blue"/>
      <StatCard label="Builds active" value={fleet.builds_active} note={`Build limit ${fleet.settings.max_parallel_builds}`} icon="⚙" tone="violet"/>
      <StatCard label="Free disk" value={Number((fleet.disk.free_bytes / 1024**3).toFixed(1))} note={`Reserve target ${formatBytes(fleet.disk.required_reserve_bytes)} · ${fleet.disk.can_admit_run ? 'admission available' : 'admission paused'}`} icon="▤" tone={fleet.disk.can_admit_run ? 'blue' : 'amber'}/>
    </section>
    {fleet.draining && <div className="storage-alert warning"><b>Capacity is draining</b><span>Current runs continue; new runs wait until the active count falls below the new limit.</span></div>}
    {fleet.credential_cooldowns.length>0 && <div className="storage-alert warning"><b>CLI credential cooldown</b><span>{fleet.credential_cooldowns.map(item=>`${item.adapter} · ${item.executable} (${item.reason}, until ${date(item.expires_at)})`).join(' · ')}</span></div>}
    <section className="two-col storage-details">
      <div className="panel"><div className="panel-heading"><div><h2>Resource limits</h2><p>Changes persist locally and apply to new admissions. Active runs are never killed to reduce capacity.</p></div></div>
        <form className="form" onSubmit={submit}>
          <label>Maximum active runs<input name="max_active_runs" type="number" min="1" max="32" defaultValue={fleet.settings.max_active_runs}/></label>
          <label>Concurrent validation/build commands<input name="max_parallel_builds" type="number" min="1" max="16" defaultValue={fleet.settings.max_parallel_builds}/></label>
          <label>Concurrent runs per CLI credential<input name="max_per_credential" type="number" min="1" max="16" defaultValue={fleet.settings.max_per_credential}/><small>Grouped by adapter and executable path.</small></label>
          <div className="form-grid"><label>Minimum free disk (GiB)<input name="min_free_disk_gb" type="number" min="0.25" max="1024" step="0.25" defaultValue={fleet.settings.min_free_disk_bytes / 1024**3}/></label><label>Disk reserve per run (GiB)<input name="disk_reserve_gb" type="number" min="0.0625" max="1024" step="0.0625" defaultValue={fleet.settings.disk_reserve_per_run_bytes / 1024**3}/></label></div>
          <div className="modal-actions"><button className="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save limits'}</button></div>
        </form>
      </div>
      <div className="panel"><div className="panel-heading"><div><h2>Active runs</h2><p>Current worker and task ownership.</p></div></div>
        {fleet.active_runs.length ? <div className="fleet-run-list">{fleet.active_runs.map(run => <div className="fleet-run" key={run.task_id}><div><b>{run.title}</b><small>{run.repository_name || 'Repository'} · {run.agent_name || 'Agent pending'}</small><small>{run.task_id.slice(0,8)}{run.run_id ? ` · run ${run.run_id.slice(0,8)}` : ''}</small></div><Status status={run.status}/></div>)}</div> : <Empty title="No active runs" text="New tasks will use the current resource limits."/>}
      </div>
    </section>
    <section className="panel tech-lead-panel"><div className="panel-heading"><div><h2>External Tech Lead</h2><p>Separate scoped session and renewable control lease. Local API health does not imply a connected Tech Lead.</p></div><span className={`agent-state ${leadStatus?.connected?'available':'disabled'}`}>{leadStatus?.connected?'CONNECTED':'DISCONNECTED'}</span></div>
      {leadStatus?.connected && leadStatus.session ? <div className="tech-lead-session"><b>{leadStatus.session.client_name}</b><small>Client {leadStatus.session.client_id} · lease until {date(leadStatus.session.lease_expires_at)}</small><div className="chips">{leadStatus.session.scopes.map(scope=><span key={scope}>{scope}</span>)}</div><button className="button danger-outline" disabled={leadBusy} onClick={onRevokeLead}>{leadBusy?'Revoking…':'Revoke control lease'}</button></div> : <div className="tech-lead-session"><p>No external lead holds the lease. Generate a one-time pairing code for a local CLI or bridge.</p><button className="button primary" disabled={leadBusy} onClick={onCreatePairing}>{leadBusy?'Preparing…':'Generate Tech Lead pairing'}</button></div>}
      {pairing && <div className="lead-pairing"><div><b>One-time pairing code</b><small>Contract v{pairing.contract_version} · expires {date(pairing.expires_at)} · {pairing.scopes.join(', ')}</small></div><code>{pairing.pairing_code}</code><label>Activation prompt<textarea readOnly rows={7} value={prompt} onFocus={event=>event.currentTarget.select()}/></label><small>Paste the prompt into the local agent and provide the code through its pairing flow. The code expires in five minutes and can be used once.</small></div>}
    </section>
    <div className="storage-note">RAM is not shown as a fixed worker estimate. This page reports configured concurrency and live disk headroom.</div>
  </div>
}
