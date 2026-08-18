#!/usr/bin/env python3
"""Measure m (output tokens per round-trip) and g (context growth per round-trip)
from Claude Code transcripts, for the context-cycle-economics model.

Key correctness points:
  * ONE API request == one round-trip, but emits MULTIPLE JSONL records
    (thinking block, text block, each tool_use block). Dedupe by requestId.
  * C (depth) = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
  * g = positive first-difference of C between consecutive requests in a stream.
    Negative diffs = compaction / context drop; reported separately, not averaged in.
  * Sidechain (subagent) turns are excluded from the director stream and
    analysed separately, split by agentType from the sibling .meta.json.

Usage: measure-token-rates.py [transcript-dir]
"""
import glob
import json
import os
import sys
import statistics as st
from collections import defaultdict

DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/.claude/projects/-home-agent-workspace")


def pct(xs, p):
    if not xs:
        return 0
    s = sorted(xs)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load_requests(path):
    """Deduped per-request records, in file order.

    NOTE: output_tokens is a RUNNING count across the records of one request
    (thinking -> text -> tool_use ...); only the LAST record holds the final
    total. So we take the MAX per requestId, not the first. Input/cache fields
    are constant across a request's records, so max is a no-op there.
    """
    idx, reqs = {}, []
    try:
        fh = open(path, errors="replace")
    except OSError:
        return reqs
    with fh:
        for line in fh:
            if '"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue
            rid = d.get("requestId") or msg.get("id")
            if rid is None:
                continue
            rec = dict(
                C=(u.get("input_tokens", 0) or 0)
                  + (u.get("cache_creation_input_tokens", 0) or 0)
                  + (u.get("cache_read_input_tokens", 0) or 0),
                w=(u.get("cache_creation_input_tokens", 0) or 0),
                r=(u.get("cache_read_input_tokens", 0) or 0),
                out=(u.get("output_tokens", 0) or 0),
                side=bool(d.get("isSidechain")),
                model=msg.get("model"),
                effort=d.get("effort"),
            )
            if rid in idx:
                prev = reqs[idx[rid]]
                prev["out"] = max(prev["out"], rec["out"])
                prev["C"] = max(prev["C"], rec["C"])
            else:
                idx[rid] = len(reqs)
                reqs.append(rec)
    return reqs


def growth(reqs):
    """Positive first-differences of C; also count drops (compaction)."""
    pos, drops = [], 0
    for a, b in zip(reqs, reqs[1:]):
        dC = b["C"] - a["C"]
        if dC >= 0:
            pos.append(dC)
        else:
            drops += 1
    return pos, drops


def describe(name, outs, gs, extra=""):
    if not outs:
        return
    print(f"\n{name}  (n={len(outs)} round-trips){extra}")
    print(f"  m  mean {st.mean(outs):7.0f} | median {st.median(outs):6.0f} "
          f"| p90 {pct(outs,90):7.0f} | p99 {pct(outs,99):8.0f} | max {max(outs):8.0f}")
    if gs:
        print(f"  g  mean {st.mean(gs):7.0f} | median {st.median(gs):6.0f} "
              f"| p90 {pct(gs,90):7.0f} | p99 {pct(gs,99):8.0f} | max {max(gs):8.0f}")


# ---------------------------------------------------------------- main sessions
print("=" * 78)
print("DIRECTOR / MAIN SESSIONS  (isSidechain=false, deduped by requestId)")
print("=" * 78)

files = sorted(glob.glob(os.path.join(DIR, "*.jsonl")),
               key=lambda p: os.path.getmtime(p))
all_out, all_g = [], []
rows = []
for f in files:
    reqs = [r for r in load_requests(f) if not r["side"]]
    if len(reqs) < 15:
        continue
    outs = [r["out"] for r in reqs]
    gs, drops = growth(reqs)
    all_out += outs
    all_g += gs
    date = __import__("time").strftime("%m-%d", __import__("time").localtime(os.path.getmtime(f)))
    rows.append((date, os.path.basename(f)[:8], len(reqs), st.mean(outs), st.median(outs),
                 st.mean(gs) if gs else 0, st.median(gs) if gs else 0,
                 reqs[0]["C"], max(r["C"] for r in reqs), drops))

print(f"\n{'date':6} {'session':9} {'RT':>4} {'m_mean':>7} {'m_med':>6} "
      f"{'g_mean':>7} {'g_med':>6} {'C_first':>8} {'C_max':>8} {'drops':>5}")
for r in rows:
    print(f"{r[0]:6} {r[1]:9} {r[2]:4d} {r[3]:7.0f} {r[4]:6.0f} "
          f"{r[5]:7.0f} {r[6]:6.0f} {r[7]:8.0f} {r[8]:8.0f} {r[9]:5d}")

describe("POOLED ALL MAIN SESSIONS", all_out, all_g)

# how much of the mean is carried by the tail
if all_out:
    s = sorted(all_out, reverse=True)
    top = sum(s[:max(1, len(s) // 20)])
    print(f"  -> top 5% of responses carry {100*top/sum(s):.0f}% of all output tokens")
if all_g:
    s = sorted(all_g, reverse=True)
    top = sum(s[:max(1, len(s) // 20)])
    print(f"  -> top 5% of round-trips carry {100*top/sum(s):.0f}% of all context growth")

# ------------------------------------------------- growth shape (concavity test)
print("\n" + "=" * 78)
print("GROWTH SHAPE: is g front-loaded? (per-session, by quintile of round-trip)")
print("=" * 78)
buckets = defaultdict(list)
for f in files:
    reqs = [r for r in load_requests(f) if not r["side"]]
    if len(reqs) < 40:
        continue
    gs, _ = growth(reqs)
    n = len(gs)
    for i, v in enumerate(gs):
        buckets[min(4, i * 5 // n)].append(v)
print(f"\n{'quintile':10} {'n':>6} {'g_mean':>8} {'g_median':>9}")
for q in range(5):
    v = buckets[q]
    if v:
        print(f"{q+1:<10} {len(v):6d} {st.mean(v):8.0f} {st.median(v):9.0f}")

# -------------------------------------------------- model x effort cross-tab
# g[i] = C[i+1]-C[i] is caused BY round-trip i, so attribute the diff to record i.
print("\n" + "=" * 78)
print("MAIN SESSIONS: m and g BY MODEL x EFFORT")
print("=" * 78)
cell_m, cell_g = defaultdict(list), defaultdict(list)
for f in files:
    reqs = [r for r in load_requests(f) if not r["side"]]
    if len(reqs) < 15:
        continue
    for i, r in enumerate(reqs):
        key = (r["model"] or "?", r["effort"] or "?")
        cell_m[key].append(r["out"])
        if i + 1 < len(reqs):
            dC = reqs[i + 1]["C"] - r["C"]
            if dC >= 0:
                cell_g[key].append(dC)

print(f"\n{'model':18} {'effort':8} {'RT':>6} {'m_mean':>7} {'m_med':>6} {'m_p90':>7} "
      f"{'g_mean':>7} {'g_med':>6} {'g_p90':>7}")
for key in sorted(cell_m, key=lambda k: (-len(cell_m[k]))):
    o, g_ = cell_m[key], cell_g[key]
    if len(o) < 25:
        continue
    print(f"{key[0][:18]:18} {str(key[1])[:8]:8} {len(o):6d} {st.mean(o):7.0f} {st.median(o):6.0f} "
          f"{pct(o,90):7.0f} {st.mean(g_) if g_ else 0:7.0f} {st.median(g_) if g_ else 0:6.0f} "
          f"{pct(g_,90) if g_ else 0:7.0f}")

# marginals
for axis, ix in (("MODEL", 0), ("EFFORT", 1)):
    agg_m, agg_g = defaultdict(list), defaultdict(list)
    for key in cell_m:
        agg_m[key[ix]] += cell_m[key]
        agg_g[key[ix]] += cell_g[key]
    print(f"\n  -- marginal by {axis} --")
    print(f"  {'':20} {'RT':>6} {'m_mean':>7} {'m_med':>6} {'g_mean':>7} {'g_med':>6}")
    for k in sorted(agg_m, key=lambda x: -len(agg_m[x])):
        o, g_ = agg_m[k], agg_g[k]
        if len(o) < 25:
            continue
        print(f"  {str(k)[:20]:20} {len(o):6d} {st.mean(o):7.0f} {st.median(o):6.0f} "
              f"{st.mean(g_) if g_ else 0:7.0f} {st.median(g_) if g_ else 0:6.0f}")

# ------------------------------------------------------------------- subagents
print("\n" + "=" * 78)
print("SUBAGENTS, BY ROLE (agentType from sibling .meta.json)")
print("=" * 78)
by_role_out, by_role_g, by_role_n = defaultdict(list), defaultdict(list), defaultdict(int)
for meta in glob.glob(os.path.join(DIR, "*", "subagents", "*.meta.json")):
    try:
        m = json.load(open(meta))
    except Exception:
        continue
    role = m.get("agentType", "?")
    tr = meta.replace(".meta.json", ".jsonl")
    reqs = load_requests(tr)
    if not reqs:
        continue
    by_role_n[role] += 1
    by_role_out[role] += [r["out"] for r in reqs]
    gs, _ = growth(reqs)
    by_role_g[role] += gs

print(f"\n{'role':18} {'agents':>7} {'RT':>6} {'RT/agent':>9} {'m_mean':>7} {'m_med':>6} "
      f"{'g_mean':>7} {'g_med':>6}")
for role in sorted(by_role_out, key=lambda k: -len(by_role_out[k])):
    o, g_, n = by_role_out[role], by_role_g[role], by_role_n[role]
    print(f"{role[:18]:18} {n:7d} {len(o):6d} {len(o)/n:9.1f} {st.mean(o):7.0f} "
          f"{st.median(o):6.0f} {st.mean(g_) if g_ else 0:7.0f} {st.median(g_) if g_ else 0:6.0f}")

tot_sub = sum(sum(v) for v in by_role_out.values())
print(f"\n  subagent round-trips: {sum(len(v) for v in by_role_out.values())} "
      f"across {sum(by_role_n.values())} agents")
