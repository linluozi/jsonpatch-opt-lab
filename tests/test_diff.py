#!/usr/bin/env python3
"""Differential test: baseline upstream jsonpatch vs the optimized copy.

Both modules are loaded from explicit file paths so the test can run against
an upstream checkout and a patched checkout side by side:

    BASELINE_JSONPATCH=/tmp/upstream/jsonpatch.py \
    OPT_JSONPATCH=/tmp/opt/jsonpatch.py python -m pytest tests -q

Every generated (src, dst) pair is de-duplicated by sha256 before it is
counted; the reported number is the number of *independent* inputs, not the
number of loop iterations.
"""
import hashlib
import importlib.util
import json
import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.environ.get("BASELINE_JSONPATCH")
OPTIMIZED = os.environ.get("OPT_JSONPATCH",
                           os.path.join(HERE, "..", "jsonpatch.py"))
TARGET = int(os.environ.get("DIFF_CASES", "1200"))
REAL_DIR = "/usr/lib/python3/dist-packages/botocore/data"


def load(name, path):
    if not path or not os.path.exists(path):
        pytest.skip("missing module path: %s" % path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules():
    return load("jsonpatch_baseline", BASELINE), load("jsonpatch_opt",
                                                      OPTIMIZED)


def canon(value):
    return json.dumps(value, sort_keys=True, default=str)


def key_of(src, dst):
    return hashlib.sha256((canon(src) + "\x00" + canon(dst)).encode()).hexdigest()


def edge_cases():
    yield {}, {}
    yield [], []
    yield {"a": None}, {"a": None, "b": None}
    yield {"a": [1, 2, 3]}, {"a": [3, 2, 1]}
    yield {"a": True}, {"a": 1}
    yield {"a": 1}, {"a": 1.0}
    yield {"a": -0.0}, {"a": 0.0}
    yield {"na\u00efve": "\u4e2d\u6587"}, {"na\u00efve": "\u4e2d\u6587!"}
    yield {"long": "x" * 4096}, {"long": "x" * 4095 + "y"}
    yield {"n": 10 ** 40}, {"n": -(10 ** 40)}
    yield ({"deep": {"a": {"b": {"c": [1, {"d": 2}]}}}},
           {"deep": {"a": {"b": {"c": [1, {"d": 3}]}}}})
    yield ({"list": [{"id": i, "v": i % 3} for i in range(50)]},
           {"list": [{"id": i, "v": (i + 1) % 3} for i in range(50)]})
    yield {"k": "\u0000\u001f"}, {"k": "\u0000\u001f\u007f"}
    yield {"s": "same"}, {"s": "same", "t": "added"}


def random_value(rng, depth=0):
    kinds = ["none", "bool", "int", "float", "str", "list", "dict"]
    kind = rng.choice(kinds if depth < 3 else kinds[:5])
    if kind == "none":
        return None
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "int":
        return rng.choice([0, 1, -1, 7, 10 ** 12, -10 ** 12])
    if kind == "float":
        return rng.choice([0.0, -0.0, 1.5, 1e-9, 1e18])
    if kind == "str":
        alphabet = "abcXYZ019 _-/\u4e2d\u6587\u00e9"
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
    if kind == "list":
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {("k%d" % i): random_value(rng, depth + 1)
            for i in range(rng.randint(0, 4))}


def mutate(rng, doc):
    """Return a mutated copy of a real JSON document (structural edit)."""
    clone = json.loads(json.dumps(doc))

    def nodes(node, parent=None, key=None):
        yield parent, key, node
        if isinstance(node, dict):
            for k in node:
                yield from nodes(node[k], node, k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                yield from nodes(item, node, i)

    options = list(nodes(clone))
    parent, key, node = options[rng.randrange(len(options))]
    action = rng.choice(["scalar", "addkey", "delkey", "push", "pop", "swap"])
    if isinstance(node, dict):
        if action == "addkey" or not node:
            node["added_%d" % rng.randrange(100)] = random_value(rng, 2)
        elif action == "delkey":
            del node[rng.choice(list(node))]
        elif action == "scalar":
            node[rng.choice(list(node))] = random_value(rng, 3)
    elif isinstance(node, list):
        if action == "push" or not node:
            node.insert(rng.randint(0, len(node)), random_value(rng, 2))
        elif action == "pop":
            node.pop(rng.randrange(len(node)))
        elif action == "swap" and len(node) > 1:
            i = rng.randrange(len(node))
            j = rng.randrange(len(node))
            node[i], node[j] = node[j], node[i]
        else:
            node[rng.randrange(len(node))] = random_value(rng, 3)
    return clone


def real_docs(limit=6):
    docs = []
    for root, _dirs, files in os.walk(REAL_DIR):
        for name in files:
            if name == "service-2.json":
                path = os.path.join(root, name)
                if 200000 < os.path.getsize(path) < 700000:
                    with open(path, "rb") as handle:
                        docs.append(json.load(handle))
                    if len(docs) >= limit:
                        return docs
    return docs


def build_cases():
    cases, seen = [], set()

    def add(src, dst):
        key = key_of(src, dst)
        if key not in seen:
            seen.add(key)
            cases.append((src, dst))

    for src, dst in edge_cases():
        add(src, dst)

    rng = random.Random(20261113)  # seeded once, outside every generator
    docs = real_docs()
    produced = 0
    while len(cases) < TARGET and produced < TARGET * 20:
        produced += 1
        if docs and produced % 2:
            doc = docs[produced % len(docs)]
            add(doc, mutate(rng, doc))
        else:
            add(random_value(rng), random_value(rng))
    return cases, produced


CASES, ATTEMPTS = build_cases()


def test_independent_case_count():
    assert len(CASES) >= 1000, len(CASES)
    print("independent samples=%d attempts=%d" % (len(CASES), ATTEMPTS))


def test_byte_identical_patches(modules):
    baseline, optimized = modules

    def run(src, dst):
        return (baseline.JsonPatch.from_diff(src, dst).to_string(),
                optimized.JsonPatch.from_diff(src, dst).to_string())

    mismatches, applied = [], 0
    for index, (src, dst) in enumerate(CASES):
        expected, actual = run(src, dst)
        if expected != actual:
            mismatches.append(index)
            continue
        if optimized.JsonPatch.from_diff(src, dst).apply(src) == dst:
            applied += 1
    print("cases=%d mismatches=%d apply_ok=%d"
          % (len(CASES), len(mismatches), applied))
    assert not mismatches, mismatches[:10]


def test_input_is_copied(modules):
    """The upstream docstring promises the patch does not alias dst."""
    _baseline, optimized = modules
    src = {"a": 1}
    dst = {"a": {"nested": ["value"]}}
    patch = optimized.JsonPatch.from_diff(src, dst)
    dst["a"]["nested"].append("sentinel")
    assert "sentinel" not in patch.to_string()
    assert patch.apply({"a": 1}) == {"a": {"nested": ["value"]}}
