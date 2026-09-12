# 老王解压 (laowang-unzip)

**Batch-extract disguised/nested archives from download folders — safely, idempotently, with full audit.**

English | [中文说明见 SKILL.md](SKILL.md)

---

## Platform support — **Windows-only in spirit (Q1 ruling, 2026-09-09)**

Scanning, extraction and reporting work on any OS, but **the deletion and recycle-bin
semantics are only complete on Windows**:

| | Windows | macOS / Linux |
|---|---|---|
| Scan / repair / extract / report | ✅ full | ✅ full |
| Delete source archives | `SHFileOperationW` + `FOF_ALLOWUNDO` → **Recycle Bin, recoverable** | `os.remove` / `shutil.rmtree` → **permanent, unrecoverable** |
| `purge-recycle` | ✅ frees space for real (one run reclaimed 230 GB) | ❌ no-op (no recycle-bin concept exposed) |
| Space reclaimed by deleting sources | ✅ after purge | ✅ immediately (but the files are gone for good) |

> **The "deleted archives are recoverable" promise holds on Windows only.**
> On macOS/Linux, always run `--dry-run` first and back up anything you cannot afford to lose.

## Design origin

Built for the Chinese netdisk (百度网盘 / 阿里云盘) download-cleanup scenario, where archives are
routinely disguised as `.mp4/.png/.txt` and bundled with split volumes and ad junk. **Nothing is
hard-wired to that scenario** — it works on any folder of archives in any language; point
`--src` at any directory. (The default source directory name `【new】` is just a habit from that
workflow; it is overridable and carries no special meaning.)

## What it does

Download folders (Baidu Netdisk and similar) are full of archives **disguised** as other files:
fake `.mp4` with a real 7z payload hidden at offset 36 to tens-of-MB, renamed volumes
(`part1.rar删`), 4 GB-split files, ZIPs with corrupted magic bytes (`PK`→`UA`), nested
archives several layers deep, plus advertising junk. This tool cleans all of it in one
single-threaded, crash-recoverable pipeline:

```
discover → probe magic → hash & dedup → header analyze / repair (carve · magic-patch ·
concatenate · rename) → volume pairing → space gate → password test → 7z extract →
recurse until converged → 12-check delete guard → junk cleanup → 9-section report
```

Key properties:

- **SQLite as the single source of truth** — every file gets one row, a 14-state machine,
  25-value failure taxonomy, and a full `events` audit trail.
- **Strictly single-threaded** — deliberate design choice (documented in
  [`references/design-v2.1.md`](references/design-v2.1.md) §3.1); no concurrency anywhere.
- **Delete only after 12 checks pass** — including "no FAILED children" (keep the source
  as a retry lead) and a protected-prefix whitelist. Password-locked and dedup-pending
  sources are **never** auto-deleted.
- **Space-aware** — recycle-bin purge before extraction (the #1 Windows disk trap), a
  `size × 1.5 + 6 GiB` gate, and triple-source free-space cross-validation.
- **Crash-safe** — `kill -9` mid-run, reboot, re-run: finished items are skipped, the rest resumes.

## Quick start

Requirements: **Python ≥ 3.9**, [7-Zip](https://www.7-zip.org/) installed
(on PATH, or point `--sevenzip` / the `sevenzip` config field at it).

Install 7-Zip if needed — Windows: the official installer; macOS: `brew install p7zip`;
Debian/Ubuntu: `apt install p7zip-full`. On macOS/Linux use `python3` (not `python`).

The CLI lives in `scripts/` — either `cd scripts/` first, or call it by full path
(`cli.py` and `pipeline.py` are equivalent entry points). Point `--src` at the directory you
want cleaned up:

```bash
cd scripts/

# 0. Stage (归集): move everything from the download dir into <root>/【done】/<date>/
python cli.py stage --root <processing-root> --src <folder-of-new-downloads>

# 0.5 Health check: 7z probe / Python version / root & source dir / db writable / password libraries
python cli.py doctor --root <processing-root> --src <folder-of-archives>

# 1. Dry run: scan + record + plan only; no extraction, no deletion
python cli.py run --root <processing-root> --src <folder-of-archives> --dry-run

# 2. Full run: runs to convergence, writes a 9-section report at the end
python cli.py run --root <processing-root> --src <folder-of-archives>

# 3. Housekeeping: read-only whole-library audit (5 sections) / gather finished
#    content into <root>/成品/ / add a password and retry dead accounts
python cli.py audit --root <processing-root>
python cli.py collect --root <processing-root>
python cli.py add-password "<password>" --test --root <processing-root>
```

macOS / Linux — same commands, `python3` instead of `python`:

```bash
python3 cli.py run --root <processing-root> --src <folder-of-archives>
```

The processing root can also be given via the `DAE_ROOT` environment variable or
`<root>/pipeline/config.local.json` (see [`SKILL.md` §4](SKILL.md) for the full config table).

## Safety tiers (summary)

What the pipeline may do on its own, what it asks about, and what it never does —
full table in [`SKILL.md` §4.3](SKILL.md).

- **Automatic (zero-risk)** — delete a source archive only after all 12 checks pass;
  delete `SYSTEM_JUNK` / `ZERO_BYTE` junk; purge the recycle bin at batch finish.
- **Asks first (中风险)** — mid-risk junk (promo `.exe`, ad `.txt`, bait `.bat`),
  duplicate hits whose type judgement disagrees, and carved archives that are
  `ENCRYPTED` / `INVALID`. Reported as a list; nothing is deleted until you confirm.
- **Never automatic** — anything `DUPLICATE_PENDING` (decide with `resolve-dup`),
  any source whose `fail_reason` is password-related (it may be the only retry lead),
  and anything under the protected prefixes (pipeline dir, config, password library).

`--ask-all` demotes every tier to "ask first"; `--dry-run` scans and plans without
extracting or deleting. On Windows, deleted archives stay recoverable in the Recycle Bin
**during the run** — the default finish-purge removes them; use `--no-purge-recycle` to keep them.

## Known limitations

These code paths are **reviewed but not covered by sample-based tests** (a third QA round
will cover the core ones; this section will be updated afterwards). Treat them as
"should work, verify before trusting":

- **4 GB-split volumes** — concatenated via `FOUR_GB_SPLIT_SIZE` detection; no real 4 GB sample yet.
- **carve** — extracts the real archive from a head-disguised file; scan capped at 64 MiB.
- **magic patch (UA→PK)** — whole-file `55 41 → 50 4B` rewrite; false-hit rate not measured.
- **「删」-suffixed first volume rename** — only `删除` / `删` suffixes are handled.
- **Watchdog `TIMEOUT` / `HANG_KILLED`** — 5400 s wall clock + 1800 s no-progress; the
  kill-then-retry-once path is untested.
- **Space gate abort** — `SpaceAbort` below `MIN_FREE_BYTES` (20 GiB); a real disk-full run is untested.
- **`MAX_DEPTH = 8`** — deeper nesting stops being unpacked (real samples only reached depth 5).
- **Long paths (> 260 chars)** — Windows `\\?\` prefix; the shell delete API rejects that prefix
  (falls back to permanent delete), extreme depths untested.

## Documentation

| File | Contents |
|---|---|
| [`SKILL.md`](SKILL.md) | Workflow, config table, password strategy, generalization notes (Chinese) |
| [`references/design-v2.1.md`](references/design-v2.1.md) | Full design: DDL, pseudo-code, every threshold's origin |
| [`references/scripts-api.md`](references/scripts-api.md) | **Implementation contract**: module list + function signatures |
| [`references/failure-matrix.md`](references/failure-matrix.md) | 25 failure enums, 7z output classification, the 12 delete checks |
| [`references/magic-signatures.md`](references/magic-signatures.md) | Magic table, head-disguise/carve/magic-patch criteria |
| [`references/pitfalls.md`](references/pitfalls.md) | 26 hard-won pitfalls (read before implementing) |

## Status

**Implementation complete and verified** — `scripts/` (entry points `cli.py` / `pipeline.py`
+ the `pipeline_lib` package, pure standard library) passes 31 smoke checks end-to-end and
27/27 regression after the QA re-review fixes (2 P0 + 3 P1). The interface contract lives in
[`references/scripts-api.md`](references/scripts-api.md).

## Passwords

Candidates are tried in a fixed order (hit = stop), matching `passwords.py::candidates_for()`:
1. **empty password** (`NONE`) — safe: `stdin=DEVNULL` means it can never hang on a prompt;
2. the parent archive's own password (`INHERITED`);
3. names mined at runtime — `解压码：/密码：/提取码：` markers and full/half-width bracket
   contents (length 3–40, e.g. `（5656456）`);
4. your local libraries (git-ignored, merged ahead of the seeds, in this order):
   `assets/passwords.local.txt`, `<root>/.pipeline/passwords.local.txt`, `<root>/password.txt`;
5. the 20 community seeds in `assets/passwords.txt`.

Password correctness is only ever judged by `7z t` (rc=0 + "Everything is Ok") — never by
`7z l`, which falsely succeeds on password-protected ZIPs.

## License

MIT — see [LICENSE](LICENSE).
