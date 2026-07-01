# Git Backend

propstore uses Dulwich (pure-Python git) to version knowledge repositories. The git object store is the single source of truth; loose files under the repository root are an explicit materialized projection. This enables store-only initialization, historical builds, branch-based isolation, and atomic commits.

Dulwich is confined to a single file (`propstore/storage/git_backend.py`). Everything else interacts through `GitStore` or `TreeReader`.

## Design Principles

- **Object store is truth, loose files are a projection.** `pks init` seeds semantic artifacts into the git store only. `pks materialize` is the explicit operation that writes committed artifacts to disk.
- **Dulwich confined to one file.** Only `propstore/storage/git_backend.py` imports Dulwich. All other modules use `GitStore` or `TreeReader`.
- **HEAD is secondary; branch refs are primary.** Branch refs (`refs/heads/{name}`) are the authoritative pointers. HEAD symref is only set when the active branch is master.
- **Linear ordinary history per branch; merge commits exist globally.** Ordinary branch commits created by `GitStore._commit()` have exactly one parent, so each branch's non-merge history stays linear. Repository merges created by `propstore/storage/merge_commit.py:create_merge_commit()` write two-parent commits on the target branch.
- **Branch metadata is ephemeral.** `_branch_meta` is stored as an attribute on the `GitStore` instance, not persisted to git. It is lost when the process exits.
- **Worldline journals are durable artifacts.** Temporal trajectories that must survive process boundaries belong in committed worldline documents as `journal` payloads, not in branch metadata.
- **Sidecar is derived runtime output.** Build products such as SQLite sidecars, WAL files, hashes, and provenance outputs are not semantic source truth and are preserved by materialization cleanup.

## GitStore

The core wrapper around a Dulwich `Repo`. All git operations go through this class.

`propstore/storage/git_backend.py:GitStore`

### Lifecycle

| Method | What it does |
|--------|-------------|
| `init(root)` | Creates a new store-only repo with an internal bare git store at `root/.git` |
| `open(root)` | Opens an existing repo at `root` |
| `is_repo(root)` | Checks for a supported propstore git store layout |

### Read Operations

| Method | What it does |
|--------|-------------|
| `read_file(path, commit=None)` | Reads blob from git tree. Optional `commit` reads from a historical state. |
| `list_dir(subdir, commit=None)` | Lists regular files in a subtree. Optional `commit` for historical listing. |

Both accept an optional commit SHA to read from any point in history without touching the working tree.

### Write Operations

| Method | What it does |
|--------|-------------|
| `commit_files(changes, message, branch="master")` | Add/update files and commit atomically |
| `commit_deletes(paths, message, branch="master")` | Delete files and commit atomically |
| `commit_batch(adds, deletes, message, branch="master")` | Add and delete in a single atomic commit |

All three delegate to `_commit()`, which:

1. Reads the branch tip from `refs/heads/{branch}` (not HEAD)
2. Flattens the current tree into a `{path: blob_sha}` dict
3. Applies adds (creates Blob objects) and deletes
4. Rebuilds the nested Tree hierarchy via `_build_tree_from_flat()`
5. Creates a Commit object with `parents = [tip_sha]`
6. Updates the branch ref; only sets HEAD symref when `branch == "master"`

`propstore/storage/git_backend.py:GitStore._commit`

`_commit()` is the ordinary single-parent commit path. Formal repository merges use `create_merge_commit()` instead; that code path writes a two-parent commit after constructing the merge framework.

### History

| Method | What it does |
|--------|-------------|
| `log(max_count=50, branch="master")` | Walks from branch tip. Returns dicts with `sha`, `message`, `time`, `author`, `parents`. The `parents` list supports Backward Uniqueness verification. |
| `head_sha()` | HEAD commit SHA |
| `branch_sha(name)` | Tip SHA for a named branch |

### Diff and Show

| Method | What it does |
|--------|-------------|
| `diff_commits(commit1=None, commit2=None)` | Flattens both trees, compares blob SHAs, returns `{added, modified, deleted}` |
| `show_commit(sha)` | Commit metadata plus diff vs parent |

### Materialization

`pks materialize` projects a selected commit or branch tip to loose files under the repository root. It refuses conflicting local content unless forced. With `--clean`, it removes stale materialized semantic files that are absent from the selected snapshot while preserving ignored runtime outputs such as sidecar SQLite files, WAL files, hash files, and provenance outputs.

`sync_worktree()` remains the low-level projection primitive used by explicit materialization/export paths. Ordinary semantic workflows commit directly to the object store and do not refresh loose files. The object store is never derived from the filesystem.

`propstore/storage/git_backend.py:GitStore.sync_worktree`

## TreeReader

A protocol with two implementations, enabling reads from either the filesystem or the git object store at any commit.

`propstore/tree_reader.py:TreeReader`

### Protocol Methods

| Method | What it does |
|--------|-------------|
| `list_yaml(subdir)` | Returns `(stem, raw_bytes)` for all `.yaml` files in a subdirectory |
| `read_yaml(path)` | Reads a single file by repo-relative path |
| `exists(subdir)` | Checks if a subdirectory has entries |

### FilesystemReader

Backed by a `Path` root. Reads from disk via `pathlib`. This is for external or explicitly materialized file trees, not the normal source-of-truth path for git-backed propstore repositories.

`propstore/tree_reader.py:FilesystemReader`

### GitTreeReader

Backed by a `GitStore` and an optional commit SHA. Delegates to `GitStore.list_dir()` and `read_file()` with the commit parameter. This is how store-backed commands read current or historical semantic state without touching loose files.

`propstore/tree_reader.py:GitTreeReader`

## Branch Operations

Branches provide isolated epistemic states (Darwiche & Pearl 1997, C1-C4). Each branch maintains independent ordinary history until an explicit merge writes a two-parent commit onto a target branch. Commits to one branch are invisible on other branches until merged.

`propstore/storage/branch.py`

### Branch Metadata vs Worldline Journals

`GitStore._branch_meta` is intentionally process-local. It can annotate an
open store instance, but it is not a storage contract and must not carry semantic
history.

Durable temporal history is stored as a worldline artifact. `pks worldline
build-journal` writes a `TransitionJournal` into the worldline definition's
`journal` field, and `pks worldline at-step NAME STEP` reads that committed
artifact to project the accepted claim view at a step. This partially resolves
the old branch-meta pain point: branch metadata may remain ephemeral because the
semantic trajectory now has a committed representation.

### BranchInfo

A dataclass with automatic kind detection from name prefixes:

| Prefix | Kind |
|--------|------|
| `paper/` | `"paper"` |
| `agent/` | `"agent"` |
| `hypothesis/` | `"hypothesis"` |
| *(other)* | `"workspace"` |

Fields: `name`, `tip_sha`, `kind`, `parent_branch`, `created_at`.

`propstore/storage/branch.py:BranchInfo`

### Operations

| Function | What it does |
|----------|-------------|
| `create_branch(kr, name, source_commit=None)` | Creates a branch ref at `source_commit` (default: master tip). Raises `ValueError` if the branch already exists. |
| `delete_branch(kr, name)` | Deletes a branch ref. Refuses to delete master — the knowledge base must always exist (Alchouron et al. 1985). |
| `list_branches(kr)` | Iterates `refs/heads/*`, infers kind from name prefix. |
| `branch_head(kr, name)` | Tip SHA lookup for a named branch. |
| `merge_base(kr, branch_a, branch_b)` | BFS from both tips, returns the first overlap. Identifies the common knowledge base for IC merging (Konieczny & Pino Perez 2002). |

## Formal Merge Layer

The repository merge path now has an explicit formal layer above raw git writes.

### Merge Framework

`propstore/storage/merge_classifier.py:build_merge_framework`

Builds a `RepositoryMergeFramework` from two branch tips. Its public payload is:

- `arguments` — branch-local argument summaries with provenance back to claims
- `framework` — a partial argumentation framework over those arguments
- `branch_a`, `branch_b` — branch provenance for the compared tips

The partial framework records `attack`, `ignorance`, and `non-attack` over the merged argument universe. This is the canonical merge object for downstream inspection, exact operators, and reporting.

### Merge Commit Creation

`propstore/storage/merge_commit.py:create_merge_commit`

1. Build the formal merge framework with `build_merge_framework()`
2. Materialize merged claim storage from the merged argument set
3. Write a git commit with `parents = [left_sha, right_sha]`
4. Update the target branch ref

The two-parent git commit is storage/provenance. The merge semantics live in the formal merge framework and its completion-based queries.

## Historical Builds

`pks checkout COMMIT` builds a sidecar from a historical commit without changing the working tree.

### Flow

1. Verify the commit exists via `repo.git.show_commit(commit)`
2. Create `GitTreeReader(repo.git, commit=commit)` — reads from the object store, not the filesystem
3. Load concepts, claims, and contexts via the reader
4. Call `build_sidecar(..., commit_hash=commit, reader=reader)`

### Commit-SHA-keyed Rebuild Skipping

When `commit_hash` is provided to `build_sidecar`:

- `content_hash` is set to the commit SHA (skips the expensive `_content_hash()` computation)
- Checks `sidecar_path.with_suffix(".hash")` for an existing hash
- If the `.hash` file matches and the sidecar exists, returns immediately (skipped)
- Otherwise rebuilds and writes the commit SHA to the `.hash` file

Each commit SHA produces at most one sidecar build. Re-running `pks checkout` with the same SHA is a no-op.

`propstore/build_sidecar.py`

## CLI Commands

### pks log

Displays recent branch-scoped commit history with operation labels inferred from
commit type and message.

```bash
# Show last 20 commits (default)
pks log

# Show branch-local history for an epistemic branch
pks log --branch agent/paper-a

# Include file-level A/M/D paths
pks log --show-files

# Emit structured YAML
pks log --format yaml

# Show last 5 commits
pks log -n 5
```

Text output format:

`{sha[:8]}  {time}  [{branch}]  {operation}  {first_line_of_message}`

When `--show-files` is set, each entry is followed by indented `A/M/D` file
lines. YAML output includes `sha`, `time`, `branch`, `operation`, `message`,
`parents`, optional merge metadata from `merge/manifest.yaml`, and optionally
`added` / `modified` / `deleted`.

### pks diff

Shows file-level changes between commits.

```bash
# Diff HEAD vs its parent
pks diff

# Diff a specific commit vs its parent
pks diff abc1234
```

Output lists files as Added, Modified, or Deleted.

### pks show

Shows commit metadata and file changes.

```bash
# Show a specific commit
pks show abc1234
```

Output includes SHA, author, date, message, and A/M/D file list.

### pks checkout

Builds a sidecar from a historical commit without touching the working tree.

```bash
# Build sidecar at a specific commit
pks checkout abc1234
```

This creates a `GitTreeReader` at the target commit and builds the sidecar using only the object store. The working tree is unaffected.

### pks proposal promote

Promotes committed proposal-branch stances into the main stance collection.

```bash
pks proposal promote
```

Reads committed `stances/*.yaml` blobs from the `proposal/stances` branch, copies them into `master`'s `stances/` tree in one atomic commit, and syncs the working tree.

### pks merge inspect

Inspect the formal merge framework between two branches.

```bash
uv run pks merge inspect agent/paper-a agent/paper-b --semantics grounded
```

Output is a YAML summary derived from `propstore/storage/merge_report.py`, including branch provenance, framework size, uncertain pairs, and acceptance under the requested semantics.

### pks merge commit

Create a two-parent storage merge commit from the formal merge framework.

```bash
uv run pks merge commit agent/paper-a agent/paper-b --target-branch master
```

Options:

- `--message` — override the default commit message
- `--target-branch` — choose which branch receives the merge commit

## References

- **Bonanno, G. (2007).** "Axiomatic Characterization of the AGM Theory of Belief Revision in a Temporal Logic." — Backward Uniqueness (claim 9): the history leading to any belief state is unique. Grounds the linear ordinary-history invariant inside a branch, not a global no-merge-commit claim.

- **Darwiche, A. & Pearl, J. (1997).** "On the Logic of Iterated Belief Revision." — C1-C4 postulates for iterated revision. Each branch is an independent epistemic state.

- **Konieczny, S. & Pino Perez, R. (2002).** "Merging Information under Constraints: A Logical Framework." — IC merging. The separate `merge_base()` helper identifies the common knowledge base between two branches.

- **Alchouron, C., Gardenfors, P. & Makinson, D. (1985).** "On the Logic of Theory Change." — AGM postulates. Master branch cannot be deleted: the knowledge base must always exist.
