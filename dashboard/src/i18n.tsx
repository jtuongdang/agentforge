import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type Lang = 'en' | 'vi'
export type LangMode = 'auto' | Lang

const STORAGE_KEY = 'agentforge.lang'

const en = {
  // Language selector
  langLabel: 'Language',
  langAuto: 'Auto',
  langEn: 'English',
  langVi: 'Tiếng Việt',

  // Sidebar & Topbar
  brandTitle: 'DevAgent Orchestrator',
  brandSubtitle: 'Multi-agent engineering',
  workspaceLabel: 'WORKSPACE',
  workspaceTitle: 'Registered projects',
  engineConnected: 'Local engine connected',
  engineConnecting: 'Connecting to API…',
  versionNote: 'v0.1.0 · localhost only',
  breadcrumbRoot: 'AI Engineering',
  localBadge: 'LOCAL',
  refreshTitle: 'Refresh',

  // Navigation
  navOverview: 'Overview',
  navTasks: 'Tasks',
  navAgents: 'Agents',
  navContracts: 'Contracts',
  navHistory: 'History',
  navStorage: 'Storage',
  navFleet: 'Fleet',
  navRemote: 'Remote',

  // Page Headings
  controlPlaneEyebrow: 'ENGINEERING CONTROL PLANE',
  overviewTitle: 'AI engineering workspace',
  overviewSubtitle: 'Coordinate independent agents across your registered Git projects.',
  newTaskBtn: 'New task',
  proposeContractBtn: 'Propose contract',

  // Setup Wizard
  firstRunEyebrow: 'FIRST RUN · STEP 1 OF 3',
  connectRepoTitle: 'Connect a project repository',
  connectRepoDesc: 'Choose a local Git checkout, review its base branch and validation setup, then add an Agent before scheduling your first task.',
  stepRepo: '1 · Repository',
  stepValidation: '2 · Validation',
  stepAgent: '3 · Agent',
  chooseRepoBtn: 'Choose repository',
  detectAgentsBtn: 'Detect Agent CLIs',

  // Overview stats & sections
  statActiveTitle: 'Active tasks',
  statActiveNote: 'Executing in isolated worktrees',
  statReviewTitle: 'Need your review',
  statReviewNote: 'Diff and validation ready',
  statHandoffTitle: 'IDE handoffs',
  statHandoffNote: 'Waiting for a manual agent run',
  statAttentionTitle: 'Needs attention',
  statAttentionNote: 'Blocked or failed tasks',
  recentTasksTitle: 'Recent tasks',
  recentTasksSubtitle: 'Live lifecycle across registered repositories',
  viewAllTasks: 'View all tasks',
  agentRegistryTitle: 'Agent registry',
  agentRegistrySubtitle: 'Capability matching · no fixed roles',
  manageBtn: 'Manage →',
  registeredReposTitle: 'Registered repositories',
  registeredReposSubtitle: 'Isolated worktree roots',
  addRepoBtn: '＋ Add repository',

  // Agents page
  registeredAgentsTitle: 'Registered agents',
  registeredAgentsDesc: 'Check authentication status and remove or restore agents from task dispatch.',
  refreshDetectionBtn: 'Refresh detection',
  registerAdapterBtn: '＋ Register adapter',

  // License Panel
  licenseTitle: 'License & trial',
  licenseDesc: 'AI execution is controlled by a signed, device-bound entitlement. Local projects remain readable and exportable.',
  licenseAiExec: 'AI execution',
  licenseEnabled: 'enabled',
  licensePaused: 'paused',
  licenseDaysLeft: 'Days remaining',
  licensePlan: 'Plan',
  licenseTrialPending: 'The 14-day device trial starts when the license hub issues the first signed entitlement.',
  licenseHubRequired: 'The license hub is required but is not configured. Agent dispatch is paused.',
  licenseConsentText: 'To start the 14-day trial or activate a license, this app sends a one-way device identifier hash to the license hub. It does not send source code, prompts, project files, email, or OS details. The trial is tied to this device.',
  licenseConsentStarting: 'Starting…',
  licenseConsentBtn: 'Agree and start device trial',
  licenseKeyLabel: 'License key',
  licenseKeyPlaceholder: 'Enter activation key',
  licenseWorking: 'Working…',
  licenseActivateBtn: 'Activate license',
  licenseChecking: 'Checking…',
  licenseRefreshBtn: 'Refresh status',
  licenseReleaseLabel: 'Release this device',
  licenseReleasePlaceholder: 'Re-enter license key',
  licenseReleasing: 'Releasing…',
  licenseDeactivateBtn: 'Deactivate on this device',

  // Account Panel
  accountEyebrow: 'ACCOUNT · GOOGLE',
  accountTitle: 'AgentForge Account',
  accountSubtitle: 'Verify Google before enabling AI. AI accounts and GitHub access are connected separately.',
  accountLoading: 'Checking login session…',
  accountHubMissingTitle: 'Login Hub not configured',
  accountHubMissingSuffix: 'Operator must provide an HTTPS Hub for this Desktop build. Local data remains readable and exportable.',
  accountGoogleFallback: 'Google Account',
  accountVerified: 'Google verified',
  accountReverify: 'Session needs re-verification',
  accountIntro: 'Continuing will open the system browser. The Hub receives your basic Google profile and device hash for account management; device trials remain device-bound.',
  accountSyncLabel: 'Allow sending provider names, connection status, and GitHub username (when available) to admin. AI credentials remain managed by keychain/CLI.',
  accountContinueGoogle: 'Continue with Google',
  accountCancelLogin: 'Cancel login',
  accountClaimTitle: 'Link local data to this account',
  accountClaimDesc: 'The app will back up SQLite before recording ownership. Current repos and worktrees are kept. A profile linked to one account rejects other accounts; per-user profile switching is being finalized.',
  accountClaimCheck: 'I confirm this Google account is authorized to own data in the current profile.',
  accountClaimBtn: 'Back up and link profile',
  accountSyncBtn: 'Sync connections to admin',
  accountLogoutConfirm: 'Log out of AgentForge and revoke Tech Lead/remote access for this profile? Complete or cancel running tasks first.',
  accountLogoutBtn: 'Log out of AgentForge',
  accountIdentityNotReady: 'Identity requirements not met for scheduling AI runs. Local read/export remains available.',
  accountIdentityReady: 'Identity is ready. License and agent credentials/quota are checked separately.',

  // Connection Wizard
  connEyebrow: 'CONNECTION SETUP',
  connTitle: 'Accounts and local AI',
  connSubtitle: 'Choose a service to open the official browser login. CLI must be installed first.',
  connStep1Title: '1 · Google / AgentForge',
  connStep1Desc: 'Requires HTTPS Hub and OAuth client for Google sign-in. License is configured separately; CLI logins below do not verify your AgentForge account.',
  connStep2Title: '2 · AI & GitHub connections',
  connStep2Desc: 'Codex uses the OS keychain. Copilot and GitHub CLI may fall back to a private file if the credential store fails; AgentForge does not collect these tokens.',
  connStorageConsent: 'I agree to Copilot/GitHub CLI credential storage, including file fallback if the keychain fails.',
  connKeychainUnavailable: 'OS credential store is unavailable; automatic sign-in is blocked.',
  connLoginCodex: 'Sign in Codex',
  connLoginCopilot: 'Sign in Copilot',
  connLoginGithub: 'Sign in GitHub',
  connHint: 'Already signed in? Use Check token in Agents. Checking Copilot sends one request and only runs on confirmation. Antigravity: sign in via the official app/CLI.',
  connOneTimeCode: 'One-time code:',
  connCancelLogin: 'Cancel login',
  connStep3Title: '3 · Verify before running',
  connStep3Desc: 'Signing in does not guarantee model/quota or task permissions. Keep default worker count at 1 on a new machine.',
} as const

export type MessageKey = keyof typeof en

const vi: Record<MessageKey, string> = {
  langLabel: 'Ngôn ngữ',
  langAuto: 'Tự động',
  langEn: 'English',
  langVi: 'Tiếng Việt',

  brandTitle: 'DevAgent Orchestrator',
  brandSubtitle: 'Điều phối đa agent',
  workspaceLabel: 'KHÔNG GIAN LÀM VIỆC',
  workspaceTitle: 'Dự án đã đăng ký',
  engineConnected: 'Engine local đã kết nối',
  engineConnecting: 'Đang kết nối API…',
  versionNote: 'v0.1.0 · chỉ localhost',
  breadcrumbRoot: 'Kỹ thuật AI',
  localBadge: 'LOCAL',
  refreshTitle: 'Làm mới',

  navOverview: 'Tổng quan',
  navTasks: 'Tác vụ',
  navAgents: 'Agents',
  navContracts: 'Hợp đồng',
  navHistory: 'Lịch sử',
  navStorage: 'Lưu trữ',
  navFleet: 'Fleet',
  navRemote: 'Từ xa',

  controlPlaneEyebrow: 'BẢNG ĐIỀU KHIỂN KỸ THUẬT',
  overviewTitle: 'Không gian làm việc AI',
  overviewSubtitle: 'Điều phối các agent độc lập trên các dự án Git đã đăng ký.',
  newTaskBtn: 'Tạo tác vụ',
  proposeContractBtn: 'Đề xuất hợp đồng',

  firstRunEyebrow: 'KHỞI CHẠY LẦN ĐẦU · BƯỚC 1 / 3',
  connectRepoTitle: 'Kết nối kho mã nguồn (repository)',
  connectRepoDesc: 'Chọn thư mục Git trên máy, kiểm tra nhánh gốc và cấu hình kiểm thử, sau đó thêm Agent trước khi giao tác vụ đầu tiên.',
  stepRepo: '1 · Kho mã nguồn',
  stepValidation: '2 · Kiểm thử',
  stepAgent: '3 · Agent',
  chooseRepoBtn: 'Chọn repository',
  detectAgentsBtn: 'Quét Agent CLIs',

  statActiveTitle: 'Tác vụ đang chạy',
  statActiveNote: 'Đang thực thi trong worktree riêng',
  statReviewTitle: 'Chờ bạn duyệt',
  statReviewNote: 'Đã có diff và kết quả kiểm thử',
  statHandoffTitle: 'Bàn giao IDE',
  statHandoffNote: 'Đang chờ agent chạy thủ công',
  statAttentionTitle: 'Cần chú ý',
  statAttentionNote: 'Tác vụ bị chặn hoặc lỗi',
  recentTasksTitle: 'Tác vụ gần đây',
  recentTasksSubtitle: 'Trạng thái trực tiếp trên các repository đã đăng ký',
  viewAllTasks: 'Xem tất cả tác vụ',
  agentRegistryTitle: 'Danh sách Agent',
  agentRegistrySubtitle: 'Khớp năng lực · không cố định vai trò',
  manageBtn: 'Quản lý →',
  registeredReposTitle: 'Repository đã đăng ký',
  registeredReposSubtitle: 'Thư mục gốc worktree độc lập',
  addRepoBtn: '＋ Thêm repository',

  registeredAgentsTitle: 'Agent đã đăng ký',
  registeredAgentsDesc: 'Kiểm tra trạng thái xác thực và gỡ hoặc khôi phục Agent khỏi lịch phân công.',
  refreshDetectionBtn: 'Quét lại CLI',
  registerAdapterBtn: '＋ Đăng ký adapter',

  licenseTitle: 'License & dùng thử',
  licenseDesc: 'Quyền chạy AI được kiểm soát bằng chứng chỉ ký theo thiết bị. Dữ liệu dự án trên máy vẫn đọc và xuất được.',
  licenseAiExec: 'Thực thi AI',
  licenseEnabled: 'đang bật',
  licensePaused: 'tạm dừng',
  licenseDaysLeft: 'Số ngày còn lại',
  licensePlan: 'Gói',
  licenseTrialPending: 'Bản dùng thử 14 ngày theo thiết bị bắt đầu khi License Hub cấp chứng chỉ đầu tiên.',
  licenseHubRequired: 'Bắt buộc phải có License Hub nhưng chưa được cấu hình. Đã tạm dừng điều phối Agent.',
  licenseConsentText: 'Để bắt đầu dùng thử 14 ngày hoặc kích hoạt license, ứng dụng gửi mã băm định danh thiết bị một chiều lên License Hub. Không gửi mã nguồn, prompt, file dự án, email hay thông tin hệ điều hành. Bản dùng thử gắn với thiết bị này.',
  licenseConsentStarting: 'Đang khởi tạo…',
  licenseConsentBtn: 'Đồng ý và bắt đầu dùng thử',
  licenseKeyLabel: 'License key',
  licenseKeyPlaceholder: 'Nhập mã kích hoạt',
  licenseWorking: 'Đang xử lý…',
  licenseActivateBtn: 'Kích hoạt license',
  licenseChecking: 'Đang kiểm tra…',
  licenseRefreshBtn: 'Làm mới trạng thái',
  licenseReleaseLabel: 'Gỡ khỏi thiết bị này',
  licenseReleasePlaceholder: 'Nhập lại license key',
  licenseReleasing: 'Đang gỡ…',
  licenseDeactivateBtn: 'Hủy kích hoạt trên máy này',

  accountEyebrow: 'TÀI KHOẢN · GOOGLE',
  accountTitle: 'Tài khoản AgentForge',
  accountSubtitle: 'Xác minh Google trước khi kích hoạt AI. Tài khoản AI và quyền truy cập GitHub được kết nối riêng.',
  accountLoading: 'Đang kiểm tra phiên đăng nhập…',
  accountHubMissingTitle: 'Chưa cấu hình Hub đăng nhập',
  accountHubMissingSuffix: 'Người vận hành cần cung cấp Hub HTTPS cho bản Desktop này. Dữ liệu local vẫn có thể đọc và export.',
  accountGoogleFallback: 'Tài khoản Google',
  accountVerified: 'Google đã xác minh',
  accountReverify: 'Cần xác minh lại phiên',
  accountIntro: 'Tiếp tục sẽ mở trình duyệt hệ thống. Hub nhận hồ sơ Google cơ bản và device hash để quản lý tài khoản; trial vẫn theo thiết bị.',
  accountSyncLabel: 'Cho phép gửi tên nhà cung cấp, trạng thái kết nối và tên tài khoản GitHub (khi có) cho admin. Credential AI tiếp tục do keychain/CLI quản lý.',
  accountContinueGoogle: 'Tiếp tục với Google',
  accountCancelLogin: 'Hủy đăng nhập',
  accountClaimTitle: 'Gắn dữ liệu local với tài khoản này',
  accountClaimDesc: 'App sẽ sao lưu SQLite trước khi ghi chủ sở hữu. Repo/worktree hiện tại được giữ. Profile đã gắn với một tài khoản sẽ từ chối tài khoản khác; chuyển profile theo user đang được hoàn thiện.',
  accountClaimCheck: 'Tôi xác nhận tài khoản Google này được quyền nhận dữ liệu trong profile hiện tại.',
  accountClaimBtn: 'Sao lưu và gắn profile',
  accountSyncBtn: 'Đồng bộ kết nối vào admin',
  accountLogoutConfirm: 'Đăng xuất AgentForge và thu hồi quyền Tech Lead/remote của profile? Hoàn tất hoặc hủy task đang chạy trước.',
  accountLogoutBtn: 'Đăng xuất AgentForge',
  accountIdentityNotReady: 'Chưa đủ điều kiện danh tính để xếp lịch chạy AI. Local read/export vẫn khả dụng.',
  accountIdentityReady: 'Danh tính đã sẵn sàng. License và credential/quota của agent được kiểm tra riêng.',

  connEyebrow: 'THIẾT LẬP KẾT NỐI',
  connTitle: 'Tài khoản và AI trên máy',
  connSubtitle: 'Chọn dịch vụ để tự mở trình duyệt đăng nhập chính thức. Cần cài CLI trước.',
  connStep1Title: '1 · Google / AgentForge',
  connStep1Desc: 'Cần Hub HTTPS và OAuth client để đăng nhập Google. License được cấu hình riêng; đăng nhập CLI bên dưới chưa xác minh tài khoản AgentForge.',
  connStep2Title: '2 · Kết nối AI và GitHub',
  connStep2Desc: 'Codex dùng OS keychain. Copilot và GitHub CLI có thể lưu token vào file riêng khi credential store lỗi; AgentForge không thu thập token này.',
  connStorageConsent: 'Tôi đồng ý cơ chế lưu credential của Copilot/GitHub CLI, gồm fallback vào file nếu keychain lỗi.',
  connKeychainUnavailable: 'OS credential store không khả dụng; đã chặn đăng nhập tự động.',
  connLoginCodex: 'Đăng nhập Codex',
  connLoginCopilot: 'Đăng nhập Copilot',
  connLoginGithub: 'Đăng nhập GitHub',
  connHint: 'Đã có phiên hợp lệ: dùng Kiểm tra token ở Agents. Kiểm tra Copilot gửi một request, chỉ chạy khi bạn xác nhận. Antigravity: đăng nhập trong ứng dụng/CLI chính thức.',
  connOneTimeCode: 'Mã một lần:',
  connCancelLogin: 'Hủy đăng nhập',
  connStep3Title: '3 · Xác minh trước khi chạy',
  connStep3Desc: 'Đăng nhập thành công chưa chứng minh model/quota hay quyền chạy task. Giữ số worker mặc định 1 trên máy mới.',
}

const dictionaries: Record<Lang, Record<MessageKey, string>> = { en, vi }

export function detectSystemLang(): Lang {
  if (typeof navigator === 'undefined') return 'en'
  const candidates = navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const item of candidates) {
    if (item && item.toLowerCase().startsWith('vi')) return 'vi'
  }
  return 'en'
}

function readStoredMode(): LangMode {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === 'en' || raw === 'vi' || raw === 'auto') return raw
  } catch { /* ignore storage errors */ }
  return 'auto'
}

type I18nValue = {
  mode: LangMode
  lang: Lang
  setMode: (mode: LangMode) => void
  t: (key: MessageKey) => string
}

const I18nContext = createContext<I18nValue | null>(null)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<LangMode>(() => readStoredMode())
  const lang: Lang = mode === 'auto' ? detectSystemLang() : mode

  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  const setMode = (next: LangMode) => {
    setModeState(next)
    try { window.localStorage.setItem(STORAGE_KEY, next) } catch { /* ignore storage errors */ }
  }

  const value = useMemo<I18nValue>(() => ({
    mode,
    lang,
    setMode,
    t: (key: MessageKey) => dictionaries[lang][key] ?? dictionaries.en[key],
  }), [mode, lang])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useI18n must be used inside LanguageProvider')
  return ctx
}
