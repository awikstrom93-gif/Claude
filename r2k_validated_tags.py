"""
r2k_validated_tags.py  --  the closed loop between recovery and the classifier. This is what makes the
recovery residual SHRINK over time instead of re-discovering the same non-standard tags every run.

THE PROBLEM it solves: the classifier (r2k_dera_classify.py) picks each role from a curated list of
as-filed tags (REV, COGS, OINC, ...). When a filer uses a tag that isn't on the list, the role comes out
BLANK, and r2k_metric_recover.py / r2k_revenue_recover.py have to recover it downstream by locating the
right as-filed tag against a Morningstar target. That recovery is correct, but it re-does the same work
on every build -- the tag is never learned.

THE LOOP: whenever a recovery pass ADOPTS a specific as-filed tag (provenance `asfiled:<tag>`), it means
that tag WAS the right line for that role and reconciled to an independent vendor. That is a validated
promotion candidate. Recovery records it here; the classifier reads it back and appends the tag to the
matching role list at LOWEST priority. Next build, the classifier picks that tag AT THE SOURCE, the role
is no longer blank, and recovery has nothing to do for it. The hole closes permanently.

SAFETY (why appending validated tags can't corrupt a good pick):
  * appended at the END of each list -> canonical curated tags always win; a promoted tag is used ONLY
    for a filing that has NO canonical tag for that role (exactly the profile it was validated on),
  * only TARGET-VALIDATED tags promote by default (they reconciled to Morningstar within tolerance);
    solo/no-target adoptions (cash, capex) need corroboration across >=2 company-years,
  * the classifier's identity/tie-out still gates the value; promotion only widens the CANDIDATE set,
  * counts are DISTINCT company-years and merged by MAX, so re-running on the same data is idempotent
    (a repeated build never inflates a count), while a larger dataset can only grow it.

FILE  validated_tags.csv  columns: field, tag, target_years, notarget_years
  field         = fundamentals role (revenue, cost_of_revenue, operating_income, total_equity, cash, capex)
  tag           = the as-filed XBRL tag that was validated for that role
  target_years  = distinct company-years where it was adopted against a Morningstar target
  notarget_years= distinct company-years where it was adopted solo (no target, single curated tag)

API   record_run(runcounts)              -- called by the recovery scripts (merge by MAX, idempotent)
      load_promotions(min_target, min_notarget) -> {field: [tags]}  -- called by the classifier
"""
import csv
import os
from collections import defaultdict
from pathlib import Path

BASE = Path(os.environ.get("R2KG_BASE", "."))
PATH = BASE / "validated_tags.csv"
FIELDS = ["field", "tag", "target_years", "notarget_years"]

# recovery `field` -> the classifier role list it should extend. Fields not here (gross_profit,
# free_cash_flow) are identity-derived in the classifier and have no promotable candidate list.
PROMOTABLE = {"revenue", "cost_of_revenue", "operating_income", "total_equity", "cash", "capex"}


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _load_raw():
    """{(field, tag): [target_years, notarget_years]} from the file (empty if absent)."""
    out = {}
    if not PATH.exists():
        return out
    with open(PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fld, tag = (r.get("field") or "").strip(), (r.get("tag") or "").strip()
            if fld and tag:
                out[(fld, tag)] = [_int(r.get("target_years")), _int(r.get("notarget_years"))]
    return out


def record_run(runcounts):
    """Merge THIS run's validated tags into validated_tags.csv, by MAX (idempotent on re-run).

    runcounts: {(field, tag): (target_years, notarget_years)} -- distinct company-year counts observed
    in this full recovery pass. MAX (not sum) so repeatedly rebuilding the same data never inflates a
    count, while a genuinely larger sample can raise it. Returns (n_rows, n_new_pairs)."""
    merged = _load_raw()
    n_new = 0
    for (fld, tag), (tgt, notgt) in runcounts.items():
        if not fld or not tag:
            continue
        key = (fld, tag)
        if key not in merged:
            merged[key] = [0, 0]
            n_new += 1
        merged[key][0] = max(merged[key][0], int(tgt or 0))
        merged[key][1] = max(merged[key][1], int(notgt or 0))
    rows = sorted(merged.items(), key=lambda kv: (kv[0][0], -kv[1][0], -kv[1][1], kv[0][1]))
    with open(PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for (fld, tag), (tgt, notgt) in rows:
            w.writerow({"field": fld, "tag": tag, "target_years": tgt, "notarget_years": notgt})
    return len(merged), n_new


def load_promotions(min_target=None, min_notarget=None):
    """{field: [tags]} to append to the classifier's role lists. A tag promotes if it was adopted
    against a Morningstar target in >= min_target company-years, OR solo (no target) in >= min_notarget.
    Env R2KG_TAGPROMOTE_MIN overrides min_target; R2KG_TAGPROMOTE_MIN_NOTARGET overrides min_notarget."""
    min_target = int(os.environ.get("R2KG_TAGPROMOTE_MIN", min_target if min_target is not None else 1))
    min_notarget = int(os.environ.get("R2KG_TAGPROMOTE_MIN_NOTARGET",
                                      min_notarget if min_notarget is not None else 2))
    promo = defaultdict(list)
    for (fld, tag), (tgt, notgt) in sorted(_load_raw().items(), key=lambda kv: (-kv[1][0], -kv[1][1])):
        if fld not in PROMOTABLE:
            continue
        if tgt >= min_target or notgt >= min_notarget:
            promo[fld].append(tag)
    return dict(promo)


if __name__ == "__main__":
    raw = _load_raw()
    if not raw:
        print(f"  {PATH.name} not present yet -- run a recovery pass to populate it.")
    else:
        print(f"  {PATH.name}: {len(raw)} validated (field, tag) pairs")
        promo = load_promotions()
        for fld in sorted(promo):
            print(f"    {fld:>16} would promote {len(promo[fld])}: {', '.join(promo[fld][:6])}"
                  + (" ..." if len(promo[fld]) > 6 else ""))
