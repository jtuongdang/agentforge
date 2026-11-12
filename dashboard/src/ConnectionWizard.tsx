import { useEffect, useState } from 'react'
import { useI18n } from './i18n'

type Login = { state: string; provider?: string; message?: string; device_code?: string; verification_url?: string }
type Api = <T>(path: string, init?: RequestInit) => Promise<T>

export function ConnectionWizard({ api }: { api: Api }) {
  const { t } = useI18n()
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
    <div className="panel-heading"><div><div className="eyebrow">{t('connEyebrow')}</div><h2>{t('connTitle')}</h2><p>{t('connSubtitle')}</p></div></div>
    <div className="form-note"><b>{t('connStep1Title')}</b><p>{t('connStep1Desc')}</p></div>
    <div className="form-note"><b>{t('connStep2Title')}</b><p>{t('connStep2Desc')}</p>
      <label><input type="checkbox" checked={storageConsent} onChange={e => setStorageConsent(e.target.checked)} disabled={login.state === 'running'}/> {t('connStorageConsent')}</label>
      {storage === false && <p role="alert">{t('connKeychainUnavailable')}</p>}
      <div className="setup-actions">
        <button className="button secondary" disabled={disabled} onClick={() => void start('codex')}>{t('connLoginCodex')}</button>
        <button className="button secondary" disabled={disabled || !storageConsent} onClick={() => void start('copilot')}>{t('connLoginCopilot')}</button>
        <button className="button secondary" disabled={disabled || !storageConsent} onClick={() => void start('github')}>{t('connLoginGithub')}</button>
      </div>
      <p>{t('connHint')}</p>
    </div>
    {login.state !== 'idle' && <div className="form-note" role="status"><b>{login.provider} · {login.state}</b><p>{login.message}</p>
      {login.device_code && <p>{t('connOneTimeCode')} <strong>{login.device_code}</strong> · <a href="https://github.com/login/device" target="_blank" rel="noreferrer">github.com/login/device</a></p>}
      {login.state === 'running' && <button className="button secondary" disabled={busy} onClick={() => void cancel()}>{t('connCancelLogin')}</button>}
    </div>}
    <div className="form-note"><b>{t('connStep3Title')}</b><p>{t('connStep3Desc')}</p></div>
    {error && <p role="alert">{error}</p>}
  </section>
}
