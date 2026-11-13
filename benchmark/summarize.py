#!/usr/bin/env python3
"""Join the benchmark files into the speedup and ablation tables.

    python benchmark/summarize.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(HERE, name)) as handle:
        return json.load(handle)


def main():
    baseline = load("baseline.json")["scales"]
    optimized = load("optimized.json")["scales"]
    copy_only = load("ablation-copy-only.json")["scales"]
    move_only = load("ablation-movekey-only.json")["scales"]

    table = {}
    for name, base in baseline.items():
        opt = optimized[name]
        table[name] = {
            "class": base["class"],
            "src_bytes": base["src_bytes"],
            "dst_bytes": base["dst_bytes"],
            "ops": base["ops"],
            "baseline_median_s": base["median_s"],
            "optimized_median_s": opt["median_s"],
            "speedup": base["median_s"] / opt["median_s"],
            "baseline_p25_s": base["p25_s"],
            "baseline_p75_s": base["p75_s"],
            "optimized_p25_s": opt["p25_s"],
            "optimized_p75_s": opt["p75_s"],
            "copy_only_median_s": copy_only[name]["median_s"],
            "movekey_only_median_s": move_only[name]["median_s"],
            "copy_only_speedup": base["median_s"] / copy_only[name]["median_s"],
            "movekey_only_speedup": base["median_s"] / move_only[name]["median_s"],
            "copy_hunk_saving_s": base["median_s"] - copy_only[name]["median_s"],
            "movekey_hunk_saving_s": base["median_s"] - move_only[name]["median_s"],
            "total_saving_s": base["median_s"] - opt["median_s"],
        }
        total = table[name]["total_saving_s"]
        if total > 0:
            explained = (table[name]["copy_hunk_saving_s"] +
                         table[name]["movekey_hunk_saving_s"])
            table[name]["ablation_explained"] = explained / total
        else:
            table[name]["ablation_explained"] = None

    real = [row["speedup"] for row in table.values()]
    scales = [table[name]["speedup"] for name in ("small", "medium", "large")]
    summary = {
        "table": table,
        "scale_median_speedup": sorted(scales)[1],
        "real_corpus_median_speedup": sorted(real)[len(real) // 2],
        "real_corpus_min_speedup": min(real),
        "real_corpus_files": sum(
            2 for _ in table),
        "patch_identical_sha256": all(
            baseline[name]["patch_sha256"] == optimized[name]["patch_sha256"]
            for name in baseline),
    }
    with open(os.path.join(HERE, "summary.json"), "w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
