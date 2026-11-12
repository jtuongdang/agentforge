import { useEffect, useState } from 'react'

type Login = { state: string; provider?: string; message?: string; device_code?: string; verification_url?: string }
type Api = <T>(path: string, init?: RequestInit) => Promise<T>

export function ConnectionWizard({ api }: { api: Api }) {
  const [login, setLogin] = useState<Login>({ state: 'idle' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [storageConsent, setStorageConsent] = useState(false)
  const [storage, setStorage] = useState<boolean | null>(null)
  useEffect(() => {
    let active = true
    api<Login>('/connections/login').then(value => { if (active) setLogin(value) }).catch(e => { if (active) setError(String(e)) })
    api<{ available: boolean }>('/providers/credential-store').then(value => { if (active) setStorage(value.available) }).catch(e => { if (active) setError(String(e)) })
    return () => { active = false }
  }, [api])
  useEffect(() => {
    if (login.state !== 'running') return
    let active = true
    const timer = window.setInterval(() => {
      api<Login>('/connections/login').then(value => { if (active) { setLogin(value); setError('') } })
        .catch(e => { if (active) setError(String(e)) })
    }, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [api, login.state])
  async function start(provider: string) {
    setBusy(true); setError('')
    try { setLogin(await api<Login>(`/connections/${provider}/login?acknowledge_storage_risk=${storageConsent}`, { method: 'POST' })) }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  async function cancel() {
    setBusy(true); setError(''); setLogin(current => ({ ...current, device_code: undefined, verification_url: undefined }))
    try { setLogin(await api<Login>('/connections/login', { method: 'DELETE' })) }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  const disabled = busy || login.state === 'running' || storage !== true
  return <section className="panel connection-wizard">
    <div className="panel-heading"><div><div className="eyebrow">THIẾT LẬP KẾT NỐI</div><h2>Tài khoản và AI trên máy</h2><p>Chọn dịch vụ để tự mở trình duyệt đăng nhập chính thức. Cần cài CLI trước.</p></div></div>
    <div className="form-note"><b>1 · Google / AgentForge</b><p>Chưa có Hub HTTPS và OAuth client để đăng nhập Google. License được cấu hình riêng; đăng nhập CLI bên dưới chưa xác minh tài khoản AgentForge.</p></div>
    <div className="form-note"><b>2 · Kết nối AI và GitHub</b><p>Codex dùng OS keychain. Copilot và GitHub CLI có thể lưu token vào file riêng khi credential store lỗi; AgentForge không thu thập token này.</p>
      <label><input type="checkbox" checked={storageConsent} onChange={e => setStorageConsent(e.target.checked)} disabled={login.state === 'running'}/> Tôi đồng ý cơ chế lưu credential của Copilot/GitHub CLI, gồm fallback vào file nếu keychain lỗi.</label>
      {storage === false && <p role="alert">OS credential store không khả dụng; đã chặn đăng nhập tự động.</p>}
      <div className="setup-actions">
        <button className="button secondary" disabled={disabled} onClick={() => void start('codex')}>Đăng nhập Codex</button>
        <button className="button secondary" disabled={disabled || !storageConsent} onClick={() => void start('copilot')}>Đăng nhập Copilot</button>
        <button className="button secondary" disabled={disabled || !storageConsent} onClick={() => void start('github')}>Đăng nhập GitHub</button>
      </div>
      <p>Đã có phiên hợp lệ: dùng Kiểm tra token ở Agents. Kiểm tra Copilot gửi một request, chỉ chạy khi bạn xác nhận. Antigravity: đăng nhập trong ứng dụng/CLI chính thức; chưa có lệnh browser login được kiểm chứng.</p>
    </div>
    {login.state !== 'idle' && <div className="form-note" role="status"><b>{login.provider} · {login.state}</b><p>{login.message}</p>
      {login.device_code && <p>Mã một lần: <strong>{login.device_code}</strong> · <a href="https://github.com/login/device" target="_blank" rel="noreferrer">github.com/login/device</a></p>}
      {login.state === 'running' && <button className="button secondary" disabled={busy} onClick={() => void cancel()}>Hủy đăng nhập</button>}
    </div>}
    <div className="form-note"><b>3 · Xác minh trước khi chạy</b><p>Đăng nhập thành công chưa chứng minh model/quota hay quyền chạy task. Copilot tự động đang chờ nghiệm thu quyền file/tool; có thể dùng handoff hoặc worker đã xác minh. Giữ số worker mặc định 1 trên máy mới.</p></div>
    {error && <p role="alert">{error}</p>}
  </section>
}
