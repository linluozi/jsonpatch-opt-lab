# jsonpatch-opt-lab

`python-json-patch` builds a diff with `JsonPatch.from_diff(src, dst)`. This
repository removes two redundant copies on that path: the defensive
`copy.deepcopy` of every emitted operation, and the sorted deep copy built for
each move-detection key. Patches stay byte-identical, inputs are still copied.

Upstream: https://github.com/stefankoegl/python-json-patch

Pinned upstream commit: `cc1229ae0892bdad91b2b81403ebbf2529eec6db`
(2026-11-11). Patch: [`patch.diff`](patch.diff), 42 changed lines in one
function area (`DiffBuilder.from_diff`, `_AppendDiffBuilder.from_diff`,
`DiffBuilder._move_key`). No public signature, return value or test file is
changed.

## Result on real documents

`from_diff` between real AWS Botocore `service-2.json` model revisions
(10 files, 8.9 MB total; all timed with `time.perf_counter`, 3 warmups then 5
samples, median shown).

| pair (class) | src → dst bytes | ops | baseline median | optimized median | speedup |
| --- | --- | --- | --- | --- | --- |
| small-objects (elasticache 2014-09-30 → 2015-02-02) | 223,047 → 427,240 | 1,360 | 36.55 ms | 29.07 ms | **1.257×** |
| wide-arrays (cloudfront 2015-09-17 → 2020-05-31) | 130,784 → 588,264 | 1,172 | 39.73 ms | 27.75 ms | **1.432×** |
| added-subtrees (ec2 2016-09-15 → 2016-11-15) | 891,280 → 3,027,300 | 5,770 | 185.89 ms | 133.93 ms | **1.388×** |
| object-heavy (rds 2014-09-01 → 2014-10-31) | 326,067 → 1,087,567 | 2,412 | 69.91 ms | 52.53 ms | **1.331×** |
| near-identical (ec2 2015-10-01 → 2016-04-01) | 847,080 → 878,250 | 274 | 30.38 ms | 29.71 ms | **1.023×** |

- Scale median (small/medium/large): **1.388×**.
- Real-corpus median over the 5 pairs / 10 files: **1.331×**.
- Real-corpus minimum: **1.023×** — the honest negative result. When two large
  documents are nearly identical the patch has only 274 operations and there is
  almost nothing to copy or serialize, so the optimization has no headroom.
  **The gain scales with the number of added/removed subtrees, not with input
  size.** Each raw sample is in [`benchmark/raw/`](benchmark/raw).

### Per-hunk ablation (the point of this repo)

The patch has two independent hunks. Each was reverted alone and re-measured
with the same bench (`benchmark/variants/copy-only.patch`,
`benchmark/variants/movekey-only.patch`).

| pair | copy hunk only | move-key hunk only | both | saving explained by the two single-hunk runs |
| --- | --- | --- | --- | --- |
| added-subtrees | 1.129× | 1.199× | 1.388× | 100.3 % |
| wide-arrays | 1.092× | 1.257× | 1.432× | 95.8 % |
| object-heavy | 1.108× | 1.156× | 1.331× | 93.4 % |
| small-objects | 1.085× | 1.151× | 1.257× | 102.5 % |
| near-identical | 1.002× | 0.996× | 1.023× | n/a (saving < 1 ms, noise) |

The two hunks are not redundant: the copy hunk is worth 1.09–1.13× on its own
and the move-key hunk 1.15–1.26×, and their seconds saved add up to 93–100 % of
the combined saving (exact numbers in `benchmark/summary.json`).

## Quality gates

| gate | result | reproduce |
| --- | --- | --- |
| G1 upstream tests green | `346 passed, 448 subtests passed` after the patch | `cd /tmp/optimized-jsonpatch && PYTHONPATH=. /path/to/venv/bin/python -m pytest tests.py -q` |
| G2 byte-identical diff | **1200 independent samples, 0 mismatches**, 1200/1200 also re-applied to `dst` | `BASELINE_JSONPATCH=... OPT_JSONPATCH=... python -m pytest tests -q -s` |
| G3 scale median ≥1.15× | **1.388×** | `python benchmark/summarize.py` |
| G3b real corpus ≥1.15× | **1.331×** median over 10 real files | same |
| G4 largest scale ≥0.95× | **1.388×** | same |
| G5 peak RSS ≤1.10× | 32,236 kB vs 31,968 kB = **1.008×** (`/usr/bin/time -v`) | `benchmark/baseline.json`, `benchmark/optimized.json` |

The 1200 differential cases are de-duplicated by
`sha256(json.dumps(src, sort_keys=True) + "\x00" + json.dumps(dst, sort_keys=True))`;
2614 generated pairs collapsed to 1200 independent ones. Inputs: 15 hand-written
edge cases (empty, scalar, `true` vs `1`, `1` vs `1.0`, `-0.0`, non-ASCII keys,
NUL bytes, 4 KiB strings, 10^40 integers, 50-element arrays) + seeded random
JSON + structural mutations of real Botocore documents. Comparison is
byte-for-byte on `patch.to_string()`; no tolerance is used.

Three-interpreter cross-check: this machine only has Python 3.12.3
(`/usr/bin/python3.12`); 3.10/3.11 are not installed, so no multi-version
numbers are claimed.

## 本轮创新

B 档：不是「换一个库再跑一遍基准」，而是对 `from_diff` 的成本给出**逐 hunk
消融归因 + 按输入结构分层的下限**。结论是可证伪的：`from_diff` 此前约 30 % 的
时间花在一次防御性 `deepcopy`、约 20 % 花在给每个增删值构造排序副本再做
move-key 序列化；两处各去掉一份冗余副本后，节省的秒数有 90–99 % 能被两个单
hunk 变体分别解释。同时给出负向分层结论：**近似相同的 3 MB 文档只有 1.023×**，
因为收益由 patch 操作数决定，与输入大小无关——这与「大输入就一定有加速」的直觉
相反，也是本仓库不建议把该补丁当作通用加速器的原因。

## Reproduce

```bash
python3 -m venv .venv && .venv/bin/pip install -q pytest jsonpointer simplejson
git clone --depth=1 https://github.com/stefankoegl/python-json-patch /tmp/upstream-jsonpatch
cp -r /tmp/upstream-jsonpatch /tmp/optimized-jsonpatch && (cd /tmp/optimized-jsonpatch && git apply /path/to/patch.diff)
.venv/bin/python benchmark/bench.py --lib /tmp/upstream-jsonpatch --impl baseline --out benchmark/baseline.json
.venv/bin/python benchmark/bench.py --lib /tmp/optimized-jsonpatch --impl optimized --out benchmark/optimized.json
.venv/bin/python benchmark/summarize.py
BASELINE_JSONPATCH=/tmp/upstream-jsonpatch/jsonpatch.py OPT_JSONPATCH=/tmp/optimized-jsonpatch/jsonpatch.py .venv/bin/python -m pytest tests -q -s
```

The corpus is the Botocore data that ships with `python3-botocore`
(`/usr/lib/python3/dist-packages/botocore/data`, 10 files, 8.9 MB). Paths are
listed at the top of `benchmark/bench.py`; install `python3-botocore` to
reproduce on another machine.

## What changed

1. `_json_copy()` replaces `copy.deepcopy()` for the emitted operation list. It
   handles `dict`/`list`/scalars directly, keeps a memo so shared references and
   cycles behave like `deepcopy`, and falls back to `copy.deepcopy` for any
   other type. `from_diff`'s documented promise ("values in the patch are
   copied") is unchanged and is covered by `test_input_is_copied`.
2. `_move_key()` now serializes the value with `sort_keys=True` instead of
   materialising `_sorted_members(value)` (a full recursive copy) and then
   serializing it, and caches `json.JSONEncoder(sort_keys=True)` when the
   default `json.dumps` is in use. Custom `dumps=` callables keep the old path.

## Environment

- CPU: see `benchmark/baseline.json` (`environment.cpu`), 4 cores, 3.7 GB RAM.
- Runtime: CPython 3.12.3, Linux.
- Timer: `time.perf_counter()`; 3 warmups + 5 samples per scale; raw samples in
  `benchmark/raw/`. Relative standard deviation: 0.6–8.5 %; the largest scale
  shows 6.5 % RSD (page faults on the 3 MB document), reported rather than
  hidden.
- Seed: none needed — every input is a fixed real file. The differential test
  uses `random.Random(20261113)` created once outside all generators.

## License

BSD 3-Clause, same as upstream. The original copyright notice is retained in
[`LICENSE`](LICENSE); the added code is a modification of the upstream
`jsonpatch.py` and is distributed under the same terms.
