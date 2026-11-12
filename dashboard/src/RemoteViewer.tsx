import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './RemoteViewer.css'

type PairRequest = { request_id: string; poll_secret: string; expires_at: string }
type RemoteDevice = { device_id: string; access_token: string; expires_at: string }
type RemoteTask = { id: string; title: string; status: string; priority: string; repository_name: string; updated_at: string; validation_passed: boolean; full_diff_available: boolean }
type DiffResponse = { revision_sha: string; diff: string; offset: number; next_offset: number | null; truncated: boolean }
const pairingPolls = new Map<string, Promise<RemoteDevice & { state: string }>>()

async function remoteFetch<T>(path: string, token?: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/remote/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers },
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try { const body = await response.json() as { detail?: string }; message = body.detail || message } catch { /* keep status */ }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export default function RemoteViewer() {
  const [code, setCode] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('pairing') ?? '')
  const [deviceName, setDeviceName] = useState('')
  const [pairRequest, setPairRequest] = useState<PairRequest | null>(null)
  const [device, setDevice] = useState<RemoteDevice | null>(null)
  const [tasks, setTasks] = useState<RemoteTask[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [diff, setDiff] = useState<DiffResponse | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const wsActive = useRef(false)
  const wsSocket = useRef<WebSocket | null>(null)
  const wsRetryDelay = useRef(1000)
  const wsRetryTimer = useRef(0)

  useEffect(() => {
    if (!code) return
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}`)
  }, [code])

  const refresh = useCallback(async (token: string) => {
    const result = await remoteFetch<{ tasks: RemoteTask[] }>('/tasks', token)
    setTasks(result.tasks)
  }, [])

  useEffect(() => {
    if (!pairRequest || device) return
    let active = true
    const poll = async () => {
      try {
        const existing = pairingPolls.get(pairRequest.request_id)
        const current = existing ?? remoteFetch<RemoteDevice & { state: string }>(`/pairings/${pairRequest.request_id}/poll`, undefined,
          { method: 'POST', body: JSON.stringify({ poll_secret: pairRequest.poll_secret }) })
        if (!existing) pairingPolls.set(pairRequest.request_id, current)
        const result = await current.finally(() => { if (pairingPolls.get(pairRequest.request_id) === current) pairingPolls.delete(pairRequest.request_id) })
        if (!active) return
        if (result.state === 'approved') {
          const session = result as RemoteDevice
          setDevice(session); setPairRequest(null); setMessage('Thiết bị đã được duyệt. Phiên chỉ lưu trong bộ nhớ tab này.')
          await refresh(session.access_token)
        } else if (result.state === 'denied' || result.state === 'expired') {
          setPairRequest(null); setError(result.state === 'denied' ? 'Yêu cầu ghép nối đã bị từ chối.' : 'Mã ghép nối đã hết hạn. Hãy xin mã mới.')
        }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : 'Không thể kiểm tra ghép nối') }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 2500)
    return () => { active = false; window.clearInterval(timer) }
  }, [pairRequest, device, refresh])

  useEffect(() => {
    if (!device) return
    let active = true
    const load = async () => {
      try { await refresh(device.access_token); if (active) setError('') }
      catch (e) { if (active) { setError(e instanceof Error ? e.message : 'Phiên đã hết hạn'); setDevice(null); setDiff(null) } }
    }
    const timer = window.setInterval(() => void load(), 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [device, refresh])

  useEffect(() => {
    if (!device || !selected) return
    wsActive.current = true
    wsRetryDelay.current = 1000
    const connect = async () => {
      try {
        await remoteFetch<void>('/ws-ticket', device.access_token, { method:'POST', body:JSON.stringify({task_id:selected}) })
        if (!wsActive.current) return
        const url = new URL(`/remote/ws/tasks/${encodeURIComponent(selected)}/events`, location.origin)
        url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
        const connection = new WebSocket(url)
        wsSocket.current = connection
        connection.onopen = () => { wsRetryDelay.current = 1000 }
        connection.onmessage = () => { if (wsActive.current) void refresh(device.access_token) }
        connection.onclose = () => {
          if (!wsActive.current) return
          wsRetryTimer.current = window.setTimeout(() => void connect(), wsRetryDelay.current)
          wsRetryDelay.current = Math.min(15000, wsRetryDelay.current * 2)
        }
        connection.onerror = () => connection.close()
      } catch {
        if (wsActive.current) { wsRetryTimer.current = window.setTimeout(() => void connect(), wsRetryDelay.current); wsRetryDelay.current = Math.min(15000, wsRetryDelay.current * 2) }
      }
    }
    void connect()
    return () => { wsActive.current = false; window.clearTimeout(wsRetryTimer.current); wsSocket.current?.close(); wsSocket.current = null }
  }, [device, selected, refresh])

  const pair = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(''); setMessage('')
    try {
      const request = await remoteFetch<PairRequest>('/pairings', undefined, { method: 'POST', body: JSON.stringify({
        pairing_code: code.trim(), client_id: crypto.randomUUID(), client_name: deviceName.trim(),
      }) })
      setPairRequest(request); setCode(''); setMessage('Đã gửi yêu cầu. Hãy đợi owner duyệt trong ứng dụng desktop.')
    } catch (e) { setError(e instanceof Error ? e.message : 'Không thể gửi yêu cầu ghép nối') }
    finally { setBusy(false) }
  }

  const openDiff = async (task: RemoteTask) => {
    setSelected(task.id); setDiff(null); setError('')
    if (!device || !task.full_diff_available) return
    try { setDiff(await remoteFetch<DiffResponse>(`/tasks/${encodeURIComponent(task.id)}/diff?limit=24000`, device.access_token)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Không tải được diff') }
  }

  const loadMoreDiff = async () => {
    if (!device || !selected || !diff?.next_offset) return
    try {
      const next = await remoteFetch<DiffResponse>(`/tasks/${encodeURIComponent(selected)}/diff?offset=${diff.next_offset}&limit=24000`, device.access_token)
      setDiff(current => current ? { ...next, diff: current.diff + next.diff } : next)
    } catch (e) { setError(e instanceof Error ? e.message : 'Không tải tiếp được diff') }
  }

  return <main className="remote-shell">
    <header className="remote-header"><div className="remote-mark">D</div><div><strong>DevAgent Remote Review</strong><small>Private VPN · HTTPS · Read only</small></div>
      {device && <button className="remote-button subtle" onClick={() => { setDevice(null); setTasks([]); setDiff(null); setSelected(null); setMessage('Đã ngắt phiên trên tab này.') }}>Disconnect</button>}
    </header>
    {error && <div className="remote-alert error" role="alert">{error}</div>}{message && <div className="remote-alert" role="status">{message}</div>}
    {!device ? <section className="remote-card">
      <p className="remote-eyebrow">OWNER APPROVAL REQUIRED</p><h1>Ghép nối thiết bị</h1>
      <p>Nhập mã mời ngắn hạn do owner cấp. Owner sẽ thấy tên thiết bị và phải duyệt trước khi thiết bị đọc được danh sách task.</p>
      <form onSubmit={event => void pair(event)} className="remote-form">
        <label>Tên thiết bị<input required maxLength={120} value={deviceName} onChange={event => setDeviceName(event.target.value)} placeholder="Ví dụ: Laptop review" disabled={!!pairRequest}/></label>
        <label>Mã ghép nối<input required maxLength={256} autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} placeholder="Dán mã mời" disabled={!!pairRequest}/></label>
        {!pairRequest ? <button className="remote-button primary" disabled={busy || !code.trim() || !deviceName.trim()}>{busy ? 'Đang gửi…' : 'Yêu cầu ghép nối'}</button>
          : <button type="button" className="remote-button subtle" onClick={() => {
            const pending = pairRequest
            void remoteFetch(`/pairings/${pending.request_id}/cancel`, undefined,
              { method: 'POST', body: JSON.stringify({ poll_secret: pending.poll_secret }) })
              .finally(() => setPairRequest(null))
          }}>Hủy yêu cầu đang chờ</button>}
      </form>
      <small className="remote-footnote">Phiên truy cập chỉ lưu trong bộ nhớ tab; tải lại trang sẽ cần ghép nối lại. Không có quyền tạo task, duyệt publication, truy cập log hoặc cấu hình owner.</small>
    </section> : <section className="remote-content">
      <div className="remote-card remote-device"><div><p className="remote-eyebrow">PAIRED DEVICE</p><h1>Task overview</h1><p>Phiên hết hạn {new Date(device.expires_at).toLocaleString()} · quyền <b>summary:read</b></p></div><span className="remote-badge">READ ONLY</span></div>
      <div className="remote-task-list">{tasks.length === 0 ? <div className="remote-card">Chưa có task để hiển thị.</div> : tasks.map(task => <article className={`remote-task ${selected === task.id ? 'selected' : ''}`} key={task.id}>
        <button className="remote-task-button" onClick={() => void openDiff(task)}><div className="remote-task-line"><strong>{task.title}</strong><span>{task.status.replaceAll('_', ' ')}</span></div>
          <div className="remote-task-meta"><span>{task.repository_name}</span><span>{task.priority}</span><span>{task.validation_passed ? 'Validation passed' : 'Validation pending'}</span></div>
          <small>{task.full_diff_available ? 'Owner granted diff for this revision' : 'Summary only · no diff grant'}</small></button>
        {selected === task.id && diff && <div className="remote-diff"><p>Revision <code>{diff.revision_sha.slice(0, 12)}</code>{diff.truncated ? ' · diff còn phần tiếp theo' : ''}</p><pre>{diff.diff}</pre>
          {diff.next_offset !== null && <button className="remote-button subtle" onClick={() => void loadMoreDiff()}>Load next diff page</button>}</div>}
      </article>)}</div>
    </section>}
    <footer className="remote-footer">Remote access is available only through the configured HTTPS ingress on the private VPN.</footer>
  </main>
}
