# Phân tích Desktop Product Spec và kế hoạch nâng cấp

Ngày khảo sát ban đầu: **11/11/2026**. Ngày rà soát bổ sung: **11/11/2026**. Nguồn yêu cầu: `docs/DESKTOP_PRODUCT_SPEC.md` (19 mục, 1.497 dòng).

Trạng thái: **đang triển khai theo phase** trên nhánh `codex/desktop-upgrade`, đồng bộ với repository `jtuongdang/agentforge`. Phần khảo sát ban đầu dưới đây được giữ làm lịch sử; tiến độ thực thi cập nhật ở mục Phase 0. Tài liệu này phân biệt yêu cầu trong spec, bằng chứng từ mã nguồn và quyết định kiến trúc đề xuất. Mã nguồn được dẫn theo phiên bản đang có trong workspace; số dòng có thể thay đổi sau refactor.

### Kết nối tài khoản — cập nhật 12/11/2026

Owner đã duyệt mở rộng G1–G5 trong [proposal](DESKTOP_ACCOUNT_AGENT_LINKING_PROPOSAL.md).
Tiến độ mới, cổng nghiệm thu và thứ tự tiếp theo nằm ở
[ACCOUNT_LINKING_PROGRESS.md](ACCOUNT_LINKING_PROGRESS.md). Docker admin đã publish;
source Desktop có browser login CLI. Google broker/session/profile chưa hoàn tất,
Copilot worker đang tạm dừng để nghiệm thu quyền; không coi toàn bộ Phase 0–8 là
đã hoàn thành production chỉ dựa trên bộ cài alpha.

### Đóng gói nội bộ — cập nhật 12/11/2026

Mức đáp ứng chức năng hiện hành và kiểm thử chạy lại được tổng hợp trong
[FUNCTIONALITY_AUDIT.md](FUNCTIONALITY_AUDIT.md): 16 nhóm đã kiểm chứng trong
phạm vi alpha, 9 nhóm có giới hạn/nghiệm thu còn mở, 4 nhóm chưa đáp ứng và
1 yêu cầu đã đổi theo quyết định owner. Các ghi chú tiến độ cũ bên dưới không
thay thế bảng kiểm kê hiện hành này.

Đã công bố [GitHub prerelease v0.1.0-alpha.1](https://github.com/jtuongdang/agentforge/releases/tag/v0.1.0-alpha.1), tag tại `f3137b0`: 2 DMG, EXE, MSI, hướng dẫn, manifest và SHA256SUMS. Hai DMG đều pass kiểm tra image và đúng kiến trúc; installer Windows khớp hash CI. Dung lượng và SHA-256 trên GitHub khớp cả 7 asset local trước khi publish. Các commit đóng gói bên dưới có mã runtime giống release commit (đã đối chiếu Git diff); manifest ghi riêng source commit thực tế của mỗi installer. Đây là mốc đóng gói alpha nội bộ, chưa đóng cổng phát hành production của Phase 7A.

| Target | Commit | Trạng thái gần nhất |
|---|---|---|
| macOS Intel | `b69f3c4` | [Job 131031659954](https://github.com/jtuongdang/agentforge/actions/runs/43172793380/job/131031659954) pass build, DMG, packaged smoke và upload. Đã tải DMG về, `hdiutil verify` và SHA-256 pass. |
| macOS ARM64 | `6aa5ad8` | [Run 43179037298](https://github.com/jtuongdang/agentforge/actions/runs/43179037298) pass build, DMG, packaged smoke, manifest/checksum và upload artifact (51.889.570 bytes). |
| Windows x64 | `3b4d6fd` | [Run 43179272029](https://github.com/jtuongdang/agentforge/actions/runs/43179272029) pass build, packaged smoke, manifest/checksum và upload artifact (49.270.099 bytes). |

Workflow xuất `release-manifest.json` + `SHA256SUMS` sau packaged smoke. Bản Intel cũ được tạo hai file này tại máy sau khi download, ghi đúng commit nguồn. EXE/MSI Windows đã tải về và đối chiếu SHA-256 với manifest CI, đều pass. Lượt Windows tạo checksum bằng CRLF nên công cụ kiểm tra trên macOS đọc thừa ký tự cuối tên file; đã chuẩn hóa file checksum local về LF và sửa script xuất LF trên mọi OS. Các target trên chưa được xác nhận publisher signing/notarization; dùng cho alpha nội bộ. Hướng dẫn: [DESKTOP_INSTALL.md](DESKTOP_INSTALL.md). Run Windows trước bị hủy với `runner_id=0`, annotation ghi không cấp được hosted runner; đây không phải lỗi compile.

## 1. Kết luận khảo sát ban đầu (lịch sử)

Hệ thống hiện tại là một **orchestrator local có dashboard web**, đã có scheduler, worktree, contract gate, CLI adapters, validation, bounded recovery, lịch sử và RAG tài liệu. Chưa có Desktop shell, onboarding native, fleet slots, phiên Tech Lead, rescue worker, license hub hay remote pairing.

Nên triển khai theo thứ tự: **ổn định và bảo vệ lõi → cấu hình/onboarding → Desktop alpha → fleet → Tech Lead → rescue → code RAG → thương mại hóa → remote**. Có thể tái sử dụng nhiều module, nhưng không nên coi scheduler, validation và cleanup là thành phần đã hoàn thiện để giữ nguyên.

Mốc đầu tiên đề xuất là **Desktop alpha trên macOS arm64**: mở app, chọn repo, nhận diện CLI, tạo task, theo dõi log, xem đầy đủ diff, hoàn tất local hoặc tạo Draft PR sau duyệt. Windows và macOS x86_64 được kiểm chứng riêng ở mốc phát hành đa nền tảng.

Ước tính “70% giữ nguyên / 20% sửa / 10% thêm” và “~2 ngày” trong spec chưa có cơ sở đo. Phạm vi mới gồm nhiều subsystem và các lỗi lõi cần sửa trước. Không nên dùng các con số đó làm cam kết giao hàng.

## 2. Baseline đã kiểm tra

| Kiểm tra | Kết quả ngày 11/11/2026 | Ý nghĩa |
|---|---|---|
| `.venv/bin/python -m pytest backend/tests -q` (khảo sát ban đầu) | **37 passed, 1 failed**, 1 deprecation warning | Lỗi lịch sử do conventions chưa có; cập nhật lần chạy Phase 0 xem bên dưới |
| Test thất bại | `test_project_guidance_loads_root_and_scoped_agent_instructions`, `backend/tests/test_core.py:44` | Thiếu `docs/PROJECT_CONVENTIONS.md` mà code và test yêu cầu |
| `npm run build` tại `dashboard/` | Pass | TypeScript và Vite build được; chưa chứng minh Desktop chạy được |
| `npm run lint` tại `dashboard/` | Exit 0; **6 warnings**, 0 errors | Các cảnh báo về effect/dependency và biến trong async ở `App.tsx` |
| Cấu trúc Desktop | Không có `src-tauri/`, không có component onboarding | Chưa có nền tảng đóng gói native |
| Quản lý mã nguồn workspace (khảo sát ban đầu) | `git status` báo không có Git repository | Đã khởi tạo local baseline và nối remote sau khi owner gửi repo |
| Tài liệu tham chiếu lúc khảo sát đầu | README trỏ tới `PROJECT_CONVENTIONS`, `COMPLETION_ROADMAP`, `IMPLEMENTATION_PLAN`, `ENVIRONMENT_REPORT`, `OPERATIONS` nhưng lúc đó chưa thấy các file | Đã cập nhật lại bên dưới theo workspace hiện tại |

**Cập nhật khi rà soát lại:** `docs/PROJECT_CONVENTIONS.md` và `docs/COMPLETION_ROADMAP.md` đã có; README được sửa để chỉ liên kết tới tài liệu hiện hữu. `COMPLETION_ROADMAP.md` là roadmap hoàn thiện Orchestrator, còn tài liệu này lập kế hoạch cho Desktop; Desktop phụ thuộc vào các cổng chất lượng/bảo mật của roadmap Orchestrator, không thay thế chúng. Workspace ban đầu chưa có `.git`; sau khi owner gửi remote, đã khởi tạo branch `codex/desktop-upgrade`, tạo baseline local và nối remote GitHub rỗng.

**Đánh giá `PROJECT_CONVENTIONS.md`:** phù hợp làm quy ước nền và đã được `project_guidance.py` nạp vào task. Nội dung về synthetic data, secret, file scope, migration, xác minh và báo cáo khớp mục tiêu sản phẩm. Trước khi dùng làm chuẩn cho cả dự án, nên bổ sung quy ước Python/FastAPI, React/TypeScript và SQLite; hiện phần Java/Clean Architecture khá chi tiết nhưng không phản ánh cấu trúc code chính. Cụm “hướng dẫn gần file nhất (`AGENTS.md`) ưu tiên quy ước riêng của repo” cũng nên đổi: đề xuất thứ tự là task/contract → quy ước riêng của repo → `AGENTS.md` áp dụng trực tiếp tại path → quy ước nền, miễn không trái quy tắc an toàn bắt buộc. Đây là hạng mục cập nhật tài liệu trong Phase 0, không phải yêu cầu viết lại toàn bộ conventions.

Giới hạn khảo sát: đọc code, chạy suite hiện có và thử nghiệm fixture local; chưa test bản cài Desktop, Windows, quota thật, license server hay publish GitHub. Không chạy task AI trả phí và không push/merge repo của người dùng trong các thử nghiệm bổ sung.

## 3. Đối chiếu yêu cầu với hiện trạng

“Một phần” nghĩa là có nền móng nhưng chưa đủ hành vi mô tả trong spec.

| Yêu cầu trong spec | Hiện trạng | Bằng chứng / phần còn thiếu | Phase |
|---|---|---|---|
| Bỏ repo mặc định (§5–6) | Đã có | `main.py:35`: bootstrap không đăng ký repo mặc định; đã có test lịch sử | 1: giữ hành vi, thêm onboarding |
| Quy ước Agent (§6) | Đã có, cần hiệu chỉnh stack | `docs/PROJECT_CONVENTIONS.md` được nạp bởi `project_guidance.py`; trọng tâm hiện tại là Java dù lõi là Python/FastAPI và React/TypeScript | 0 |
| Chọn repo và kiểm tra môi trường (§3, §18) | Một phần | Có đăng ký path và kiểm tra Git/base; chưa native picker, chưa preflight theo stack | 1–2 |
| Đa framework (§5–6) | Một phần | `validation.py:33`: Maven auto và custom argv; chưa profile Node/Python/Go riêng | 1 |
| Nhận diện CLI/auth/quota (§2) | Một phần | `registry.py`: discovery, credential probe, quota có chọn lọc; chưa login flow/BYOK | 1, 7 |
| Git author theo user (§6.1) | Chưa | `git_ops.py:192`: còn author `DevAgent Orchestrator` | 1 |
| Worktree + scope + contract + recovery (§5–6) | Một phần | Có module, nhưng có lỗi phạm vi, cleanup và lifecycle bên dưới | 0 |
| Log thời gian thực và diff (§3, §18) | Một phần | Có WebSocket và textual diff; diff chỉ commit cuối, chưa split diff/reconnect hoàn chỉnh | 0, 3 |
| Fleet nhiều slot, hot apply (§12–14) | Một phần | Có `max_parallel`, chưa slot/run identity, API cập nhật fleet và global budget; refresh ghi đè capacity mặc định | 3 |
| External Tech Lead + activation (§7, §11–12, §17) | Chưa | Có REST task API; không có role/session/handshake/lease/handover | 4 |
| DAG trực quan (§5) | Chưa | Có dependencies trong dữ liệu; chưa graph UI | 3 |
| Auto-review / auto-merge (§5, §18) | Chưa | `approve-draft-pr` chỉ push và tạo Draft PR; không merge | 4: policy; 7: integration riêng |
| Rescue khi rate limit (§15) | Chưa | Retry hiện tại cùng agent; chưa phân loại lỗi, checkpoint và chuyển ownership | 5 |
| Disk guard/watchdog/backoff (§16) | Một phần | Có storage, prune, timeout; chưa reserve disk, terminate process tree, retry theo loại lỗi | 0, 3, 5 |
| Backup/restore portable (§16.6) | Một phần | `.devagent` đóng gói SQLite + Git bundle + tùy chọn uncommitted; restore remap repo/worktree và reconcile task/run; RAG rebuild, profile activation đang chờ packaged CI | 1, 7 |
| Hybrid code RAG (§19) | Một phần | `rag.py` chỉ index Markdown bằng FTS5/BM25; DB riêng `rag.sqlite3`; chưa AST/symbol graph | 6 |
| Tauri + backend binary (§4–6) | Một phần | Shell, lifecycle, per-target sidecar và build/smoke matrix đã có; ARM64 runner, DMG packaging ổn định, signing và updater còn thiếu | 2, 7 |
| Google login/trial/license (§8–10, §18.2) | Chưa | Chỉ có thiết kế và code mẫu trong spec | 7 |
| Remote QR (§3) | Chưa | Launcher bind loopback; chưa pairing, transport và quyền remote | 8 |

## 4. Các vấn đề cần xử lý trước khi mở rộng

### 4.1. P0 — Cleanup có thể chọn xóa worktree còn dữ liệu chưa bảo toàn

`worktrees.py:45` coi remote-tracking branch tồn tại là đã push; `:133` coi có `pr_url` là đủ để xóa. Nhánh có thể có commit mới chưa push hoặc file chưa commit. Đường xóa dùng `git worktree remove --force`, sau đó fallback `shutil.rmtree` (`:158`). Chưa kiểm tra dirty state, độ khớp SHA remote, managed-root containment hay ownership với worker đang dùng.

`handoff_ready` không có trong `ACTIVE_STATUSES` của cleanup (`:12`), dù vẫn là công việc người dùng đang làm. `_clean_targets_in_worktree` xóa thư mục tên `target` mà không kiểm tra có source được Git theo dõi bên trong. Scheduler tự gọi prune khi khởi động vòng lặp và sau đó mỗi giờ (`scheduler.py:48`), nên đây không chỉ là rủi ro từ nút bấm.

**Đã tái hiện bằng dry-run:** fixture `draft_pr` cũ có file `uncommitted.txt` vẫn được chọn vào `fully_removed_count=1`. Chỉ chạy dry-run; fixture không bị cleanup xóa.

**Tiêu chí sửa:** chỉ xóa trong vùng quản lý; bảo vệ trạng thái active/handoff/review/rescue; xác minh không có process owner; kiểm tra staged/unstaged/untracked và commit cần giữ; có snapshot hoặc bằng chứng bảo toàn đúng SHA; recheck ngay trước xóa. Không dùng tên thư mục, URL PR hay remote ref tồn tại làm bằng chứng duy nhất. Cleanup build artifacts không được xóa file tracked. Mặc định preview trước, cơ chế tự dọn chỉ bật lại sau khi đủ kiểm chứng.

### 4.2. P0 — Scope và review chưa bao phủ toàn bộ thay đổi của task

`git_ops.py:144` chỉ lấy diff commit khi không còn thay đổi chưa commit. Nếu worker commit `a.py` rồi sửa tiếp `b.md`, danh sách kiểm tra chỉ còn `b.md`. Scope và secret scan có thể bỏ sót `a.py`. `task_diff` (`:160`) dùng `git show <commit>`, chỉ hiển thị patch commit cuối thay vì toàn bộ task.

**Đã tái hiện:** fixture có commit sửa `a.py` và thay đổi chưa commit ở `b.md` trả về `changed_paths == ['b.md']`; khi commit tiếp `b.md`, preview không chứa `a.py`.

**Tiêu chí sửa:** lưu `base_sha` bất biến khi tạo worktree; lấy hợp của thay đổi từ base tới task head và staged/unstaged/untracked, xử lý delete/rename. Review, scope scan, secret scan, validation và publish phải cùng trỏ tới một task revision. Lưu `validated_head_sha`; khi HEAD hoặc nội dung thay đổi sau kiểm định, kết quả review/validation phải hết hiệu lực. Trước publish kiểm tra lại SHA và trạng thái sạch.

### 4.3. P0 — Validation có đường bỏ qua kiểm thử sai

`validation.py:37` bỏ build cho mọi path bắt đầu bằng `docs/`, bất kể đó là code hay cấu hình thực thi. Cơ chế này còn chạy trước custom validator.

**Đã tái hiện:** `paths=['docs/runtime.py']` được trả `passed=True`, `profile='docs_skip'`, không chạy validator.

**Tiêu chí sửa:** docs-only phải là policy tường minh dựa trên loại nội dung và cấu hình repo; không tự bỏ custom check bắt buộc. Áp dụng trên toàn bộ changeset của task, không chỉ file đang dirty. Không dùng “build pass” như bằng chứng mọi acceptance criterion đã đạt.

### 4.4. P0 — Local API chưa xác thực quyền điều khiển

`main.py` có CORS allowlist nhưng không có authentication/authorization cho REST và WebSocket. Các route có thể đăng ký `exec` adapter, tạo task, duyệt publication và cleanup. Confirmation text ở Draft PR là xác nhận thao tác, không xác thực danh tính. Loopback giới hạn kết nối mạng nhưng không phân quyền client local.

**Tiêu chí sửa:** token phiên cho UI/backend; credential riêng có scope cho external lead; kiểm tra Origin/Host phù hợp; bảo vệ WebSocket; revoke/rotate; mặc định không cấp publish/cleanup cho lead. Không đặt token vào URL/query log hoặc prompt được lưu lâu dài. Thiết kế quyền khác nhau cho desktop owner, lead và remote viewer.

### 4.5. P1 — Hủy task chưa bảo đảm dừng tiến trình con

`engine.py:229` tạo subprocess nhưng không có cleanup process trong nhánh cancellation của `_invoke`; timeout gọi `proc.kill()` cho process trực tiếp. Validator cũng có timeout riêng và không có quản lý cây tiến trình thống nhất. Slot có thể được trả trong khi descendant vẫn ghi worktree.

**Tiêu chí sửa:** abstraction quản lý process tree theo OS; graceful stop rồi force stop; chờ xác nhận đã dừng mới trả slot/chuyển worker/cleanup. Test phải có process con thật. Pause/resume chỉ hiển thị khi adapter/OS có semantics đã được kiểm chứng; alpha hỗ trợ cancel, không giả lập pause.

### 4.6. P1 — Dependency “xong” không đồng nghĩa code đã nằm trong base

`scheduler.py:14` coi `awaiting_pr_approval`, `draft_pr`, `completed` là dependency thành công. Nhưng task sau tạo worktree từ base branch, không tự có commit của task trước. Ví dụ B cần class do A tạo sẽ thiếu class dù A đã pass test và chờ duyệt PR. Contract đồng bộ interface không tự tích hợp implementation.

**Tiêu chí sửa:** phân biệt dependency về quyết định/contract với dependency cần code. Loại cần code chỉ chạy khi commit đã được tích hợp vào base được chọn, hoặc trên integration ref có pin SHA và provenance rõ ràng. Không đánh đồng Draft PR, local completion và merged.

### 4.7. P1 — Dữ liệu vận hành và state cần hợp nhất

- `db.py`, worktree, handoff, RAG, static assets đang dựa vào source root; không phù hợp trực tiếp với bundle cài đặt hoặc thư mục giải nén onefile.
- Migration là `ALTER TABLE` cộng dồn chưa có schema version và quy trình backup/restore theo release.
- Một số nhánh `break`/cancellation trong execution có thể kết thúc task mà run chưa được finalize. Cần invariant: mọi run kết thúc đều có trạng thái, exit reason và timestamp.
- Guidance/RAG hiện đọc từ source checkout trước khi chuẩn bị worktree (`engine.py:80`); có thể khác branch và chứa nội dung chưa commit của người dùng. Context phải gắn repo + base SHA/task revision.
- Event `details`, report, diff và các kênh log cần redaction thống nhất; regex hiện tại chưa chứng minh bao phủ mọi provider secret hoặc PII. Log kỹ thuật có retention, khác với audit milestone; README/spec cần mô tả đúng việc prune hiện có.

## 5. Các giả định trong spec cần chỉnh

| Nội dung spec | Điều chỉnh đề xuất |
|---|---|
| “100% On-Premise”, không dòng code nào rời máy | Tách lưu trữ/điều phối local khỏi suy luận. Adapter dùng cloud gửi prompt/context tới provider. Offline chỉ là chế độ riêng khi toàn bộ model và dependency cần thiết có sẵn local. |
| Tất cả phiên CLI tự được Keychain cấp | Dùng CLI auth command chính thức; không tự đọc token của provider. Codex có nhiều chế độ credential store, gồm cả file; không mặc định luôn Keychain. Theo [OpenAI Docs về authentication](https://learn.chatgpt.com/docs/auth). |
| Dán prompt là ChatGPT/Codex tự gọi được localhost và sáng đèn xanh | Codex có công cụ local theo quyền môi trường; cần xác định surface và bridge cụ thể. Suy luận thiết kế: copy prompt chỉ hỗ trợ hướng dẫn, không phải proof of connection. Dùng handshake có xác thực, session ID và heartbeat; `GET /health` không xác nhận vai trò Tech Lead. [OpenAI Docs về Codex CLI](https://learn.chatgpt.com/docs/codex/cli). |
| `approve-draft-pr` gộp code vào main | Sửa toàn bộ prompt và UX: đây là **push + Draft PR**, không merge. Auto-merge cần API, policy, quyền và kiểm định riêng. |
| Nhiều tab cùng provider có quota độc lập | Slot chỉ là năng lực thực thi. Credential/account là nguồn quota dùng chung; lead và worker có thể cạnh tranh cùng hạn mức. Chỉ hiển thị quota thật khi provider cung cấp dữ liệu đáng tin cậy. |
| Biên dịch Nuitka là chống dịch ngược 100% | Xem như lựa chọn đóng gói và tăng chi phí phân tích, không bảo đảm tuyệt đối. Chứng minh bằng binary chạy trên máy sạch; không dùng obfuscation làm ranh giới bảo mật. |
| Một lệnh compile `backend/app/main.py` là đủ | Hiện file dùng relative imports và chỉ khai báo FastAPI app. Cần executable entrypoint khởi động server, resource paths và build theo target. Nuitka standalone/onefile có thể bỏ yêu cầu cài Python ở máy đích; đó không phải bằng chứng script mẫu đã chạy được. [Nuitka Manual](https://nuitka.net/user-documentation/user-manual.html). |
| Tauri shell <30 MB / <50 MB RAM | Đặt ngân sách đo cho cả shell, backend, CLI, build và model; đo riêng idle và tải. Sidecar phải có binary theo target triple và quyền invoke cụ thể. [Tauri Sidecar](https://v2.tauri.app/develop/sidecar/), [Tauri Capabilities](https://v2.tauri.app/security/capabilities/). |
| HWID hash là ẩn danh và bất biến tuyệt đối | Xem là định danh thiết bị có khả năng thay đổi/collision/spoofing, cần quy trình đổi máy. Spec vừa ghi “không thu thập dữ liệu cá nhân” vừa lưu IP/email; phải thống nhất mô tả dữ liệu và retention. Không dùng kết luận pháp lý từ spec làm tiêu chí kỹ thuật. |
| Revoke license tự xóa dữ liệu tạm | Đề xuất chỉ hạn chế khởi chạy tính năng có license; cho phép đọc lịch sử/export, bảo toàn worktree và xử lý an toàn run đang chạy. Không để license service điều khiển xóa code người dùng. |
| License hub “0đ trọn đời / 24×7”, core planner trên cloud | Chi phí/availability phải khảo sát riêng tại lúc chọn hạ tầng. Cloud planner cần opt-in và data boundary rõ ràng; không đưa vào MVP local. |
| RAG loại trùng lặp 100%, giảm token 75%, index 1,5 giây | Chuyển thành giả thuyết cần benchmark trên tập repo có kích thước xác định; không cam kết trước khi đo. Hiện mới có Markdown retrieval. |
| Snapshot DB phục hồi toàn bộ dự án | SQLite snapshot đơn lẻ chưa đủ. Luồng `.devagent` hiện ghép metadata với Git bundles và opt-in working-tree overlays, scrub credentials/local paths rồi remap/reconcile khi restore; RAG index cần rebuild. |

Code mẫu License Hub ở §10.3 cũng chưa sẵn sàng production: kiểm tra rồi thêm thiết bị không atomic; activation chưa kiểm tra `ExpiresAt`; verify đọc device status không tái đối chiếu license bị revoke/hết hạn; chưa có signed offline entitlement. Đây là backlog Phase 7, không phải implementation đã có.

## 6. Kiến trúc đích đề xuất

```mermaid
flowchart LR
    Owner[Người dùng] --> UI[React Dashboard]
    UI --> Shell[Tauri: lifecycle / dialog / IPC]
    Shell --> API[FastAPI sidecar: loopback + auth]
    Lead[External Lead có công cụ local] -->|Credential có scope| API
    API --> Scheduler[Scheduler + quota / resource budget]
    Scheduler --> Runner[Process supervisor + adapters]
    Runner --> WT[Worktree theo task + pin base SHA]
    WT --> Gate[Scope + secrets + validation + review SHA]
    Gate --> Local[Hoàn tất local]
    Gate -->|Được cấp quyền publish| PR[Push + Draft PR]
    API --> DB[(SQLite + schema version)]
    API --> Context[Guidance + RAG theo revision]
    Shell -. Tùy chọn .-> License[License Hub]
```

Các nguyên tắc triển khai:

1. Giữ Python backend làm lõi; Rust shell phụ trách native/lifecycle. Không viết lại scheduler bằng Rust chỉ để đóng gói.
2. Gom cấu hình `app_data_dir`, `db_path`, `worktrees_dir`, `handoffs_dir`, `rag_path`, `resources_dir`, host/port và timeouts vào một nguồn cấu hình. Tách thư mục writable khỏi tài nguyên bundle readonly.
3. Giữ REST cho headless/external lead. UI có API transport riêng cho web dev và Desktop; xử lý HTTP lẫn WebSocket, không giả định luôn cùng origin hay cổng 8765.
4. Chọn cổng có cơ chế discovery và xác minh đúng instance. Không attach nhầm service đang chiếm cổng; external client cần endpoint và credential đã cấp, không chỉ URL hardcode.
5. Một backend owner cho mỗi data directory ở giai đoạn đầu. Nếu cần nhiều process scheduler, phải có reservation/capacity/scope leases bền vững; atomic claim từng task hiện tại chưa giải quyết tổng capacity giữa process.
6. Phân biệt `provider`, `credential_profile`, `agent configuration`, `worker_slot`, `run`, `lead_session`. Giữ `agents` làm cấu hình executor hiện tại; migration bổ sung dần thay vì tạo lại dữ liệu.
7. Tách quyền review, hoàn tất local, publish Draft PR và merge. Mặc định lead chỉ được tạo task/review theo repo được cấp; quyền mở rộng do owner cấu hình.

## 7. Các phase nâng cấp

Ước lượng dưới đây là **ngày công kỹ thuật cho một người**, gồm implementation và kiểm chứng trong phạm vi nêu ra; chưa phải deadline. Cần hiệu chỉnh sau Phase 0 và spike Desktop. Không tính thời gian chờ tài khoản ký số, review store hoặc sự cố dịch vụ bên ngoài.

| Phase | Đầu ra | Ước lượng | Phụ thuộc |
|---|---|---|---|
| **0 — Ổn định và bảo vệ lõi** | Baseline xanh; cleanup, changeset, validation, process và auth gates đủ tin cậy | 4–7 ngày | Bắt đầu ngay |
| **1 — Cấu hình và onboarding backend** | App data paths, migration, preflight, Git identity, validator profiles | 4–6 ngày | 0 |
| **2 — Desktop alpha** | Tauri macOS arm64, sidecar, native picker, wizard, bản cài nội bộ | 6–10 ngày | 1 |
| **3 — Fleet và quan sát** | Slot identity, hot apply, global budget, log/diff/DAG UI | 5–8 ngày | 0–2 |
| **4 — External Tech Lead** | Activation, xác thực session, quyền, heartbeat, handover | 4–7 ngày | 0–3 |
| **5 — Rescue và retry có kiểm soát** | Phân loại lỗi, cooldown, checkpoint, đổi worker an toàn | 5–9 ngày | 3–4 |
| **6 — Code Context Engine** | RAG theo revision + parser/symbol cho stack ưu tiên | 5–9 ngày | 0–1; tích hợp sau 5 |
| **7 — Phát hành và thương mại hóa** | Đa nền tảng, ký số/update, BYOK/Ollama, license/trial, portable backup | 7–12 ngày cho phạm vi hẹp; chia subphase | 2–6 ổn định |
| **8 — Remote pairing** | Phiên remote có giới hạn quyền, thu hồi và transport an toàn | 4–7 ngày | Auth của 0/4; release ổn định |

Tổng sơ bộ **44–75 ngày công** cho phạm vi hẹp ở từng phase; Phase 7 mở rộng nhiều provider, nền tảng và billing sẽ cần ước lượng riêng. Mốc Desktop alpha sau Phase 0–2 khoảng **14–23 ngày công** theo giả định này. Đây là ước lượng triển khai tuần tự, chưa trừ thời gian làm rõ requirements, sửa trên nhiều OS, ký số, phân phối beta và phản hồi người dùng. Sau Phase 0 phải cập nhật estimate bằng issue/task breakdown và spike Tauri. Các nhánh như code RAG có thể sắp lại sau khi lõi và dữ liệu đã ổn định.

### Trình tự triển khai và cổng quyết định

1. **Khởi động — không phát triển feature mới:** đưa workspace vào checkout có version control; xác nhận branch/base và nơi lưu dữ liệu. Rà soát conventions, cập nhật các link tài liệu sai trong README, lập danh sách vấn đề đang mở. Không cherry-pick hoặc ghi đè file người dùng để tạo checkout.
2. **Làm Phase 0 theo thứ tự:** 0A kiểm tra task revision/diff/scope/secret; 0B chặn cleanup nguy hiểm và thêm dry-run/recheck; 0C validation policy + auth REST/WS; 0D process tree, dependency/state invariants, log redaction và guidance theo revision. Chốt một thay đổi dữ liệu/API nhỏ trước khi sửa các module khác cùng phụ thuộc.
3. **Cổng G0:** suite backend xanh sau khi có regression cho phát hiện §4; dashboard build pass; cleanup fixtures chứng minh source/dirty/handoff/review không bị xóa; request không xác thực bị từ chối. Nếu chưa đạt, không khởi động Tauri hoặc fleet.
4. **Phase 1 rồi spike Phase 2:** hoàn tất config/data paths và onboarding API. Sau đó spike đóng gói backend độc lập, chọn sidecar/lifecycle và port discovery trên macOS arm64. Nếu spike không chạy ổn trên máy sạch, điều chỉnh estimate Desktop trước khi xây wizard đầy đủ.
5. **Cổng G1 — Desktop alpha:** chạy được vòng đời app và fixture repo end-to-end, diff đầy đủ, hoàn tất local; không có process sót sau đóng app và dữ liệu giữ qua lần mở lại. Khi đạt, mới mở beta nội bộ.
6. **Sau alpha:** Phase 3 (fleet) → Phase 4 (lead/session) → Phase 5 (rescue). Giữ manual approval làm mặc định; chỉ thêm tự động hóa khi có session auth, quyền và revision pin.
7. **Nhánh song song sau lõi:** Phase 6 RAG có thể nghiên cứu riêng, nhưng merge tích hợp sau pin revision/context contract. Phase 7A release pipeline là điều kiện phát hành rộng; BYOK, license và portability có thể chia release riêng. Phase 8 remote chỉ bắt đầu sau threat model và kiểm chứng auth/session.

Mỗi phase cần có: (a) API/schema/UX decision đã duyệt, (b) thay đổi trong scope có diff review được, (c) kiểm định tự động cho invariant mới, (d) hướng dẫn vận hành/migration tương ứng, và (e) demo acceptance flow. Không bắt đầu phase phụ thuộc chỉ vì code đã merge; phải đạt cổng nghiệm thu ghi dưới phase đó.

### Phase 0 — Ổn định và bảo vệ lõi

**Công việc:** xử lý §4; chuẩn hóa state transitions/invariants; cập nhật conventions cho stack Python/FastAPI, React/TypeScript và SQLite; sửa thứ tự ưu tiên guidance; cập nhật README cho đúng cleanup/log retention và loại link tài liệu sai; tạo baseline có quản lý phiên bản.

**File chính:** `docs/PROJECT_CONVENTIONS.md`, `README.md`, `backend/app/{git_ops,worktrees,validation,engine,scheduler,main,db,project_guidance}.py`, `backend/tests/test_core.py`.

**Thứ tự thực hiện:** (1) khôi phục Git baseline và chốt API/data invariants; (2) tạo changeset chuẩn dựa trên base SHA; (3) áp dụng changeset vào scope/secret scan, diff và validation; (4) khóa cleanup tới khi có ownership/dirty checks; (5) thêm API/WS auth và role scopes; (6) quản lý process tree và state/dependency; (7) cập nhật guidance/log redaction/docs.

**Điều kiện hoàn thành:** suite xanh; đủ regression cho commit hỗn hợp, nhiều commit, docs executable, dirty cleanup, đổi SHA sau validation, cancel process tree, dependency integration; REST/WS từ client chưa được cấp quyền bị từ chối. Không yêu cầu chạy AI thật để chứng minh các invariant này.

**Tiến độ thực thi (11/11/2026):** Phase 0 đã hoàn tất và push commit `8ea7d57` lên `codex/desktop-upgrade`. Changeset/diff dùng base SHA; cleanup preview và chỉ xóa worktree sạch đã tích hợp; docs-only policy không bỏ validator bắt buộc; API local có token, WebSocket dùng ticket một lần; process-group cancellation; dependency cùng repo và commit đã vào base; revision được kiểm tra trước review/publish/complete. Tại cổng G0: **45 passed**; dashboard production build pass; lint exit 0 với 6 warning React; `git diff --check` sạch.

**Kiểm định CI đa môi trường (12/11/2026):** Ubuntu CI run `43106581095` trên `a31873c` có dashboard và desktop-config pass; backend test có 2 failure vì fixture giả định Antigravity.app có sẵn và `dashboard/dist` được build trong job riêng. Hai test đã được làm xác định theo trạng thái app/mount fixture. Run `43106983524` trên `4c640bf` xác nhận lại **backend, dashboard và desktop-config đều pass**; backend 96 tests. Toàn backend suite tại máy hiện tại cũng pass ở `nice -n 10` và giới hạn một luồng thư viện số.

### Phase 1 — Cấu hình và onboarding backend

**Công việc:** `Settings` thống nhất; versioned migrations + backup trước migration; empty-state/`needs_onboarding`; preflight kiểm tra Git, repo, base, disk, CLI và validator theo stack; đọc Git identity theo repo trước global và cho cấu hình fallback. Maven/Node/Python/Go dùng profile và argv được xem trước, không tự chạy script repo chỉ để “quét môi trường”.

**Đầu ra:** API setup/status và preflight; schema cho cấu hình được persist, credential chỉ là reference; manifest snapshot ở mức thiết kế. Tên endpoint mới phải được chốt bằng OpenAPI trước khi dùng trong prompt/UI.

**Điều kiện hoàn thành:** DB trống vào onboarding; repo bất kỳ hợp lệ đăng ký được; repo dirty báo rõ worktree chỉ lấy committed base; thiếu tool/identity/validator có hướng dẫn cụ thể; dữ liệu cũ nâng cấp được từ snapshot test; không viết vào bundle.

**Thứ tự thực hiện:** cấu hình đường dẫn → versioned migration/backup → preflight API → validator profiles → Git identity → onboarding UI contract. Định dạng settings và response phải ổn định trước khi Desktop tiêu thụ.

**Tiến độ thực thi (11/11/2026):** Phase 1 đã hoàn tất trên working tree sau Phase 0. Đã thêm cấu hình tập trung, writable data directory theo platform khi đóng gói, schema `user_version` + snapshot SQLite trước migration, API setup status/preflight (Git/base/dirty/disk/validator/Agent CLI), profile Maven/Node/Python/Go/custom theo argv, Git identity repo-local/global/fallback và cấu hình host/port loopback. Kiểm định: **50 passed**; dashboard production build pass; lint exit 0 với 6 warning React; snapshot CLI khởi chạy được; `git diff --check` sạch. Chưa đóng gói ứng dụng native ở phase này.

### Phase 2 — Desktop alpha

**Công việc:** spike executable entrypoint và bundle backend trước; Tauri start/ready/shutdown/crash recovery; single instance; port discovery; native folder picker; wizard ba bước; transport HTTP/WS; PATH khác nhau giữa Finder và terminal; quyền dialog/shell tối thiểu và CSP.

**File mới dự kiến:** `src-tauri/`, `backend/desktop_entry.py`, `dashboard/src/lib/api.ts`, `dashboard/src/components/Onboarding/`, scripts build theo nền tảng. Tên file là đề xuất, chưa tồn tại.

**Điều kiện hoàn thành:** bản cài nội bộ chạy trên macOS arm64 sạch không cần Python/npm của developer; chọn folder có dấu/khoảng trắng; không attach nhầm port; đóng app không để subprocess mồ côi; dữ liệu còn sau reopen; chạy trọn flow fixture repo → task → validation → full diff → complete local. Draft PR được test bằng mock và chỉ smoke thật khi có repo thử nghiệm được cấp.

**Phạm vi alpha:** dùng CLI đã cài/đăng nhập, manual review; chưa cần Google login, license server, BYOK, auto-merge hoặc remote.

**Thứ tự thực hiện:** build/run sidecar tối thiểu → lifecycle/readiness/port discovery → data paths → Tauri permissions/dialog → API + WebSocket transport → onboarding → máy sạch smoke flow → đóng gói beta nội bộ.

**Tiến độ thực thi (11/11/2026):** đã dựng Tauri v2 shell cho macOS arm64, backend sidecar Nuitka onefile, cấp cổng loopback động qua readiness `READY:<port>`, API token theo app data, retry frontend, native folder picker, single instance, PATH cho app mở từ Finder và dừng sidecar khi thoát. Đã build `.app` (29.83 MiB) và DMG (20.86 MiB); `cargo check`, frontend production build/lint và đóng gói Tauri đều pass. Smoke test trên máy hiện tại xác nhận app kết nối API, hộp chọn thư mục macOS mở được, đóng app không còn sidecar, mở lại kết nối được và thư mục DB/token vẫn còn. Backend suite chạy với ưu tiên CPU thấp: **50 passed**, 1 warning deprecation. **Cổng Phase 2 chưa nghiệm thu đầy đủ:** chưa chạy trên máy sạch; chưa kiểm tra chọn path chứa dấu/khoảng trắng; chưa chạy trọn luồng UI fixture repo → task → validation → full diff → complete local. Vì vậy hiện coi đây là alpha build nội bộ, chưa phải beta hoàn tất.

### Phase 3 — Fleet và quan sát

**Công việc:** slot/run identity; API lưu/hot-apply enabled/capacity không bị refresh ghi đè; global max chạy/build và disk reservations; giới hạn dùng chung theo credential; giảm capacity theo cơ chế drain run hiện tại. UI fleet, task tabs, DAG, diff đầy đủ, reconnect log bằng event cursor và giới hạn bộ nhớ.

**Điều kiện hoàn thành:** không vượt budget khi nhiều task cùng vào; đổi 4 → 2 slot không kill ngẫu nhiên run; restart giữ cấu hình; scopes xung đột không chạy chung; thiếu disk ngăn cấp worktree mới; mất WebSocket kết nối lại không trùng/mất mốc quan trọng. RAM ước lượng phải ghi rõ cách đo, không lấy số cố định cho mọi worker.

**Thứ tự thực hiện:** lưu cấu hình pool bền vững → slot/run model → global resource budget/lease → endpoint hot apply/drain → reconnect event stream → UI fleet/DAG. UI không được cấp slot vượt capacity backend.

**Tiến độ thực thi (11/11/2026):** Phase 3 đã hoàn tất phạm vi hiện tại. Cấu hình fleet lưu SQLite và hot-apply; scheduler khóa claim bằng `BEGIN IMMEDIATE`, áp giới hạn active run, nhóm CLI credential theo adapter + executable và chặn cấp slot khi không đủ disk reserve. Hạ capacity chuyển sang drain, không hủy run đang hoạt động; validation/build có limiter riêng. `/api/fleet` và trang Fleet hiển thị giới hạn, run owner, trạng thái drain và disk headroom. Task drawer có tab Overview/Activity/Diff, dependency flow và diff tải theo chunk 250 KB; event stream reconnect bằng cursor, khử duplicate và giữ tối đa 1.000 event trong UI. Nghiệm thu: **57 passed** (thêm claim song song, disk gate, persist/drain, build hot-apply, diff pagination và WebSocket reconnect cursor); frontend build pass; lint exit 0 với 5 warning React `set-state-in-effect`; Tauri arm64 `.app` 29.84 MiB + DMG 20.88 MiB build pass; smoke UI xác nhận API kết nối, Fleet và shutdown sạch. RAM không được ước lượng bằng hằng số theo worker. Build limiter áp dụng trong backend instance; Desktop dùng single-instance.

### Phase 4 — External Tech Lead

**Công việc:** chọn Manual/Codex/CLI khác theo capability; activation prompt sinh từ API contract hiện hành; endpoint handshake riêng với client/session identity; heartbeat, expiry và lease quyền lead; handover snapshot gồm task, contract, revision, pending review và fleet. Lease cũ hết quyền điều khiển khi đổi lead, workers tiếp tục.

**Điều kiện hoàn thành:** `GET /health` không làm UI tự báo lead connected; session chưa cấp quyền không tạo task/publish; handover không dispatch lặp; app/CLI có công cụ local thực hiện được end-to-end. Surface không có bridge phù hợp phải hiện hướng dẫn cài bridge, không báo đã kết nối chỉ vì copy prompt.

Auto-review ở phase này tạo nhận xét/quyết định có audit. Auto-publish chỉ hoạt động với policy owner đã cấu hình và SHA đã duyệt. Merge vẫn là luồng riêng.

**Thứ tự thực hiện:** API contract → session credential/scopes → handshake/heartbeat → lead lease + revoke → handover snapshot → prompt generator và UI indicator → test integration bằng local fixture. Nút publish vẫn cần owner policy riêng.

**Tiến độ thực thi (11/11/2026):** đã thêm schema v3, API contract v1 và luồng pairing owner-issued dùng một lần (5 phút), scope `handover:read`/`task:create`/`task:review`, session token ngẫu nhiên chỉ lưu hash, một lease active toàn cục 90 giây được heartbeat mỗi 30 giây, session tối đa 8 giờ và revoke tức thì. Client token chỉ truy cập các route `/api/lead/client/*`; không truy cập local API/fleet/publish, không có scope publish/merge. Handover snapshot gồm task/revision/contract/pending review/fleet; external task creation chịu scheduler budget; review decision/comments được audit theo session. Fleet UI có trạng thái Tech Lead tách khỏi `/api/health`, sinh prompt từ contract và hiển thị code riêng, không tự gửi/copy code. Nghiệm thu API: **59 passed**, gồm scope denial, fixture task/review, hash/revoke/lease và CORS PATCH; frontend build pass, lint exit 0 với 5 warning React effect. Build `NUITKA_BUILD_JOBS=1 CARGO_BUILD_JOBS=1` hoàn tất; smoke `.app` 29.88 MiB xác nhận Fleet báo đúng `DISCONNECTED` khi không có lead; đóng app sạch. Tauri DMG bundler bị `hdiutil` báo mount tạm `Resource busy`; mount do build tạo đã được tháo và DMG 21 MiB dựng từ `.app` bằng `hdiutil`. **Cổng Phase 4 chưa nghiệm thu trọn:** chưa kết nối một CLI/bridge Tech Lead thật để chạy handshake → heartbeat → handover → task/review liên tục; hiện có API và prompt, chưa có adapter/bridge cục bộ được smoke-tested. Không phát hành pairing code thật trong smoke test.

**Bổ sung bridge CLI (12/11/2026):** `scripts/tech_lead_bridge.py` chạy trong terminal, chỉ nhận HTTP(S) loopback, tắt proxy/redirect, đọc pairing code bằng prompt ẩn, chỉ giữ session token trong RAM và tự heartbeat khi người dùng nhập lệnh. Hỗ trợ handover, tạo task từ JSON, diff và review; không có publish/push/merge. Prompt pairing trong Fleet UI đã trỏ tới CLI. Integration test nối bridge qua HTTP transport vào FastAPI thật và xác nhận pairing → heartbeat → handover → create task → review → revoke, token bridge bị từ chối ở owner API và không được trả ra từ `pair()`. Test phát hiện queued task bị thiếu khỏi handover; route đã sửa để bao gồm queued. Smoke riêng trên app FastAPI tạm, port loopback ngẫu nhiên và Git fixture xác nhận CLI process thật pairing → heartbeat → handover → tạo task queued → ghi review và thoát sạch; pairing code không xuất hiện trên terminal. Đã thử kiểm tra native app bằng CUA nhưng host báo Mac đang khóa và không thể tự mở khóa; GUI flow trong Tauri app đã đóng gói vẫn chưa xác nhận, nên cổng Phase 4 chưa qua UI acceptance.

### Phase 5 — Rescue và retry có kiểm soát

**Công việc:** phân loại auth/rate-limit/network/build/timeout; cooldown và `Retry-After` khi có; bounded backoff; checkpoint lưu tracked/untracked phù hợp và được scan; chuyển worker trong cùng task/worktree qua ownership lease; quota/budget cho rescue; audit worker trước/sau.

**Điều kiện hoàn thành:** worker cũ và toàn bộ process con đã dừng trước worker mới; không có hai writer; code dở không mất sau restart; retry không lặp publish; rescue không vượt resource budget; giải phóng process/slot sau run, giữ worktree theo retention an toàn. Không hiển thị “70% hoàn thành” như dữ kiện nếu chỉ là ước lượng của model.

**Thứ tự thực hiện:** phân loại exit reason → retry/backoff theo provider → checkpoint và scan → writer lease → đổi worker giữ task/run provenance → recovery sau restart. Không thêm auto-rescue trước khi checkpoint + single-writer gate đạt.

**Tiến độ thực thi (11/11/2026):** đã thêm bộ phân loại `auth`, `rate_limit`, `network`, `timeout`, `build`, `worker_exit` và `blocked`; auth không retry cùng credential nhưng được thử CLI khác khi đủ retry budget. Đọc `Retry-After` dạng giây/HTTP-date; backoff chặn ở 30 giây và Retry-After tối đa 60 giây. Schema v5 thêm cooldown bền vững theo adapter + executable; scheduler chặn admission cho credential đang cooldown. Run report lưu failure class, retryability và checkpoint gồm worktree được giữ, base ref, changed paths đã quét. Scope/secret scan chạy cả khi worker exit khác 0 và lúc startup reconciliation; lỗi scan hoặc secret/out-of-scope chặn task. Task writer lease độc quyền được chuyển nguyên tử cùng `assigned_agent_id` và run record sau khi process group cũ dừng; rescue candidate phải khác credential, hỗ trợ capability/scope và còn trong `max_per_credential`/`max_parallel`; task giữ nguyên slot/disk reservation. Fleet và task UI hiển thị cooldown/lease; log giữ provenance trước/sau worker. Process group được dừng cả khi leader CLI đã thoát, trước khi lease chuyển. **Kiểm chứng:** 69 backend tests passed, một cảnh báo deprecation từ Starlette; dashboard build pass; lint exit 0 với 5 warning React effect; `.app` 29.91 MiB và DMG 20.95 MiB build bằng một job; packaged sidecar khởi động với data dir tạm, trả session token + Fleet snapshot, schema v5 và đã dừng (không còn process). CUA chưa kiểm tra được màn hình vì Mac đang khóa. **Phase 5 vẫn chưa hoàn tất:** startup giữ checkpoint nhưng cố ý không tự resume task; lease mồ côi cần thao tác thủ công; mô phỏng backend crash đã có test trên Windows và macOS, nhưng crash/relaunch toàn ứng dụng đã đóng gói cùng UI và failure modes thật của từng CLI/provider còn cần nghiệm thu.

**Bổ sung containment đa nền tảng (12/11/2026):** worker và validator dùng chung `create_managed_process`. POSIX giữ process-group termination; Windows gắn child process vào Job Object `KILL_ON_JOB_CLOSE`, đóng Job cả sau khi leader thoát và trước khi nhả validation/writer lease để dừng descendant còn sống. Thêm test process-con thật dùng chung trên POSIX/Windows và job Windows nhẹ trong CI. Kiểm tra local với `nice -n 10`, một luồng: process-tree tests **2 passed**; toàn backend **96 passed** trong 11.78 giây. CI run [43109612130](https://github.com/jtuongdang/agentforge/actions/runs/43109612130) đã qua trên Windows runner (hai process-tree tests), Ubuntu backend, dashboard và desktop-config. Job được gắn ngay sau khi subprocess tạo xong, do đó còn một cửa sổ rất ngắn trước lúc gắn; cổng Phase 5 vẫn cần nghiệm thu lifecycle crash/reboot, UI và failure modes provider/CLI.

**Regression validator descendant (12/11/2026):** test mới cho thấy `asyncio.Process.wait()` có thể chờ cả pipe kế thừa tới EOF thay vì chỉ chờ PID gốc; descendant giữ stdout nên validator đợi quá lâu mới gọi cleanup. Thêm `wait_for_process_exit()` dựa trên trạng thái return code của root process, dừng process tree trước khi drain reader; Maven/custom validation và worker đều dùng luồng này. Worker/validator regression tests **3 passed**, toàn backend **97 passed** với `nice -n 10` và một luồng. Windows test mô phỏng owner process `os._exit()` sau khi gắn worker vào Job Object; worker không sống sót.

**Crash containment Windows (12/11/2026):** CI run [43111262767](https://github.com/jtuongdang/agentforge/actions/runs/43111262767) xác nhận **4 process-tree tests passed trên Windows**, gồm worker descendant, validator giữ stdout và owner crash; Ubuntu backend, dashboard và desktop-config cũng đều qua.

**POSIX guardian (12/11/2026):** mỗi worker/validator có guardian độc lập làm leader của process group riêng; worker được đưa vào group trước khi chạy lệnh, nên descendants kế thừa group ngay từ đầu. Guardian giữ một pipe mở; khi backend đóng bình thường hoặc crash, EOF khiến guardian dừng toàn group. Binary Nuitka có entrypoint `--process-watchdog` để smoke packaged test không cần Python trên máy người dùng. Kiểm tra local `nice -n 10`, một luồng: toàn backend **98 passed, 1 Windows-only skipped**; test mô phỏng backend `os._exit()` xác nhận worker không tạo marker; packaged watchdog entrypoint smoke qua với sidecar shim. CI run [43113390425](https://github.com/jtuongdang/agentforge/actions/runs/43113390425) mới nhất: process-tree suite **4 passed trên Windows**, full backend **98 passed, 1 skipped trên Ubuntu**, dashboard và desktop-config pass. Chưa smoke watchdog trong binary Nuitka thực tế; desktop package matrix đang chờ ARM64 run cũ kết thúc. UI và provider/CLI thực vẫn cần nghiệm thu.

**Nuitka watchdog correction (12/11/2026):** matrix [43114794951](https://github.com/jtuongdang/agentforge/actions/runs/43114794951) build Windows x64 và packaged sidecar smoke thành công; macOS Intel tạo `.app` và DMG nhưng smoke phát hiện worker sống sót khi owner crash. Nguyên nhân là Nuitka onefile chạy watchdog trong process con, nơi `os.getpid()` không nhất thiết bằng PGID của guardian. Watchdog nay nhận PGID qua pipe owner-guard trước khi worker chạy. Regression local: POSIX guardian tests pass; toàn backend suite hiện **105 passed, 1 Windows-only skipped**. Matrix [43132980438](https://github.com/jtuongdang/agentforge/actions/runs/43132980438) trên commit `f6f88eb` vừa pass packaged sidecar smoke trên Windows x64, macOS Intel và macOS ARM64, xác nhận watchdog trong binary Nuitka trên cả ba target. Cổng này xác nhận containment đóng gói; crash/relaunch toàn ứng dụng, GUI và failure modes thật của CLI/provider vẫn cần nghiệm thu.

**Phục hồi checkpoint sau restart (12/11/2026):** thêm API/UI `Review checkpoint & requeue`. Owner phải xác nhận worker cũ đã dừng và gõ mã task; backend chỉ cho phép run bị đánh dấu `restart_interrupted`, lease orphaned, checkpoint có scan ban đầu sạch, worktree đúng managed path/branch, và fresh scan không phát hiện scope violation hay secret. Khi được duyệt, task chạy lại trên cùng worktree/branch nên thay đổi checkpoint vẫn có mặt; không tự-resume khi app khởi động. Regression xác nhận sai mã và secret mới bị chặn, còn checkpoint an toàn được requeue và giữ nội dung; toàn backend **122 passed, 1 skipped** với ưu tiên CPU thấp; dashboard TypeScript production build pass. Còn packaged UI smoke và kiểm chứng crash/relaunch toàn ứng dụng trên desktop targets.

### Phase 6 — Code Context Engine

**Công việc:** giữ Markdown FTS đang có; bổ sung source indexing và parser một stack ưu tiên trước; pin repo + revision + worktree, xử lý rename/delete; content hash để phát hiện thay đổi; budget toàn bộ context; nguồn snippet có path, line và revision. Scan/exclude secrets, symlink ngoài repo, generated/vendor files; báo rõ parser/index lỗi thay vì ngầm tuyên bố đã có đủ context.

**Điều kiện hoàn thành:** truy vấn không lẫn branch/repo; snippets khớp revision worker; symbol lookup có fixture đúng/sai; update/delete index chính xác; fallback FTS hoạt động; benchmark cold/incremental index và chất lượng retrieval trên repo mẫu. Quyết định tiếp tục DB riêng `rag.sqlite3` hay gộp DB dựa trên migration/locking, không tự đổi sang `devagent.db` theo tên ví dụ trong spec.

**Thứ tự thực hiện:** chốt stack/parser được hỗ trợ → lập chỉ mục theo commit → symbols/chunks → query API có budget/citation → context injection đúng revision → đánh giá quality/performance/privacy trước mở rộng parser.

**Tiến độ thực thi (12/11/2026):** đã thêm index Python AST + Markdown FTS trong `rag.sqlite3`, tách khỏi index Markdown legacy; phạm vi cache khóa theo repository ID, SHA và đường dẫn worktree. Chỉ file `.py`/`.md` tracked trong Git được index; worktree phải sạch để nội dung không bị gắn nhầm với SHA. Bỏ qua secret-bearing, symlink, file lớn và thư mục generated/vendor; syntax error có thống kê riêng. Query trả citation path/line/SHA/worktree/hash, giới hạn context và kiểm tra hash file trước khi xuất snippet. API `/api/repositories/{id}/code-context` và planner lấy context trước khi chạy agent từ worktree đã pin revision; lỗi index được ghi rõ và task tiếp tục với project guidance. Regression bao phủ repo/revision/worktree isolation, secret, dirty/untracked file, parse error, symbol hit/miss, rename/delete, Markdown fallback và budget. **Kiểm chứng:** toàn backend suite **71 passed**, 1 cảnh báo deprecation; fixture 100 file: cold index 0.065 s, incremental 0.058 s, citation đúng `src/module_042.py:1`. Trên repo thực tại commit `6f42f37`, đã index 35 file source còn lại sau khi bỏ 1 file có secret; 0 parse error, không truncation; cold 0.158 s, incremental 0.091 s, retrieval đúng **5/5** truy vấn gold cho fleet cooldown, validation redaction, process tree, writer lease và project guidance. Legacy Markdown RAG tiếp tục có regression index/search/update riêng. **Phase 6 hoàn tất cho stack Python/Markdown**; chưa mở rộng parser sang stack khác.

### Phase 7 — Phát hành và thương mại hóa

Tách thành bốn subphase có thể phát hành độc lập:

1. **7A — Release:** target matrix macOS arm64/x86_64 và Windows x64; backend binary theo target; ký số/notarization phù hợp; update có chữ ký, backup migration và kế hoạch phục hồi phiên bản/dữ liệu tương thích; smoke trên máy sạch. Đo kích thước/idle RAM và tải thật.
2. **7B — Provider bổ sung:** secret store native cho BYOK, chỉ reference trong SQLite; adapter protocol cho OpenAI/Claude/Gemini và Ollama; endpoint local mặc định, kiểm tra khả năng tool/model; mỗi provider có fixture và smoke riêng. Thêm từng provider theo nhu cầu, không yêu cầu tất cả để ra alpha.
3. **7C — License/trial:** chốt trial theo account hay thiết bị; Google login nếu được chọn phải tách khỏi provider login. Hub cấp entitlement có chữ ký, thời hạn/grace và revoke rõ; activation atomic; hash license key tại server; kiểm tra expiry; quản lý đổi máy; offline và clock rollback tests; app vẫn cho đọc/export khi hết license.

**Quyết định và tiến độ Phase 7C (12/11/2026):** chủ dự án chọn trial 14 ngày gắn thiết bị, không dùng Google login. Client xác minh entitlement RSA-PSS, chỉ gửi one-way OS device hash sau privacy consent, hỗ trợ trial/offline grace/clock rollback; SQLite schema v9 cache ký số và checkpoint loại cache + consent khi chuyển máy. Refresh/activation API chỉ truyền device hash và license key qua HTTPS; key không lưu hoặc ghi log. Scheduler chặn dispatch mới khi không có quyền, task vẫn queued và dữ liệu local vẫn đọc/export được. UI báo trạng thái, privacy notice, bắt đầu trial, kích hoạt key và release device. Hub .NET 8/MongoDB có unique device/license indexes, trial 14 ngày không reset, activation/deactivation giới hạn thiết bị nguyên tử, HMAC hash key, admin create/revoke có bearer token, entitlement RSA-PSS; request DTO từ chối mọi trường không khai báo. `.env` được ignore, chỉ có template placeholder. **Kiểm chứng local:** backend **121 passed, 1 skipped** (21.74 giây, tuần tự, ưu tiên CPU thấp; gồm integration với mongod tạm); integration flow trial/activation race/device transfer/revoke/privacy **1 passed**; License Hub **4 tests passed**; .NET build không warning/error; dashboard build pass, lint exit 0 với 7 React effect warnings; `git diff --check` pass. CI [43170523138](https://github.com/jtuongdang/agentforge/actions/runs/43170523138) đã pass cả 5 job, gồm license-hub build/unit/live-Mongo integration. **Phase 7C còn mở:** chưa chạy với MongoDB Atlas/external service, chưa deploy HTTPS hub, chưa cấp signing public key/hub URL cho bản desktop release và chưa kiểm tra trên staging. Không đưa source code, IP, email, OS details hay plaintext license key vào database/license request.

**CI mới nhất (12/11/2026, commit `cd97d67`):** [43171114083](https://github.com/jtuongdang/agentforge/actions/runs/43171114083) đã pass đủ 5 job: backend, dashboard, desktop-config, Windows process-tree và License Hub cùng Mongo integration. Desktop packaged smoke đã kiểm tra license status và quyền đọc/export local; thay đổi kế tiếp nâng yêu cầu package lên schema v9 và bắt buộc `license_cache`. Full packaged matrix cho commit mới cùng UI/API recovery vẫn đang chờ nghiệm thu.

**Đóng gói License Hub (12/11/2026, commit `a4f18f8`):** thêm Dockerfile multi-stage .NET 8 chạy non-root, listen trong container trên 8080 để đặt sau HTTPS ingress; `.dockerignore` loại `.env`, PEM/key/certificate và thư mục secrets khỏi build context. README mô tả deploy qua private network, secret manager và read-only signing-key mount. CI [43173057546](https://github.com/jtuongdang/agentforge/actions/runs/43173057546) pass đủ 5 job, gồm image build, License Hub unit tests và Mongo integration. Đây là artifact triển khai, chưa phải staging deployment.

**Atlas staging (12/11/2026):** owner đã cấu hình URI trong `license-hub/.env` bị Git ignore. Read-only ping Atlas pass; integration trial/activation race/device transfer/revoke/privacy và chữ ký pass **1 test trong 4.20 giây** trên database tổng hợp riêng. Lượt đầu phát hiện cluster giới hạn tên database 38 byte; fixture đã rút ngắn tên vẫn giữ hậu tố ngẫu nhiên. Bổ sung launcher `scripts/run_license_hub.py` đọc env không thực thi shell, kiểm tra cấu hình không in secret và build bằng một MSBuild worker; staging key/pepper/admin token được lưu local với quyền hạn chế, private key ở ngoài repo. Các kết quả này thay thế ghi chú trước về việc chưa kiểm tra Atlas; HTTPS ingress/hub URL cho Desktop, pin public key vào release và nghiệm thu trên staging HTTPS vẫn còn mở.
4. **7D — Portability và integration:** backup manifest cho DB + Git refs/bundle + patch/untracked được phép export, loại credentials; remap path và reconcile run khi restore. Nếu triển khai merge: kiểm tra branch protection/CI, serialize trên từng target, revalidate sau rebase, idempotency và audit; không gọi route Draft PR là merge.

**Điều kiện hoàn thành:** từng subphase có artifact và test matrix riêng; không mất dữ liệu khi update/restore/revoke; không ghi key vào DB/log; cloud license không nhận source code; hành vi offline và provider cloud được mô tả chính xác.

**Tiến độ Phase 7A (12/11/2026):** đã thêm build script sidecar chạy theo host cho macOS arm64/x86_64 và Windows x64, đặt mặc định một Nuitka job và chặn tối đa hai; Tauri dùng bundle target mặc định theo OS. Workflow `desktop-build.yml` tạo artifact matrix macOS arm64, macOS Intel và Windows x64 khi được gọi thủ công hoặc khi push tag `desktop-v*`; artifact lưu 14 ngày, chưa phát hành ra người dùng. CI PR có job nhẹ kiểm tra syntax build script và cấu hình Tauri. Thêm smoke sidecar kiểm tra readiness, token API, Fleet, integrity DB và dừng process tree; smoke local trên binary macOS arm64 17 MiB đã pass. Workflow actions được cập nhật lên Node 24. Run `43066133832` ban đầu có smoke Windows pass phần API nhưng lỗi dọn SQLite còn khóa; run `43070276329` xác nhận Windows build pass nhưng cùng lỗi cleanup. Log cho thấy Python SQLite context manager không tự đóng connection; fix `contextlib.closing` ở commit `95e1adf`. Run `43074171974` xác nhận **Windows x64 và macOS Intel build + packaged smoke + artifact upload đều pass**; artifact lần này lần lượt 39.5 MB và 45.5 MB. Job macOS arm64 của ba run trước bị cancel trước bước đầu, `runner_id=0`, nên chưa có bằng chứng lỗi build. Commit `680b947` giới hạn ma trận tối đa hai job nhưng chưa đủ tránh ARM64 bị xếp chung hàng đợi. Commit `58ffcf4` tách ARM64 thành job phụ thuộc và thêm lựa chọn dispatch riêng; run arm64-only `43079466027` đã skip hai target x64 như dự kiến nhưng sau đó bị hủy trước khi nhận runner. **Phase 7A chưa hoàn tất:** chưa có nghiệm thu build/smoke macOS arm64 trên runner, chưa có Apple signing identity (`security find-identity` từng trả 0) và repository chưa có Actions secrets (`gh secret list` từng rỗng); chưa cấu hình được certificate/notarization hoặc updater signing và chưa phát hành bundle unsigned.

**Cập nhật kiểm chứng đóng gói macOS (12/11/2026, commit `015de96`):** CI run `43087945665` đã chứng minh `.app` Intel được tạo nhưng bước DMG của Tauri thất bại khi `hdiutil` eject image trung gian (`Resource busy`). Workflow giờ chỉ yêu cầu Tauri tạo `.app`, sau đó script `scripts/build_macos_dmg.sh` dựng DMG từ staging folder mà không mount image trung gian. Local smoke dùng `.app` fixture nhỏ đã tạo DMG và `hdiutil verify` pass; YAML workflow parse và `sh -n` pass. Run `43100790464` đã **pass** build `.app`, tạo DMG không mount image, packaged sidecar smoke và upload artifact trên runner macOS Intel. Windows x64 run `43098509055` cũng **pass** build, packaged sidecar smoke và upload artifact. Hai run này kiểm chứng commit cũ hơn phần provider worker trong working tree; ARM64 vẫn chưa có runner, ký số/notarization chưa cấu hình.

**Cập nhật tiến độ (12/11/2026):** run ARM64-only `43079466027` kết thúc `cancelled` trước khi có step hoặc runner (`runner_id=0`); đây vẫn là hạn chế hàng đợi/cấp phát runner, chưa phải kết quả build. Ở nhánh `codex/desktop-upgrade`, Phase 7D đã có export và restore checkpoint `.devagent`: SQLite snapshot nhất quán, token/credential và đường dẫn máy nguồn được loại bỏ; Git refs được bundle và quét secret trong reachable history; uncommitted files mặc định bỏ qua, chỉ thêm sau opt-in + scan; archive có checksum, SQLite integrity và path traversal checks. Dashboard có preview/export; desktop dùng native Save dialog, open-file và folder picker. Restore tạo profile mới cạnh data hiện tại, remap repositories/worktrees, vô hiệu hóa agents/credentials, đánh dấu task cần review là blocked và run đang chạy là interrupted; remote Git được lọc credential, không chạy global filter/hook cấu hình; RAG index bị loại và cần dựng lại. Commit `7b9519e` thêm lựa chọn kích hoạt profile restore ở lần mở app kế tiếp, quay về profile ban đầu và mở rộng packaged smoke để export/restore checkpoint. Run macOS Intel `43087945665` đã compile Rust/Tauri và tạo `.app`, nhưng DMG step fail do `hdiutil` không eject được mounted image (`Resource busy`), vì vậy packaged smoke chưa chạy. Run Windows `43091260355` **pass** Tauri build, packaged sidecar smoke và artifact upload trên commit `42c2ee8`; run này chưa gồm profile activation hoặc smoke export/restore mới của `7b9519e`, sẽ cần rerun. Backend suite **81 passed**, `npm run build`, `cargo fmt --check` và `git diff --check` pass; không chạy `cargo check`/Tauri bundle local để hạn chế tải máy.

**Windows revalidation (12/11/2026):** run [43105031532](https://github.com/jtuongdang/agentforge/actions/runs/43105031532) đã **pass** trên `aaf2403`: Tauri Windows x64 build, packaged sidecar smoke (API/token, OS credential store, schema v7, provider metadata, checkpoint export/restore) và artifact upload; artifact 42,639,469 bytes. Bản này vẫn trước Windows Job Object và validator wait fix. ARM64-only run [43108291870](https://github.com/jtuongdang/agentforge/actions/runs/43108291870) hiện đã nhận runner và đang build `.app` trên commit `2c10dd0`. Đã xếp hàng full matrix mới nhất [43111694227](https://github.com/jtuongdang/agentforge/actions/runs/43111694227), target `all`, trên commit `aea11fb` để revalidate ba target sau các chỉnh sửa Phase 5. CUA kiểm tra lại vẫn báo Mac khóa, nên GUI acceptance chưa thực hiện được.

**Cập nhật portability/security (12/11/2026, commit `2f7d313`):** full backend suite lần chạy mới phát hiện path tuyệt đối còn sót trong free SQLite pages sau khi scrub checkpoint. `_scrub_snapshot_database` nay bật `secure_delete` trước khi thay dữ liệu và chạy `VACUUM` trước kiểm integrity/export. Regression checkpoint trước đó fail, sau sửa pass; toàn backend **107 passed, 1 Windows-only skipped** (18.58 giây, tuần tự với ưu tiên CPU thấp). Test `keyring` ban đầu không chạy được do dependency thiếu trong `.venv`; cài đúng bản pin `25.7.0` theo requirements rồi full suite pass. Đây là bằng chứng đóng thêm cổng Phase 7D về loại đường dẫn máy nguồn khỏi artifact; build/package của commit này còn cần smoke riêng.

**Matrix kiểm chứng Phase 5 (12/11/2026):** run [43114794951](https://github.com/jtuongdang/agentforge/actions/runs/43114794951) trên `f3fed1d`: Windows x64 Tauri build, packaged backend smoke (bao gồm Nuitka watchdog owner-crash test) và artifact upload **pass**; macOS Intel build + DMG **pass**, nhưng packaged watchdog smoke phát hiện worker còn sống khi owner crash; artifact bị bỏ qua và ARM64 bị skip do dependency. Đây là bằng chứng macOS containment chưa đạt; source đã được sửa để truyền PGID qua owner pipe. Bản sửa này đang chờ targeted packaged CI run, chưa được tính là verified.

**Bổ sung tiến độ Phase 7B (12/11/2026):** đã thêm schema v6 có backup SQLite trước khi nâng cấp từ v5; adapter protocol/catalog cho OpenAI, Anthropic, Gemini và Ollama; credential reference ngẫu nhiên trong SQLite nhưng khóa chỉ lưu qua OS keychain đã allowlist (macOS Keychain, Windows Credential Manager, Secret Service/KWallet), fail-closed khi không có backend được duyệt. API key không trả về API/UI; model discovery dùng endpoint cố định, timeout 8 giây, body tối đa 1 MB, không theo redirect; Ollama chỉ nhận loopback và bỏ qua proxy. Trang Agents cho phép thêm/xóa kết nối, kiểm tra model và chọn model ưu tiên. Checkpoint scrub loại provider secret reference và model cache theo chính sách portability. Kiểm định mock cho bốn provider, API authentication/secret redaction, keyring fail-closed, checkpoint redaction và migration v5→v6; toàn backend suite **87 passed** với `nice -n 10` và một luồng thư viện số; trên máy hiện tại `_keyring()` chọn macOS Keychain, dashboard production build và `git diff --check` pass. **Phase 7B chưa hoàn tất:** kết nối hiện chỉ quản lý credential/model và kiểm tra catalog; chưa có provider inference/tool-call được tích hợp làm coding worker, chưa có smoke với credential thật (không gửi prompt và không tiêu quota trong kiểm định này), và chưa xác minh sidecar Nuitka trên máy sạch có native keyring dependency. Run `43094308449` (HEAD `88e13c4`) đã pass Tauri build và packaged sidecar checkpoint export/restore smoke. Bước artifact upload báo failure và run/job còn trạng thái GitHub `in_progress`, nhưng artifact registry có artifact Windows **39,806,526 bytes**; artifact đã tồn tại nhưng chưa tải xuống để xác minh nội dung. Run này không chứa thay đổi BYOK. Packaged smoke đã được mở rộng cục bộ để kiểm tra provider metadata trên run kế tiếp.

**Cập nhật kiểm chứng Phase 7B (12/11/2026, commit `f1517c4`):** backend có endpoint credential-store status; UI Agents hiển thị backend keyring thực tế hoặc lý do không khả dụng, và packaged smoke sẽ fail nếu binary không nạp được OS credential store được duyệt. Sau thay đổi này, provider tests **6 passed**, backend suite **87 passed trong 11.14 giây**, `npm run build`, `py_compile` và `git diff --check` đều pass. Test chạy tuần tự với ưu tiên CPU thấp, không build Tauri/Rust cục bộ. Workflow Windows hiện chạy trên commit cũ hơn `c6a6f42`; kết quả đó chưa xác nhận endpoint/smoke mới trong `f1517c4`.

**Bổ sung Phase 7B — coding worker (12/11/2026, working tree):** đã nối OpenAI Chat Completions, Anthropic Messages, Gemini `generateContent` và Ollama `/api/chat` thành worker chỉ chạy khi người dùng chọn agent provider cụ thể; provider/Ollama không được auto-match. Worker chỉ có ba công cụ filesystem giới hạn theo giao của task/agent globs, chặn symlink, credential/private-key files và nội dung khớp secret scanner; không có shell hay công cụ network tùy ý. Cloud task yêu cầu consent riêng trước khi gửi prompt và file scope, credential chỉ đọc từ OS keychain; Ollama giới hạn loopback và bỏ qua proxy. Bổ sung timeout/giới hạn request, response, file, số lượt và runtime; log chỉ lưu tên tool/path. Schema nâng lên v7, thêm consent task và provider agent, backup SQLite trước migration; validation sửa `**/` thành phần tùy chọn để scope khớp file ở thư mục gốc. Kiểm định mock: **93 backend tests pass** với `nice -n 10` và giới hạn thư viện một luồng; dashboard production build pass. Không gửi prompt tới provider thật, không dùng credential/quota thật và chưa chạy build Tauri/Rust cục bộ. Cần packaged smoke/CI trên commit có provider worker, smoke thủ công với provider credential nếu người dùng cấu hình, cùng kiểm tra restore/lease khi khởi động lại trước khi đóng Phase 7B.

**Cập nhật bảo vệ dữ liệu và kiểm chứng (12/11/2026):** rà soát prompt phát hiện cloud worker còn nhận project guidance/trích đoạn source ngoài file edit scope và task markdown có đường dẫn tuyệt đối của worktree. Checkbox consent nay liệt kê rõ prompt, guidance, source excerpts và nội dung file worker đọc; engine bỏ đường dẫn cục bộ khỏi prompt provider. Test regression chứng minh engine từ chối khi thiếu consent, truyền đúng cả hai scope đến worker, log tool không ghi nội dung file và prompt provider không chứa local path. `list_files` dùng ngân sách tối đa 10.000 filesystem entries cho mỗi worker task, kể cả khi model yêu cầu quét lặp lại. Toàn backend suite **96 passed trong 11.68 giây**, test chạy `nice -n 10` với giới hạn thư viện số một luồng; dashboard build, `py_compile` và `git diff --check` pass. Windows desktop run `43104066329` trên `559c366` đã pass Tauri build, packaged sidecar smoke và artifact upload; đây là bằng chứng đóng gói cho worker/httpx và consent lưu trên task, nhưng chưa gồm disclosure đầy đủ, loại local path khỏi prompt và giới hạn listing. Windows run `43105031532` trên `aaf2403` đang build Tauri để xác nhận các chỉnh sửa mới hơn.

### Phase 8 — Remote pairing

**Quyết định owner (12/11/2026):** dùng VPN riêng + HTTPS ingress do operator quản lý; client trình duyệt; quyền mặc định chỉ đọc; full diff cần cấp quyền riêng theo từng task. Không cho remote tạo task, quyết định review, publish, cleanup hoặc truy cập owner API.

**Công việc:** QR/mã mời một lần TTL 5 phút; browser gửi pairing request; owner duyệt tên thiết bị trong desktop UI; credential remote tách khỏi local API token, hash khi lưu và chỉ giữ trong bộ nhớ browser; session TTL 12 giờ/revoke; task summary dùng field allowlist; full diff grant pin theo task + SHA validation; status WebSocket có cookie ticket HttpOnly ngắn hạn, không đưa token vào URL; giới hạn request/diff/connections; HTTPS ingress proxy vào loopback gateway riêng. Gateway chỉ khởi động khi cấu hình exact HTTPS origin và trusted proxy hợp lệ; API owner vẫn dùng loopback + port ngẫu nhiên. Sidecar đóng gói và phục vụ browser viewer/asset dưới `/remote` mà không lộ dashboard owner.

**Điều kiện hoàn thành:** invite hết hạn/tái sử dụng bị từ chối; owner approval bắt buộc; session hash/expiry/revoke; token remote không truy cập owner API; task summary loại prompt/log/environment/local paths; full diff chỉ đọc khi grant đúng task/SHA; revoke cắt REST và WebSocket; ingress chỉ nhận VPN clients và HTTPS; backup/checkpoint không chứa remote session.

**Thứ tự thực hiện:** threat model và owner decision → schema v8 + opt-in loopback gateway → pairing request/owner approval → browser read-only API/UI → diff grant theo SHA → WebSocket revoke/expiry → cấu hình ingress → kiểm thử LAN/VPN trên hai thiết bị.

**Tiến độ triển khai (12/11/2026):** đã thêm schema v8 với backup SQLite trước migration; remote invite token 256-bit chỉ lưu SHA-256, TTL 5 phút và dùng một lần; pairing request có poll-secret hash, owner phải approve/deny qua dashboard; device token riêng lưu hash, chỉ giữ trong RAM trình duyệt, lần giao token có TTL 60 giây và session hết hạn khi app restart. QR mở trực tiếp HTTPS `/remote` với invite trong fragment; viewer điền mã rồi xóa fragment khỏi thanh địa chỉ. Summary tối đa 50 task chỉ trả field allowlist, đã redacted secret-like values/local paths. Owner có thể revoke thiết bị hoặc grant/revoke full diff sau khi gõ xác nhận gắn SHA validation hiện hành. Diff giới hạn mỗi trang 24,000 ký tự, tổng offset 1,000,000; REST 120 request/phút/thiết bị; WebSocket chỉ đẩy status/update timestamp, ticket 30 giây qua Secure/HttpOnly/SameSite cookie, tối đa 2/device và 32 tổng, kiểm tra revoke mỗi 2 giây. Gateway phụ bind `127.0.0.1:8766` chỉ khi `CLINIC_ORCHESTRATOR_REMOTE_ORIGIN` + `CLINIC_ORCHESTRATOR_REMOTE_PROXY_IPS` hợp lệ; không mở bind LAN/VPN hay đổi owner API. Checkpoint scrub xóa invites, pairing requests, devices và diff grants. Viewer và assets được phục vụ riêng dưới `/remote` và `/remote/assets/*`, qua đúng Host/HTTPS/proxy; HTML đặt CSP/no-referrer, assets chặn traversal. **Kiểm chứng cục bộ:** toàn backend suite **106 passed, 1 Windows-only skipped** (19.06 giây, tuần tự với ưu tiên CPU thấp); gồm body cap, diff paging, rate/connection, asset allow-boundary/path traversal, delivery expiry/revoke và schema/pairing/ingress regressions. `npm run build` pass; lint exit 0 với 6 React effect warnings trong `App.tsx`; `py_compile` và `git diff --check` pass. Matrix [43132980438](https://github.com/jtuongdang/agentforge/actions/runs/43132980438) đã pass Tauri build, packaged sidecar smoke và artifact upload trên cả Windows x64, macOS Intel và macOS ARM64; lượt này chạy trước khi sidecar bundle viewer assets. Commit `4329593` bổ sung dashboard dist vào sidecar và mở rộng packaged smoke cho `/remote`, favicon, JS cùng owner API isolation. Matrix [43137379555](https://github.com/jtuongdang/agentforge/actions/runs/43137379555) đã pass Tauri/DMG build trên macOS Intel và installer build Windows x64 nhưng packaged smoke nhận HTTP 404 trên cả hai, nên ARM64 bị skip và chưa có artifact. Commit `139dc9d` định vị viewer dist theo thư mục giải nén của Nuitka onefile entrypoint và smoke hiện ghi URL/body của lỗi; hai focused regressions pass. Matrix macOS Intel-only [43142274793](https://github.com/jtuongdang/agentforge/actions/runs/43142274793) đã pass Tauri build, DMG, packaged viewer/assets, OS credential store, schema v8, checkpoint export/restore và process crash containment; qua đó xác nhận path viewer trong Nuitka onefile. Full matrix [43144561953](https://github.com/jtuongdang/agentforge/actions/runs/43144561953) đã pass macOS Intel + Windows x64 build, packaged smoke và artifact upload; macOS ARM64 build/smoke đang chạy. Đã thêm operator template [`REMOTE_INGRESS.md`](REMOTE_INGRESS.md) với VPN-only bind/allowlist, exact Host/HTTPS headers, WebSocket upgrade và tắt access log; placeholder phải được thay và review trước deployment. **Phase 8 chưa nghiệm thu trọn:** chưa cấu hình ingress/certificate/VPN thực tế, chưa thử hai thiết bị qua VPN, chưa xác nhận proxy access-log/route allowlist và native Tauri GUI acceptance.

## 8. Backlog Phase 0 (lịch sử, đã đóng)

Các ID UPG-001–UPG-011 là backlog từ khảo sát ban đầu. Chúng đã được triển khai và nghiệm thu trong Phase 0; giữ bảng dưới đây để truy vết nguồn gốc thay đổi, không phải danh sách việc còn mở.

| ID | Ưu tiên | Công việc | Nghiệm thu | Phụ thuộc |
|---|---|---|---|---|
| UPG-001 | P0 | Đưa workspace vào version control; rà soát conventions mới và sửa link README sai | Có baseline review được; conventions nêu rõ Python/FastAPI, React/TypeScript, SQLite và precedence; link trỏ tới file tồn tại | — |
| UPG-002 | P0 | Pin `base_sha`, `validated_head_sha`, version migration cần thiết | DB cũ migrate an toàn; SHA base không đổi khi base branch tiến lên | 001 |
| UPG-003 | P0 | Changeset toàn task và review range | Fixture nhiều commit + dirty + rename/delete/untracked được kiểm tra đủ | 002 |
| UPG-004 | P0 | Validation policy docs/custom và kiểm tra revision sau validate | `docs/runtime.py` không tự pass; custom required check không bị skip; sửa code sau test làm mất hiệu lực | 003 |
| UPG-005 | P0 | Cleanup policy, pin/ownership, bảo toàn dirty/commit và giới hạn managed root | Dirty/handoff/review/source tracked trong `target/` không bị xóa; preview đúng; recheck trước delete | 001; tích hợp 002 |
| UPG-006 | P0 | REST/WS authentication và scoped authorization | Client lạ không đọc log hay tạo task/exec/publish/cleanup; revoke có hiệu lực | 001 |
| UPG-007 | P1 | Process supervisor chung | Cancel/timeout dừng cả cây process; không trả slot trước khi process dừng | 001 |
| UPG-008 | P1 | Dependency integration và state/run invariants | B không chạy trên base thiếu code A; mọi run terminal có timestamp/reason | 002, 007 |
| UPG-009 | P1 | Kiểm tra SHA/idempotency khi publish | HEAD thay đổi bị chặn; retry sau lỗi push/PR không tự tạo tác dụng lặp | 003, 004, 006 |
| UPG-010 | P1 | Guidance theo revision và redaction thống nhất | Không dùng source checkout dirty làm context chuẩn; secret fixture không lọt log/details/report | 002, 003 |
| UPG-011 | P1 | Cập nhật baseline/docs và kiểm chứng Phase 0 | Toàn suite pass; các case §4 có regression; báo rõ lint còn tồn tại | 004–010 |

**Thứ tự triển khai lịch sử:** 001 → 002 → 003/005/006/007 → 004/008/010 → 009 → 011. Phase 0 đã xác nhận các điều kiện này bằng regression suite và CI.

## 9. Phạm vi mặc định và quyết định còn mở

Quyết định đã chốt: ưu tiên macOS arm64; reuse Python/FastAPI và React; manual review là mặc định; trial 14 ngày gắn thiết bị, không dùng Google login; remote chỉ qua VPN riêng + HTTPS ingress do operator quản lý, viewer trình duyệt chỉ đọc và full diff cần grant theo task; code/dữ liệu người dùng được giữ khi task hoặc license gặp lỗi.

Các cổng còn phụ thuộc môi trường ngoài gồm Apple signing/notarization và updater secrets, License Hub HTTPS + signing public key + URI MongoDB đã xoay vòng trong secret local/manager, cùng VPN/domain/certificate/proxy của môi trường remote. Chúng không cần mã nguồn mới để thiết kế; cần cấu hình staging và nghiệm thu trước phát hành.

Thành công của mốc đầu tiên được đo bằng **flow Desktop chạy được, thay đổi được kiểm định đầy đủ và dữ liệu được bảo toàn**, trước khi tăng số worker hoặc thêm cơ chế thương mại.
