import { useEffect, useRef, useState } from 'react'
import { useI18n } from './i18n'

type Api = <T>(path: string, init?: RequestInit) => Promise<T>
type Account = { configured: boolean; required: boolean; authenticated: boolean; stage: string; message: string;
  user: { user_id: string; display_name: string; email: string } | null; profile_claim_required: boolean;
  metadata_sync: boolean; ai_identity_allowed: boolean }

export function AccountPanel({ api }: { api: Api }) {
  const { t } = useI18n()
  const [account, setAccount] = useState<Account | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [sync, setSync] = useState(false)
  const [claim, setClaim] = useState(false)
  const version = useRef(0)
  const waiting = account?.stage === 'opening_browser' || account?.stage === 'awaiting_browser'
  useEffect(() => {
    let active = true
    const load = async () => {
      const current = version.current
      try { const value = await api<Account>('/account'); if (active && current === version.current) setAccount(value) }
      catch (e) { if (active && current === version.current) setError(String(e)) }
    }
    void load()
    const timer = window.setInterval(() => void load(), waiting ? 1500 : 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [api, waiting])
  async function action(path: string, method = 'POST') {
    const current = ++version.current
    setBusy(true); setError('')
    try { const value = await api<Account>(path, { method }); if (current === version.current) { setAccount(value); setClaim(false) } }
    catch (e) { if (current === version.current) setError(e instanceof Error ? e.message : String(e)) }
    finally { if (current === version.current) setBusy(false) }
  }
  return <section className="panel account-panel">
    <div className="panel-heading"><div><div className="eyebrow">{t('accountEyebrow')}</div><h2>{t('accountTitle')}</h2><p>{t('accountSubtitle')}</p></div></div>
    {!account && <p role="status">{t('accountLoading')}</p>}
    {account && !account.configured && <div className="form-note"><b>{t('accountHubMissingTitle')}</b><p>{account.message} {t('accountHubMissingSuffix')}</p></div>}
    {account?.configured && <>
      {account.user && <div className="account-identity"><strong>{account.user.display_name || t('accountGoogleFallback')}</strong><span>{account.user.email}</span><small>{account.authenticated ? t('accountVerified') : t('accountReverify')}</small></div>}
      {!account.user && <>
        <p>{t('accountIntro')}</p>
        <label className="account-checkbox"><input type="checkbox" checked={sync} onChange={e => setSync(e.target.checked)} disabled={busy || waiting}/> {t('accountSyncLabel')}</label>
        <div className="setup-actions"><button className="button primary" disabled={busy || waiting} onClick={() => void action(`/account/login?sync_metadata=${sync}`)}>{t('accountContinueGoogle')}</button>
          {waiting && <button className="button secondary" disabled={busy} onClick={() => void action('/account/login', 'DELETE')}>{t('accountCancelLogin')}</button>}</div>
      </>}
      {account.profile_claim_required && <div className="form-note"><b>{t('accountClaimTitle')}</b><p>{t('accountClaimDesc')}</p>
        <label className="account-checkbox"><input type="checkbox" checked={claim} onChange={e => setClaim(e.target.checked)} disabled={busy}/> {t('accountClaimCheck')}</label>
        <button className="button primary" disabled={busy || !claim} onClick={() => void action('/account/claim-profile?acknowledge_data_ownership=true')}>{t('accountClaimBtn')}</button>
      </div>}
      {account.user && <div className="setup-actions">
        {account.authenticated && !account.profile_claim_required && <button className="button secondary" disabled={busy} onClick={() => void action('/account/sync-connections')}>{t('accountSyncBtn')}</button>}
        <button className="button secondary" disabled={busy} onClick={() => { if (window.confirm(t('accountLogoutConfirm'))) void action('/account/logout') }}>{t('accountLogoutBtn')}</button>
      </div>}
      <p role="status">{account.message}</p>
      {account.required && !account.ai_identity_allowed && <p className="form-note">{t('accountIdentityNotReady')}</p>}
      {account.ai_identity_allowed && account.authenticated && <p className="hint">{t('accountIdentityReady')}</p>}
    </>}
    {error && <p role="alert" className="account-error">{error}</p>}
  </section>
}
