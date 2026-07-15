"""Per-item verdict table with persona, memory, tool call, and timing breakdown.

Usage:
    python -m harness.analyze                          # all runs, text
    python -m harness.analyze --run-id pref-04
    python -m harness.analyze --run-id pref-04 --band-size 50
    python -m harness.analyze --run-id pref-04 --html  # writes report.html
    python -m harness.analyze --run-id pref-04 --html out.html
    python -m harness.analyze --run-id pref-04 --bands          # persona + caps by band
    python -m harness.analyze --run-id pref-04 --bands --band-size 25
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "harness.db"

VERDICT_LABEL = {
    "local2_better": "L2 WIN",
    "native_better": "NAT WIN",
    "tie":           "TIE",
    None:            "—",
}

V_SHORT = {
    "local2_better": "L2>",
    "native_better": "N>",
    "tie":           "tie",
    "":              "—",
}

PERSONAS = ["analytic", "empathic", "creative", "pragmatic", "bridging"]


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(v, unit="s"):
    return f"{v}{unit}" if v is not None else "—"


def _load_rows(con: sqlite3.Connection, run_id: str | None) -> list[dict]:
    where = "WHERE i.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()

    rows_data = con.execute(f"""
        SELECT
          ROW_NUMBER() OVER (ORDER BY i.timestamp) AS row_num,
          i.run_id,
          i.item_id,
          i.prompt,
          j.verdict,
          j.verdict_pass1,
          j.verdict_pass2,
          j.rubric,
          i.arm_a_tool_calls,
          i.arm_b_tool_calls,
          i.arm_a_capsules,
          i.arm_a_candidates,
          i.arm_a_t_submit,
          i.arm_a_t_first_event,
          i.arm_a_t_complete,
          i.arm_b_t_submit,
          i.arm_b_t_first_event,
          i.arm_b_t_complete
        FROM items i
        LEFT JOIN judgments j
               ON j.item_id = i.item_id
              AND j.run_id  = i.run_id
        {where}
        ORDER BY i.timestamp
    """, params).fetchall()

    rows = []
    for r in rows_data:
        tool_calls = json.loads(r["arm_a_tool_calls"] or "[]")
        capsules   = json.loads(r["arm_a_capsules"]   or "[]")

        b_tool_calls = json.loads(r["arm_b_tool_calls"] or "[]")

        persona_mode = ""
        web_search = 0
        lib_search = 0
        for tc in tool_calls:
            t = tc.get("tool", "")
            if t == "persona":
                args = tc.get("args", {})
                persona_mode = args.get("name") or args.get("mode") or "?"
            elif t == "web_search":
                web_search += 1
            elif t in ("search_library", "consult_librarian"):
                lib_search += 1

        web_n = sum(1 for tc in b_tool_calls if tc.get("tool") == "web_search")

        a_sub  = _safe_float(r["arm_a_t_submit"])
        a_fe   = _safe_float(r["arm_a_t_first_event"])
        a_comp = _safe_float(r["arm_a_t_complete"])
        b_sub  = _safe_float(r["arm_b_t_submit"])
        b_fe   = _safe_float(r["arm_b_t_first_event"])
        b_comp = _safe_float(r["arm_b_t_complete"])

        prompt_raw = (r["prompt"] or "").strip().replace("\n", " ")
        rubric_raw = r["rubric"] or ""
        rows.append({
            "row":          r["row_num"],
            "run_id":       r["run_id"] or "",
            "prompt":       prompt_raw,
            "verdict":      r["verdict"],
            "vp1":          r["verdict_pass1"] or "",
            "vp2":          r["verdict_pass2"] or "",
            "rubric":       rubric_raw,
            "rubric_cat":   _classify_rubric(rubric_raw) if rubric_raw else "—",
            "persona":      persona_mode,
            "caps":     len(capsules),
            "web":      web_search,
            "web_n":    web_n,
            "lib":      lib_search,
            "a_ttfe":   round(a_fe - a_sub, 1)   if a_fe   and a_sub  else None,
            "a_total":  round(a_comp - a_sub, 1)  if a_comp and a_sub  else None,
            "b_ttfe":   round(b_fe - b_sub, 1)    if b_fe   and b_sub  else None,
            "b_total":  round(b_comp - b_sub, 1)  if b_comp and b_sub  else None,
        })
    return rows


# ── Text output ───────────────────────────────────────────────────────────────

def run_text(run_id: str | None, band_size: int) -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = _load_rows(con, run_id)

    header = (
        f"{'#':>3}  {'Verdict':<9}  {'P1':>5}  {'P2':>5}"
        f"  {'Persona':<10}  {'Caps':>4}"
        f"  {'WebL':>4}  {'WebN':>4}  {'Lib':>3}"
        f"  {'L2-ttfe':>7}  {'L2-tot':>6}  {'N-ttfe':>6}  {'N-tot':>6}"
    )
    print(header)
    print("─" * len(header))

    band_stats: dict[int, dict] = {}

    for r in rows:
        band = ((r["row"] - 1) // band_size) + 1
        bs = band_stats.setdefault(band, {
            "l2": 0, "nat": 0, "tie": 0, "n": 0,
            "cap_sum": 0, "web_sum": 0,
        })
        bs["n"]       += 1
        bs["cap_sum"] += r["caps"]
        bs["web_sum"] += r["web"]
        if r["verdict"] == "local2_better":   bs["l2"] += 1
        elif r["verdict"] == "native_better": bs["nat"] += 1
        elif r["verdict"] == "tie":           bs["tie"] += 1

        if r["row"] > 1 and (r["row"] - 1) % band_size == 0:
            _print_band_summary(band_stats[band - 1], band - 1, band_size)

        vl  = VERDICT_LABEL.get(r["verdict"], r["verdict"] or "—")
        p1s = V_SHORT.get(r["vp1"], r["vp1"][:3] if r["vp1"] else "—")
        p2s = V_SHORT.get(r["vp2"], r["vp2"][:3] if r["vp2"] else "—")

        print(
            f"{r['row']:>3}  {vl:<9}  {p1s:>5}  {p2s:>5}"
            f"  {r['persona'] or '—':<10}  {r['caps']:>4}"
            f"  {r['web']:>4}  {r['web_n']:>4}  {r['lib']:>3}"
            f"  {_fmt(r['a_ttfe']):>7}  {_fmt(r['a_total']):>6}"
            f"  {_fmt(r['b_ttfe']):>6}  {_fmt(r['b_total']):>6}"
        )

    if rows:
        last_band = ((rows[-1]["row"] - 1) // band_size) + 1
        _print_band_summary(band_stats[last_band], last_band, band_size,
                            last_row=rows[-1]["row"])

    total_l2  = sum(b["l2"]  for b in band_stats.values())
    total_nat = sum(b["nat"] for b in band_stats.values())
    total_tie = sum(b["tie"] for b in band_stats.values())
    total_n   = len(rows)
    print(f"\nTotal: {total_n} items  |  L2={total_l2} ({total_l2*100//max(total_n,1)}%)"
          f"  NAT={total_nat} ({total_nat*100//max(total_n,1)}%)"
          f"  TIE={total_tie} ({total_tie*100//max(total_n,1)}%)")

    _print_persona_breakdown(rows)
    _print_caps_breakdown(rows)


def _print_band_summary(bs: dict, band: int, band_size: int,
                        last_row: int | None = None) -> None:
    n   = max(bs["n"], 1)
    lo  = (band - 1) * band_size + 1
    hi  = last_row if last_row is not None else band * band_size
    win_non_tie = bs["l2"] + bs["nat"]
    l2_rate = bs["l2"] * 100 // win_non_tie if win_non_tie else 0
    print(
        f"\n  ── Band {band} (items {lo}–{hi}):"
        f"  L2={bs['l2']} ({bs['l2']*100//n}%)"
        f"  NAT={bs['nat']} ({bs['nat']*100//n}%)"
        f"  TIE={bs['tie']} ({bs['tie']*100//n}%)"
        f"  |  among decisive: L2={l2_rate}%"
        f"  |  avg_caps={bs['cap_sum']/n:.1f}"
        f"  web={bs['web_sum']} ──\n"
    )


def _print_persona_breakdown(rows: list[dict]) -> None:
    stats: dict[str, dict] = {}
    for r in rows:
        p = r["persona"] or "none"
        s = stats.setdefault(p, {"l2": 0, "nat": 0, "tie": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1
        elif r["verdict"] == "tie":           s["tie"] += 1

    print("\nPersona breakdown:")
    print(f"  {'Persona':<12}  {'n':>4}  {'L2':>4}  {'NAT':>4}  {'TIE':>4}  {'L2%':>5}  {'decisive-L2%':>13}")
    for persona, s in sorted(stats.items(), key=lambda x: -x[1]["n"]):
        n = max(s["n"], 1)
        decisive = s["l2"] + s["nat"]
        dl2 = s["l2"] * 100 // decisive if decisive else 0
        print(f"  {persona:<12}  {s['n']:>4}  {s['l2']:>4}  {s['nat']:>4}"
              f"  {s['tie']:>4}  {s['l2']*100//n:>4}%  {dl2:>12}%")


def _print_caps_breakdown(rows: list[dict]) -> None:
    stats: dict[int, dict] = {}
    for r in rows:
        s = stats.setdefault(r["caps"], {"l2": 0, "nat": 0, "tie": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1
        elif r["verdict"] == "tie":           s["tie"] += 1

    print("\nCapsules-used breakdown:")
    print(f"  {'Caps':>5}  {'n':>4}  {'L2':>4}  {'NAT':>4}  {'TIE':>4}  {'L2%':>5}  {'decisive-L2%':>13}")
    for caps, s in sorted(stats.items()):
        n = max(s["n"], 1)
        decisive = s["l2"] + s["nat"]
        dl2 = s["l2"] * 100 // decisive if decisive else 0
        print(f"  {caps:>5}  {s['n']:>4}  {s['l2']:>4}  {s['nat']:>4}"
              f"  {s['tie']:>4}  {s['l2']*100//n:>4}%  {dl2:>12}%")


# ── Band drill-down ───────────────────────────────────────────────────────────

def run_bands(run_id: str | None, band_size: int) -> None:
    """Persona and caps breakdown within each band."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = _load_rows(con, run_id)

    if not rows:
        print("No data.")
        return

    last_band = ((rows[-1]["row"] - 1) // band_size) + 1

    for band in range(1, last_band + 1):
        band_rows = [r for r in rows
                     if ((r["row"] - 1) // band_size) + 1 == band]
        if not band_rows:
            continue

        lo = band_rows[0]["row"]
        hi = band_rows[-1]["row"]
        n  = len(band_rows)
        l2  = sum(1 for r in band_rows if r["verdict"] == "local2_better")
        nat = sum(1 for r in band_rows if r["verdict"] == "native_better")
        tie = sum(1 for r in band_rows if r["verdict"] == "tie")
        decisive = l2 + nat
        dl2 = l2 * 100 // decisive if decisive else 0

        print(f"\n{'═'*68}")
        print(f"  Band {band}  (items {lo}–{hi},  n={n})"
              f"   L2={l2} ({l2*100//n}%)  NAT={nat} ({nat*100//n}%)  "
              f"TIE={tie} ({tie*100//n}%)  decisive-L2={dl2}%")
        print(f"{'═'*68}")

        # Persona sub-table
        ps: dict[str, dict] = {}
        for r in band_rows:
            p = r["persona"] or "none"
            s = ps.setdefault(p, {"n": 0, "l2": 0, "nat": 0, "tie": 0})
            s["n"] += 1
            if r["verdict"] == "local2_better":   s["l2"] += 1
            elif r["verdict"] == "native_better": s["nat"] += 1
            elif r["verdict"] == "tie":           s["tie"] += 1

        print(f"\n  Persona")
        print(f"  {'name':<12}  {'n':>4}  {'L2':>3}  {'NAT':>3}  {'TIE':>3}  {'tie%':>5}  {'dL2%':>6}")
        for p, s in sorted(ps.items(), key=lambda x: -x[1]["n"]):
            sn = max(s["n"], 1)
            sd = s["l2"] + s["nat"]
            sdl2 = s["l2"] * 100 // sd if sd else 0
            print(f"  {p:<12}  {s['n']:>4}  {s['l2']:>3}  {s['nat']:>3}  {s['tie']:>3}"
                  f"  {s['tie']*100//sn:>4}%  {sdl2:>5}%")

        # Caps sub-table
        cs: dict[int, dict] = {}
        for r in band_rows:
            s = cs.setdefault(r["caps"], {"n": 0, "l2": 0, "nat": 0, "tie": 0})
            s["n"] += 1
            if r["verdict"] == "local2_better":   s["l2"] += 1
            elif r["verdict"] == "native_better": s["nat"] += 1
            elif r["verdict"] == "tie":           s["tie"] += 1

        print(f"\n  Capsules")
        print(f"  {'caps':>4}  {'n':>4}  {'L2':>3}  {'NAT':>3}  {'TIE':>3}  {'tie%':>5}  {'dL2%':>6}")
        for c, s in sorted(cs.items()):
            sn = max(s["n"], 1)
            sd = s["l2"] + s["nat"]
            sdl2 = s["l2"] * 100 // sd if sd else 0
            print(f"  {c:>4}  {s['n']:>4}  {s['l2']:>3}  {s['nat']:>3}  {s['tie']:>3}"
                  f"  {s['tie']*100//sn:>4}%  {sdl2:>5}%")

    print()
    total_n   = len(rows)
    total_l2  = sum(1 for r in rows if r["verdict"] == "local2_better")
    total_nat = sum(1 for r in rows if r["verdict"] == "native_better")
    total_tie = sum(1 for r in rows if r["verdict"] == "tie")
    decisive  = total_l2 + total_nat
    print(f"Overall: {total_n} items  L2={total_l2} ({total_l2*100//max(total_n,1)}%)"
          f"  NAT={total_nat} ({total_nat*100//max(total_n,1)}%)"
          f"  TIE={total_tie} ({total_tie*100//max(total_n,1)}%)"
          f"  decisive-L2={total_l2*100//decisive if decisive else 0}%")


# ── Rubric classification ─────────────────────────────────────────────────────

def _classify_rubric(rubric: str) -> str:
    r = rubric.lower()
    if any(w in r for w in ("ambiguous", "unclear", "vague", "clarif")):
        return "ambiguity"
    if any(w in r for w in ("empathy", "emotional", "emotion", "delicate", "mental health")):
        return "empathy"
    if any(w in r for w in ("cultural", "diversity", "stereotype", "multilingual")):
        return "cultural"
    if any(w in r for w in ("evidence", "citation", "attribution", "public figure", "accurate", "factual")):
        return "accuracy"
    if any(w in r for w in ("humor", "sarcasm", "wit", "entertain", "engagement", "compelling", "creative")):
        return "creativity"
    if any(w in r for w in ("terminolog", "jargon")):
        return "technical"
    if any(w in r for w in ("adapt", "comprehension", "language skill", "language style", "language complex")):
        return "adaptability"
    return "other"


# ── HTML output ───────────────────────────────────────────────────────────────

def run_html(run_id: str | None, band_size: int, out_path: Path) -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = _load_rows(con, run_id)

    # Collect all persona modes seen (beyond the known list)
    seen_personas = []
    for p in PERSONAS:
        if any(r["persona"] == p for r in rows):
            seen_personas.append(p)
    # Any unknown modes
    for r in rows:
        if r["persona"] and r["persona"] not in seen_personas:
            seen_personas.append(r["persona"])

    # Band aggregation
    band_stats: dict[int, dict] = {}
    for r in rows:
        band = ((r["row"] - 1) // band_size) + 1
        bs = band_stats.setdefault(band, {
            "l2": 0, "nat": 0, "tie": 0, "pending": 0, "n": 0,
            "cap_sum": 0, "web_sum": 0, "lib_sum": 0,
            "lo": (band - 1) * band_size + 1, "hi": band * band_size,
        })
        bs["n"]       += 1
        bs["hi"]       = r["row"]
        bs["cap_sum"] += r["caps"]
        bs["web_sum"] += r["web"]
        bs["lib_sum"] += r["lib"]
        if r["verdict"] == "local2_better":   bs["l2"] += 1
        elif r["verdict"] == "native_better": bs["nat"] += 1
        elif r["verdict"] == "tie":           bs["tie"] += 1
        else:                                 bs["pending"] += 1

    # Persona summary table
    persona_stats: dict[str, dict] = {}
    for r in rows:
        p = r["persona"] or "none"
        s = persona_stats.setdefault(p, {"l2": 0, "nat": 0, "tie": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1
        elif r["verdict"] == "tie":           s["tie"] += 1

    caps_stats: dict[int, dict] = {}
    for r in rows:
        s = caps_stats.setdefault(r["caps"], {"l2": 0, "nat": 0, "tie": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1
        elif r["verdict"] == "tie":           s["tie"] += 1

    rubric_stats: dict[str, dict] = {}
    for r in rows:
        cat = _classify_rubric(r["rubric"]) if r["rubric"] else "other"
        s = rubric_stats.setdefault(cat, {"l2": 0, "nat": 0, "tie": 0, "n": 0})
        s["n"] += 1
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1
        elif r["verdict"] == "tie":           s["tie"] += 1

    total_n   = len(rows)
    total_l2  = sum(b["l2"]  for b in band_stats.values())
    total_nat = sum(b["nat"] for b in band_stats.values())
    total_tie = sum(b["tie"] for b in band_stats.values())

    title = f"LoCAL2 Harness — {run_id or 'all runs'}"
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── band drill-down HTML ────────────────────────────────────────────
    def _mini_summary_table(data: dict, key_label: str, sort_numeric: bool = False) -> str:
        header = (f'<tr><th>{key_label}</th><th>n</th><th>L2</th><th>NAT</th>'
                  f'<th>TIE</th><th>tie%</th><th>dL2%</th><th>dist</th></tr>')
        body_rows = []
        sort_key = (lambda x: x[0]) if sort_numeric else (lambda x: -x[1]["n"])
        for k, s in sorted(data.items(), key=sort_key):
            sn = max(s["n"], 1)
            sd = s["l2"] + s["nat"]
            sdl2 = s["l2"] * 100 // sd if sd else 0
            bl2  = s["l2"]  * 100 // sn
            bnat = s["nat"] * 100 // sn
            btie = s["tie"] * 100 // sn
            body_rows.append(
                f'<tr>'
                f'<td class="ps-name">{k}</td>'
                f'<td class="ps-n">{s["n"]}</td>'
                f'<td class="ps-l2">{s["l2"]}</td>'
                f'<td class="ps-nat">{s["nat"]}</td>'
                f'<td class="ps-tie">{s["tie"]}</td>'
                f'<td class="ps-pct">{s["tie"]*100//sn}%</td>'
                f'<td class="ps-dl2">{sdl2}%</td>'
                f'<td class="ps-bar"><div class="mini-bar">'
                f'<div class="mb-l2" style="width:{bl2}%"></div>'
                f'<div class="mb-nat" style="width:{bnat}%"></div>'
                f'<div class="mb-tie" style="width:{btie}%"></div>'
                f'</div></td>'
                f'</tr>'
            )
        return f'<table class="summary-table"><thead>{header}</thead><tbody>{"".join(body_rows)}</tbody></table>'

    last_band = ((rows[-1]["row"] - 1) // band_size) + 1 if rows else 0
    band_sections_html = []
    for band in range(1, last_band + 1):
        band_rows = [r for r in rows if ((r["row"] - 1) // band_size) + 1 == band]
        if not band_rows:
            continue
        lo = band_rows[0]["row"]; hi = band_rows[-1]["row"]; bn = len(band_rows)
        bl2  = sum(1 for r in band_rows if r["verdict"] == "local2_better")
        bnat = sum(1 for r in band_rows if r["verdict"] == "native_better")
        btie = sum(1 for r in band_rows if r["verdict"] == "tie")
        bd   = bl2 + bnat
        bdl2 = bl2 * 100 // bd if bd else 0

        ps: dict[str, dict] = {}
        cs: dict[int, dict] = {}
        rs: dict[str, dict] = {}
        for r in band_rows:
            p = r["persona"] or "none"
            sp = ps.setdefault(p, {"n":0,"l2":0,"nat":0,"tie":0})
            sp["n"] += 1
            sc = cs.setdefault(r["caps"], {"n":0,"l2":0,"nat":0,"tie":0})
            sc["n"] += 1
            cat = _classify_rubric(r["rubric"]) if r["rubric"] else "other"
            sr = rs.setdefault(cat, {"n":0,"l2":0,"nat":0,"tie":0})
            sr["n"] += 1
            for d in (sp, sc, sr):
                if r["verdict"] == "local2_better":   d["l2"] += 1
                elif r["verdict"] == "native_better": d["nat"] += 1
                elif r["verdict"] == "tie":           d["tie"] += 1

        header_html = (
            f'<div class="band-header">'
            f'<span class="bh-title">Band {band} &nbsp; items {lo}–{hi}</span>'
            f'&nbsp;&nbsp;<span class="bs-l2">L2 {bl2} ({bl2*100//bn}%)</span>'
            f'&nbsp;<span class="bs-nat">NAT {bnat} ({bnat*100//bn}%)</span>'
            f'&nbsp;<span class="bs-tie">TIE {btie} ({btie*100//bn}%)</span>'
            f'&nbsp;&nbsp;decisive-L2 {bdl2}%'
            f'</div>'
        )
        band_sections_html.append(
            f'<div class="band-section">'
            f'{header_html}'
            f'<div class="band-grid">'
            f'{_mini_summary_table(ps, "Persona")}'
            f'{_mini_summary_table(cs, "Caps", sort_numeric=True)}'
            f'{_mini_summary_table(rs, "Rubric")}'
            f'</div></div>'
        )

    # ── persona colspan header ──────────────────────────────────────────
    n_persona_cols = len(seen_personas) + 1   # +1 for "none"
    persona_th = "".join(f'<th class="persona-col">{p}</th>' for p in seen_personas)
    persona_th += '<th class="persona-col none-col">none</th>'

    # ── build table rows ────────────────────────────────────────────────
    table_rows_html = []
    prev_band = 0

    for r in rows:
        band = ((r["row"] - 1) // band_size) + 1

        # Band separator row
        if band != prev_band and prev_band > 0:
            bs = band_stats[prev_band]
            n  = max(bs["n"], 1)
            decisive = bs["l2"] + bs["nat"]
            dl2 = bs["l2"] * 100 // decisive if decisive else 0
            table_rows_html.append(
                f'<tr class="band-sep">'
                f'<td colspan="{5 + n_persona_cols + 4}" class="band-summary">'
                f'Band {prev_band} &nbsp;·&nbsp; items {bs["lo"]}–{bs["hi"]}'
                f'&nbsp;&nbsp;<span class="bs-l2">L2 {bs["l2"]} ({bs["l2"]*100//n}%)</span>'
                f'&nbsp;<span class="bs-nat">NAT {bs["nat"]} ({bs["nat"]*100//n}%)</span>'
                f'&nbsp;<span class="bs-tie">TIE {bs["tie"]} ({bs["tie"]*100//n}%)</span>'
                f'&nbsp;&nbsp;decisive-L2 {dl2}%'
                f'&nbsp;&nbsp;avg caps {bs["cap_sum"]/n:.1f}'
                f'&nbsp;&nbsp;web {bs["web_sum"]}'
                f'</td></tr>'
            )
        prev_band = band

        # Verdict cell
        v = r["verdict"]
        if v == "local2_better":
            v_cls, v_lbl = "v-l2",  "L2 WIN"
        elif v == "native_better":
            v_cls, v_lbl = "v-nat", "NAT WIN"
        elif v == "tie":
            v_cls, v_lbl = "v-tie", "TIE"
        else:
            v_cls, v_lbl = "v-pending", "—"

        # Prompt cell (strip surrounding quotes, truncate)
        prompt_display = r["prompt"].strip('"\'').strip()
        prompt_short   = prompt_display[:60]
        prompt_title   = prompt_display[:300].replace('"', '&quot;')

        # Run cell (strip run_id prefix noise)
        run_display = r["run_id"]

        # Persona dot columns
        persona_cells = ""
        active = r["persona"]
        for p in seen_personas:
            if active == p:
                persona_cells += f'<td class="persona-dot active-{p}">●</td>'
            else:
                persona_cells += '<td class="persona-dot"></td>'
        # none column
        if not active:
            persona_cells += '<td class="persona-dot active-none">●</td>'
        else:
            persona_cells += '<td class="persona-dot"></td>'

        # Caps badge
        if r["caps"] == 0:
            caps_cls = "caps-zero"
        elif r["caps"] <= 3:
            caps_cls = "caps-low"
        elif r["caps"] <= 5:
            caps_cls = "caps-mid"
        else:
            caps_cls = "caps-high"
        caps_cell = f'<span class="caps-badge {caps_cls}">{r["caps"]}</span>'

        # Tool cells
        def _tool_cell(n, highlight="tool-badge"):
            return f'<span class="{highlight}">{n}</span>' if n else '<span class="tool-zero">—</span>'

        web_l_cell = _tool_cell(r["web"])
        web_n_cell = _tool_cell(r["web_n"], "tool-badge-n")
        # highlight rows where only Native searched (interesting anomaly)
        only_n_cls = " only-n-web" if r["web_n"] and not r["web"] else ""
        lib_cell   = _tool_cell(r["lib"])

        rcat = r.get("rubric_cat", "—")
        rcat_cls = f'rc-{rcat}' if rcat != "—" else "rc-other"
        table_rows_html.append(
            f'<tr class="data-row {v_cls}-row{only_n_cls}">'
            f'<td class="num">{r["row"]}</td>'
            f'<td class="run-id">{run_display}</td>'
            f'<td class="prompt" title="{prompt_title}">{prompt_short}</td>'
            f'<td class="{v_cls} verdict-cell">{v_lbl}</td>'
            f'<td class="rubric-cat {rcat_cls}">{rcat}</td>'
            f'{persona_cells}'
            f'<td class="caps">{caps_cell}</td>'
            f'<td class="tool">{web_l_cell}</td>'
            f'<td class="tool">{web_n_cell}</td>'
            f'<td class="tool">{lib_cell}</td>'
            f'</tr>'
        )

    # Final band summary
    if rows:
        last_band = ((rows[-1]["row"] - 1) // band_size) + 1
        bs = band_stats[last_band]
        n  = max(bs["n"], 1)
        decisive = bs["l2"] + bs["nat"]
        dl2 = bs["l2"] * 100 // decisive if decisive else 0
        table_rows_html.append(
            f'<tr class="band-sep">'
            f'<td colspan="{5 + n_persona_cols + 4}" class="band-summary">'
            f'Band {last_band} &nbsp;·&nbsp; items {bs["lo"]}–{bs["hi"]}'
            f'&nbsp;&nbsp;<span class="bs-l2">L2 {bs["l2"]} ({bs["l2"]*100//n}%)</span>'
            f'&nbsp;<span class="bs-nat">NAT {bs["nat"]} ({bs["nat"]*100//n}%)</span>'
            f'&nbsp;<span class="bs-tie">TIE {bs["tie"]} ({bs["tie"]*100//n}%)</span>'
            f'&nbsp;&nbsp;decisive-L2 {dl2}%'
            f'&nbsp;&nbsp;avg caps {bs["cap_sum"]/n:.1f}'
            f'&nbsp;&nbsp;web {bs["web_sum"]}'
            f'</td></tr>'
        )

    # ── persona summary table ──────────────────────────────────────────
    persona_summary_rows = []
    for persona, s in sorted(persona_stats.items(), key=lambda x: -x[1]["n"]):
        n = max(s["n"], 1)
        decisive = s["l2"] + s["nat"]
        dl2 = s["l2"] * 100 // decisive if decisive else 0
        bar_l2  = s["l2"]  * 100 // n
        bar_nat = s["nat"] * 100 // n
        bar_tie = s["tie"] * 100 // n
        persona_summary_rows.append(
            f'<tr>'
            f'<td class="ps-name">{persona}</td>'
            f'<td class="ps-n">{s["n"]}</td>'
            f'<td class="ps-l2">{s["l2"]}</td>'
            f'<td class="ps-nat">{s["nat"]}</td>'
            f'<td class="ps-tie">{s["tie"]}</td>'
            f'<td class="ps-pct">{s["l2"]*100//n}%</td>'
            f'<td class="ps-dl2">{dl2}%</td>'
            f'<td class="ps-bar"><div class="mini-bar">'
            f'<div class="mb-l2" style="width:{bar_l2}%"></div>'
            f'<div class="mb-nat" style="width:{bar_nat}%"></div>'
            f'<div class="mb-tie" style="width:{bar_tie}%"></div>'
            f'</div></td>'
            f'</tr>'
        )

    # ── caps summary table ─────────────────────────────────────────────
    caps_summary_rows = []
    for caps, s in sorted(caps_stats.items()):
        n = max(s["n"], 1)
        decisive = s["l2"] + s["nat"]
        dl2 = s["l2"] * 100 // decisive if decisive else 0
        bar_l2  = s["l2"]  * 100 // n
        bar_nat = s["nat"] * 100 // n
        bar_tie = s["tie"] * 100 // n
        caps_summary_rows.append(
            f'<tr>'
            f'<td class="ps-n">{caps}</td>'
            f'<td class="ps-n">{s["n"]}</td>'
            f'<td class="ps-l2">{s["l2"]}</td>'
            f'<td class="ps-nat">{s["nat"]}</td>'
            f'<td class="ps-tie">{s["tie"]}</td>'
            f'<td class="ps-pct">{s["l2"]*100//n}%</td>'
            f'<td class="ps-dl2">{dl2}%</td>'
            f'<td class="ps-bar"><div class="mini-bar">'
            f'<div class="mb-l2" style="width:{bar_l2}%"></div>'
            f'<div class="mb-nat" style="width:{bar_nat}%"></div>'
            f'<div class="mb-tie" style="width:{bar_tie}%"></div>'
            f'</div></td>'
            f'</tr>'
        )

    # ── rubric category summary table ──────────────────────────────────
    rubric_summary_rows = []
    for cat, s in sorted(rubric_stats.items(), key=lambda x: -x[1]["n"]):
        n = max(s["n"], 1)
        decisive = s["l2"] + s["nat"]
        dl2 = s["l2"] * 100 // decisive if decisive else 0
        bar_l2  = s["l2"]  * 100 // n
        bar_nat = s["nat"] * 100 // n
        bar_tie = s["tie"] * 100 // n
        rubric_summary_rows.append(
            f'<tr>'
            f'<td class="ps-name">{cat}</td>'
            f'<td class="ps-n">{s["n"]}</td>'
            f'<td class="ps-l2">{s["l2"]}</td>'
            f'<td class="ps-nat">{s["nat"]}</td>'
            f'<td class="ps-tie">{s["tie"]}</td>'
            f'<td class="ps-pct">{s["tie"]*100//n}%</td>'
            f'<td class="ps-dl2">{dl2}%</td>'
            f'<td class="ps-bar"><div class="mini-bar">'
            f'<div class="mb-l2" style="width:{bar_l2}%"></div>'
            f'<div class="mb-nat" style="width:{bar_nat}%"></div>'
            f'<div class="mb-tie" style="width:{bar_tie}%"></div>'
            f'</div></td>'
            f'</tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  :root {{
    --bg:       #0f1117;
    --bg2:      #181c26;
    --bg3:      #1e2335;
    --border:   #2a2f45;
    --muted:    #5a6080;
    --text:     #c8cfe8;
    --text2:    #8891b0;
    --l2:       #34d399;
    --l2-dim:   #1a3d2e;
    --nat:      #f87171;
    --nat-dim:  #3d1a1a;
    --tie:      #6b7280;
    --tie-dim:  #1e2030;
    --accent:   #818cf8;
    --yellow:   #fbbf24;
    --orange:   #fb923c;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 12px;
    padding: 24px 20px 48px;
  }}
  h1 {{ font-size: 18px; font-weight: 600; color: var(--accent); margin-bottom: 4px; }}
  .meta {{ color: var(--muted); font-size: 11px; margin-bottom: 20px; }}
  .scoreboard {{
    display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap;
  }}
  .score-card {{
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 20px; min-width: 110px;
  }}
  .score-card .label {{ font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }}
  .score-card .value {{ font-size: 28px; font-weight: 700; margin-top: 2px; }}
  .sc-l2   .value {{ color: var(--l2);     }}
  .sc-nat  .value {{ color: var(--nat);    }}
  .sc-tie  .value {{ color: var(--tie);    }}
  .sc-n    .value {{ color: var(--accent); }}

  /* main table */
  .wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 900px; }}
  th {{
    background: var(--bg3); color: var(--text2);
    font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
    padding: 7px 10px; border-bottom: 1px solid var(--border);
    white-space: nowrap; text-align: center;
  }}
  th.left {{ text-align: left; }}
  td {{ padding: 5px 10px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  tr.data-row:hover td {{ background: var(--bg3); }}

  td.num       {{ color: var(--muted); text-align: right; width: 36px; }}
  td.run-id    {{ color: var(--text2); font-size: 10px; white-space: nowrap; }}
  td.prompt    {{ color: var(--text);  max-width: 220px; overflow: hidden;
                  text-overflow: ellipsis; white-space: nowrap; cursor: default; }}
  td.verdict-cell {{ text-align: center; font-weight: 600; font-size: 11px;
                     white-space: nowrap; border-radius: 4px; }}
  td.v-l2      {{ color: var(--l2);  }}
  td.v-nat     {{ color: var(--nat); }}
  td.v-tie     {{ color: var(--tie); }}
  td.v-pending {{ color: var(--muted); }}

  tr.v-l2-row  td {{ background: rgba(52,211,153,.04); }}
  tr.v-nat-row td {{ background: rgba(248,113,113,.04); }}

  /* persona dots */
  .persona-group-header {{ text-align: center; border-left: 1px solid var(--border);
                           border-right: 1px solid var(--border); color: var(--accent); }}
  td.persona-dot {{ text-align: center; font-size: 14px; color: transparent;
                    width: 62px; border-left: 1px solid var(--border); }}
  td.persona-dot:last-of-type {{ border-right: 1px solid var(--border); }}
  td.active-analytic   {{ color: #818cf8; }}
  td.active-empathic   {{ color: #f472b6; }}
  td.active-creative   {{ color: #fb923c; }}
  td.active-pragmatic  {{ color: #34d399; }}
  td.active-bridging   {{ color: #fbbf24; }}
  td.active-none       {{ color: var(--muted); font-size: 11px; }}

  /* caps badge */
  td.caps {{ text-align: center; width: 48px; }}
  .caps-badge {{
    display: inline-block; border-radius: 4px;
    padding: 1px 7px; font-size: 11px; font-weight: 600;
  }}
  .caps-zero {{ background: rgba(248,113,113,.15); color: var(--nat); }}
  .caps-low  {{ background: rgba(251,191,36,.12);  color: var(--yellow); }}
  .caps-mid  {{ background: rgba(52,211,153,.12);  color: var(--l2); }}
  .caps-high {{ background: rgba(52,211,153,.22);  color: var(--l2); }}

  /* tool cells */
  td.tool {{ text-align: center; width: 40px; }}
  .tool-badge   {{ color: var(--orange); font-weight: 600; }}
  .tool-badge-n {{ color: #a78bfa;     font-weight: 600; }}
  .tool-zero    {{ color: var(--border); }}
  tr.only-n-web td {{ background: rgba(167,139,250,.06); }}

  /* rubric category */
  td.rubric-cat {{ text-align: center; font-size: 10px; white-space: nowrap; font-weight: 600; }}
  .rc-empathy    {{ color: #f472b6; }}
  .rc-accuracy   {{ color: var(--l2); }}
  .rc-creativity {{ color: var(--orange); }}
  .rc-technical  {{ color: #a78bfa; }}
  .rc-ambiguity  {{ color: var(--yellow); }}
  .rc-cultural   {{ color: #67e8f9; }}
  .rc-adaptability {{ color: #60a5fa; }}
  .rc-other      {{ color: var(--muted); }}

  /* band separator */
  tr.band-sep td {{ padding: 0; border-bottom: none; }}
  .band-summary {{
    background: var(--bg3); border-top: 1px solid var(--accent);
    border-bottom: 1px solid var(--accent);
    color: var(--text2); font-size: 10px; padding: 5px 12px;
    letter-spacing: .04em;
  }}
  .bs-l2  {{ color: var(--l2);  font-weight: 600; }}
  .bs-nat {{ color: var(--nat); font-weight: 600; }}
  .bs-tie {{ color: var(--muted); }}

  /* summary tables */
  h2 {{ font-size: 14px; color: var(--accent); margin: 32px 0 10px; }}
  .summary-grid {{ display: flex; gap: 32px; flex-wrap: wrap; align-items: flex-start; }}
  .summary-table {{ border-collapse: collapse; }}
  .summary-table th {{
    background: var(--bg3); color: var(--text2);
    font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
    padding: 6px 10px; border: 1px solid var(--border);
  }}
  .summary-table td {{ padding: 5px 10px; border: 1px solid var(--border); }}
  .ps-name {{ color: var(--accent); font-weight: 600; }}
  .ps-n    {{ color: var(--text2); text-align: right; }}
  .ps-l2   {{ color: var(--l2);   text-align: right; font-weight: 600; }}
  .ps-nat  {{ color: var(--nat);  text-align: right; font-weight: 600; }}
  .ps-tie  {{ color: var(--tie);  text-align: right; }}
  .ps-pct  {{ color: var(--l2);   text-align: right; }}
  .ps-dl2  {{ color: var(--yellow); text-align: right; font-weight: 600; }}
  .ps-bar  {{ width: 120px; }}
  .mini-bar {{
    display: flex; height: 8px; border-radius: 4px; overflow: hidden;
    background: var(--bg3); width: 100%;
  }}
  .mb-l2  {{ background: var(--l2);  height: 100%; }}
  .mb-nat {{ background: var(--nat); height: 100%; }}
  .mb-tie {{ background: var(--tie); height: 100%; opacity: .5; }}

  /* band drill-down */
  .band-section {{ margin-bottom: 28px; }}
  .band-header {{
    background: var(--bg3); border: 1px solid var(--accent);
    border-radius: 6px; padding: 8px 14px; margin-bottom: 10px;
    font-size: 12px; color: var(--text);
  }}
  .band-header .bh-title {{ color: var(--accent); font-weight: 600; margin-right: 16px; }}
  .band-grid {{ display: flex; gap: 24px; flex-wrap: wrap; }}
</style>
</head>
<body>

<h1>{title}</h1>
<div class="meta">Generated {generated} &nbsp;·&nbsp; {total_n} items &nbsp;·&nbsp; band size {band_size}</div>

<div class="scoreboard">
  <div class="score-card sc-n">
    <div class="label">Total</div>
    <div class="value">{total_n}</div>
  </div>
  <div class="score-card sc-l2">
    <div class="label">LoCAL2 wins</div>
    <div class="value">{total_l2}</div>
  </div>
  <div class="score-card sc-nat">
    <div class="label">Native wins</div>
    <div class="value">{total_nat}</div>
  </div>
  <div class="score-card sc-tie">
    <div class="label">Ties</div>
    <div class="value">{total_tie}</div>
  </div>
  <div class="score-card sc-l2">
    <div class="label">Decisive L2%</div>
    <div class="value">{total_l2*100//(total_l2+total_nat) if (total_l2+total_nat) else 0}%</div>
  </div>
</div>

<div class="wrap">
<table>
<thead>
  <tr>
    <th rowspan="2" class="left">#</th>
    <th rowspan="2" class="left">Run</th>
    <th rowspan="2" class="left" style="min-width:180px">Prompt</th>
    <th rowspan="2">Verdict</th>
    <th rowspan="2">Rubric</th>
    <th colspan="{n_persona_cols}" class="persona-group-header">Persona</th>
    <th rowspan="2">Caps</th>
    <th rowspan="2">Web L</th>
    <th rowspan="2">Web N</th>
    <th rowspan="2">Lib</th>
  </tr>
  <tr>
    {persona_th}
  </tr>
</thead>
<tbody>
{"".join(table_rows_html)}
</tbody>
</table>
</div>

<h2>Persona breakdown</h2>
<div class="summary-grid">
<table class="summary-table">
  <tr>
    <th>Persona</th><th>n</th><th>L2</th><th>NAT</th><th>TIE</th>
    <th>L2%</th><th>decisive-L2%</th><th>distribution</th>
  </tr>
  {"".join(persona_summary_rows)}
</table>

<table class="summary-table">
  <tr>
    <th>Caps</th><th>n</th><th>L2</th><th>NAT</th><th>TIE</th>
    <th>L2%</th><th>decisive-L2%</th><th>distribution</th>
  </tr>
  {"".join(caps_summary_rows)}
</table>
</div>

<h2>Breakdown by rubric category</h2>
<table class="summary-table">
  <tr>
    <th>Category</th><th>n</th><th>L2</th><th>NAT</th><th>TIE</th>
    <th>tie%</th><th>decisive-L2%</th><th>distribution</th>
  </tr>
  {"".join(rubric_summary_rows)}
</table>

<h2>Breakdown by band</h2>
{"".join(band_sections_html)}

</body>
</html>
"""

    out_path.write_text(html, encoding="utf-8")
    print(f"Written: {out_path}")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LoCAL2 harness verdict analysis")
    parser.add_argument("--run-id",    default=None,  help="Filter to a specific run_id")
    parser.add_argument("--band-size", default=50,    type=int,
                        help="Items per band for summary rows (default: 50)")
    parser.add_argument("--html",      nargs="?",     const=True, metavar="FILE",
                        help="Write HTML report (default: harness/report.html)")
    parser.add_argument("--bands",     action="store_true",
                        help="Persona + caps breakdown per band")
    args = parser.parse_args()

    if args.html:
        out = Path(args.html) if isinstance(args.html, str) else \
              Path(__file__).parent / "report.html"
        run_html(args.run_id, args.band_size, out)
    elif args.bands:
        run_bands(args.run_id, args.band_size)
    else:
        run_text(args.run_id, args.band_size)
