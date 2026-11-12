# Tiến độ triển khai kết nối tài khoản và AI

Cập nhật: 12/11/2026. Nhánh `codex/desktop-upgrade`.
Yêu cầu đã duyệt: [proposal G1–G5](DESKTOP_ACCOUNT_AGENT_LINKING_PROPOSAL.md).

## Kết quả rà soát

Lõi local, SQLite, worktree, scheduler, validation, license và remote đã có mã
nguồn/tests; bộ cài alpha.1 đã phát hành. Đây chưa phải cổng nghiệm thu production
của toàn bộ Phase 0–8. Hai khoảng trống chính của hành trình mới là danh tính
Google của AgentForge và bằng chứng provider/worker có quyền chạy thực tế.

Không đồng nhất các bằng chứng: tìm thấy CLI, CLI báo đã login, request model
thành công, worker có ranh giới file/tool, và tài khoản AgentForge đã xác minh.

## Các mốc triển khai

| Mốc | Đã triển khai / bằng chứng | Còn lại để đóng mốc |
|---|---|---|
| Admin Docker | Admin web quản trị user/license/device; token trong RAM; khóa/mở user; image amd64/arm64 đã publish và inspect registry | Người vận hành deploy HTTPS; admin profile hiện chưa phải Google identity |
| G1 · Kết nối CLI | Panel Overview lần đầu + Agents; official Codex/Copilot/GitHub browser login, timeout/cancel/cleanup process tree; không lưu raw output; mã GitHub tạm thời; check OS store/env override/capability; discovery không suy ra Copilot/Antigravity đã login | Nghiệm thu browser login thật trên từng OS/CLI, trạng thái quota/policy thực; hoàn thiện scoped-tool/sandbox Copilot. Worker Copilot tạm dừng, đã gỡ `--allow-all-tools` |
| G2 · Google broker | Phương án đã duyệt; nguồn chính thức Google OIDC và ASP.NET OIDC đã đối chiếu | Viết broker + transaction/PKCE + identity/session/rotation/invite; validate bằng fake issuer rồi Google HTTPS thật. Chưa có server HTTPS/OAuth client cấu hình |
| G3 · Account Desktop | Đã có OS keychain infrastructure và profile activation/backup nền | Loopback handoff, keychain phiên AgentForge, UI Account, profile theo user, nhận dữ liệu legacy có backup, license-owner binding, login gate khi dispatch |
| G4 · E2E | Test fixture local và browser UI đã chạy | Máy sạch: Google → CLI → task → validation → diff → complete/restart/offline/revoke, Windows + macOS; không mở prompt trả phí ngầm |
| G5 · Phát hành mới | Alpha.1 cũ đã có 4 installer + checksums; Docker admin.1 đã có | Sau G2–G4 pass: image có broker + Desktop alpha mới pin Hub/public key; ký số/notarization cần chứng thư riêng |

## Bằng chứng kiểm định đợt này

- `.venv/bin/python -m pytest backend/tests -q`: **131 passed, 1 skipped**,
  22.39 giây, 1 deprecation warning. Suite chạy tuần tự, `nice -n 10`.
- Test mới tập trung vào allowlist login, OS store, consent fallback, env override
  không lộ secret, mã một lần, không giữ raw output, timeout/cancel và auth API.
- Sau chỉnh cleanup/cache headers: test module login **8 passed**, 0.30 giây.
- `npm run build`: pass. `npm run lint`: exit 0, không có error, 7 React warnings
  ở App.tsx hiện hữu.
- Browser smoke trên production frontend với API giả: pass first-run wizard,
  consent, trạng thái login, hủy/xóa mã, không tự login lúc mở app, không dùng
  local/session storage. Đây không phải nghiệm thu OAuth thật.
- Registry inspect xác nhận image `jtuongdev/agentforge-admin:0.1.0-admin.1`
  có `linux/amd64` + `linux/arm64`; digest
  `sha256:55c681c07c43d384b57bcd043f31c7b5a97a4725c1c51ffa780eee0c04c34782`.
  Chi tiết chạy container: [deploy guide](../deploy/license-hub/README.md).

## Thứ tự công việc tiếp theo

1. Broker Google với OIDC middleware được duy trì, scope `openid email profile`;
   state/nonce/audience/issuer/expiry/email_verified, PKCE và mã một lần TTL ngắn.
2. Identity theo provider/sub, admin invite/approval cho hồ sơ cũ; session hash,
   refresh rotation/revoke, disabled-user checks. Không tự merge chỉ theo email.
3. Desktop loopback handoff + keychain + profile isolation và license binding;
   trial thiết bị 14 ngày giữ nguyên, local read/export vẫn dùng được.
4. Copilot scoped tool/file policy, chặn push/merge và kiểm thử vượt quyền trước
   khi trả worker sang Ready. Không dựa vào setting sandbox toàn cục trên máy dev.
5. E2E staging, sau đó phát hành Docker/Desktop mới và cập nhật link/checksum.

Cần thông tin bên ngoài cho nghiệm thu Google thật: URL Hub HTTPS hoạt động,
OAuth Web Client cấu hình callback `/auth/google/callback`, consent/test users,
Client ID/Secret lưu riêng trên server và public key license. Docker Hub chỉ
lưu image; URL Docker Hub không phải URL backend callback.

## Hướng dẫn đã đọc

README, PROJECT_CONVENTIONS, product spec, upgrade plan, functionality audit,
proposal đã duyệt và OpenAI Docs skill. Không tìm thấy AGENTS.md,
CONTRIBUTING.md hay hướng dẫn kiến trúc/UI riêng trong checkout này. Thay đổi
tuân argv subprocess, timeout/process-tree cleanup, API local bearer và synthetic
tests; không sửa dữ liệu project của người dùng hay đưa server secret vào image.
