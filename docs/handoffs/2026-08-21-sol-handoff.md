# Sol handoff — finalization day, 2026-08-21

Written by Claude (Fable 5) at the end of Bryce's last subscribed session, to be
picked up cold by Sol. Everything verified is marked as such; everything
unfinished says exactly where it stopped. Read `docs/HANDOFF.md` first for the
architecture; this file is the work ledger and the queue.

---

## Linked workspaces, provider sessions, GitHub, and IDE handoff — completed and deployed 2026-08-22

Sentry now provides one operational Work surface for the linked coding
ecosystems and exact locations Bryce asked Hermes to act on. This is live for
every user; neither users nor the operator have to install an update or run a
server command.

- The shipped functional revisions are SentryAssistant `be2e2c5` (Gateway
  compatibility fix `42e043d`, Windows node and session-title release
  `be2e2c5`) and SentryWebUI `b6a68932`. They are pushed to the private remotes
  and GitHub mirrors. The hidden Windows task is running the headless binary at
  `C:\Users\Bryce\AppData\Local\SentryAssistant\node\releases\be2e2c5\sentry-node.exe`.
- Linked sessions are grouped by Codex and Claude Code in the Chat/Work sidebar.
  Sync reads only the providers' local installation logs whose recorded working
  directory is inside the selected allowlisted workspace. Selecting a row opens
  a bounded, read-only transcript with a working **Watch live** control and an
  **Open in IDE** handoff. Injected plugin, policy, and environment context is
  excluded from titles and transcripts, so the list uses the first real user
  request instead of repeated setup text.
- The composer target control lists the owner's online linked workspaces and all
  currently allowlisted Server Control services. Choosing a machine or service
  from Chat automatically opens Work, persists the exact target on that
  conversation, and sends the validated target to Hermes. A target is context,
  not extra authority: it cannot enlarge a workspace, service allowlist,
  approval policy, or sandbox.
- The right Work inspector has live **Changes**, **GitHub**, and
  **Integrations** tabs. Changes renders the repository's unified diff and
  changed-item count. GitHub detects the linked machine's authenticated GitHub
  CLI, repository remote, branch, and current pull request when the selected
  repository has a GitHub remote. The current `server-work` and `enfusion`
  workspace roots have no GitHub remote, so their GitHub tab truthfully says
  **Not GitHub** instead of inventing repository data; GitHub CLI authentication
  is nevertheless confirmed live. Integrations shows the real outbound Hermes
  route, local Codex/Claude availability, and installed Visual Studio Code
  handoff.
- Provider OAuth remains model access, not a private-history API. Sentry can
  sync and live-view local Codex and Claude Code sessions; it does not claim to
  import ChatGPT.com, Claude.ai, or other provider-website conversations that
  those services do not expose. The boundary is stated next to the session
  list and in the inspector.
- Every workstation operation is an owner-scoped, signed, read-only work order
  claimed over the existing outbound node connection. The browser supplies
  opaque node/workspace/session identifiers, never a filesystem path. Session
  reads, diff output, and live watches are bounded; raw node paths and private
  executable locations are removed. No inbound workstation listener, new
  service, dependency, or general remote shell was added.
- Production proof found **49** eligible local sessions in `server-work`
  (**40 Codex, 9 Claude Code**), read a Codex transcript, completed a live watch,
  inspected the Git repository, detected one installed IDE, confirmed GitHub
  CLI authentication, and returned all **21** allowlisted services. The final
  title/transcript audit found **0** injected-context titles and **0** injected
  context messages. No provider-chat body or title was printed by the release
  smoke.
- Browser acceptance exercised target selection, automatic Chat-to-Work
  switching, multi-workspace sync, a real provider transcript,
  start/stop live watch, the Changes/GitHub/Integrations inspector, service
  targets, and the installed-IDE action without launching the IDE during QA.
  The final 1280 x 720 visual check kept linked sessions, work, and inspector
  legible together; DeepSeek remained the local QA default. The release image
  is `C:\Users\Bryce\.codex\visualizations\2026\08\21\01a026ad-069e-72d1-8b42-da64b642592b\sentry-integrations-release-qa.png`.
- The authoritative WebUI run completed with **15089 passed, 296 skipped, 2
  xfailed, 1 xpassed, 13 warnings, and 45 subtests passed**. The result-proxy
  regression is **4/4**, Gateway is **568 passed** plus **4/4** timestamp/schema
  coverage, and the exact SDK Windows node run is **114 total, 112 passed and 2
  deliberate opt-in skips**. The final C# release build has no warnings or
  errors.
- All six production services are running and every configured healthcheck is
  healthy. Gateway readiness reports signing, database, Hermes `0.19.0`,
  cancellation, and session search healthy. The public service worker advertises
  `hermes-shell-source-622e280571ced211` with `Cache-Control: no-store`; the
  running WebUI and Gateway integration files byte-match their clean server
  checkouts. There are zero unfinished or failed integration work orders and no
  Gateway exception in the final production window. The Windows rollout left
  exactly one Sentry node, no QA tunnel/server process, no QA listening port,
  and did not increase `cmd.exe` (`27` before the node swap, `27` after).

Two defects found by acceptance were corrected before release: the Gateway
result reader now uses the production event table's `occurred_at` column, and
the WebUI result proxy no longer shadows its URL encoder. Both were reproduced
through the live path and covered by regressions.

Minimum architecture decision: reuse the Gateway work-order queue, existing
outbound Windows node, provider-local session logs, `git`/authenticated `gh`,
the existing workspace panel, and the Server Control allowlist. One bounded
integration harness and one owner-scoped Gateway route satisfy the request
without another listener, agent, credential store, database migration, or
speculative integration platform.

---

## Profile-scoped agent behavior — completed and deployed 2026-08-22

Sentry's single signed-in profile now has a concrete purpose: it controls how
the agent behaves on new turns. The previous profile switcher that briefly
opened and immediately collapsed is gone. The update is live for every user
and requires no server or client action.

- Runtime heads are SentryAssistant `6e145d2` and SentryWebUI `5c454c75`.
  Both are pushed to the private remotes and GitHub mirrors and are the exact
  revisions built into the production Gateway and WebUI images.
- In Sentry, the left navigation, title bar, help content, and composer control
  consistently call this feature **Agent behavior** or **Behavior**. Clicking
  the composer control routes directly to the persistent editor; it no longer
  opens the irrelevant identity dropdown. Upstream Hermes multi-profile mode
  retains its original profile switcher and active-profile source of truth.
- The editor reads and writes the authenticated profile's existing `soul`
  memory section through the real profile-memory API. Saved guidance persists
  across reloads and applies to future turns. The screen explains scope,
  precedence, retry/error behavior, and the 8,000-character bound rather than
  presenting decorative capability claims.
- Hermes Chat already consumes this profile memory. Native Codex Work now
  receives the same saved behavior inside the signed work-order prompt. It is
  framed as user-authored preference data, length-bounded, and explicitly
  unable to override system/developer/project instructions, approvals,
  sandboxing, privacy, or safety rules. Provider and model defaults are
  unchanged; Hermes remains on Nous DeepSeek and no Sol route is automatic.
- Rendered interaction QA passed on desktop and a 390 x 844 mobile viewport:
  Work mode remained selected, the Behavior control stayed open, saved text
  survived reload, responsive controls remained usable, and the browser console
  was clean. Focused profile, navigation, functional-panel, and Chat/Work
  coverage is **51 passed**. Gateway is **564 passed with 1 known framework
  warning**. The authoritative Linux WebUI run completed with **15086 passed,
  296 skipped, 2 xfailed, 1 xpassed, 13 warnings, and 45 subtests passed**.
- The rebuilt production Gateway and WebUI containers are healthy. The Gateway
  readiness endpoint reports signing, database, and Hermes runtime checks as
  healthy. The live WebUI is `source-9398343a9f3f8e47`; the public `panels.js`
  byte-matches both the committed source and running container and serves the
  Agent behavior editor and save path.

Minimum architecture decision: reuse the authenticated profile-memory `soul`
field, the existing panel/composer surfaces, and the signed native Codex work
order. The release adds no profile service, persona store, provider route,
dependency, or speculative preset system.

---

## Clipboard image delivery — completed and deployed 2026-08-22

Pasted and picked images now travel through the real Sentry execution path.
The release is live for every user and requires no server-side action or manual
client update.

- Runtime heads are SentryAssistant `96258ce` and SentryWebUI `2086332d`.
  Both are pushed to the private remotes and GitHub mirrors and are the exact
  clean revisions checked out in `/srv/sentry/repo` and `/srv/sentry/webui`.
- Sentry no longer hides the existing attachment button, preview tray, or drop
  target. Pasting a clipboard image creates the same removable preview chip as
  picking or dropping it. The composer accepts PNG, JPEG, GIF, and WebP, with
  a maximum of five images and 20 MiB total per turn; rejection feedback can no
  longer be overwritten by a false **Image pasted** confirmation.
- The WebUI reads uploads only from its confined attachment inbox or the active
  workspace, verifies size, MIME type, and magic bytes, and sends validated
  image data through the authenticated Gateway turn. The Gateway repeats the
  count, size, base64, format, and magic-byte checks instead of trusting the
  browser or WebUI sidecar.
- Hermes Chat keeps Nous `deepseek/deepseek-v4-flash` as its conversational
  model. Because that DeepSeek route is text-only, the configured auxiliary
  vision route (`google/gemini-3.7-flash` through the same Nous subscription)
  first converts the image into explicitly quoted, untrusted visual reference
  data. A failed vision pass fails the turn instead of letting DeepSeek invent
  an answer without seeing the image. No Sol model or automatic route changed.
- A native Codex Work turn follows the existing signed outbound workstation
  path. Image inputs are persisted only until claim, hash-bound into the signed
  work order, cleared transactionally at dispatch, revalidated by the Windows
  node, written into a private per-turn temporary directory, and passed to the
  official Codex App Server as `localImage`. The directory is removed after the
  turn. Repository-review actions reject image input because the public review
  method has no corresponding image contract.
- Migration `017_work_order_input_images.sql` is applied. The hidden task
  **Frontir Sentry Execution Node** is running
  `C:\Users\Bryce\AppData\Local\SentryAssistant\node\releases\96258ce\sentry-node.exe`.
  Before and after the rollout, resident counts were `cmd.exe=35`,
  `node.exe=44`, with exactly one Sentry node process. The completed native
  smoke left zero temporary image directories.
- Live browser-to-DeepSeek proof uploaded a generated PNG containing the
  seven-segment code `4827`. The production stream used
  `deepseek/deepseek-v4-flash` via Nous, emitted tool and token events, and
  answered exactly `IMAGE_CODE_4827`. The temporary QA conversation was then
  deleted.
- Live native proof uploaded the same independently coloured PNG, selected
  `chatgpt-plan/gpt-5.4-mini`, `server-work`, read-only sandbox, low effort, and
  the default collaboration mode. The Windows Codex App Server answered exactly
  `NATIVE_IMAGE_CODE_4827`. Work order
  `1a686c54-3fde-43ee-8310-de28cd9e1649` reached `readyForReview`, and its
  retained image count is zero.
- Rendered QA pasted a real PNG into the composer and observed one image preview
  chip, the visible attachment tray, and no error overlay. Focused WebUI image,
  paste, and composer coverage is **34 passed**. Gateway is **561 passed**;
  Windows node tests are **111 total, 109 passed and 2 deliberate opt-in
  skips**; the C# release build has **0 warnings and 0 errors**. The authoritative
  Linux WebUI run completed with **15081 passed, 296 skipped, 2 xfailed, 1
  xpassed, 13 warnings, and 45 subtests passed**.
- All six production services are running and every configured healthcheck is
  healthy. Eight existing browser auth sessions survived. Public `boot.js`,
  `ui.js`, and `style.css` byte-match the committed sources. The live WebUI is
  `source-d57e60183f3722e1`; `/sw.js` advertises
  `hermes-shell-source-d57e60183f3722e1` with `Cache-Control: no-store`, so end
  users discover the update without an operator or user cache-clearing step.

Minimum architecture decision: reuse the existing paste/upload surface,
authenticated Gateway turn, configured Hermes auxiliary vision model, signed
work-order queue, and outbound Windows node. The release adds one bounded image
field and one authenticated vision route; it adds no new service, dependency,
credential store, inbound workstation listener, or generic file transport.

---

## Composer-first native Codex tooling — completed and deployed 2026-08-22

The full-width Codex configuration ribbon has been replaced with the
composer-first interaction Bryce requested. The release is live for every
Sentry user and does not require anyone to update or operate the server.

- The final SentryWebUI head is `4b5db869` (feature commit `00e1ac9b` plus the
  composer-structure compatibility correction). It is pushed to the private
  remote and GitHub mirror, checked out cleanly in `/srv/sentry/webui`, and
  built into the production WebUI image.
- A connected native Codex model now adds one stable `+` control beside the
  composer. The old eight-control ribbon is gone. The control and menu are
  absent for non-Codex models, so Hermes/DeepSeek Chat remains visually quiet.
- The menu uses progressive disclosure without weakening functionality:
  **Files and folders** selects the real allowlisted workstation workspace;
  **Plan mode** and **Review changes** call the existing native option path;
  **Work settings** exposes supported reasoning, file sandbox, personality,
  and approval policy; **Tools and extensions** renders the live App Server
  inventory and provides search for large installations.
- Nothing in the menu is a capability badge or mock control. The values still
  persist through the Sentry session, signed work order, Windows execution
  node, and official Codex App Server methods documented in the earlier native
  continuation. A separate Goal item was deliberately not invented because
  the current public App Server path does not expose a corresponding durable
  control.
- The production runtime currently reports 8 Codex models, 154 skills, 17 MCP
  servers, 31 plugins, 0 installed apps, and 0 hooks. The collapsed menu shows
  the 202 installed tool/extension items it can actually browse; absent
  categories remain absent. The rendered interaction was also stress-checked
  with 214 searchable rows.
- Keyboard behavior is explicit: opening `+` focuses the available workspace
  selector, Escape closes the nested inventory before the menu, and closing
  the menu returns focus to `+`. Click-outside dismissal, runtime offline
  state, disabled controls, reduced motion, and 44 px mobile targets are
  preserved. Desktop and 390 px visual checks showed no clipping; the mobile
  menu measured equal client and scroll widths after the overflow correction.
- The `ui-ux-pro-max` guidance drove the final hierarchy: prompt first, one
  clear disclosure point, real task controls first, advanced settings second,
  and inventory last. No new settings screen, registry, dependency, backend,
  or design-system layer was added.
- The first authoritative run caught one legacy composer-structure assertion:
  the new wrapper was the first nested `div`, so a historical test stopped
  finding Attach/Microphone. The wrapper was changed to an inline container
  without changing the UI. The final exact-head run completed with **15077
  passed, 296 skipped, 2 xfailed, 1 xpassed, 13 warnings, and 45 subtests
  passed**. Focused native/composer/panel coverage was 31/31 before the full
  run and the compatibility plus native slice was 71/71 after the correction.
- Production is serving image manifest
  `sha256:991eda4912e04fb861480b6444b04164915c833f2536fd56b6c352ffd6779859`,
  application version `source-249e987c6feed46f`, and service-worker cache
  `hermes-shell-source-249e987c6feed46f` with `no-store`. All six services are
  running and every configured healthcheck is healthy.
- All eight pre-existing browser sessions survived the WebUI-only recreate and
  independently returned HTTP 200 from the live model boundary. The live
  catalogue remains 299 choices: Anthropic 12, Nous Portal 278, OpenAI Codex
  8, and one Sentry route. Nous DeepSeek Flash remains present and automatic;
  no Sol model or routing default changed. The Codex runtime remains available
  with both `enfusion` and `server-work`.

Minimum architecture decision: reuse the existing composer, native option
state, persistence route, and live App Server inventory. The only new surface
is one contextual menu; there is no duplicate state or speculative feature.

---

## Functional Sentry surface and native Codex controls — completed and deployed 2026-08-22

This continuation addresses the gap between advertising features and executing
them. The Chat/Work surface, connected-model picker, native Codex controls, and
left navigation now resolve to live backends and real state. The implementation
is deployed to all users; no server action is handed to the user.

- Runtime feature heads are SentryAssistant `bd9ca89` and SentryWebUI
  `320f0701`; this handoff-only commit follows the runtime commit. Both runtime
  heads are pushed to the private remotes and GitHub mirrors and are the exact
  commits checked out in `/srv/sentry/repo` and `/srv/sentry/webui`.
- Selecting a live `chatgpt-plan/*` model automatically moves that session to
  **Work** and reveals the executable native surface in context. Workspace,
  turn/review action, Default/Plan collaboration, model-supported reasoning,
  personality, approval policy, sandbox, and review target are persisted with
  the session, signed into the work order, validated by the Windows node, and
  sent to the official Codex App Server through `thread/settings/update`,
  `turn/start`, or `review/start`. These are not decorative controls.
- The native inventory drawer is populated from the signed-in Codex
  installation through the App Server's model, skill, app, MCP, plugin, hook,
  collaboration-mode, and capability endpoints. Production currently reports
  8 Codex models, 154 skills, 17 MCP servers, 31 plugins, 0 installed apps, and
  0 hooks. Empty categories are represented honestly rather than claimed as
  installed.
- Interactive Codex approvals and multi-question input use the existing Sentry
  cards and relay exact, profile-bound responses to the waiting App Server
  request. Plans, review/diff events, tool activity, reasoning, messages,
  token/rate-limit updates, interruption, local thread continuation, sandbox,
  filesystem/worktree access, web search, images, subagents, and MCP activity
  flow through the real native event channel.
- Chat remains the Hermes assistant lane. Its automatic provider is still
  `nous`, model `deepseek/deepseek-v4-flash`, reasoning `low`. Delegation,
  compression, and background review also remain Nous DeepSeek Flash. No Sol
  model is selected automatically. Selecting any non-Codex model clears the
  native controls and routes back through Hermes.
- The model picker remains in Chat and Work, groups only linked/available
  providers, and contains 299 selectable choices. All eight preserved active
  browser sessions independently returned HTTP 200 from the live model
  boundary with the complete 300-entry API payload.
- Every left navigation destination now has a functioning production data
  source plus visible orientation, **Guide & FAQ**, and a safe **Try it** entry
  point. Work is a real six-column durable work-order board with transitions
  and details; Skills merges the installed Codex inventory; Memory exposes
  user/memory/soul state; Files shows the linked workstation workspaces;
  Profiles, Plan, and Insights/Activity use their live Gateway projections.
  Unsupported Sentry attachments and generic reasoning toggles remain hidden
  instead of implying a capability that the selected runtime cannot execute.
- The requested `ui-ux-pro-max` pass followed by the dedicated
  `ui-ux-pro-max:design` pass kept controls in the task context, clarified
  disclosure and status semantics, preserved keyboard/focus behavior, added
  44 px mobile targets, removed horizontal overflow, respected reduced motion,
  and avoided adding a second settings or feature-gallery surface.
- Migration `016_native_runtime_options.sql` is applied. The Windows task
  **Frontir Sentry Execution Node** is running the hidden release
  `C:\Users\Bryce\AppData\Local\SentryAssistant\node\releases\bd9ca89\sentry-node.exe`.
  After the final native turn, resident process counts remained `cmd.exe=22`,
  `node.exe=26`, with exactly one Sentry node process; the smoke added no leak
  or visible console process.
- Final production proof selected `chatgpt-plan/gpt-5.4-mini`, `server-work`,
  read-only sandbox, turn/default, `low` effort, pragmatic personality,
  on-request approval, and uncommitted-changes review target. The live stream
  returned HTTP 200, emitted 28 events, replied exactly
  `SENTRY_NATIVE_OPTIONS_READY`, ended with `turn.completed`, and persisted a
  `succeeded` run whose work order is `readyForReview` with every option intact.
  A preceding diagnostic deliberately injected `minimal`, which the App Server
  correctly refused because that effort cannot be combined with `web_search`;
  the live model inventory and UI offer `low` through `xhigh` for this model, so
  that invalid diagnostic combination is not user-selectable.
- Verification on the exact runtime heads: Gateway **555 passed**; Windows node
  **108 total, 0 failed, 2 opt-in skipped**; protocol contracts **114 total, 0
  failed**; C# release build **0 warnings, 0 errors**. The authoritative remote
  WebUI suite completed with **15076 passed, 296 skipped, 2 xfailed, 1 xpassed,
  13 warnings, and 45 subtests passed**. Focused native controls (11),
  functional panels (5), model registry (11), memory (13), extensions (24),
  and context (12) all passed, as did JavaScript syntax, Python compilation,
  and whitespace checks.
- Release QA briefly replaced the WebUI session file with a root-owned
  temporary copy, which made the WebUI initialize an empty store. This was
  caught immediately. The exact eight unexpired sessions were restored from
  the nightly grain backup with correct ownership/mode; stale refresh rows for
  their two device identities were revoked and each session received a fresh,
  matching access/refresh pair. Every restored session now returns HTTP 200 and
  the full model payload. The deliberately revoked QA identity stayed revoked,
  credential-bearing temporary files were removed, and end users do not need
  to sign in again.
- All six production services are running and every configured healthcheck is
  healthy. Public `/health` is `ok`, the edge is serving application version
  `source-5dfd2355726bc99b`, the production QA tabs are closed, and the local
  and remote runtime worktrees are clean.

Honest boundary: Sentry executes every relevant feature exposed by the public
Codex App Server contract and the connected local installation. It cannot
import private ChatGPT consumer UI, server-side chat history/memory, or an
unpublished OpenAI feature with no App Server API. The UI now distinguishes
reported, installed, unavailable, and unsupported capabilities instead of
presenting those private surfaces as if they were wired.

Minimum architecture decision: extend the existing session metadata, signed
work order, outbound Windows node, and existing panels. No dependency, second
queue, inbound workstation listener, generic remote shell, feature-gallery
screen, or new credential store was added.

---

## Unavailable-session recovery — completed and deployed 2026-08-22

The WebUI no longer strands someone on **Session not available in web UI.**
when a saved URL, local browser pointer, or sidebar row refers to a session that
the WebUI sidecar no longer has. Recovery is automatic; no server action is
handed to the user.

- The final SentryWebUI head is `cce47a82`. It includes the bounded recovery
  commits `5d996e57`, `bf12945b`, and `cce47a82`, is clean in
  `/srv/sentry/webui`, and is present at the same commit in the private remote
  and GitHub mirror.
- Root cause: the API correctly returned 404 for a genuinely absent session,
  but the browser retained the dead session as its active runtime state. The
  old fallback either left the pane on the permanent error or, during boot,
  left **Loading conversation…** over the otherwise fresh composer.
- A missing active session now clears only that dead runtime reference and
  immediately opens a fresh composer. A click on a different missing sidebar
  row restores the previously healthy conversation. Typed draft text and
  pending attachments are preserved through both paths.
- Boot-time stale links use the same fresh-chat fallback, but the loading
  placeholder is cleared only after a confirmed session 404. Later unrelated
  boot failures therefore cannot erase an already loaded conversation.
- No session transcript is fabricated, deleted, or silently substituted. If
  its server-side content is genuinely gone, that content remains
  unrecoverable; the correction is that Sentry no longer traps the user on the
  missing reference.
- Authoritative verification on the exact final head: **15036 passed, 268
  skipped, 2 xfailed, 1 xpassed, 4 warnings, and 45 subtests passed**. The
  focused 14-test regression suite, JavaScript syntax checks, and whitespace
  check also passed.
- Authenticated production QA opened the known-deleted route
  `/session/880a7ac9d390`. It returned to `/`, rendered **What can I help
  with?**, showed neither the unavailable-session message nor the loading
  placeholder, and produced no browser warnings or errors. The temporary QA
  session artifacts remain absent and the production QA tab was closed.
- Production is serving image manifest
  `sha256:a75cc63b6806aa30f666c184756ef1d5a181adbde9c87be4dbdfbd15f5d0f0c2`
  with application version `source-2f1019aec06deaa9`. All six services are
  running, every configured healthcheck is healthy, and public `/health` is
  `ok`.
- Model routing and subscription behavior were not changed. The automatic Nous
  route remains `deepseek/deepseek-v4-flash`; no Sol model was made automatic.
  A separate 401 seen from the deliberately revoked QA browser identity was
  confirmed at the Gateway access/refresh boundary and was not a Codex,
  Anthropic, or DeepSeek provider failure.

Minimum architecture decision: reuse the existing `loadSession` 404 handling
and boot fallback. No dependency, service, route, screen, persistent state, or
recovery store was added.

---

## Dedicated design follow-up — completed and deployed 2026-08-22

The requested follow-up with the dedicated `ui-ux-pro-max:design` guidance is
live for end users. It is a bounded refinement of the existing Chat/Work model
picker rather than a rebrand or a new settings surface.

- The final SentryWebUI head is `3248caee`. It supersedes the intermediate
  design head `1a6de7e7`, is clean in `/srv/sentry/webui`, and is present at the
  same commit in the private remote and GitHub mirror.
- The picker now has a sticky composed header: the account/runtime advisory is
  split into a strong scope statement and quieter detail, and model search stays
  available while the 299-row catalogue scrolls. The surface is 448 px on
  desktop with restrained layered shadow, contained overscroll, and stable
  scrollbar space.
- Provider and vendor headings have clearer hover feedback. The selected model
  row has a theme-token accent rail, stronger name hierarchy, and more readable
  secondary identifiers in both themes. Mobile retains 44 px search controls
  and has no horizontal overflow.
- Authenticated production QA rendered all **299** choices, with **OpenAI Codex
  (8)** first, **Nous Portal (278)** available, and
  `deepseek-v4-flash` selected. The header remained fixed after 850–900 px of
  picker scrolling; Escape returned focus to the correct desktop and mobile
  model triggers. Dark desktop, dark 390 px mobile, and light desktop captures
  are stored in
  `C:\Users\Bryce\Documents\ServerWork\output\playwright\sentry-design-pass`.
- Live QA caught and fixed one mount-order defect before handoff: the initial
  sticky wrapper was empty because its children were reparented immediately
  before the catalogue rebuild. `3248caee` keeps both controls inside the
  wrapper and includes a regression assertion for that exact failure.
- Authoritative verification on the exact final head: **15032 passed, 268
  skipped, 2 xfailed, 1 xpassed, 13 warnings, and 45 subtests passed**. The
  focused picker suite, JavaScript syntax check, and whitespace check also
  passed. A fresh production tab had no console errors.
- There are no routing or default changes. Chat, delegation, compression, and
  background review all remain provider `nous` with
  `deepseek/deepseek-v4-flash`; no Sol model is automatic. Live `/api/models`
  reports the Codex workstation runtime available with both `server-work` and
  `enfusion` workspaces.
- The release sweep found the hidden Windows bridge process connected but with
  a stale heartbeat. With zero work orders in flight, its existing scheduled
  task was restarted automatically; the same enrolled device and execution-node
  identities resumed sub-five-second heartbeats. No server action was handed
  to the user.
- All six production services are running and every configured healthcheck is
  healthy. Public `/health` is `ok`. No new QA enrollment/device was created;
  the existing signed-in browser identity was reused, the stored dark theme was
  restored after the light-only visual probe, and all QA tabs were closed.

Minimum architecture decision: use the existing picker DOM and theme tokens.
No dependency, service, screen, route, persistent state, or design-system layer
was added.

---

## Final model-picker UI pass — completed and deployed 2026-08-22

The requested final `ui-ux-pro-max` pass is live for end users. It preserves
Sentry's restrained console visual language while making the linked-model and
native Codex Work controls easier to scan, operate, and understand on desktop
and mobile.

- The final SentryWebUI feature head is `c58d3dfe`. It is committed, present in
  the private remote and GitHub mirror, pulled into `/srv/sentry/webui`, built
  into the production WebUI image, and serving at the public edge.
- **OpenAI Codex** is the first picker section, so the eight models reported by
  the signed-in native workstation runtime are immediately available. The
  large Nous catalogue is divided into collapsible vendor sections; the vendor
  containing the current selection opens automatically. This keeps Codex and
  the selected DeepSeek route visible together without removing or filtering
  any choices.
- The live authenticated picker contains all **299** available choices.
  `deepseek-v4-flash` remains selected and its `nous::deepseek` section opens
  automatically. The server defaults remain provider `nous`, model
  `deepseek/deepseek-v4-flash`; no Sol model was made automatic by this pass.
- Provider and vendor disclosures now expose their expanded state to assistive
  technology. Model rows are keyboard-operable, identify the active choice,
  and meet the mobile touch-target floor. Escape closes only the nested mobile
  picker before the parent controls and reliably returns focus to the model
  trigger on both layouts. Reduced-motion preferences are respected.
- Selected, native-runtime, primary, and fallback badges now use theme tokens
  instead of dark-only colors. The final interface was visually checked in
  desktop dark, desktop light, and mobile dark layouts; the user's stored dark
  theme was restored after the non-persistent light-theme check.
- Native Codex controls now say **Workspace on your machine**, report runtime
  state through a live region, expose correct disclosure semantics, and provide
  clear focus, disabled, busy, success, and failure behavior without adding a
  new screen or settings workflow.
- Authoritative verification on the exact final head: **15031 passed, 268
  skipped, 2 xfailed, 1 xpassed, and 45 subtests passed**. JavaScript syntax and
  whitespace checks also passed. Live desktop and mobile probes both rendered
  all 299 rows, placed Codex first, kept DeepSeek selected at scroll position
  zero, and returned focus to the correct trigger after Escape.
- The temporary browser identity used for authenticated production QA was
  revoked through the Gateway API, including immediate in-process denial; both
  of its refresh-token rows are revoked. Temporary QA tabs are closed. Two
  unused pairing codes remain only as expired audit records and cannot be
  redeemed.

Minimum architecture decision: refine the existing picker, native-runtime
controls, and theme tokens. No dependency, service, route, screen, persistent
store, or design-system layer was added.

---

## Native Codex Work continuation — completed and deployed 2026-08-22

This continuation supersedes the earlier statement that an OpenAI Codex link
contributes models only. Selecting a connected `chatgpt-plan/*` model now moves
that Sentry session into **Work** and runs the turn through the official Codex
App Server on Bryce's own workstation. DeepSeek and every non-Codex selection
continue to run through Hermes exactly as before.

- Runtime feature heads are SentryAssistant `f8add52` and SentryWebUI
  `c0e3ad92`; this handoff-only commit follows `f8add52`. Both runtime heads are
  in the private server remotes and live checkouts. Migration
  `015_native_codex_relay.sql` is applied to production.
- The hidden Windows node is installed and running from release `f8add52`. It
  starts the npm package's native `codex.exe` directly—never a Store alias,
  `cmd /c`, or `npx` wrapper—and keeps the workstation outbound-only. There is
  still no inbound listener or arbitrary client-supplied path.
- The local App Server is the authoritative model catalogue. The live picker
  exposes all eight models it reported:
  `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`,
  `gpt-daybreak-blue-latest`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, and
  `gpt-5.3-codex-spark`. Newly reported models appear without relinking. The
  complete Codex section remains hidden when the Sentry subscription link or
  live workstation runtime is unavailable.
- Native Work retains the App Server's Codex thread and project-instruction
  behavior and surfaces its streamed lifecycle, plans/review/diffs, tool
  activity, token/rate-limit events, approvals, multi-question input, MCP
  elicitation, skills, apps/connectors, MCP servers, plugins, sandbox,
  filesystem/worktrees, web search, images, subagents, hooks, configuration,
  and interrupt capability. A Sentry session is durably mapped to its local
  Codex thread so later turns resume the same native session.
- Only the named `server-work` and `enfusion` roots can be selected. Local paths
  are sanitized before event relay. Chat cannot select a Codex model; native
  Codex is Work-only, with `workspace-write` inside the selected allowlisted
  root and App Server `on-request` approvals. Approval/question responses are
  profile-bound, exact-request, audited, idempotent, and sent back through the
  node's existing outbound long poll.
- Stopping or closing a live stream now cancels the durable work order, signals
  the node, calls native `turn/interrupt`, and force-cleans the direct App
  Server child tree if graceful interruption does not finish. The locally
  persisted thread mapping survives node and browser restarts.
- End-to-end production proof selected `chatgpt-plan/gpt-5.4-mini` in
  `server-work`, streamed real App Server MCP/thread/turn/item/token events, and
  returned exactly `NATIVE_CODEX_READY`. The temporary desktop QA identity was
  revoked; the successful native work order is durable as `readyForReview`.
- Verification: Gateway **549 passed**; Windows node **106 total, 104 passed
  and 2 deliberate opt-in skips**; affected WebUI slices **173 passed**; the
  authoritative full grain WebUI suite finished with **15054 passed, 296
  expected skips, 2 xfailed, 1 xpassed, and 45 subtests passed**. The public
  sign-in shell rendered with no browser-console errors. Gateway and WebUI are
  healthy, and the public edge responds normally.
- Process hygiene is unchanged after the real App Server turn: resident
  `cmd.exe` stayed at 8 and `node.exe` stayed at 10. The two visible
  `codex.exe` processes predate this release and belong to the Codex desktop
  app; the per-turn npm App Server process and its descendants were reaped.

Honest product boundary: Sentry embeds the native capabilities OpenAI exposes
through the public Codex App Server protocol. It does not claim to clone or
import proprietary ChatGPT UI, consumer chat history/memory, or any private
service that OpenAI does not expose to third-party clients. Within that public
surface, Codex models are no longer Hermes model proxies—they use the signed-in
local Codex runtime and its native tools/features.

Minimum architecture decision: extend the existing signed work-order store and
outbound workstation node with one App Server adapter, ordered event rows, and
exact response rows. No second queue, daemon, inbound port, model proxy, or
credential store was added.

---

## Chat/Work and linked-model continuation — completed 2026-08-22

This continuation is deployed to end users, mirrored to both remotes, and
verified against the live authenticated Sentry catalogue.

- Runtime feature heads are SentryAssistant `3ca953a` and SentryWebUI
  `ae53c578`. The live checkouts are `/srv/sentry/repo` and
  `/srv/sentry/webui`; this documentation update follows the runtime commit in
  SentryAssistant only.
- Sentry now has explicit **Chat** and **Work** lanes. New Sentry sessions start
  in Chat. Existing sessions retain their recorded lane, and legacy sessions
  without a lane remain Work so an old executable session is never silently
  downgraded or misrepresented.
- Chat is the conversational-assistant lane. The browser removes workspace,
  terminal, code, browser/computer, plugin/MCP, delegation, scheduling, and
  workstation affordances. The Gateway also enforces the boundary on every
  request: it sends only a bounded safe candidate set, and Hermes intersects
  that set with the operator-enabled toolsets. A hidden child session, fork,
  compression recovery, crafted request, or stale browser cannot promote Chat
  to Work.
- Work is the execution lane. It retains the profile-approved Hermes skills,
  plugins/MCP tools, workspace, and the existing outbound Windows execution
  node. Gateway remains the signed ingress/egress and audit boundary; no second
  queue, inbound workstation listener, or arbitrary path/shell was added.
- The model picker lives directly in both lanes and groups models by linked
  account. A provider section appears only while that account is connected.
  Model choices still route through Sentry, so provider credentials never enter
  the browser. The Agent subscription cards now open this shared picker instead
  of duplicating selectors or requiring a separate **Use in chat** action.
- Provider OAuth contributes model routes, not a provider application's private
  skills or plugins. Sentry/Hermes continues to own and govern skills, plugins,
  memory, workspace, and workstation access. The UI states this boundary in the
  picker and the per-lane **Access** explanation.
- DeepSeek remains the automatic selection:
  `deepseek/deepseek-v4-flash` is still configured for chat, delegation,
  compression, and background review. The user can choose any visible linked
  model per session without changing those defaults.
- Authenticated live `/api/models` verification returned Chat/Work with Chat as
  the default, `catalog_restricted=true`, DeepSeek present, and these visible
  sections: Anthropic 12, Nous Research 278, OpenAI Codex 11, and one Sentry
  friendly route. Disconnected xAI and MiniMax sections are absent rather than
  being relabelled as generic Sentry routes.
- Verification: Gateway **542 passed**. The final full Linux WebUI suite was
  **15041 passed, 296 skipped, 2 xfailed, 1 xpassed, and 45 subtests passed**.
  Browser QA used the production HTML/JS/CSS at 1440x1000 and 390x844; Chat had
  no workspace surface or arbitrary custom-model field, Work retained its
  access surface, the grouped picker fit without horizontal overflow, and
  DeepSeek remained selected.
- Production was rebuilt from those exact heads. Gateway, Hermes, WebUI,
  Postgres, and Server Control are healthy; cloudflared is running; Gateway
  readiness reports Hermes 0.19.0 healthy. The built Hermes image contains the
  request-scoped toolset patch, validation cap, configured-toolset
  intersection, and agent-thread propagation.
- The public service worker returns 200 with `Cache-Control: no-store` and cache
  key `hermes-shell-source-9078c2e6769e51c3`. Hosted assets contain the
  Chat/Work switch, Access explanation, and **Choose in Chat or Work** copy, so
  installed and browser clients discover this release without a manual cache
  clear.

Minimum architecture decision: reuse the Gateway policy boundary, Hermes
runtime, existing outbound workstation node, session record, and model picker.
No dependency, service, persistent store, or execution path was added beyond
what the requested separation requires.

---

## Sol continuation — completed later on 2026-08-21

- Gateway cron hardening commit `eb50e5d` was rebuilt and deployed. Readiness
  passed; invalid schedule creation returned 422; a throwaway every-minute job
  fired through Hermes, recorded `ok` and the exact `CRON_OK` summary, then was
  deleted.
- WebUI review fixes were recovered from `stash@{0}`, completed across all 15
  locales, committed as `3afce1d4`, mirrored to GitHub, rebuilt, and deployed.
  The four `test_sprint3.py` workspace failures were proven to be Windows-only:
  they reproduced at clean `df1ac48f` on Windows and passed on grain. Focused
  verification was 348 passed plus the four known Windows failures; the
  browser-layer harness was 39/39. The required full grain suite finished with
  **14993 passed, 268 environment-dependent skips, 2 xfailed, 1 xpassed, 0
  failed, and 0 errors**.
- The authenticated Nous catalogue exposed 373 entries. The picker now carries
  the complete interactive agent subset: 278 entries that advertise tool use
  and are not batch-only. Embeddings and batch models are deliberately absent.
  The current short default alias remains, so Hermes and Gateway each advertise
  280 unique entries including `hermes-agent`. Owner and teammate seeds were
  committed as `0d1278f`; the live owner config was updated with a dated backup.
  A real Gateway turn selected `openai/gpt-5.6-sol` and completion evidence
  reported that exact model.

The immediate queue in sections 3 and 4 is complete. Section 6 remains an
optional follow-on polish queue; Kanban remains the separately scoped product
decision described in section 5.

Follow-up: at Bryce's request, chat, delegation, compression, and background
review now default to the least expensive interactive DeepSeek route,
`deepseek/deepseek-v4-flash`. `openai/gpt-5.6-sol` and its `-pro` variant were
removed from the owner live registry and both provisioning seeds. The earlier
Sol turn above remains historical verification evidence only; it never changed
the default.

Latest preference clarification: both Sol routes were restored to the optional
picker so it again mirrors all 278 interactive Nous models. DeepSeek remains the
default for every automatic path; restoring catalogue visibility did not alter
the selection.

### End-user rollout — complete

- The current runtime feature heads are SentryAssistant `9dd0f7c` and
  SentryWebUI `42840d6a` (followed only by documentation records in
  SentryAssistant).
  Both are present in the server bare repositories, live checkouts, and GitHub
  mirrors.
- Existing owner runtime configuration and both provisioning seeds expose the
  same 278-model interactive Nous catalogue plus the friendly alias. Hermes and
  Gateway each expose 280 picker choices including `hermes-agent`.
  `deepseek/deepseek-v4-flash` remains selected for chat, delegation,
  compression, and background review. Sol is optional only.
- WebUI `38da7f4e` closes the hosted browser-update gap: Compose builds now
  derive a deterministic source version when no release tag is supplied. The
  public service worker is live with cache key
  `hermes-shell-source-37528cc811798a79`, versioned asset URLs, and
  `Cache-Control: no-store`, so existing installed/browser clients discover the
  new bundle rather than remaining on `hermes-shell-unknown`.
- Final live verification: all six services are running; Gateway, Hermes,
  Postgres, server-control, and WebUI are healthy; public `/sw.js` returns 200;
  the WebUI PWA regression suite is 47/47. The pre-existing `stress` runtime is
  an isolated test profile and is not an end-user profile.

### Provider connection and workstation continuation — complete

The two follow-up requests that arrived after the original ledger are also
deployed, mirrored to GitHub, and live for end users.

- SentryAssistant `366460f` and SentryWebUI `1b94347e` replace the Agent panel's
  server chores with in-app provider connection flows. Nous, OpenAI Codex, xAI,
  and MiniMax use device/browser OAuth; Anthropic retains its browser-plus-paste
  flow. The browser receives only the public URL/code and status. OAuth tokens,
  CLI output, and child processes stay inside Hermes. Cancel reaps the process,
  and repeated Connect cancels the prior flow. Qwen is shown as retired rather
  than offering its discontinued OAuth command.
- SentryAssistant `9dd0f7c` closes the remaining post-login gap: a successful
  connection now publishes that account's chat-model routes immediately,
  persists the managed routes atomically, and removes them from the live
  picker on disconnect. Existing authenticated accounts are activated on the
  first provider-status read. No config edit or Hermes restart is part of the
  user flow. Nous retains the deployment's filtered 278-model interactive
  catalogue rather than importing its broader batch/embedding catalogue.
- SentryWebUI `42840d6a` replaces credential-centric cards with **Your AI
  subscriptions**. Each connected account has one model selector and one **Use
  in chat** action. Rendering or connecting an account does not change the
  conversation model; DeepSeek stays selected until the user explicitly picks
  another model. Route-publication failures remain visible instead of reporting
  the account as ready.
- The hosted JS bundle now contains the OAuth start/poll/cancel flow and contains
  neither `Sign in on the server` nor `hermes auth add`. A live OpenAI Codex
  device flow reached `auth.openai.com`, returned a public user code, cancelled,
  and left zero OAuth child processes. No credential or code was printed during
  verification.
- SentryAssistant `dc04616` adds a single outbound Windows execution node to the
  existing signed work-order store. `fb5f341` binds the bridge to the profile
  actually returned by enrollment instead of the deployment's historical
  runtime bootstrap UUID. There is no inbound listener, second queue, arbitrary
  path, or general shell.
- Bryce's machine is installed at the current-user local app-data boundary as a
  hidden, limited scheduled task named `Frontir Sentry Execution Node`. Its
  rotating access/refresh credentials and work-order signing key are protected
  with Windows DPAPI. It starts at logon, restarts after failure, and survived a
  manual stop/start with the same node identity.
- Only `server-work` and `enfusion` are exposed. Each advertises `shell` and
  `claude`, with `readOnly` and bounded `workspaceWrite`; read-only is the chat
  default and write mode is only for an explicit file-change request. Local
  paths never leave the node. Elevated work, credential access, deletion,
  network shell, git push/reset/clean, and arbitrary process control remain
  unavailable.
- Live DeepSeek verification used model `deepseek-v4-flash`. The model called
  `workstation_status`, then dispatched a read-only `git rev-parse
  --is-inside-work-tree` work order through `workstation_run`. Work order
  `ce5585de-1526-4ae5-b506-3653e95e864c` reached `readyForReview`, returned
  `true`, and was reported as succeeded. The node has one established IPv6
  loopback connection to Gateway port 8090 and zero listening sockets.
- Verification totals for this continuation: Gateway **533 passed**; execution
  node **103 total, 101 passed and 2 opt-in smoke tests skipped**; focused
  provider bridge **33 passed**; focused WebUI OAuth/model surfaces **63 passed**
  locally and **24 passed** on grain. Compose validation and self-contained
  Windows publishing passed. All six production services are running; Gateway,
  Hermes, WebUI, Postgres, and Server Control are healthy.
- Subscription-usability verification: Gateway **537 passed**; the complete
  grain WebUI suite finished with **15033 passed, 296 expected skips, 2 xfailed,
  1 xpassed, and 45 subtests passed**. Desktop, 390px, sign-in, and explicit
  model-selection browser passes had no product console errors or horizontal
  overflow. After rollout all services are running and the five healthchecked
  services are healthy. Hermes advertises 280 unique choices; Nous is connected
  with 278 selectable models and no route error. Chat, delegation, compression,
  and background review all remain `deepseek/deepseek-v4-flash`. The hosted PWA
  cache is `hermes-shell-source-4ef260af074a0d4c`.

---

## 1. State of the world (verified end of day)

- **Live and healthy on grain** (`bishop@205.209.116.114`): gateway, webui,
  hermes (with healthcheck), postgres, cloudflared, server-control-mcp. Public
  edge `https://sentry.frontir.solutions` serves the current bundle.
- **Deployed feature heads**: SentryAssistant `9dd0f7c` (branch
  `25vid/sentry-foundation`, followed only by handoff documentation) and
  SentryWebUI `42840d6a` (branch `frontir`) — mirrored across the local clone,
  `/srv/git/*` bare repos, `/srv/sentry/*` checkouts, and GitHub.
- **Current test state**: Gateway **537 passed**; the complete grain WebUI run
  finished with **15033 passed, 296 expected skips, 2 xfailed, 1 xpassed, and
  45 subtests passed**. The public subscription JS and service worker both
  return 200; the old server-command copy is absent and the new cache is served
  with `no-store`.
- **Unattended operation is armed**: `sentry-update.service` (boot; fetches the
  `github` remote explicitly — push to GitHub, restart the unit or reboot, and
  grain rolls forward with no workstation), `sentry-import.timer` (daily
  session import, verified firing), `grain-backup.timer` (nightly; now includes
  `sentry-hermes-data` = the agent's home/memory/workspace, and `sentry.env`;
  restore listing verified — 1741 files).
- The cron scheduler is live and was proven end-to-end: a real job fired
  through Hermes/deepseek and recorded `ok` + the reply in `last_summary`.

## 2. What shipped today (all deployed unless marked otherwise)

1. **Upstream merge**: hermes-webui `exp-v0.52.121 → exp-v0.52.260` (541
   commits) merged into `frontir` (d4084971), after reconciling the diverged
   local/server lines (3c882db8). Semantic invariant re-verified: sentry turns
   fail closed without a per-user token; no shared-key fallback.
2. **Every dead sentry panel wired or honestly gated** (39095d90 + follow-ups):
   Insights renders the Gateway envelope; Memory reads/writes the Gateway
   store; Tasks/cron has full CRUD **and a Gateway scheduler that actually
   fires jobs** (27f1135, e865061, migration 013, `SENTRY_CRON_TIMEZONE=
   America/New_York` in live .env + compose); Agent panel gained the Inbox
   (list/mark-read/send + Gateway mark-read route); Kanban retired wholesale on
   501 (df1ac48f) — nav hidden, one message, pollers stopped; Skills is a
   read-only governance view; Settings model/provider/profile mutations 501
   with the reason; real token usage from completion evidence.
3. **Provider flip committed**: Nous Portal OAuth / `deepseek-v4-flash-0731`
   default, raw Nous proxy for Tardia on `127.0.0.1:8645`, `start-hermes.sh`
   pair-reaping (988bc57) — was live-only drift, now in git.
4. **Deploy-loop traps fixed**: server checkouts now track `origin` (they
   tracked `github`, so `git pull` after a push to origin silently found
   nothing); `conftest.py` nulls `GIT_CONFIG_GLOBAL/SYSTEM` (bishop's
   `tag.gpgsign=true` was the REAL cause of the "19 environmental errors"
   blamed on upstream since July).
5. **Adversarial review pass** (three agents: WebUI diff + dead affordances,
   gateway scheduler, deployment/logs/backups). Deployment audit: all green;
   backup gap closed same day. The other two produced the queues below.

## 3. Completed continuation work (historical task brief)

Everything in this section is deployed. The original task detail remains below
as an audit trail; it is not an active queue.

### 3a. Gateway: commit `eb50e5d` — deployed and verified

Fixes all five review-confirmed cron bugs, 515 tests green. Migration
`014_cron_claim.sql` is applied to the live database. The Gateway was rebuilt,
and invalid-schedule rejection plus a real every-minute Hermes job were
verified end to end.

1. Schedules validated on create/update (422 on garbage; before, an invalid
   schedule was stored enabled and silently never fired).
2. Fall-back DST: the in-memory fired-minute guard is now a UTC instant
   (PEP 495 made the two local 1:30s compare equal, swallowing one).
3. Completion is terminal: a malformed trailing SSE frame can no longer flip a
   completed run to `failed`.
4. Manual runs share the scheduler's per-job guard (409 when busy) and the
   schedule claim moved to a new `claimed_minute` column (migration 014,
   already applied live) — so a manual run can't eat that minute's scheduled
   fire.
5. Each tick is bounded (`asyncio.timeout`, 120s) so a wedged DB logs and
   retries instead of freezing the loop forever. (Note: `asyncio.wait_for`
   specifically was avoided — 3.11 swallowed-cancellation bug hung the suite.)

### 3b. WebUI review fixes — deployed and verified

The second-wave review fixes were recovered from the historical stash,
completed across all locales, committed as `3afce1d4`, and deployed. They
include:

- `_sentry_cron_view` ships `schedule_display` + object `schedule
  {kind, expression}` — without this, **editing any Gateway job shows an empty
  required schedule field** (worst UX bug found).
- `/api/default-model` 501-gated under sentry (it escaped the settings gate —
  Save toasted success into a config no runtime reads).
- Attachments warn-and-strip on sentry turns (they were silently dropped while
  the transcript rendered them).
- Run-now timeouts raised (server 300s, client 310s — a >15s job read as
  "gateway unreachable" while it succeeded).
- Inbox: 401-no-identity renders as unavailable, not "No messages"; send
  success keyed on a guaranteed `delivered` flag.
- Embedded **terminal 403-gated under sentry — security fix**: it opens a
  shell in the SHARED WebUI container (env, other users' server-side state).
- Profiles: `single_profile_mode` + `profiles_backend` flags; no more false
  "Gateway stopped" dots or "API key: Not configured" rows.
- Providers pane: sentry-managed message instead of a dead credential catalog.
- Cron form/detail consume `cron_backend`: only Name/Schedule/Prompt render
  under sentry (the deliver/model/skills/toast selectors LOOKED live and were
  dropped server-side); false "gateway not configured" banner suppressed; new
  `Latest status` row. Two new i18n keys in ALL 15 locales
  (`cron_last_status_label`, `cron_sentry_form_hint`) + `providers_sentry_note`
  (en-only as stashed — **add the other 14 locales or the parity tests fail**).
- Regeneration token tests added to `test_sentry_token_no_shared_fallback.py`
  (the registered-session-token path regeneration depends on was unpinned).

The four workspace-validation failures were reproduced at the clean prior head
on Windows and passed on grain, confirming an environment-only discrepancy.
The browser-layer harness passed 39/39 and the required full grain suite
finished with zero failures or errors.

## 4. Full Nous model catalogue — completed

The picker now exposes the full interactive, tool-capable Nous catalogue while
keeping DeepSeek selected. The implementation deliberately uses the existing
route registry; no second catalogue or selection system was added.

The end-user path is now entirely inside Sentry:

1. Open **Agent → Your AI subscriptions** and choose **Connect**.
2. Complete the provider's secure browser/device flow. Anthropic uses the same
   browser flow with a pasted confirmation value.
3. When the account says **Ready**, choose one of its models and select **Use in
   chat**.

That is the complete workflow. Users are not asked to SSH, run a Hermes command,
edit YAML, or restart a service. DeepSeek stays selected unless they perform
step 3. Disconnecting an account immediately removes its routes from the live
model list.

Internally, the route table remains the single integration registry
(`docs/plans/2026-08-18-model-routing-design.md`). The admin bridge activates and
persists provider-owned route blocks after OAuth, while Gateway continues to
reject unadvertised models rather than silently substituting another backend.
The legacy `deploy/linux/attach-subscription.py` remains only as an operator
break-glass recovery tool; it is not a support instruction or normal onboarding
path. `deploy/linux/register-hosted-models.py` is unrelated (it registers a
Server Control dashboard tab, not model routes).

## 5. Kanban board source — recommendation on record

If Bryce wants a real board: **project it from `work_orders`**. Migration
`003_workorders.sql` says it outright: "a runtime work board is a rebuildable
projection keyed to work_orders.id." The 13-state machine maps onto columns,
transitions are audited, and routes exist for create/get/transition. Missing:
a `GET /api/workorders` list route, a board projection behind the Gateway's
`/api/kanban/board`, and wiring card-moves to `POST /{id}/transition`. The
WebUI's kanban retirement is response-driven, so the panel lights back up on
its own once the routes stop 501ing. Do NOT build a second task store
(two-registries drift), and don't wait for a Hermes board plugin (the runtime's
probe of `/api/plugins/kanban/board` 404s on current builds). Follow-on that
makes it sing: a bounded agent tool to list/transition its own orders, so the
agent can move its card to `readyForReview` — same pattern as Server Control
MCP. This also gives Tardia's filed work orders (which pile up as invisible
drafts) a face.

## 6. Remaining review findings — accepted, smallest first

From the WebUI dead-affordance sweep (none regress anything; all are "panel
shows local-container affordances under sentry"):

- **Settings default/aux model sections**: saves now 501 honestly (after 3b),
  but the sections still render; hide them under sentry. The cleanest dialect
  signal for Settings JS: the sentry `/api/models` envelope has
  `active_provider === "sentry"`, or inject a `__WEBUI_DIALECT__` token via
  `_render_index_shell_base()` (it already substitutes process constants).
- **Workspaces panel / composer workspace chip / file tree**: operate on the
  WebUI container's filesystem; a sentry turn transmits no workspace. Hide
  under sentry.
- **Todos panel**: fed by local `todo_state` events the sentry translation
  never emits; permanently empty. Hide.
- **Extensions / Plugins / MCP settings**: local-agent-only integration;
  toggles succeed locally, never affect a sentry chat. Gate or hide. Low.
- Cosmetics: memory-unavailable branch leaves stale detail content in the main
  view; Insights still fires `/api/wiki/status` + `/api/skills/usage` it
  ignores; usage harvesting drops digit-string token counts (accept
  `isinstance(int) or digit-str`); `agent_admin.js` inline-onclick id
  interpolation would be cleaner as data-attributes + listeners.

From the gateway review, documented-not-fixed (in the scheduler docstring):
spring-forward jobs in the skipped 02:00–02:59 hour don't fire that day (no
Vixie catch-up); stepped-star day fields (`*/2` in dom) count as "restricted"
for the dom/dow OR rule, diverging from Vixie. Also: cross-process *overlap*
(not double-fire) is possible with two gateways; DB/app clock skew beyond the
tick offset could theoretically reopen a same-minute race — one DB, one
`now()`, low risk.

Upstream PRs worth pulling when they merge: **#6829** (empty chunked POST
bodies behind proxies — most relevant open PR to this deployment), **#7105**
(SSE half-open thread leak; branch `fix/7105-sse-subscriber-lease`), #7088
(PWA false-offline recovery).

## 7. Runbook (the short version; details in docs/HANDOFF.md)

```bash
# Deploy loop (push from workstation to origin, then on grain):
cd /srv/sentry/webui && git pull --ff-only && git push github frontir
cd /srv/sentry/repo  && git pull --ff-only && git push github 25vid/sentry-foundation
cd deploy/linux && docker compose build gateway webui && docker compose up -d gateway webui

# Migrations (idempotent, out-of-band; no migrations table — check objects):
docker exec -i sentry-postgres-1 psql -U sentry -d sentry \
  < /srv/sentry/repo/server/sentry_gateway/migrations/014_cron_claim.sql

# Tests:
#   gateway (fine anywhere): server/sentry_gateway/.venv python -m pytest
#   WebUI FULL suite: ONLY on grain (/srv/sentry/webui/.venv). On Windows:
#   single files only, one invocation at a time, output to a file (AGENTS.md).
# Workstation-less updates: push to GitHub, then on grain
#   sudo systemctl restart sentry-update.service   # or just reboot
```

Landmines inherited and new: IPv6 loopback forwards only (`[::1]`) on Bryce's
machine; the live Hermes config is `data/hermes/personal/config.yaml`, never
the repo seed; every new UI string needs ALL 15 locales (parity tests) and
`{0}`-bearing keys must sit after each locale block's `_label` line; kanban
501s are load-bearing (they trigger the panel retirement); `/wyvrn/Synapse`
403s in gateway logs are Bryce's Razer Synapse port-scanning through the
tunnel — ignore; the `sudo ls /root-dir/glob*` trap (calling shell expands the
glob) — wrap in `sudo bash -c`.

Good luck. The platform is self-sufficient — reboot-safe, self-updating from
GitHub, backed up nightly. What remains is polish and the two queues above.
