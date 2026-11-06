# Third-party notice: shadcn/ui components

`shadcn-components.tar.gz` holds 45 files of shadcn/ui registry output: 43
`.tsx` components, `lib/utils.ts` and `hooks/use-toast.ts`.

## Where this archive came from

- Distribution: `skills/web-artifacts-builder/scripts/shadcn-components.tar.gz`
  in [anthropics/skills](https://github.com/anthropics/skills), verified
  byte-for-byte at public commit
  [`ef740771ac90`](https://github.com/anthropics/skills/commit/ef740771ac901e03fbca3ce4e1c453a96010f30a)
  (2025-12-01; that commit is where this path appears after an upstream
  restructuring, not a claim about when the components were created). This
  copy is that file byte for byte:
  sha256 `1296f069e825de7933de7e8ac63672495cacce3f88f2dc45554dbcddbcbca60f`.
- Copyright: the components are shadcn/ui registry output. shadcn/ui is MIT
  licensed, `Copyright (c) 2023 shadcn`. The full text is in
  [SHADCN-UI-LICENSE.txt](SHADCN-UI-LICENSE.txt), copied verbatim from
  <https://raw.githubusercontent.com/shadcn-ui/ui/728cb4cfa533884521eb8450be63ce70d67b02ab/LICENSE.md>
  (sha256 `1564074e13439397221ffd522e2e504d56561994a23d371aa5e3ad43e4f5423f`).

The distributor did not record which shadcn/ui revision it installed. By content
comparison the 45 files are consistent with the `new-york` registry as it stood
at commit
[`728cb4cfa5`](https://github.com/shadcn-ui/ui/commit/728cb4cfa533884521eb8450be63ce70d67b02ab)
(2025-07-09), the last revision before the `www` app was deprecated: with the
import-alias rewrite below applied, 44 of 45 files are identical, and the 45th
differs only by one dropped comment. That is matching evidence, not a claim that
this is that commit's output — no revision was published, so treat the shadcn/ui
revision as unidentified.

## Differences from that revision

These were already present in the distributed archive; nothing here was
re-authored:

| Difference | Files |
| --- | --- |
| `@/registry/new-york/ui/…` / `…/hooks/…` imports rewritten to `@/components/ui/…` / `@/hooks/…` (this skill's Vite alias layout) | 7 |
| `"use client"` dropped (34 files upstream, 15 here) | 19 |
| `// Inspired by react-hot-toast library` comment dropped from `hooks/use-toast.ts` | 1 |

No component logic, markup, styling or export differs.

## Changes made in this repository

- Added `SHADCN-UI-LICENSE.txt` (verbatim upstream license) and this notice.
- `init-artifact.sh` checks for both before it creates anything: a missing
  notice is an error exit, not a warning, so it cannot generate a project that
  ships the components without their notice. After extraction it copies both
  into `src/components/ui/`. The archive itself is unmodified.

If you redistribute the archive or an extraction of it, keep
`SHADCN-UI-LICENSE.txt` with it: MIT requires the copyright and permission
notice to travel with copies and substantial portions. The same applies to a
published `bundle.html` from `bundle-artifact.sh` — it is compiled output of
these components, so reproduce the notice wherever that file is distributed.

## Verify

```bash
sha256sum shadcn-components.tar.gz SHADCN-UI-LICENSE.txt   # hashes above
tar -tzf shadcn-components.tar.gz | grep -c '\.tsx\?$'     # 45 files
bash test/web-artifacts-notice.test.sh                     # from the repo root
curl -fsSL https://raw.githubusercontent.com/shadcn-ui/ui/728cb4cfa533884521eb8450be63ce70d67b02ab/LICENSE.md \
  | cmp - SHADCN-UI-LICENSE.txt
```
