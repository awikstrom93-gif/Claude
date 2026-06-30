# Build Audit — R2000G Benchmark Review pipeline

Scope: how every step builds the constituent **universe**, resolves **CIK identity**, picks the
fiscal year (**fy0**), and computes **metrics**. Goal: find where steps can silently disagree
(the CZR/2016 episode was a symptom, not the disease).

## TL;DR

| Concern | Status |
|---|---|
| Universe loader | **1 divergence** — step3 was the lone user of `load_membership`; steps 5/6/8/9 use `load_monthly_holdings`+`annual_spine`. |
| Snapshot date / `TARGET_MONTH` | Consistent (all `SNAP_MONTH=4`; probe confirmed identical 4/30 dates every year). |
| `pick_fy0`, `company_metrics` | Single source (all import from step3). Consistent by construction. |
| CIK resolution | **Root cause.** Legacy step3 = *temporal-first*; steps 5/6/8/9 = *base-first*. The two maps disagree → CZR resolved to two different companies. |
| Shared helpers (`norm_facts`, `ticker_cik_map`, `fund_for`, `annual_spine`) | Copy-pasted in 3–4 files. **Logic byte-identical today**, but no single source of truth → latent drift hazard. |
| step4 (performance) | Does not touch fundamentals; out of scope for aggregate-NI consistency. |

## The actual root cause (CIK maps, not the loader)

Every modern step resolves identity as:

```python
def hcik(h): return h["cik"] or tmap.get(h["nt"])   # tmap = base-first (security map wins)
```

Legacy step3 resolved it as **temporal-first**:

```python
cand = temporal.get(skey,{}).get(raw) or temporal.get(skey,{}).get(raw.upper()) or base.get(nt)
```

For CZR @ 2016, base-first → **858339** (Caesars Entertainment Corp, the real April-2016 constituent),
temporal-first → **1590895** (the modern Eldorado-Caesars entity that holds the ticker *today*).
A point-in-time map returning the *present-day* CIK for a *historical* snapshot is backwards — so
**`temporal_cik_map.json` is the thing that's wrong**, and legacy step3 was the only consumer that
trusted it first. The $6.7B 2016 NI gap is ~$5.8B CZR + ~$0.8B BBBY; everything else is <$50M.

## ⚠️ Caveat on the fix already shipped

The committed change makes step3 read the holdings file's own `CIK` column and prefer it. **That only
helps if the R2000G holdings file actually has a populated CIK column** — and step5's own comment says
it does *not* (`# R2000G holdings lack a CIK column`). If that comment is right, the shipped fix is
**inert** (it falls through to the still-temporal-first fallback) and the correct fix is instead to make
step3's fallback **base-first**, matching steps 5/6/8/9. `r2k_build_audit_probe.py` settles this (Q1).

## Recommended remediation (pending probe confirmation)

1. **Identity:** make step3 resolve CIK identically to the other steps — `cik_file or tmap.get(nt)`
   with a **base-first** `tmap` fallback (drop temporal-first). One source of truth for identity.
2. **Loader:** retire `load_membership`; have step3 consume `load_monthly_holdings`+`annual_spine`
   like everyone else, so there is exactly one universe path.
3. **Helpers:** lift `norm_facts`/`ticker_cik_map`/`fund_for`/`annual_spine` into one shared module
   (e.g. `r2k_universe.py`) and import everywhere — kill the copy-paste before it drifts.
4. **Map:** rebuild/repair `temporal_cik_map.json` so historical snapshots carry the period's CIK,
   not today's. (Lower priority once step3 stops trusting it first, but it's a real data bug.)
5. **Guard:** a preflight that asserts overlapping aggregates (e.g. 2016 Total NI) agree across
   steps within tolerance before step7 consolidates — the tripwire that would have caught CZR.

Items 1–2 collapse the divergence; 3–5 prevent recurrence. None touch the accounting engine.
