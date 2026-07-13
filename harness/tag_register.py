"""Register tagging + re-cut analysis.

Assigns each prompt a register label (direct / meta / third_person_narrative),
detects markdown structure in each response, then re-cuts decisive-L2% by
register × persona × structure presence.

Usage:
    python -m harness.tag_register --run-id pref-04
    python -m harness.tag_register --run-id pref-04 --out harness/register_report.html
    python -m harness.tag_register --run-id pref-04 --dry-run   # tag only, no report
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from pathlib import Path

import ollama
import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_DB_PATH = Path(__file__).parent.parent / "harness.db"

_REGISTER_PROMPT = """\
Classify the following prompt into exactly one of three categories:

direct          — The prompt is written in first person by someone expressing their own distress, struggle, or emotion, addressed to the model as if seeking personal comfort or help. ("I've been feeling really anxious lately", "After my breakup I don't know how to cope")

meta            — The prompt asks the model to explain, design, or demonstrate how a chatbot, friend, colleague, or system *should* respond to a distressed person. The human is not themselves in distress — they want a framework or example. ("How should an AI respond to a grieving user?", "A chatbot is being tested for emotional intelligence...")

third_person_narrative — The prompt describes a person in distress in third person but does NOT ask for a designed or instructional response — the expected answer is direct comfort or advice addressed to that person. ("A 16-year-old is struggling with self-esteem and asks for advice", "A bereaved individual is expressing sorrow...")

Reply with exactly one word: direct, meta, or third_person_narrative.

Prompt to classify:
{prompt}"""


def _classify_register(prompt: str, model: str) -> str:
    """Call LLM to classify prompt register. Returns one of direct/meta/third_person_narrative."""
    try:
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": _REGISTER_PROMPT.format(prompt=prompt[:1200])}],
            options={"temperature": 0, "num_predict": 2000},
        )
        content = resp["message"]["content"].strip().lower()
        if not content:
            logger.warning("empty model response for prompt %.60s", prompt)
            return "direct"
        # scan entire response for the label — models may not output just one word
        for label in ("third_person_narrative", "meta", "direct"):
            if label in content:
                return label
        logger.warning("no label found in response for prompt %.60s", prompt)
        return "direct"
    except Exception as exc:
        logger.error("register classification failed: %s", exc)
        return "direct"


def _has_structure(text: str) -> bool:
    """Return True if text contains markdown headers, tables, or numbered lists."""
    if not text:
        return False
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^#{1,4}\s", s):
            return True
        if re.match(r"^\|.+\|", s):
            return True
        if re.match(r"^\d+\.\s", s):
            return True
    return False


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


def _load_items(run_id: str | None) -> list[dict]:
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    where = "WHERE i.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()
    rows = con.execute(f"""
        SELECT
          ROW_NUMBER() OVER (ORDER BY i.timestamp) AS row_num,
          i.item_id,
          i.prompt,
          i.arm_a_output,
          i.arm_a_tool_calls,
          i.arm_b_output,
          j.verdict,
          j.rubric
        FROM items i
        LEFT JOIN judgments j ON j.item_id = i.item_id AND j.run_id = i.run_id
        {where}
        ORDER BY i.timestamp
    """, params).fetchall()

    items = []
    for r in rows:
        tool_calls = json.loads(r["arm_a_tool_calls"] or "[]")
        persona = ""
        for tc in tool_calls:
            if tc.get("tool") == "persona":
                persona = tc.get("args", {}).get("mode", "")
        items.append({
            "row":         r["row_num"],
            "item_id":     r["item_id"],
            "prompt":      (r["prompt"] or "").strip(),
            "arm_a":       r["arm_a_output"] or "",
            "arm_b":       r["arm_b_output"] or "",
            "verdict":     r["verdict"],
            "rubric":      r["rubric"] or "",
            "rubric_cat":  _classify_rubric(r["rubric"] or ""),
            "persona":     persona or "none",
        })
    return items


def _tag_items(items: list[dict], model: str) -> list[dict]:
    """Add register and structure tags to each item."""
    total = len(items)
    for i, item in enumerate(items, 1):
        if i % 25 == 0 or i == 1:
            logger.info("tagging %d / %d", i, total)
        item["register"]    = _classify_register(item["prompt"], model) if item["prompt"] else "direct"
        item["l2_struct"]   = _has_structure(item["arm_a"])
        item["nat_struct"]  = _has_structure(item["arm_b"])
    return items


# ── stats helpers ─────────────────────────────────────────────────────────────

def _verdict_stats(rows: list[dict]) -> dict:
    s = {"l2": 0, "nat": 0, "tie": 0, "n": len(rows)}
    for r in rows:
        if r["verdict"] == "local2_better":   s["l2"] += 1
        elif r["verdict"] == "native_better": s["nat"] += 1
        elif r["verdict"] == "tie":           s["tie"] += 1
    d = s["l2"] + s["nat"]
    s["dl2"] = s["l2"] * 100 // d if d else None
    return s


def _table_html(rows_html: list[str], headers: list[str]) -> str:
    ths = "".join(f"<th>{h}</th>" for h in headers)
    return (f'<table class="rt">'
            f'<thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table>')


def _stat_row(label: str, s: dict) -> str:
    dl2 = f'{s["dl2"]}%' if s["dl2"] is not None else "—"
    cls = ("green" if (s["dl2"] or 0) >= 65 else
           "red"   if (s["dl2"] or 0) <  50 else "")
    return (f'<tr><td class="lbl">{label}</td>'
            f'<td>{s["n"]}</td><td class="l2">{s["l2"]}</td>'
            f'<td class="nat">{s["nat"]}</td><td class="tie">{s["tie"]}</td>'
            f'<td class="dl2 {cls}">{dl2}</td></tr>')


# ── HTML report ───────────────────────────────────────────────────────────────

def _build_html(items: list[dict], run_id: str | None) -> str:
    REGISTERS = ["direct", "meta", "third_person_narrative"]
    PERSONAS  = sorted({r["persona"] for r in items})

    # ── register breakdown ────────────────────────────────────────────────
    reg_rows = []
    for reg in REGISTERS:
        subset = [r for r in items if r["register"] == reg]
        if not subset:
            continue
        reg_rows.append(_stat_row(reg, _verdict_stats(subset)))

    # ── register × persona (empathic highlighted) ─────────────────────────
    reg_persona_rows = []
    for reg in REGISTERS:
        for p in PERSONAS:
            subset = [r for r in items if r["register"] == reg and r["persona"] == p]
            if not subset:
                continue
            cls = ' class="emp"' if p == "empathic" else ""
            s = _verdict_stats(subset)
            dl2 = f'{s["dl2"]}%' if s["dl2"] is not None else "—"
            dcls = ("green" if (s["dl2"] or 0) >= 65 else
                    "red"   if (s["dl2"] or 0) <  50 else "")
            reg_persona_rows.append(
                f'<tr{cls}><td class="lbl">{reg}</td><td class="lbl">{p}</td>'
                f'<td>{s["n"]}</td><td class="l2">{s["l2"]}</td>'
                f'<td class="nat">{s["nat"]}</td><td class="tie">{s["tie"]}</td>'
                f'<td class="dl2 {dcls}">{dl2}</td></tr>'
            )

    # ── structure presence by register ────────────────────────────────────
    struct_rows = []
    for reg in REGISTERS:
        for l2_s in (True, False):
            subset = [r for r in items if r["register"] == reg and r["l2_struct"] == l2_s]
            if not subset:
                continue
            s = _verdict_stats(subset)
            dl2 = f'{s["dl2"]}%' if s["dl2"] is not None else "—"
            dcls = ("green" if (s["dl2"] or 0) >= 65 else
                    "red"   if (s["dl2"] or 0) <  50 else "")
            label = f"L2 {'structured' if l2_s else 'plain'}"
            struct_rows.append(
                f'<tr><td class="lbl">{reg}</td><td class="lbl">{label}</td>'
                f'<td>{s["n"]}</td><td class="l2">{s["l2"]}</td>'
                f'<td class="nat">{s["nat"]}</td><td class="tie">{s["tie"]}</td>'
                f'<td class="dl2 {dcls}">{dl2}</td></tr>'
            )

    # ── per-item register table ────────────────────────────────────────────
    item_rows = []
    for r in items:
        v = r["verdict"]
        vcls = ("v-l2" if v == "local2_better" else
                "v-nat" if v == "native_better" else
                "v-tie" if v == "tie" else "")
        vlbl = {"local2_better": "L2", "native_better": "NAT", "tie": "TIE"}.get(v, "—")
        prompt_short = r["prompt"].strip('"\'')[:70]
        item_rows.append(
            f'<tr>'
            f'<td class="num">{r["row"]}</td>'
            f'<td class="reg">{r["register"]}</td>'
            f'<td class="lbl">{r["persona"]}</td>'
            f'<td class="lbl">{r["rubric_cat"]}</td>'
            f'<td class="{vcls} vc">{vlbl}</td>'
            f'<td class="struct">{"■" if r["l2_struct"] else "·"}</td>'
            f'<td class="struct">{"■" if r["nat_struct"] else "·"}</td>'
            f'<td class="prompt" title="{r["prompt"][:300].replace(chr(34), chr(39))}">{prompt_short}</td>'
            f'</tr>'
        )

    title = f"Register Analysis — {run_id or 'all runs'}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  :root {{
    --bg:#0f1117; --bg2:#181c26; --bg3:#1e2335;
    --border:#2a2f45; --muted:#5a6080; --text:#c8cfe8; --text2:#8891b0;
    --l2:#34d399; --nat:#f87171; --tie:#6b7280; --accent:#818cf8;
    --yellow:#fbbf24;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text);
          font-family:'SF Mono','Fira Code',monospace; font-size:12px;
          padding:24px 28px 64px; }}
  h1 {{ font-size:17px; color:var(--accent); margin-bottom:4px; }}
  h2 {{ font-size:13px; color:var(--accent); margin:28px 0 10px;
        border-bottom:1px solid var(--border); padding-bottom:5px; }}
  .meta {{ color:var(--muted); font-size:11px; margin-bottom:20px; }}
  .note {{ background:rgba(129,140,248,.08); border-left:3px solid var(--accent);
           padding:8px 12px; margin:12px 0; color:var(--text2); font-size:11px;
           border-radius:0 4px 4px 0; }}
  .grid {{ display:flex; gap:32px; flex-wrap:wrap; align-items:flex-start; margin-bottom:24px; }}
  table.rt {{ border-collapse:collapse; }}
  table.rt th {{ background:var(--bg3); color:var(--text2); font-size:10px;
                 text-transform:uppercase; letter-spacing:.06em;
                 padding:6px 10px; border:1px solid var(--border); }}
  table.rt td {{ padding:5px 10px; border:1px solid var(--border); }}
  td.lbl  {{ color:var(--accent); font-weight:600; white-space:nowrap; }}
  td.reg  {{ color:var(--yellow); font-size:10px; white-space:nowrap; }}
  td.l2   {{ color:var(--l2); text-align:right; font-weight:600; }}
  td.nat  {{ color:var(--nat); text-align:right; font-weight:600; }}
  td.tie  {{ color:var(--tie); text-align:right; }}
  td.dl2  {{ text-align:right; font-weight:700; }}
  td.dl2.green {{ color:var(--l2); }}
  td.dl2.red   {{ color:var(--nat); }}
  td.num  {{ color:var(--muted); text-align:right; width:32px; }}
  td.vc   {{ text-align:center; font-weight:600; font-size:11px; }}
  td.v-l2  {{ color:var(--l2); }}
  td.v-nat {{ color:var(--nat); }}
  td.v-tie {{ color:var(--tie); }}
  td.struct {{ text-align:center; color:var(--muted); width:32px; }}
  td.prompt {{ max-width:280px; overflow:hidden; text-overflow:ellipsis;
               white-space:nowrap; color:var(--text2); cursor:default; }}
  tr.emp td {{ background:rgba(244,114,182,.06); }}
  tr:hover td {{ background:var(--bg3); }}
  table#items {{ width:100%; border-collapse:collapse; margin-top:8px; }}
  table#items th {{ background:var(--bg3); color:var(--text2); font-size:10px;
                    text-transform:uppercase; letter-spacing:.05em;
                    padding:6px 8px; border-bottom:1px solid var(--border);
                    white-space:nowrap; text-align:left; }}
  table#items td {{ padding:4px 8px; border-bottom:1px solid var(--border); }}
</style>
</head>
<body>

<h1>{title}</h1>
<div class="meta">{len(items)} items · register labels assigned by LLM · structure detected by regex</div>

<div class="note">
  <strong>Register taxonomy:</strong>
  <strong>direct</strong> = first-person distress addressed to model ·
  <strong>meta</strong> = asks model to design/explain a response framework ·
  <strong>third_person_narrative</strong> = third-person setup where direct comfort is expected
</div>

<h2>Decisive-L2% by register</h2>
<div class="grid">
{_table_html(reg_rows, ["Register","n","L2","NAT","TIE","dL2%"])}
</div>

<h2>Decisive-L2% by register × persona</h2>
<div class="note">Empathic rows highlighted. Cells with n &lt; 5 are suggestive only.</div>
<div class="grid">
{_table_html(reg_persona_rows, ["Register","Persona","n","L2","NAT","TIE","dL2%"])}
</div>

<h2>Structure presence (L2) × register — tests the markdown-structure hypothesis</h2>
<div class="note">
  ■ = response contains markdown headers / tables / numbered lists · · = plain prose.
  If structure penalises L2, structured rows should show lower dL2% than plain rows
  within the same register — especially in the <em>direct</em> bucket.
</div>
<div class="grid">
{_table_html(struct_rows, ["Register","L2 format","n","L2","NAT","TIE","dL2%"])}
</div>

<h2>Per-item register tags</h2>
<table id="items">
  <thead>
    <tr>
      <th>#</th><th>Register</th><th>Persona</th><th>Rubric</th>
      <th>Verdict</th><th>L2 struct</th><th>NAT struct</th><th>Prompt</th>
    </tr>
  </thead>
  <tbody>{"".join(item_rows)}</tbody>
</table>

</body>
</html>"""


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    model = cfg.get("judge", {}).get("model", "gemma4:e4b-mlx")

    ap = argparse.ArgumentParser(description="Register tagging + re-cut analysis")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out",    default=None, help="Output HTML path (default: harness/register_report.html)")
    ap.add_argument("--dry-run", action="store_true", help="Tag and print counts; skip HTML")
    ap.add_argument("--model",  default=model, help=f"Ollama model for register classification (default: {model})")
    args = ap.parse_args()

    logger.info("loading items (run_id=%s)", args.run_id)
    items = _load_items(args.run_id)
    logger.info("loaded %d items", len(items))

    logger.info("tagging registers using model=%s", args.model)
    items = _tag_items(items, args.model)

    # Summary counts
    from collections import Counter
    reg_counts = Counter(r["register"] for r in items)
    logger.info("register distribution: %s", dict(reg_counts))

    if args.dry_run:
        print("\nRegister distribution:")
        for reg, n in sorted(reg_counts.items(), key=lambda x: -x[1]):
            print(f"  {reg:<30} {n}")
        return

    html = _build_html(items, args.run_id)
    out = Path(args.out) if args.out else Path(__file__).parent / "register_report.html"
    out.write_text(html, encoding="utf-8")
    logger.info("written: %s", out)
    print(f"Written: {out}")


if __name__ == "__main__":
    main()
