"""Generate `presentation.pptx` from the slide outline.

Run from project root: `python docs/presentation/build_pptx.py`.
Outputs `docs/presentation/presentation.pptx`. Iterate the outline in
`outline.md` first; this script is the rendering layer.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

OUT_PATH = Path(__file__).parent / "presentation.pptx"
FIGURES_DIR = Path(__file__).parent.parent.parent / "experiments" / "figures"

NAVY = RGBColor(0x0B, 0x2B, 0x4A)
ACCENT = RGBColor(0xC9, 0x4A, 0x2D)
MUTED = RGBColor(0x55, 0x55, 0x55)
LIGHT_BG = RGBColor(0xF4, 0xF1, 0xEC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def _set_run(run, *, size=18, bold=False, color=NAVY, font="Calibri"):
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color


def _add_title_bar(slide, title: str, subtitle: str | None = None) -> None:
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.33), Inches(0.9))
    bar.fill.solid()
    bar.fill.fore_color.rgb = NAVY
    bar.line.fill.background()
    tf = bar.text_frame
    tf.margin_left = Inches(0.4)
    tf.margin_top = Inches(0.15)
    tf.margin_bottom = Inches(0.05)
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = title
    _set_run(r, size=24, bold=True, color=WHITE)
    if subtitle:
        p2 = tf.add_paragraph()
        r2 = p2.add_run()
        r2.text = subtitle
        _set_run(r2, size=12, color=WHITE)


def _add_body_box(slide, left, top, width, height):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    return tf


def _bullet(tf, text: str, *, level=0, size=16, bold=False, color=NAVY, first=False):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.level = level
    r = p.add_run()
    r.text = text
    _set_run(r, size=size, bold=bold, color=color)
    return p


def _footer(slide, idx: int, total: int) -> None:
    box = slide.shapes.add_textbox(Inches(11.5), Inches(7.0), Inches(1.7), Inches(0.3))
    tf = box.text_frame
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    r = p.add_run()
    r.text = f"{idx} / {total}"
    _set_run(r, size=10, color=MUTED)


def slide_title(prs, total: int) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.33), Inches(7.5))
    bg.fill.solid()
    bg.fill.fore_color.rgb = NAVY
    bg.line.fill.background()

    tf = _add_body_box(slide, 0.8, 2.4, 11.7, 3.2)
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = "Predicting IPR Patent Cancellation"
    _set_run(r, size=44, bold=True, color=WHITE)
    p2 = tf.add_paragraph()
    r2 = p2.add_run()
    r2.text = "Day-1 outcome prediction for USPTO inter partes review trials"
    _set_run(r2, size=20, color=WHITE)

    tf2 = _add_body_box(slide, 0.8, 5.6, 11.7, 1.5)
    _bullet(tf2, "Esade — Machine Learning — May 2026", first=True, size=16, color=WHITE)
    _bullet(tf2, "[Team members]", size=14, color=WHITE)


def slide_problem(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "What's an IPR — and why predict it",
                   "Inter partes review = administrative patent-cancellation trial at the USPTO")

    tf = _add_body_box(slide, 0.5, 1.1, 6.3, 5.8)
    _bullet(tf, "What's an IPR?", first=True, size=16, bold=True, color=ACCENT)
    _bullet(tf, "USPTO administrative trial that can cancel an issued patent",
            level=1, size=13)
    _bullet(tf, "Filed by an accused infringer to preempt district-court litigation",
            level=1, size=13)
    _bullet(tf, "Cohort: 15,030 trials, 2012-09-16 → 2026-01-05",
            level=1, size=13)
    _bullet(tf, "", size=6)
    _bullet(tf, "Target variable", size=16, bold=True, color=ACCENT)
    _bullet(tf, "cancelled ∈ {0, 1} — 1 if the original Final Written",
            level=1, size=13)
    _bullet(tf, "Decision holds ALL challenged claims unpatentable",
            level=1, size=13)
    _bullet(tf, "Base rate: 17.65% positive class", level=1, size=13)
    _bullet(tf, "", size=6)
    _bullet(tf, "Why supervised learning?", size=16, bold=True, color=ACCENT)
    _bullet(tf, "Outcomes are observed (decided trials) and labelled,",
            level=1, size=13)
    _bullet(tf, "with rich pre-T₀ structured + textual evidence per filing",
            level=1, size=13)

    tf2 = _add_body_box(slide, 7.0, 1.1, 6.0, 5.8)
    _bullet(tf2, "Decision the model supports", first=True, size=16, bold=True, color=ACCENT)
    _bullet(tf2, "Petitioner: file vs. settle vs. wait", level=1, size=13)
    _bullet(tf2, "Patent owner: settle early vs. defend", level=1, size=13)
    _bullet(tf2, "Both: pricing of licensing / settlement", level=1, size=13)
    _bullet(tf2, "", size=6)
    _bullet(tf2, "Why prediction is useful here", size=16, bold=True, color=ACCENT)
    _bullet(tf2, "Per-case cost ≈ $300K–$1M for the petitioner;", level=1, size=13)
    _bullet(tf2, "an early probability shifts the expected-value math", level=1, size=13)
    _bullet(tf2, "Today the same call is made by attorneys reading", level=1, size=13)
    _bullet(tf2, "the petition cold — expensive, slow, judgment-driven", level=1, size=13)
    _bullet(tf2, "", size=6)
    _bullet(tf2, "Stakes for getting it wrong", size=16, bold=True, color=ACCENT)
    _bullet(tf2, "False high → client proceeds, loses case", level=1, size=13)
    _bullet(tf2, "False low → client settles a winnable case", level=1, size=13)
    _footer(slide, idx, total)


def slide_label(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Label and the inviolable T₀ constraint",
                   "Every feature observable at petition-filing-date or earlier — period.")

    rows = [
        ("Outcome", "Label"),
        ("FWD — all challenged claims unpatentable", "1"),
        ("Adverse judgment (patent owner concession)", "1"),
        ("FWD — any claim survives", "0"),
        ("Institution denied / discretionary denial", "0"),
        ("Settled / procedural termination", "0"),
        ("Trial still pending", "excluded"),
    ]
    table = slide.shapes.add_table(len(rows), 2, Inches(0.4), Inches(1.2), Inches(5.0), Inches(3.4)).table
    table.columns[0].width = Inches(3.7)
    table.columns[1].width = Inches(1.3)
    for i, (a, b) in enumerate(rows):
        for j, txt in enumerate((a, b)):
            cell = table.cell(i, j)
            cell.text = ""
            tf_c = cell.text_frame
            p = tf_c.paragraphs[0]
            r = p.add_run()
            r.text = txt
            _set_run(r, size=11, bold=(i == 0), color=WHITE if i == 0 else NAVY)
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if i == 0 else (LIGHT_BG if i % 2 else WHITE)

    ttf_path = FIGURES_DIR / "12_time_to_fwd.png"
    if ttf_path.exists():
        slide.shapes.add_picture(
            str(ttf_path),
            Inches(5.6), Inches(1.15),
            width=Inches(7.5), height=Inches(4.8),
        )

    tf_b = _add_body_box(slide, 5.6, 6.0, 7.5, 1.2)
    _bullet(tf_b, "Empirical median time-to-FWD ≈ 592 days; cutoff = 600 sits past the bell's peak",
            first=True, size=11, bold=True, color=ACCENT)
    _bullet(tf_b, "Mature-days filter drops younger trials so the held-out tail isn't dominated by",
            size=10, color=MUTED)
    _bullet(tf_b, "fast-resolution outcomes (settlements/denials, all labelled 0)",
            size=10, color=MUTED)

    tf = _add_body_box(slide, 0.4, 4.7, 5.0, 2.8)
    _bullet(tf, "T₀ = petition_filing_date", first=True, size=14, bold=True, color=ACCENT)
    _bullet(tf, "Single inviolable constraint — every feature observable at or before T₀",
            level=1, size=11, color=MUTED)
    _bullet(tf, "", size=4)
    _bullet(tf, "Mature-days filter (600)", size=14, bold=True, color=ACCENT)
    _bullet(tf, "Drops trials younger than 600 days at training time —",
            level=1, size=11, color=MUTED)
    _bullet(tf, "past the empirical FWD-time peak, not in the middle of it",
            level=1, size=11, color=MUTED)
    _bullet(tf, "", size=4)
    _bullet(tf, "FWD verdict is NOT in the structured API — only on the PDF cover page",
            size=12, bold=True)
    _footer(slide, idx, total)


def slide_pipeline(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Pipeline architecture",
                   "8 stage drivers; 3-way parallel ingestion → join → features → model")

    boxes = [
        ("proceedings", 0.7, 1.4),
        ("decisions", 0.7, 2.5),
        ("petitions", 0.7, 3.6),
        ("patents", 3.3, 1.4),
        ("fwd_texts", 3.3, 2.5),
        ("petition_texts", 3.3, 3.6),
        ("join", 6.3, 2.5),
        ("features", 8.5, 2.5),
        ("model", 10.7, 2.5),
    ]
    shape_refs = {}
    for label, x, y in boxes:
        shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(2.0), Inches(0.7))
        shp.fill.solid()
        shp.fill.fore_color.rgb = LIGHT_BG if label not in {"join", "features", "model"} else NAVY
        shp.line.color.rgb = NAVY
        tf_s = shp.text_frame
        tf_s.margin_left = Inches(0.05)
        tf_s.margin_top = Inches(0.1)
        p = tf_s.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = label
        _set_run(r, size=13, bold=True, color=WHITE if label in {"join", "features", "model"} else NAVY)
        shape_refs[label] = shp

    def connect(a: str, b: str) -> None:
        s = shape_refs[a]
        e = shape_refs[b]
        s_end_x = s.left + s.width
        s_end_y = s.top + s.height // 2
        e_start_x = e.left
        e_start_y = e.top + e.height // 2
        line = slide.shapes.add_connector(1, s_end_x, s_end_y, e_start_x, e_start_y)
        line.line.color.rgb = NAVY
        line.line.width = Pt(1.5)

    connect("proceedings", "patents")
    connect("decisions", "fwd_texts")
    connect("petitions", "petition_texts")
    connect("patents", "join")
    connect("fwd_texts", "join")
    connect("petition_texts", "join")
    connect("join", "features")
    connect("features", "model")

    tf = _add_body_box(slide, 0.5, 5.0, 12.5, 2.0)
    _bullet(tf, "4 structured API surfaces (USPTO ODP) + 2 PDF-text surfaces",
            first=True, size=16)
    _bullet(tf, "Weekly refresh on AWS Step Functions + ECS Fargate; same code runs locally", size=16)
    _bullet(tf, "Each parquet is keyed and joinable; the master joined frame is the contract", size=16)
    _footer(slide, idx, total)


def slide_features(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Feature matrix — 36 raw features, all T₀-clean",
                   "Row-local engineering; cross-row stats (priors, frequencies) live in the preprocessor")

    rows = [
        ("Family", "Count", "Examples"),
        ("Calendar / era", "4", "filing_year, filing_month, art_unit_group, ptab_era"),
        ("Missingness pairs", "4", "prosecution_span_days(_missing), days_grant_to_petition(_missing)"),
        ("Patent aggregator", "11", "n_events, n_assignments, n_distinct_assignees, n_pe/ex/aa/ad/iss/maint/other"),
        ("Patent assignment / span", "2", "days_since_last_assignment, no_recorded_assignment"),
        ("CPC breadth", "2", "n_cpc_codes, n_cpc_subclasses"),
        ("Categorical (OHE + freq)", "7", "technology_center, cpc_section, entity_size, petitioner/owner..."),
        ("Petition text (Tier A)", "6", "n_grounds, n_grounds_102, n_grounds_103, sotera, fintiv, len"),
        ("→ Repeat-player priors", "+12", "(added by preprocessor) rolling cancel rate × 6 keys × {rate, count}"),
    ]
    tbl = slide.shapes.add_table(len(rows), 3, Inches(0.4), Inches(1.2), Inches(12.5), Inches(4.8)).table
    tbl.columns[0].width = Inches(2.7)
    tbl.columns[1].width = Inches(0.9)
    tbl.columns[2].width = Inches(8.9)
    for i, row in enumerate(rows):
        for j, txt in enumerate(row):
            cell = tbl.cell(i, j)
            cell.text = ""
            p = cell.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = txt
            _set_run(r, size=12, bold=(i == 0), color=WHITE if i == 0 else NAVY)
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if i == 0 else (LIGHT_BG if i % 2 else WHITE)

    tf = _add_body_box(slide, 0.5, 6.2, 12.5, 1.2)
    _bullet(tf, "36 raw features → ≈62–72 columns post-preprocessing (after OHE expansion + 12 prior outputs)",
            first=True, size=14, bold=True, color=ACCENT)
    _bullet(tf, "Stateless transforms only at the feature stage; cross-row stats refit per fold in the preprocessor",
            size=12, color=MUTED)
    _footer(slide, idx, total)


def slide_petition_text(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Petition-text precision discipline",
                   "Why we drop more regex features than we keep")

    tf = _add_body_box(slide, 0.5, 1.2, 6.5, 5.6)
    _bullet(tf, "Tier A features extracted by regex over PDF-extracted petition text",
            first=True, size=16)
    _bullet(tf, "", size=8)
    _bullet(tf, "The Sotera trap (IPR2020-00967):", size=18, bold=True, color=ACCENT)
    _bullet(tf, "regex /Sotera Wireless/ matched the petitioner's name,", level=1, size=14)
    _bullet(tf, "not the legal stipulation. Silent FP across an entire trial.", level=1, size=14)
    _bullet(tf, "", size=8)
    _bullet(tf, "Three-pass audit:", size=18, bold=True, color=ACCENT)
    _bullet(tf, "1. True-hit context check (≈150-char surround per match)", level=1, size=14)
    _bullet(tf, "2. False-sweep — broad probes for alternative phrasings", level=1, size=14)
    _bullet(tf, "3. Decision: broaden / drop / keep based on FP risk", level=1, size=14)

    tf2 = _add_body_box(slide, 7.3, 1.2, 5.7, 5.6)
    _bullet(tf2, "Casualties (dropped May 2026):", first=True, size=16, bold=True)
    _bullet(tf2, "mentions_motivation_to_combine", level=1, size=13, color=MUTED)
    _bullet(tf2, "mentions_general_plastic", level=1, size=13, color=MUTED)
    _bullet(tf2, "mentions_aapa", level=1, size=13, color=MUTED)
    _bullet(tf2, "Broadening would have added FP risk", level=1, size=12, color=MUTED)
    _bullet(tf2, "", size=8)
    _bullet(tf2, "Survivors (5 detection features at ≈100% precision):", size=15, bold=True)
    _bullet(tf2, "n_grounds, n_grounds_102, n_grounds_103", level=1, size=13, color=MUTED)
    _bullet(tf2, "has_sotera_stipulation, mentions_fintiv_factors", level=1, size=13, color=MUTED)
    _bullet(tf2, "+ petition_text_length (complexity proxy)", level=1, size=13, color=MUTED)
    _bullet(tf2, "", size=8)
    _bullet(tf2, "Audited on a 154-trial manually-labelled cohort", size=14, bold=True, color=ACCENT)
    _bullet(tf2, "(stratified 2017–2026, asserted in CI integration test)", level=1, size=11, color=MUTED)
    _footer(slide, idx, total)


def slide_preprocessor(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Preprocessing + leakage-safe PriorEncoder",
                   "Refits inside every CV fold — no statistic crosses fold boundaries")

    tf = _add_body_box(slide, 0.5, 1.2, 6.3, 5.6)
    _bullet(tf, "ColumnTransformer", first=True, size=18, bold=True, color=ACCENT)
    _bullet(tf, "numeric  → SimpleImputer(median) + passthrough", level=1, size=13)
    _bullet(tf, "categorical  → OneHotEncoder + missing-sentinel", level=1, size=13)
    _bullet(tf, "high-cardinality  → FrequencyEncoder (training freq, 0 for unseen)", level=1, size=13)
    _bullet(tf, "identity keys  → PriorEncoder × 6 group keys", level=1, size=13)
    _bullet(tf, "", size=8)
    _bullet(tf, "PriorEncoder group keys (each → rate + count):", size=15, bold=True)
    _bullet(tf, "petitioner / owner / pair / TC / patent / era", level=1, size=13, color=MUTED)

    tf2 = _add_body_box(slide, 7.0, 1.2, 6.0, 5.6)
    _bullet(tf2, "Per-row computation:", first=True, size=18, bold=True, color=ACCENT)
    _bullet(tf2, "rolling cancellation rate over rows where", level=1, size=14)
    _bullet(tf2, "petition_filing_date < current_row_date", level=1, size=14, bold=True)
    _bullet(tf2, "Strict inequality — no same-day leakage", level=1, size=12, color=MUTED)
    _bullet(tf2, "", size=8)
    _bullet(tf2, "Cold-start fallback:", size=16, bold=True)
    _bullet(tf2, "rolling GLOBAL mean (not zero, not corpus mean)", level=1, size=13, color=MUTED)
    _bullet(tf2, "Zero would imply 'petitioner has lost every prior case'", level=1, size=12, color=MUTED)
    _bullet(tf2, "Corpus mean leaks future base rates into past predictions", level=1, size=12, color=MUTED)
    _bullet(tf2, "", size=8)
    _bullet(tf2, "5 of 6 keys are identity-based", size=15, bold=True, color=ACCENT)
    _bullet(tf2, "encoding the 'repeat players win' empirical pattern", level=1, size=12, color=MUTED)
    _footer(slide, idx, total)


def slide_cv(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Forward-walking time-series CV",
                   "Why random K-fold is wrong here, and what we use instead")

    tf = _add_body_box(slide, 0.5, 1.2, 12.3, 6.0)
    _bullet(tf, "Random K-fold leaks future cancellation rates into past predictions",
            first=True, size=18, bold=True, color=ACCENT)
    _bullet(tf, "via the rolling priors → optimistic AUC by 5-10 points", level=1, size=14, color=MUTED)
    _bullet(tf, "", size=8)
    _bullet(tf, "Forward-walking CV:", size=18, bold=True, color=ACCENT)
    _bullet(tf, "train on [date_min, t),  validate on [t, t+Δ)", level=1, size=14)
    _bullet(tf, "fold boundaries snapped to row dates — no row split across folds", level=1, size=14)
    _bullet(tf, "entire pipeline (preprocessor + estimator) refits per fold", level=1, size=14)
    _bullet(tf, "", size=8)
    _bullet(tf, "Held-out tail:", size=18, bold=True, color=ACCENT)
    _bullet(tf, "rows with petition_filing_date ≥ holdout_after —", level=1, size=14)
    _bullet(tf, "never seen by CV, hyperparameter search, or feature ablation", level=1, size=14)
    _bullet(tf, "the deployment-honest evaluation slice", level=1, size=14)
    _footer(slide, idx, total)


def slide_results(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Results — held-out tail (≥ 2023-01-01)",
                   "Headline metrics + ROC curve on the deployment-honest slice")

    rows = [
        ("Metric", "Random Forest"),
        ("CV ROC-AUC (5-fold)", "0.569 ± .015"),
        ("Held-out ROC-AUC", "0.631"),
        ("Held-out Average Precision", "0.317"),
        ("Held-out Accuracy", "0.766"),
        ("Train rows", "11,689"),
        ("Held-out rows", "1,907 (23% cancelled)"),
    ]
    tbl = slide.shapes.add_table(len(rows), 2, Inches(0.4), Inches(1.2), Inches(5.4), Inches(3.6)).table
    tbl.columns[0].width = Inches(3.2)
    tbl.columns[1].width = Inches(2.2)
    for i, row in enumerate(rows):
        for j, txt in enumerate(row):
            cell = tbl.cell(i, j)
            cell.text = ""
            p = cell.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = txt
            _set_run(r, size=13, bold=(i == 0), color=WHITE if i == 0 else NAVY)
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if i == 0 else (LIGHT_BG if i % 2 else WHITE)

    tf = _add_body_box(slide, 0.4, 5.0, 5.4, 2.2)
    _bullet(tf, "Why ROC-AUC + AP together", first=True, size=14, bold=True, color=ACCENT)
    _bullet(tf, "ROC-AUC: rank quality, class-balance-blind", level=1, size=12)
    _bullet(tf, "AP: precision under the prevalence we actually face", level=1, size=12)
    _bullet(tf, "Random ranker would score AUC = 0.500, AP = 0.227", level=1, size=12, color=MUTED)
    _bullet(tf, "Lift over random: +26% AUC, +40% AP", level=1, size=12, bold=True)

    roc_path = FIGURES_DIR / "01_roc_curve.png"
    if roc_path.exists():
        slide.shapes.add_picture(
            str(roc_path),
            Inches(6.0), Inches(1.1),
            width=Inches(7.0), height=Inches(5.6),
        )
    _footer(slide, idx, total)


def slide_pr_business(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Business implication — a ranker, not a classifier",
                   "Precision–Recall on the held-out tail (class prior = 0.227)")

    pr_path = FIGURES_DIR / "02_pr_curve.png"
    if pr_path.exists():
        slide.shapes.add_picture(
            str(pr_path),
            Inches(0.3), Inches(1.1),
            width=Inches(5.0), height=Inches(3.3),
        )
    sweep_path = FIGURES_DIR / "09_threshold_sweep.png"
    if sweep_path.exists():
        slide.shapes.add_picture(
            str(sweep_path),
            Inches(0.3), Inches(4.4),
            width=Inches(5.0), height=Inches(3.0),
        )

    tf = _add_body_box(slide, 5.5, 1.15, 7.6, 6.2)
    _bullet(tf, "PR curve — AP = 0.317 vs class prior 0.227",
            first=True, size=14, bold=True, color=ACCENT)
    _bullet(tf, "≈ 40% relative lift over random ranking", level=1, size=11, color=MUTED)
    _bullet(tf, "Sweet spot at recall ≈ 0.10 — precision peaks 0.40–0.45,", level=1, size=11)
    _bullet(tf, "i.e. top-flagged trials cancel at ~2× the base rate", level=1, size=11, bold=True)
    _bullet(tf, "", size=4)
    _bullet(tf, "Threshold sweep (lower-left chart)", size=14, bold=True, color=ACCENT)
    _bullet(tf, "Visualizes the FP/FN trade-off as the lawyer slides", level=1, size=11)
    _bullet(tf, "their threshold — same model, different operating points", level=1, size=11)
    _bullet(tf, "Petitioner-side use (avoid losing) → high-precision threshold", level=1, size=11)
    _bullet(tf, "Owner-side use (don't miss a cancellation) → high-recall threshold", level=1, size=11)
    _bullet(tf, "", size=4)
    _bullet(tf, "Operating mode", size=14, bold=True, color=ACCENT)
    _bullet(tf, "Use the model to prioritize, not to pass/fail at 0.5", level=1, size=11)
    _bullet(tf, "Counsel team triaging incoming IPRs acts on the top ~10%", level=1, size=12)
    _bullet(tf, "of the ranked list → ~2× base cancellation rate in that slice", level=1, size=12)
    _bullet(tf, "Defensible attention-allocation signal:", level=1, size=12, bold=True)
    _bullet(tf, "which cases warrant early settlement scoping", level=2, size=11, color=MUTED)
    _bullet(tf, "or extra prep budget", level=2, size=11, color=MUTED)
    _footer(slide, idx, total)


def slide_feature_importance(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "What the model actually learned",
                   "Top-15 Gini importances — repeat-player priors dominate")

    fi_path = FIGURES_DIR / "04_feature_importance.png"
    if fi_path.exists():
        slide.shapes.add_picture(
            str(fi_path),
            Inches(0.4), Inches(1.1),
            width=Inches(7.4), height=Inches(5.7),
        )

    tf = _add_body_box(slide, 8.0, 1.15, 5.0, 5.7)
    _bullet(tf, "Top of the list", first=True, size=16, bold=True, color=ACCENT)
    _bullet(tf, "All 6 PriorEncoder rate/count features in top-12", level=1, size=12)
    _bullet(tf, "owner_real_party_prior_rate alone ≈ 11% of total importance", level=1, size=12)
    _bullet(tf, "", size=6)
    _bullet(tf, "Reading", size=16, bold=True, color=ACCENT)
    _bullet(tf, "Repeat-player history (party / pair / TC / era /", level=1, size=12)
    _bullet(tf, "patent-campaign) is the dominant signal — consistent", level=1, size=12)
    _bullet(tf, "with prior empirical IPR-outcome literature", level=1, size=12)
    _bullet(tf, "", size=6)
    _bullet(tf, "Non-prior signals that survive", size=16, bold=True, color=ACCENT)
    _bullet(tf, "petition_text_length (complexity proxy)", level=1, size=12)
    _bullet(tf, "days_grant_to_petition (patent age at challenge)", level=1, size=12)
    _bullet(tf, "art_unit_group, filing_year, prosecution_span_days", level=1, size=12)
    _bullet(tf, "", size=6)
    _bullet(tf, "Caveat: Gini measures train-fit reliance,", size=12, bold=True, color=MUTED)
    _bullet(tf, "not held-out lift. Counsel features were tried,", level=1, size=11, color=MUTED)
    _bullet(tf, "scored 21% gini, but added ~zero held-out AUC and were dropped.", level=1, size=11, color=MUTED)
    _footer(slide, idx, total)


def slide_diagnostic(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Generalization diagnostic — CV vs. held-out",
                   "Why fold-by-fold AUC stays flat while the held-out tail jumps")

    cv_path = FIGURES_DIR / "06_cv_vs_holdout.png"
    if cv_path.exists():
        slide.shapes.add_picture(
            str(cv_path),
            Inches(0.4), Inches(1.1),
            width=Inches(7.0), height=Inches(5.4),
        )

    tf = _add_body_box(slide, 7.6, 1.15, 5.5, 5.7)
    _bullet(tf, "The puzzle", first=True, size=16, bold=True, color=ACCENT)
    _bullet(tf, "Prior-feature depth grows monotonically with each fold", level=1, size=12)
    _bullet(tf, "→ intuition says CV AUC should also grow.", level=1, size=12)
    _bullet(tf, "It doesn't (flat ~0.53–0.55), yet held-out hits 0.58.", level=1, size=12)
    _bullet(tf, "", size=6)
    _bullet(tf, "Two opposing forces", size=16, bold=True, color=ACCENT)
    _bullet(tf, "Prior depth grows (helps)", level=1, size=12, bold=True)
    _bullet(tf, "Feature→outcome map shifts (hurts):", level=1, size=12, bold=True)
    _bullet(tf, "cancellation rate swings 6% → 36% across folds", level=2, size=11, color=MUTED)
    _bullet(tf, "Iancu, NHK-Fintiv, post-Fintiv regime breaks", level=2, size=11, color=MUTED)
    _bullet(tf, "", size=6)
    _bullet(tf, "Why the held-out tail wins both", size=16, bold=True, color=ACCENT)
    _bullet(tf, "Deepest accumulated priors AND a stable", level=1, size=12)
    _bullet(tf, "post-Fintiv regime — both align in 2023+", level=1, size=12)
    _bullet(tf, "CV folds never enjoy both at once (regime cuts", level=1, size=11, color=MUTED)
    _bullet(tf, "between 2014 and 2022 always straddle a break)", level=1, size=11, color=MUTED)
    _footer(slide, idx, total)


def slide_conclusions(prs, idx: int, total: int) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_bar(slide, "Critical reflection",
                   "Construct validity · drift · calibration · fairness · deployment posture")

    tf = _add_body_box(slide, 0.4, 1.1, 6.0, 6.0)
    _bullet(tf, "Construct validity (label)", first=True, size=14, bold=True, color=ACCENT)
    _bullet(tf, "We predict 'all claims unpatentable in original FWD'.", level=1, size=11)
    _bullet(tf, "Settlements code as 0 — but settlement is often the", level=1, size=11)
    _bullet(tf, "petitioner's actual goal. Label ≠ petitioner success.", level=1, size=11)
    _bullet(tf, "", size=3)
    _bullet(tf, "Distributional shift (concept drift)", size=14, bold=True, color=ACCENT)
    _bullet(tf, "Cancellation rate swings 9% → 27% across PTAB eras", level=1, size=11)
    _bullet(tf, "(top-right chart ➞). Each Director / §314(a) memo", level=1, size=11)
    _bullet(tf, "redefines P(Y|X). Retraining cadence must be regime-aware.", level=1, size=11)
    _bullet(tf, "", size=3)
    _bullet(tf, "Calibration: measured, not fitted", size=14, bold=True, color=ACCENT)
    _bullet(tf, "Reliability diagram (slide 9) shows mid-range over-confidence.", level=1, size=11)
    _bullet(tf, "We do *not* fit a post-hoc calibrator: monotonic ⇒ no AUC gain,", level=1, size=11)
    _bullet(tf, "and a chronological carve-off costs more held-out AUC than", level=1, size=11)
    _bullet(tf, "calibration buys back (same regime-drift effect).", level=1, size=11)
    _bullet(tf, "", size=3)
    _bullet(tf, "Fairness", size=14, bold=True, color=ACCENT)
    _bullet(tf, "26% of filings are first-time petitioners (bottom-right ➞).", level=1, size=11)
    _bullet(tf, "5 of 6 PriorEncoder keys are identity-based — first-timers", level=1, size=11)
    _bullet(tf, "cold-start to the rolling-global fallback. Risk: reinforces", level=1, size=11)
    _bullet(tf, "repeat-player advantage; chills legitimate first-time challenges.", level=1, size=11)
    _bullet(tf, "", size=3)
    _bullet(tf, "Honest deployment posture", size=14, bold=True, color=ACCENT)
    _bullet(tf, "Decision support, not autonomous decisioning;", level=1, size=11)
    _bullet(tf, "outputs as probabilities, owned by lawyers.", level=1, size=11)

    era_path = FIGURES_DIR / "10_per_era_cancellation.png"
    if era_path.exists():
        slide.shapes.add_picture(
            str(era_path),
            Inches(6.7), Inches(1.1),
            width=Inches(6.4), height=Inches(3.0),
        )
    cold_path = FIGURES_DIR / "11_cold_start_histogram.png"
    if cold_path.exists():
        slide.shapes.add_picture(
            str(cold_path),
            Inches(6.7), Inches(4.2),
            width=Inches(6.4), height=Inches(3.0),
        )
    _footer(slide, idx, total)


def main() -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    builders = [
        slide_title,
        slide_problem,
        slide_label,
        slide_pipeline,
        slide_features,
        slide_petition_text,
        slide_preprocessor,
        slide_cv,
        slide_results,
        slide_pr_business,
        slide_feature_importance,
        slide_diagnostic,
        slide_conclusions,
    ]
    total = len(builders)
    for i, build in enumerate(builders, 1):
        if build is slide_title:
            build(prs, total)
        else:
            build(prs, i, total)

    prs.save(OUT_PATH)
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
