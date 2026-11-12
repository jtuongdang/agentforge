# Thống kê chức năng và mức đáp ứng thực tế

Ngày kiểm tra: **12/11/2026**. Mã nguồn đối chiếu: `5bc01db` trên nhánh
`codex/desktop-upgrade`. Bản cài đang phát hành:
[v0.1.0-alpha.1](https://github.com/jtuongdang/agentforge/releases/tag/v0.1.0-alpha.1).

Đối chiếu [product spec](DESKTOP_PRODUCT_SPEC.md), [upgrade plan](DESKTOP_UPGRADE_PLAN.md),
README, project conventions, mã nguồn và kiểm thử. Báo cáo này mô tả hiện trạng;
các đoạn khảo sát/tiến độ cũ trong upgrade plan được giữ làm lịch sử.

## 1. Kết quả thống kê

| Phân loại | Số nhóm | Ý nghĩa |
|---|---:|---|
| A — Đã triển khai và có kiểm chứng trong phạm vi alpha | 16 | Có mã nguồn và bằng chứng kiểm thử/đóng gói cho phạm vi được ghi rõ bên dưới |
| B — Đã triển khai, còn giới hạn hoặc thiếu nghiệm thu thực tế | 9 | Chưa đủ căn cứ tuyên bố đáp ứng toàn bộ yêu cầu của spec hoặc trải nghiệm production |
| C — Chưa triển khai/vận hành | 4 | Chưa có cơ chế tương ứng hoặc chưa được cấu hình để phát hành |
| D — Yêu cầu đã thay đổi theo quyết định owner | 1 | Không tính là backlog phải làm theo mô hình cũ |
| **Tổng** | **30** | Các nhóm chức năng dùng để kiểm kê, không phải 30 mục nguyên gốc của spec |

Không dùng tỷ lệ 16/30 làm phần trăm hoàn thành sản phẩm: mỗi nhóm khác nhau về
khối lượng, mức kiểm chứng và phụ thuộc bên ngoài. Build pass cũng không chứng
minh toàn bộ giao diện đã được nghiệm thu trên máy sạch.

## 2. Nhóm A — Đã triển khai và có bằng chứng alpha

| ID | Chức năng | Có thể đáp ứng | Bằng chứng chính |
|---|---|---|---|
| A01 | Đăng ký repository và preflight | Chọn Git checkout; kiểm tra branch/base, repo dirty, Git identity, tool/validator, disk; không tự gắn một dự án mặc định | `main.py`: setup/repository routes; tests preflight và bootstrap |
| A02 | Cấu hình và dữ liệu local | App data riêng theo OS; SQLite có schema version, snapshot trước migration; không ghi dữ liệu người dùng vào app bundle | `settings.py`, `db.py`; migration/snapshot tests |
| A03 | Khai báo task có ranh giới | Title, objective, acceptance criteria, priority, file globs, capabilities, dependencies, contract, agent ưu tiên | `models.py`, `planner.py`; task/contract tests |
| A04 | Worktree và revision riêng | Branch/worktree riêng cho task; pin base SHA; không sao chép thay đổi chưa commit từ source checkout | `git_ops.py`, `worktrees.py`; Git fixture tests |
| A05 | Điều phối task | Ưu tiên task; capability matching; dependency phải hoàn tất và commit đã vào base; file scopes xung đột phải chờ | `scheduler.py`; priority, dependency, scope và atomic claim tests |
| A06 | Fleet và giới hạn tải cấu hình | Giới hạn run, build, agent/credential; hot apply; giảm capacity bằng drain; disk reserve trước admission | `fleet.py`, `scheduler.py`; concurrency/budget/persist/drain tests. Đây chưa phải tự điều tiết theo nhiệt CPU |
| A07 | Validation theo dự án | Maven auto; Node/Python/Go/custom qua argv được cấu hình; thiếu validator báo blocked; custom check không bị bỏ qua vì file docs | `validation.py`; docs/custom/unsupported validator tests. Không tự cài dependency hoặc tự suy ra mọi lệnh build |
| A08 | Chặn vượt scope và secret | Kiểm tra thay đổi toàn task; chặn file ngoài scope, một số secret patterns, symlink/path escape ở worker provider; redact output | `validation.py`, `provider_worker.py`; scope/secret/redaction tests. Scanner dựa trên pattern, không chứng minh phát hiện mọi PII/secret |
| A09 | Diff và kiểm tra revision | Có changed paths/diff theo base; phân trang; từ chối review/complete khi HEAD hoặc worktree thay đổi sau validation | `git_ops.py`, `main.py`; multi-commit, diff paging và stale revision tests |
| A10 | Hoàn tất cục bộ | Task đã validate có thể hoàn tất và giữ commit/branch local, không cần remote GitHub | `main.py`: complete-local; local completion và stale worktree test |
| A11 | Theo dõi và lịch sử | Task state, run report, events/log, activity/diff tabs; WebSocket reconnect theo cursor và khử duplicate | `reporting.py`, `main.py`, `App.tsx`; cursor/reconnect tests |
| A12 | Xác thực API local | Bearer token cho API; bootstrap session giới hạn origin/loopback; WebSocket ticket ngắn hạn, dùng một lần | `main.py`; unauthorized API và WebSocket tests |
| A13 | Nạp quy ước dự án | Đưa baseline conventions và AGENTS/guidance phù hợp vào task theo worktree/revision; ghi danh sách nguồn | `project_guidance.py`, `engine.py`; guidance/RAG injection tests |
| A14 | Backup và portability | Export `.devagent`: SQLite + Git bundles, opt-in uncommitted files sau scan; checksum/integrity/path checks; restore sang profile mới, remap và reconcile | `portability.py`; export/restore/redaction/race/filter tests; packaged checkpoint smoke. Chuyển profile qua native UI vẫn thuộc gate B24 |
| A15 | Storage và cleanup an toàn | Thống kê SQLite/worktree/disk; cleanup preview mặc định; chỉ xóa worktree sạch đã tích hợp; giữ dirty/untracked/unmerged work | `storage.py`, `worktrees.py`; cleanup/data preservation tests |
| A16 | Đóng gói và phân phối alpha | App Tauri + sidecar Nuitka; cổng local động; DMG ARM64/Intel, EXE/MSI Windows x64; manifest/checksum và GitHub prerelease | 3 target build + packaged smoke pass; 2 DMG image verify pass; 7 release assets khớp digest GitHub. Chưa ký số/notarize |

Mã nguồn liên quan: [backend/app](../backend/app), [dashboard](../dashboard/src/App.tsx),
[backend regression tests](../backend/tests/test_core.py),
[packaged smoke](../scripts/smoke_desktop_backend.py).

## 3. Nhóm B — Đã có, còn giới hạn hoặc nghiệm thu

| ID | Chức năng | Đã làm và bằng chứng | Giới hạn còn lại |
|---|---|---|---|
| B17 | Nhận diện CLI/session và kiểm tra credential | Adapter Codex/Copilot/Antigravity; phát hiện executable; kiểm tra login/usage theo khả năng CLI; disable/restore agent giữ lịch sử | Chưa có OAuth/Device Flow tích hợp cho mọi nhà cung cấp; không biết quota chung của mọi CLI. Copilot token check gửi một prompt ngắn và tiêu một request; IDE-only là handoff |
| B18 | BYOK và coding worker | OpenAI, Anthropic, Gemini, Ollama; OS keychain; model catalog; cloud consent; worker có list/read/write trong scope và giới hạn runtime/request | Fixture protocol/tool tests pass; chưa nghiệm thu coding task bằng credential/model thật cho từng provider. Không có shell/network tùy ý cho provider worker; Ollama chỉ loopback |
| B19 | External Tech Lead | One-time pairing, scoped session, heartbeat/lease, handover, tạo task, review audit, revoke; bridge CLI có integration/fixture smoke | ChatGPT/Claude Desktop cần công cụ local/bridge thực sự. Dán prompt hoặc gọi `/health` không tự tạo kết nối; chưa nghiệm thu native app-to-app workflow đầy đủ |
| B20 | Rescue, retry và phục hồi restart | Phân loại lỗi, bounded backoff/Retry-After, credential cooldown, chuyển lease sau khi dừng cây process; POSIX guardian/Windows Job Object; checkpoint review & requeue | Automated/fixture và packaged containment đã có bằng chứng; chưa nghiệm thu mọi failure mode của CLI/provider thật và full app crash/relaunch bằng GUI. Rescue dùng agent đã cấu hình, không tự tạo account/tab credential mới; không tự resume sau restart |
| B21 | Local Code Context Engine | Python AST + Markdown FTS; index theo repo/SHA/worktree, incremental/hash/rename/delete; citations và context budget; fallback guidance | Đạt phạm vi Python/Markdown đã chọn; chưa có parser Java/TS/Go, vector embeddings hay call graph đa ngôn ngữ như tầm nhìn rộng của spec. Worktree dirty không được index thành context của SHA sạch |
| B22 | Trial/license | Trial thiết bị 14 ngày; consent/device hash; RSA-PSS verification, cache, grace/clock rollback; activate/deactivate/device limit/revoke; local read/export giữ được; .NET/Mongo Hub + Docker | Unit/local integration pass; Atlas isolated integration đã pass. Chưa deploy HTTPS Hub và chưa pin URL/public key vào bản Desktop phát hành. `.env` của Hub không tự cấu hình license cho app; bản alpha không chứng minh enforcement thương mại trên staging HTTPS |
| B23 | Remote browser review | Invite/QR một lần, owner approve, device session hash/expiry/revoke; summary allowlist; full diff grant pin task/SHA; riêng gateway/REST/WS; assets có trong sidecar | API và bundle tests pass; chưa cấu hình VPN/domain/certificate/ingress thực tế, chưa thử hai thiết bị. Remote mặc định chỉ đọc, không tạo task/review/publish/merge; không phải điều khiển máy từ xa đầy đủ |
| B24 | Native GUI và vòng đời cài đặt | Tauri shell, native folder/file/save picker, single instance, app data, profile activation, sidecar startup/shutdown; bộ cài đã có | Chưa chạy đầy đủ trên máy sạch: chọn folder có dấu/khoảng trắng qua picker → task → worker → validation → full diff → complete local → reopen/restore. Test filesystem Unicode đã pass, nhưng chưa thay thế nghiệm thu picker/UI |
| B25 | Push và Draft PR có duyệt | Owner confirmation, validation/scope/revision gate; GitHub remote check; atomic state claim; helper push và tạo Draft PR | Có code và một số invariant được kiểm thử; lần rà soát này không tạo PR thật qua ứng dụng. Cần smoke trên repo thử nghiệm được chỉ định, bao gồm push/PR lỗi và retry. GitHub Release do agent xuất bản không phải bằng chứng luồng Draft PR trong app đã được nghiệm thu |

Nguồn chính: [registry.py](../backend/app/registry.py),
[provider_worker.py](../backend/app/provider_worker.py),
[tech_lead.py](../backend/app/tech_lead.py), [engine.py](../backend/app/engine.py),
[code_context.py](../backend/app/code_context.py), [license.py](../backend/app/license.py),
[License Hub](../license-hub/README.md), [remote_pairing.py](../backend/app/remote_pairing.py),
[Tauri shell](../dashboard/src-tauri/src/lib.rs), [main.py](../backend/app/main.py).

## 4. Nhóm C — Chưa đáp ứng

| ID | Yêu cầu | Hiện trạng |
|---|---|---|
| C26 | Built-in AI Tech Lead/autopilot hoàn toàn | Planner hiện tạo execution charter từ task đã khai báo; chưa có brain tích hợp tự phân rã yêu cầu lớn, tự quyết định toàn bộ backlog và điều khiển cả vòng đời không cần external lead/operator |
| C27 | Auto-merge và tích hợp branch tự động | Có complete local và Draft PR; chưa có merge engine xử lý branch protection, CI, rebase/conflict, serialize target và audit merge |
| C28 | Phát hành thương mại có ký số + auto update | Chưa có publisher certificate/notarization được nghiệm thu, chưa tích hợp updater/feed/signing để update tự động; rollback app/data giữa phiên bản chưa nghiệm thu |
| C29 | Tự điều tiết theo CPU/RAM/nhiệt máy | Hiện dùng slot/build/disk budgets cấu hình; chưa có sensor nhiệt/CPU/RAM để tự giảm tải. Không có căn cứ bảo đảm “luôn mát” hay thời gian hoàn thành 15–20 phút như ví dụ trong spec |

Biên dịch Nuitka và Rust đã có trong đóng gói. Điều đó không chứng minh mã nguồn
“không thể dịch ngược”; không có cơ chế chống sao chép tuyệt đối hay cloud brain
độc quyền đang vận hành để ghi nhận là đã đáp ứng.

## 5. Nhóm D — Quyết định đã thay đổi

**D30 — Google account/login cho trial:** owner đã chọn trial **14 ngày gắn thiết bị**,
không dùng Google login. Vì vậy thiếu Google login không phải lỗi so với quyết định
triển khai hiện hành. Đây vẫn là khác biệt so với §18.2 của spec ban đầu.

## 6. Kết quả kiểm tra và phạm vi bằng chứng

| Kiểm tra | Kết quả | Phạm vi |
|---|---|---|
| Backend pytest chạy lại ngày 12/11 | **122 passed, 1 skipped**, 22.39 s; 1 Starlette deprecation warning | Một test Windows-only bị skip trên macOS; local Mongo fixture, không dùng URI Atlas cho lượt toàn suite này |
| License Hub .NET test chạy lại | **4 passed**, 0 failed | Build và unit tests; một MSBuild worker |
| Dashboard production build chạy lại | **Pass** | TypeScript/Vite; không phải GUI E2E |
| Dashboard lint chạy lại | **Exit 0**, 7 React effect warnings | Chưa coi codebase là không còn cảnh báo |
| Atlas isolated integration trong lượt trước | **1 passed**, 4.20 s | Trial/activation race/transfer/revoke/privacy/signatures, database tổng hợp riêng; không lặp lại kết nối Atlas trong lượt audit này |
| macOS Intel package | **Pass** | [Job 131031659954](https://github.com/jtuongdang/agentforge/actions/runs/43172793380/job/131031659954); run tổng thể có Windows thiếu runner, riêng job Intel pass |
| macOS ARM64 package | **Pass** | [Run 43179037298](https://github.com/jtuongdang/agentforge/actions/runs/43179037298) |
| Windows x64 package | **Pass** | [Run 43179272029](https://github.com/jtuongdang/agentforge/actions/runs/43179272029) |
| Release assets | **7/7 hash và dung lượng khớp** | 4 installer + manifest + checksum + guide; cả hai DMG pass image verification |

Test được chạy tuần tự với ưu tiên CPU thấp; không build lại native packages,
không gọi model/provider thật, không tiêu quota AI và không test dữ liệu production.
Các source/fixture/API tests không được dùng để tuyên bố đã nghiệm thu toàn bộ
native GUI hoặc hành vi của mọi phiên bản CLI/provider bên ngoài.

## 7. Có thể dùng để làm gì hiện nay?

1. **Thử nghiệm điều phối coding local:** đăng ký repo, cấu hình CLI/model và
   validator, tạo task có scope, chạy worker, xem validation/diff, complete local.
   Các thành phần backend có kiểm chứng; nên chạy demo fixture bằng bộ cài trước
   khi áp dụng vào dự án thật.
2. **Quản lý nhiều worker có kiểm soát:** giới hạn slot/build, chặn scope xung đột,
   áp dependency/contract và theo dõi lịch sử. Số slot phải do người vận hành chọn
   phù hợp máy; app chưa có bộ điều nhiệt CPU tự động.
3. **Bảo toàn và di chuyển công việc:** export/restore checkpoint, giữ worktree
   bị gián đoạn, review rồi requeue khi an toàn; cấu hình lại credential sau restore.
4. **Tích hợp external lead qua bridge/API:** khả dụng cho client có công cụ local
   và thực hiện đúng pairing/heartbeat/scopes; chưa phải trải nghiệm một click
   chung cho mọi ứng dụng chat.
5. **Thử nghiệm license và remote trong staging:** có nền tảng client/server,
   nhưng phải hoàn thiện HTTPS, public-key pin, VPN và nghiệm thu thiết bị thật
   trước khi coi là dịch vụ cho khách hàng.

Mốc tiếp theo có giá trị nhất là nghiệm thu bộ cài trên máy sạch và một repository
fixture trọn vòng đời. Sau đó mới đóng các gate provider thật, Draft PR thật,
HTTPS license/remote và phát hành ký số; không cần coi các mục này đã đạt chỉ vì
installer được tạo thành công.
