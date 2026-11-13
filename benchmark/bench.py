#!/usr/bin/env python3
"""Measure JsonPatch.from_diff on real AWS service-2.json documents.

Usage:
    python benchmark/bench.py --lib <dir-with-jsonpatch.py> --impl baseline \
        --out benchmark/baseline.json

Each timed sample is one JsonPatch.from_diff(src, dst) call.  Every sample is
written to benchmark/raw/<impl>-<scale>-<i>.json before aggregation.
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCALES = [
    # name, class, src, dst  (all real botocore service models)
    ("small", "small-objects",
     "/usr/lib/python3/dist-packages/botocore/data/elasticache/2014-09-30/service-2.json",
     "/usr/lib/python3/dist-packages/botocore/data/elasticache/2015-02-02/service-2.json"),
    ("medium", "wide-arrays",
     "/usr/lib/python3/dist-packages/botocore/data/cloudfront/2015-09-17/service-2.json",
     "/usr/lib/python3/dist-packages/botocore/data/cloudfront/2020-05-31/service-2.json"),
    ("large", "added-subtrees",
     "/usr/lib/python3/dist-packages/botocore/data/ec2/2016-09-15/service-2.json",
     "/usr/lib/python3/dist-packages/botocore/data/ec2/2016-11-15/service-2.json"),
]
REAL = SCALES + [
    ("ec2-near-identical", "near-identical",
     "/usr/lib/python3/dist-packages/botocore/data/ec2/2015-10-01/service-2.json",
     "/usr/lib/python3/dist-packages/botocore/data/ec2/2016-04-01/service-2.json"),
    ("rds-objects", "object-heavy",
     "/usr/lib/python3/dist-packages/botocore/data/rds/2014-09-01/service-2.json",
     "/usr/lib/python3/dist-packages/botocore/data/rds/2014-10-31/service-2.json"),
]


def cpu_model():
    try:
        with open("/proc/cpuinfo") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def mem_total_kb():
    with open("/proc/meminfo") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
    return 0


def percentile(values, fraction):
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def measure(pair, warmup, repeats):
    name, klass, src_path, dst_path = pair
    with open(src_path, "rb") as handle:
        src_raw = handle.read()
    with open(dst_path, "rb") as handle:
        dst_raw = handle.read()
    src = json.loads(src_raw)
    dst = json.loads(dst_raw)

    for _ in range(warmup):
        jsonpatch.make_patch(src, dst)

    samples = []
    for index in range(repeats):
        start = time.perf_counter()
        patch = jsonpatch.make_patch(src, dst)
        elapsed = time.perf_counter() - start
        record = {
            "scale": name,
            "class": klass,
            "sample": index,
            "seconds": elapsed,
            "ops": len(patch.patch),
            "src_bytes": len(src_raw),
            "dst_bytes": len(dst_raw),
            "patch_sha256": hashlib.sha256(
                patch.to_string().encode()).hexdigest(),
            "rss_peak_kb": resource.getrusage(
                resource.RUSAGE_SELF).ru_maxrss,
        }
        samples.append(record)
        yield record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lib", required=True, help="dir holding jsonpatch.py")
    parser.add_argument("--impl", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--raw", default=os.path.join(HERE, "raw"))
    args = parser.parse_args()

    sys.path.insert(0, os.path.abspath(args.lib))
    global jsonpatch
    import jsonpatch  # noqa: E402  (path chosen above)
    if os.path.dirname(os.path.abspath(jsonpatch.__file__)) != \
            os.path.abspath(args.lib):
        raise SystemExit("imported jsonpatch from %s, not %s"
                         % (jsonpatch.__file__, args.lib))

    os.makedirs(args.raw, exist_ok=True)
    for pair in REAL:
        for record in measure(pair, args.warmup, args.repeats):
            path = os.path.join(
                args.raw, "%s-%s-%d.json"
                % (args.impl, record["scale"], record["sample"]))
            with open(path, "w") as handle:
                json.dump(record, handle, indent=1, sort_keys=True)

    results = {"impl": args.impl, "lib": os.path.abspath(args.lib),
               "environment": {
                   "cpu": cpu_model(),
                   "mem_total_kb": mem_total_kb(),
                   "python": sys.version.split()[0],
                   "platform": platform.platform(),
                   "warmup": args.warmup,
                   "repeats": args.repeats,
                   "seed": None,
                   "seed_note": "inputs are fixed real files; no RNG",
                   "timer": "time.perf_counter",
                   "command": " ".join([sys.executable] + sys.argv),
               },
               "scales": {}}
    for pair in REAL:
        name = pair[0]
        records = []
        for index in range(args.repeats):
            path = os.path.join(
                args.raw, "%s-%s-%d.json" % (args.impl, name, index))
            with open(path) as handle:
                records.append(json.load(handle))
        times = [record["seconds"] for record in records]
        median = statistics.median(times)
        results["scales"][name] = {
            "class": pair[1],
            "src": pair[2],
            "dst": pair[3],
            "src_bytes": records[0]["src_bytes"],
            "dst_bytes": records[0]["dst_bytes"],
            "ops": records[0]["ops"],
            "patch_sha256": records[0]["patch_sha256"],
            "median_s": median,
            "p25_s": percentile(times, 0.25),
            "p75_s": percentile(times, 0.75),
            "min_s": min(times),
            "max_s": max(times),
            "rsd": (statistics.pstdev(times) / median) if median else 0.0,
            "rss_peak_kb": max(r["rss_peak_kb"] for r in records),
            "samples": times,
        }
    with open(args.out, "w") as handle:
        json.dump(results, handle, indent=2, sort_keys=True)
    for name, data in results["scales"].items():
        print("%-20s %8.4fs  p25 %.4f p75 %.4f  rsd %5.1f%%  ops %d"
              % (name, data["median_s"], data["p25_s"], data["p75_s"],
                 100 * data["rsd"], data["ops"]))


if __name__ == "__main__":
    main()
