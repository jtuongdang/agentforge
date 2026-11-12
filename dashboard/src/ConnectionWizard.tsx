import { useEffect, useRef, useState } from 'react'
import { useI18n } from './i18n'

type Login = { state: string; provider?: string; message?: string; device_code?: string; verification_url?: string }
type AgentSummary = { id: string; name: string; adapter: string; enabled: boolean; available: boolean; credential_status: string }
type Api = <T>(path: string, init?: RequestInit) => Promise<T>

const AGENT_ID_BY_PROVIDER: Record<string, string> = {
  codex: 'codex-local',
  copilot: 'copilot-cli',
  antigravity: 'antigravity-cli',
}

export function ConnectionWizard({ api, agents = [], onRefresh }: { api: Api; agents?: AgentSummary[]; onRefresh?: () => Promise<void> | void }) {
  const { t } = useI18n()
  const [login, setLogin] = useState<Login>({ state: 'idle' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [storageConsent, setStorageConsent] = useState(false)
  const [storage, setStorage] = useState<boolean | null>(null)
  const prevStateRef = useRef<string>('idle')

  useEffect(() => {
    let active = true
    api<Login>('/connections/login').then(value => { if (active) { setLogin(value); prevStateRef.current = value.state } }).catch(e => { if (active) setError(String(e)) })
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

  useEffect(() => {
    const prev = prevStateRef.current
    prevStateRef.current = login.state
    if (prev === 'running' && login.state === 'completed') {
      const targetAgentId = login.provider ? AGENT_ID_BY_PROVIDER[login.provider] : undefined
      void (async () => {
        try {
          await api('/agents/refresh', { method: 'POST' })
          if (targetAgentId) {
            await api(`/agents/${encodeURIComponent(targetAgentId)}/check-token`, { method: 'POST' })
          }
        } catch {
          // Ignore probe errors if CLI is not registered yet
        } finally {
          await onRefresh?.()
        }
      })()
    }
  }, [api, login.provider, login.state, onRefresh])

  async function start(provider: string) {
    setBusy(true); setError('')
    try {
      prevStateRef.current = 'running'
      const next = await api<Login>(`/connections/${provider}/login?acknowledge_storage_risk=${storageConsent}`, { method: 'POST' })
      setLogin(next)
    }
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
  const codexAgent = agents.find(a => a.adapter === 'codex')
  const copilotAgent = agents.find(a => a.adapter === 'copilot')
  const antigravityAgent = agents.find(a => a.adapter === 'antigravity') || agents.find(a => a.id === 'antigravity-ide')
  const codexReady = Boolean(codexAgent && (codexAgent.available || codexAgent.credential_status === 'valid'))
  const copilotReady = Boolean(copilotAgent && (copilotAgent.available || copilotAgent.credential_status === 'valid'))
  const antigravityReady = Boolean(antigravityAgent && (antigravityAgent.available || antigravityAgent.credential_status === 'valid'))
  const anyAiReady = codexReady || copilotReady || antigravityReady

  return <section className="panel connection-wizard">
    <div className="panel-heading">
      <div>
        <div className="eyebrow">{t('connEyebrow')}</div>
        <h2>{t('connTitle')}</h2>
        <p>{t('connSubtitle')}</p>
      </div>
      <div className={`ai-readiness-bar ${anyAiReady ? 'ready' : 'pending'}`} role="status">
        <div className="ai-readiness-head">
          <span className={`status-dot ${anyAiReady ? 'online' : ''}`}/>
          <strong>{t('connReadinessTitle')}:</strong>
          <span>{anyAiReady ? t('connReadinessReady') : t('connReadinessPending')}</span>
        </div>
      </div>
    </div>

    <div className="wizard-provider-list">
      <div className="wizard-provider-row">
        <div className="wizard-provider-info">
          <span className={`status-dot ${codexReady ? 'online' : ''}`}/>
          <div>
            <strong>OpenAI Codex CLI</strong>
            <small>{codexReady ? t('connStatusReady') : t('connStatusNotVerified')} · Keychain</small>
          </div>
        </div>
        <div className="wizard-provider-actions">
          <span className={`readiness-pill ${codexReady ? 'ready' : 'pending'}`}>
            {codexReady ? `✓ ${t('connStatusReady')}` : t('connStatusNotVerified')}
          </span>
          <button className={`button secondary ${codexReady ? 'connected' : ''}`} disabled={disabled} onClick={() => void start('codex')}>
            {codexReady ? `✓ ${t('connLoginCodex')}` : t('connLoginCodex')}
          </button>
        </div>
      </div>

      <div className="wizard-provider-row">
        <div className="wizard-provider-info">
          <span className={`status-dot ${antigravityReady ? 'online' : ''}`}/>
          <div>
            <strong>Google Antigravity</strong>
            <small>{antigravityReady ? t('connStatusReady') : t('connStatusNotVerified')} · Local App / CLI</small>
          </div>
        </div>
        <div className="wizard-provider-actions">
          <span className={`readiness-pill ${antigravityReady ? 'ready' : 'pending'}`}>
            {antigravityReady ? `✓ ${t('connStatusReady')}` : t('connStatusNotVerified')}
          </span>
          <button className={`button secondary ${antigravityReady ? 'connected' : ''}`} disabled={disabled} onClick={() => void start('antigravity')}>
            {antigravityReady ? `✓ ${t('connLoginAntigravity')}` : t('connLoginAntigravity')}
          </button>
        </div>
      </div>

      <div className="wizard-provider-row">
        <div className="wizard-provider-info">
          <span className={`status-dot ${copilotReady ? 'online' : ''}`}/>
          <div>
            <strong>GitHub Copilot CLI</strong>
            <small>{copilotReady ? t('connCopilotPolicyNote') : t('connStatusNotVerified')}</small>
          </div>
        </div>
        <div className="wizard-provider-actions">
          <span className={`readiness-pill ${copilotReady ? 'handoff' : 'pending'}`}>
            {copilotReady ? `✓ ${t('connCopilotPolicyNote')}` : t('connStatusNotVerified')}
          </span>
          <button className={`button secondary ${copilotReady ? 'connected' : ''}`} disabled={disabled || !storageConsent} onClick={() => void start('copilot')}>
            {copilotReady ? `✓ ${t('connLoginCopilot')}` : t('connLoginCopilot')}
          </button>
        </div>
      </div>

      <div className="wizard-provider-row">
        <div className="wizard-provider-info">
          <span className="status-dot online"/>
          <div>
            <strong>GitHub CLI (Pull Request)</strong>
            <small>{t('connStep3Desc')}</small>
          </div>
        </div>
        <div className="wizard-provider-actions">
          <button className="button secondary" disabled={disabled || !storageConsent} onClick={() => void start('github')}>
            {t('connLoginGithub')}
          </button>
        </div>
      </div>
    </div>

    <div className="wizard-consent-bar">
      <label className="account-checkbox">
        <input type="checkbox" checked={storageConsent} onChange={e => setStorageConsent(e.target.checked)} disabled={login.state === 'running'}/>
        <span>{t('connStorageConsent')}</span>
      </label>
      <small className="muted">{t('connHint')}</small>
    </div>

    {storage === false && <p role="alert" className="account-error">{t('connKeychainUnavailable')}</p>}
    {login.state !== 'idle' && <div className={`form-note ${login.state === 'completed' ? 'success' : ''}`} role="status">
      <b>{login.provider} · {login.state === 'completed' ? `✓ ${login.state}` : login.state}</b>
      <p>{login.state === 'completed' ? t('connCompletedReady') : login.message}</p>
      {login.device_code && <p>{t('connOneTimeCode')} <strong>{login.device_code}</strong> · <a href="https://github.com/login/device" target="_blank" rel="noreferrer">github.com/login/device</a></p>}
      {login.state === 'running' && <button className="button secondary" disabled={busy} onClick={() => void cancel()}>{t('connCancelLogin')}</button>}
    </div>}
    {error && <p role="alert" className="account-error">{error}</p>}
  </section>
}
