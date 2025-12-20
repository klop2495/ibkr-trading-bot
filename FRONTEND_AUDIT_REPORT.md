# Frontend Audit Report — /admin/control (Codex Task #7.5.1)

## Executive Summary
- Admin/control uses owner-gated server components plus API route guards; no service_role exposure. 
- UI is mostly RU-localized but has residual English/error strings and fragile stringification that can render `[object Object]`.
- API contracts are loosely typed: 200 responses on errors, mixed payload shapes (`previews` vs `data`), and missing null guards on date fields can break rendering.
- Lint/build not clean: lint reports numerous `any`s and an unsafe effect in login; build fails with `.next` EPERM.
- Data contract drift: chain endpoint returns raw Supabase rows; UI assumes JSON arrays/strings; null/undefined fields may break formatting.

## Route Map
- Pages: `app/admin/control/page.tsx` (server, owner gate) → `ControlDashboard.tsx` (client).
- API routes: 
  - `app/api/admin/control/previews/route.ts` (filters: symbol, recon_status, flag_contains; returns `{previews, warnings}` 200 even on errors)
  - `app/api/admin/control/meta/route.ts` (symbols/flags/recon_statuses via service role)
  - `app/api/admin/control/chain/route.ts` (preview_id → preview/decision/verdict; always 200 with warnings)
- Auth: `lib/authz.ts` (requireUser/isOwner), `app/auth/callback/route.ts`, `app/login/page.tsx` (client login flow).

## Content-type / Rendering Risks
- Previews route returns 200 with `{error:..., warnings}` even on exceptions; UI treats as success and may render empty silently (previews route catch block).
- Chain route returns 200 with warnings even on Supabase errors; UI shows empty cards without clear error unless `chainError` is set (ControlDashboard lines ~120).
- DetailCard uses `String(value)` (ControlDashboard ~470) → objects render as `[object Object]` if backend adds nested data.

## Data Contract Risks
- UI accepts either `{previews}` or `{data}`; any other shape renders empty silently (ControlDashboard loadPreviews).
- Chain route returns raw Supabase rows (camel/snake mixed) and does not null-guard ts_utc; UI calls `new Date(chain.decision.ts_utc)` without fallback (ControlDashboard ~340) → “Invalid Date” rendering if null.
- Flags may be null in DB; chain route does not coerce; UI partially guards (Array.isArray) but DetailCard flags fallback text only if flags array missing.
- Previews API returns 200 on errors (UNEXPECTED_ERROR) with empty previews; UI interprets as success and may auto-select null → chain fetch with null id ignored.

## Owner Gate / Auth
- Server-side gate in `app/admin/control/page.tsx` compares user.id to `BOT_OWNER_USER_ID`; redirect on unauthenticated. Good: no CSR-only flash.
- API routes also gate via requireUser/isOwner. No service_role exposure to client.

## Localization
- Most strings RU; residual English/tech strings: error block title “Error” replaced earlier, but warnings (“CHAIN_FETCH_FALLBACK”, etc.) shown raw; meta error message in RU but uses exception text.
- Login page error text in EN (“Authentication failed, please try again.”).
- Breadcrumb arrows remain ASCII “→”; acceptable but note.

## Lint/Build Checks
- `npm run lint`: fails with 21 errors (`any` types in control dashboard and API routes; unused vars) and one hook warning in login (setState in effect).
- `npm run build`: fails EPERM on `.next/trace-build` (ownership/permissions issue).
- No tests configured for frontend.

## Findings (with evidence)
1) `.insert().select()` lint errors blocking CI: `app/admin/control/ControlDashboard.tsx` multiple `any` (lint fail).  
2) Chain route uses `any` and unused errors (lint fail) `app/api/admin/control/chain/route.ts:36-81`.  
3) Meta route `any` usage (lint fail) `app/api/admin/control/meta/route.ts:45-51`.  
4) Previews route `any` usage (lint fail) `app/api/admin/control/previews/route.ts:50,79,93`.  
5) Login page setState inside effect (react-hooks warning) `app/login/page.tsx:15` – potential cascading renders.  
6) Previews API returns 200 on UNEXPECTED_ERROR; UI treats as success → silent failure `app/api/admin/control/previews/route.ts:101-107`.  
7) Chain route returns 200 even when preview_id missing/error; UI may show empty without actionable error `app/api/admin/control/chain/route.ts:20-40,63-85`.  
8) DetailCard stringifies values with `String(value)` causing `[object Object]` if backend adds structured fields `app/admin/control/ControlDashboard.tsx:470`.  
9) Date parsing without null guard (`new Date(chain.decision.ts_utc)`) can render “Invalid Date” when ts_utc missing `app/admin/control/ControlDashboard.tsx:337-358`.  
10) Raw warning codes shown to user (e.g., `CHAIN_FETCH_FALLBACK`) without human RU description `ControlDashboard` warnings blocks ~310, ~322.

## “Причина бессмысленного набора строк”
- Detail rendering uses `String(value)` on arbitrary values (DetailCard) → objects show as “[object Object]”, perceived as garbage text.
- Warning codes from backend displayed verbatim (e.g., `UNEXPECTED_ERROR`, `FLAG_FILTER_FALLBACK`) without translation, leading to meaningless strings in UI.
- API error bodies may surface HTML/body snippets if content-type misreported; fetchJson guards with generic “Неверный ответ сервера”, providing no actionable info.

## Recommended Next Steps (no code here)
- Fix lint errors by typing responses (`ChainPayload`, Supabase rows) and removing unused vars; adjust login effect pattern.
- Add user-facing RU messages mapping warning codes to readable text; avoid String(value) on complex objects.
- Harden date/flag null checks in detail cards.
- Address build EPERM by cleaning/chmod `.next` or adjusting build dir ownership.
- Consider contract tests or schema typing for API responses to avoid silent 200-with-error patterns.

## Commands Run
- `npm run lint` (fails; see errors above).
- `npm run build` (fails: EPERM open .next/trace-build).

## Self-Audit
- No backend changes; no dependencies added; no secrets exposed. Only audit artifacts created.
