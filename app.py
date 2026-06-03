"""
DataGenius - Intelligent Data Analysis Web Application
A Flask-based app that analyzes CSV/Excel files and provides insights,
visualizations, and a helpful chatbot assistant.
"""

import os
import io
import json
import uuid
import base64
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend (must be set before pyplot)
import matplotlib.pyplot as plt
import seaborn as sns
from flask import Flask, render_template, request, jsonify, send_from_directory, session
from werkzeug.utils import secure_filename
from prometheus_flask_exporter import PrometheusMetrics

# ----------------------------------------------------------------------------
# App configuration
# ----------------------------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'datalens-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['CHARTS_FOLDER'] = os.path.join('static', 'charts')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB upload limit
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls', 'tsv'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CHARTS_FOLDER'], exist_ok=True)

# ── Prometheus metrics (/metrics endpoint) ──────────────────────────────────
prom_metrics = PrometheusMetrics(app)
prom_metrics.info('app_info', 'DataGenius application info', version='1.0.0')


@app.route('/health')
def health():
    """Health check endpoint for load balancers and monitoring."""
    from datetime import datetime, timezone
    return {'status': 'ok', 'timestamp': datetime.now(timezone.utc).isoformat()}, 200

# Seaborn / matplotlib defaults — warm editorial palette to match the UI
_PALETTE = ["#b8533a", "#3d6b63", "#b08534", "#7a6a8f", "#4f7a4d", "#a3402f"]
sns.set_theme(style="whitegrid", palette=_PALETTE)
plt.rcParams.update({
    "figure.dpi": 110,
    "savefig.dpi": 130,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "font.family": "DejaVu Sans",
    "axes.edgecolor": "#cdc2ad",
    "axes.labelcolor": "#1c1a17",
    "xtick.color": "#4a463f",
    "ytick.color": "#4a463f",
    "text.color": "#1c1a17",
    "grid.color": "#e0d8c8",
    "figure.facecolor": "white",
    "axes.facecolor": "#fdfcf8",
})

# In-memory store of dataset metadata, keyed by session_id.
# For a production app you'd use a real database; this is fine for a demo.
DATASETS = {}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def read_dataframe(filepath: str) -> pd.DataFrame:
    """Read a CSV/Excel/TSV file into a DataFrame, with a few fallbacks."""
    ext = filepath.rsplit('.', 1)[1].lower()
    if ext == 'csv':
        # Try common encodings/separators
        for enc in ('utf-8', 'latin-1', 'cp1252'):
            try:
                return pd.read_csv(filepath, encoding=enc)
            except UnicodeDecodeError:
                continue
            except Exception:
                break
        return pd.read_csv(filepath, encoding='utf-8', engine='python', on_bad_lines='skip')
    if ext == 'tsv':
        return pd.read_csv(filepath, sep='\t')
    # xlsx / xls
    return pd.read_excel(filepath)


def df_summary(df: pd.DataFrame) -> dict:
    """Generate a structured summary of the dataframe."""
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    # 'str' is included for pandas-3 forward-compat (it's a no-op on pandas-2)
    try:
        categorical_cols = df.select_dtypes(include=['object', 'category', 'string']).columns.tolist()
    except Exception:
        categorical_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
    datetime_cols = df.select_dtypes(include=['datetime64[ns]', 'datetime64']).columns.tolist()

    # Try to detect date columns that are stored as strings
    detected_dates = []
    for col in list(categorical_cols):
        sample = df[col].dropna().head(20)
        if len(sample) == 0:
            continue
        try:
            # format='mixed' tells pandas to infer per-element without warning
            parsed = pd.to_datetime(sample, errors='coerce', format='mixed')
            if parsed.notna().sum() >= max(3, int(0.7 * len(sample))):
                detected_dates.append(col)
        except Exception:
            continue

    missing = df.isnull().sum()
    missing_dict = {col: int(val) for col, val in missing.items() if val > 0}

    # Numeric statistics — convert NaN/Inf to None so the response is valid JSON
    numeric_stats = {}
    if numeric_cols:
        desc = df[numeric_cols].describe().round(3).to_dict()
        for col, stats in desc.items():
            numeric_stats[col] = {
                k: (None if (isinstance(v, float) and (np.isnan(v) or np.isinf(v))) else v)
                for k, v in stats.items()
            }

    # Categorical top values
    categorical_stats = {}
    for col in categorical_cols[:10]:  # cap to avoid huge payloads
        vc = df[col].value_counts(dropna=True).head(5)
        categorical_stats[col] = {
            'unique': int(df[col].nunique(dropna=True)),
            'top_values': [{'value': str(k), 'count': int(v)} for k, v in vc.items()],
        }

    duplicate_count = int(df.duplicated().sum())

    # Build a string preview that is safe for any dtype (incl. nullable
    # boolean / Int / category): cast to object first so filling NA with ''
    # never violates an extension dtype's allowed values.
    preview_df = df.head(10).astype(object).where(df.head(10).notna(), '')
    preview = preview_df.astype(str).to_dict(orient='records')

    return {
        'rows': int(df.shape[0]),
        'columns': int(df.shape[1]),
        'column_names': df.columns.tolist(),
        'dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()},
        'numeric_columns': numeric_cols,
        'categorical_columns': categorical_cols,
        'datetime_columns': datetime_cols,
        'detected_date_columns': detected_dates,
        'missing_values': missing_dict,
        'total_missing': int(missing.sum()),
        'duplicate_rows': duplicate_count,
        'memory_usage_kb': round(df.memory_usage(deep=True).sum() / 1024, 2),
        'numeric_stats': numeric_stats,
        'categorical_stats': categorical_stats,
        'preview': preview,
    }


def build_narrative(filename: str, summary: dict) -> list:
    """Build a human-readable narrative about the dataset."""
    insights = []
    rows = summary['rows']
    cols = summary['columns']

    insights.append(
        f"The dataset **{filename}** contains **{rows:,} rows** and **{cols} columns**, "
        f"using approximately {summary['memory_usage_kb']:,.1f} KB of memory."
    )

    n_num = len(summary['numeric_columns'])
    n_cat = len(summary['categorical_columns'])
    n_dt = len(summary['datetime_columns']) + len(summary['detected_date_columns'])
    insights.append(
        f"It includes **{n_num} numeric**, **{n_cat} categorical**, and **{n_dt} date/time** columns."
    )

    # Missing values
    if summary['total_missing'] == 0:
        insights.append("There are **no missing values** — the dataset is complete. ✅")
    else:
        pct = (summary['total_missing'] / (rows * cols)) * 100 if rows and cols else 0
        worst = sorted(summary['missing_values'].items(), key=lambda x: -x[1])[:3]
        worst_str = ", ".join([f"`{c}` ({v:,})" for c, v in worst])
        insights.append(
            f"Found **{summary['total_missing']:,} missing values** "
            f"({pct:.2f}% of cells). Top affected columns: {worst_str}."
        )

    # Duplicates
    if summary['duplicate_rows'] > 0:
        insights.append(
            f"Detected **{summary['duplicate_rows']:,} duplicate rows** — "
            f"consider de-duplication before further analysis."
        )

    # Numeric insights
    if summary['numeric_stats']:
        first_num = summary['numeric_columns'][0]
        stats = summary['numeric_stats'][first_num]
        insights.append(
            f"For `{first_num}`, values range from **{stats.get('min', 'n/a')}** to "
            f"**{stats.get('max', 'n/a')}**, with a mean of **{stats.get('mean', 'n/a')}**."
        )

    # Categorical
    if summary['categorical_stats']:
        first_cat = next(iter(summary['categorical_stats']))
        info = summary['categorical_stats'][first_cat]
        if info['top_values']:
            top = info['top_values'][0]
            insights.append(
                f"In `{first_cat}`, there are **{info['unique']:,} unique values**, "
                f"the most common being **\"{top['value']}\"** ({top['count']:,} times)."
            )

    return insights


# ----------------------------------------------------------------------------
# AI Insights Generator
# ----------------------------------------------------------------------------
def generate_ai_insights(df: pd.DataFrame, summary: dict) -> dict:
    """Analyze the dataset and produce categorized, prioritized insights.

    This is a statistical "insight engine": it inspects distributions,
    relationships, data quality, and structure to surface non-obvious
    findings in natural language. Runs fully offline (no external LLM).

    Returns a dict:
        {
          'headline': str,
          'groups': [ {'title','icon','items':[{'severity','text'}]} ],
          'count': int
        }
    severity ∈ {'positive','info','warning','critical'}
    """
    import numpy as _np

    rows = summary['rows']
    num_cols = list(summary['numeric_columns'])
    cat_cols = list(summary['categorical_columns'])

    quality, distribution, relationship, structure = [], [], [], []

    def add(bucket, severity, text):
        bucket.append({'severity': severity, 'text': text})

    # ---------- Data quality ----------
    total_cells = rows * summary['columns'] if rows and summary['columns'] else 0
    if summary['total_missing'] == 0:
        add(quality, 'positive', "The dataset is **complete** — no missing values detected.")
    else:
        pct = (summary['total_missing'] / total_cells * 100) if total_cells else 0
        worst = sorted(summary['missing_values'].items(), key=lambda x: -x[1])[:3]
        worst_str = ", ".join(f"`{c}` ({v:,}, {v/rows*100:.0f}%)" for c, v in worst)
        sev = 'critical' if pct > 20 else ('warning' if pct > 5 else 'info')
        add(quality, sev,
            f"**{summary['total_missing']:,} missing values** ({pct:.1f}% of all cells). "
            f"Most affected: {worst_str}.")

    if summary['duplicate_rows'] > 0:
        dpct = summary['duplicate_rows'] / rows * 100 if rows else 0
        add(quality, 'warning' if dpct > 1 else 'info',
            f"**{summary['duplicate_rows']:,} duplicate rows** ({dpct:.1f}%) — "
            f"consider de-duplication to avoid biasing aggregates.")

    # Columns that are almost constant (low variance / dominant category)
    for col in cat_cols:
        info = summary['categorical_stats'].get(col)
        if not info or not info['top_values']:
            continue
        top = info['top_values'][0]
        share = top['count'] / rows if rows else 0
        if share >= 0.95 and info['unique'] > 1:
            add(quality, 'warning',
                f"`{col}` is **highly imbalanced** — \"{top['value']}\" accounts for "
                f"{share*100:.0f}% of rows, so it carries little information.")

    # High-cardinality categoricals (likely IDs / free text)
    for col in cat_cols:
        info = summary['categorical_stats'].get(col)
        if not info:
            continue
        if rows and info['unique'] / rows > 0.9 and info['unique'] > 20:
            add(structure, 'info',
                f"`{col}` has **{info['unique']:,} unique values** (~one per row) — "
                f"likely an identifier or free-text field rather than a category.")

    # Possible ID columns among numerics (monotonic, unique)
    for col in num_cols:
        try:
            s = df[col].dropna()
            if len(s) == rows and s.is_monotonic_increasing and s.nunique() == rows and rows > 5:
                add(structure, 'info',
                    f"`{col}` increases by row and is unique — it looks like an **index/ID** "
                    f"column, so treat its statistics as non-meaningful.")
        except Exception:
            pass

    # ---------- Distribution ----------
    for col in num_cols:
        s = df[col].dropna()
        if len(s) < 5:
            continue
        try:
            skew = float(s.skew())
        except Exception:
            skew = 0.0
        # Skewness
        if abs(skew) >= 1.0:
            direction = "right" if skew > 0 else "left"
            add(distribution, 'info',
                f"`{col}` is **strongly {direction}-skewed** (skew = {skew:.2f}); "
                f"a median or a log transform may represent it better than the mean.")
        # Outliers via IQR
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr and not _np.isnan(iqr):
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            n_out = int(((s < lo) | (s > hi)).sum())
            if n_out:
                opct = n_out / len(s) * 100
                sev = 'warning' if opct > 5 else 'info'
                add(distribution, sev,
                    f"`{col}` contains **{n_out:,} outlier(s)** ({opct:.1f}%) beyond the "
                    f"IQR fences — worth reviewing before modeling.")
        # Near-zero variance
        if s.nunique() == 1:
            add(distribution, 'warning',
                f"`{col}` is **constant** (a single value) and adds no analytical value.")
        # Potential negative values where unexpected (heuristic on name)
        if (s < 0).any() and any(k in col.lower() for k in
                                 ('age', 'count', 'qty', 'quantity', 'price', 'amount',
                                  'hours', 'score', 'weight', 'height', 'duration')):
            add(quality, 'warning',
                f"`{col}` has **negative values**, which may be unexpected for this field.")

    # ---------- Relationships ----------
    if len(num_cols) >= 2:
        try:
            corr = df[num_cols].corr(numeric_only=True)
            seen = set()
            pairs = []
            for i, a in enumerate(num_cols):
                for b in num_cols[i + 1:]:
                    r = corr.loc[a, b]
                    if pd.isna(r):
                        continue
                    pairs.append((abs(r), r, a, b))
            pairs.sort(reverse=True)
            strong = [p for p in pairs if p[0] >= 0.5]
            for absr, r, a, b in strong[:4]:
                strength = ("very strong" if absr >= 0.85 else
                            "strong" if absr >= 0.7 else "moderate")
                direction = "positive" if r > 0 else "negative"
                add(relationship, 'info',
                    f"**{strength.capitalize()} {direction} correlation** between `{a}` and "
                    f"`{b}` (r = {r:.2f})" +
                    (" — they move together." if r > 0 else " — one rises as the other falls."))
            if not strong:
                add(relationship, 'info',
                    "No strong linear correlations (|r| ≥ 0.5) were found among the numeric "
                    "columns — features appear largely independent.")
        except Exception:
            pass

    # Date span insight
    for col in (summary['datetime_columns'] + summary['detected_date_columns']):
        try:
            s = pd.to_datetime(df[col], errors='coerce').dropna()
            if len(s) > 1:
                span = s.max() - s.min()
                add(structure, 'info',
                    f"`{col}` spans **{s.min().date()} → {s.max().date()}** "
                    f"({span.days:,} days).")
        except Exception:
            pass

    # ---------- Assemble ----------
    groups = []
    if quality:
        groups.append({'title': 'Data quality', 'icon': 'shield', 'items': quality})
    if distribution:
        groups.append({'title': 'Distributions & outliers', 'icon': 'chart', 'items': distribution})
    if relationship:
        groups.append({'title': 'Relationships', 'icon': 'link', 'items': relationship})
    if structure:
        groups.append({'title': 'Structure & columns', 'icon': 'grid', 'items': structure})

    count = sum(len(g['items']) for g in groups)

    # Headline
    crit = sum(1 for g in groups for it in g['items'] if it['severity'] == 'critical')
    warn = sum(1 for g in groups for it in g['items'] if it['severity'] == 'warning')
    if crit:
        headline = (f"Found **{count} insights**, including **{crit} critical** data-quality "
                    f"issue(s) that should be addressed before analysis.")
    elif warn:
        headline = (f"Found **{count} insights**, with **{warn} item(s)** worth a closer look "
                    f"before drawing conclusions.")
    elif count:
        headline = (f"Found **{count} insights**. The data looks healthy — the notes below "
                    f"highlight its main characteristics.")
    else:
        headline = "The dataset is small or simple; no notable statistical patterns surfaced."

    return {'headline': headline, 'groups': groups, 'count': count}


# ----------------------------------------------------------------------------
# Data cleaning
# ----------------------------------------------------------------------------
# Tokens that commonly represent missing data but are stored as text.
_NULL_TOKENS = {
    '', 'na', 'n/a', 'n.a.', 'nan', 'none', 'null', 'nil', 'nat',
    '-', '--', '?', '??', 'unknown', 'unk', 'missing', '#n/a', '#na',
    'not available', 'not applicable', 'tbd', 'undefined', 'void',
}
# Truthy / falsy tokens for boolean detection.
_TRUE_TOKENS = {'true', 't', 'yes', 'y', '1', 'on'}
_FALSE_TOKENS = {'false', 'f', 'no', 'n', '0', 'off'}


def _normalize_colname(name: str) -> str:
    """snake_case a column name: strip, collapse spaces/symbols to underscores."""
    import re
    s = str(name).strip()
    s = re.sub(r'[^\w\s]', ' ', s)          # punctuation -> space
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', s)  # split camelCase
    s = re.sub(r'\s+', '_', s.strip())      # spaces -> underscore
    return s.lower() or 'column'


def _looks_numeric(series: pd.Series, thresh: float = 0.8) -> bool:
    """Heuristic: does a text column hold mostly numbers once symbols are stripped?"""
    import re
    sample = series.dropna().astype(str).head(200)
    if len(sample) == 0:
        return False
    ok = 0
    for v in sample:
        cleaned = re.sub(r'[,$%€£¥\s]', '', v)
        cleaned = cleaned.replace('(', '-').replace(')', '')
        if cleaned in ('', '-', '.'):
            continue
        try:
            float(cleaned)
            ok += 1
        except ValueError:
            pass
    return ok / len(sample) >= thresh


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """Strip currency/percent/thousands separators and parse to numbers."""
    import re
    def parse(v):
        if pd.isna(v):
            return np.nan
        s = str(v).strip()
        neg = s.startswith('(') and s.endswith(')')
        is_pct = '%' in s
        s = re.sub(r'[,$%€£¥\s()]', '', s)
        if s in ('', '-', '.'):
            return np.nan
        try:
            num = float(s)
        except ValueError:
            return np.nan
        if neg:
            num = -num
        if is_pct:
            num = num / 100.0
        return num
    return series.map(parse)


def clean_dataframe(df: pd.DataFrame, options: dict = None) -> tuple:
    """Professional-grade cleaning pipeline. Returns (cleaned_df, report).

    The pipeline runs in a deliberate order so each step benefits from the
    previous one (e.g. null-token normalization before missing-value imputation,
    type coercion before outlier handling).

    options (defaults shown):
        normalize_headers   True   snake_case + de-duplicate column names
        recognize_nulls     True   convert 'NA','?','-','none'… to real NaN
        strip_whitespace    True   trim + collapse internal whitespace in text
        coerce_numeric      True   parse "$1,200", "85%", "(50)" into numbers
        detect_booleans     True   map yes/no, true/false, 1/0 → boolean
        convert_dates       True   parse date-like strings to datetime
        standardize_categories True  unify casing/aliases of low-card text
        drop_empty_columns  True   drop 100%-missing columns
        drop_constant_columns True drop zero-variance columns
        drop_high_missing   False  drop columns above missing_threshold
        missing_threshold   0.6    used only when drop_high_missing is on
        impute_missing      True   smart impute (skew-aware numeric; mode text)
        drop_duplicates     True   remove exact duplicate rows
        outlier_strategy    'none' one of 'none' | 'cap' | 'remove' (IQR 1.5×)
        optimize_dtypes     True   downcast numerics & categorize low-card text
    """
    defaults = {
        'normalize_headers': True,
        'recognize_nulls': True,
        'strip_whitespace': True,
        'coerce_numeric': True,
        'detect_booleans': True,
        'convert_dates': True,
        'standardize_categories': True,
        'drop_empty_columns': True,
        'drop_constant_columns': True,
        'drop_high_missing': False,
        'missing_threshold': 0.6,
        'impute_missing': True,
        'drop_duplicates': True,
        'outlier_strategy': 'none',
        'optimize_dtypes': True,
    }
    opts = dict(defaults)
    if options:
        # Backward-compatibility shims for the previous option names
        legacy = dict(options)
        if 'fill_missing_numeric' in legacy or 'fill_missing_categorical' in legacy:
            opts['impute_missing'] = bool(legacy.get('fill_missing_numeric', True) or
                                          legacy.get('fill_missing_categorical', True))
        if 'remove_outliers' in legacy:
            opts['outlier_strategy'] = 'cap' if legacy.get('remove_outliers') else 'none'
        for k, v in legacy.items():
            if k in opts:
                opts[k] = v

    original_shape = df.shape
    original_missing = int(df.isnull().sum().sum())
    original_duplicates = int(df.duplicated().sum())

    cleaned = df.copy()
    actions = []  # list of {action, detail, count}

    def log(action, detail, count):
        actions.append({'action': action, 'detail': detail, 'count': int(count)})

    # 1) Normalize column headers --------------------------------------------
    if opts['normalize_headers']:
        new_names, seen, renamed = [], {}, 0
        for col in cleaned.columns:
            norm = _normalize_colname(col)
            if norm in seen:
                seen[norm] += 1
                norm = f"{norm}_{seen[norm]}"
            else:
                seen[norm] = 0
            if norm != str(col):
                renamed += 1
            new_names.append(norm)
        if renamed:
            cleaned.columns = new_names
            log('Normalized column headers',
                f"{renamed} of {len(new_names)} headers converted to snake_case", renamed)

    # 2) Recognize disguised nulls -------------------------------------------
    if opts['recognize_nulls']:
        converted = 0
        for c in cleaned.select_dtypes(include=['object']).columns:
            mask = cleaned[c].apply(
                lambda v: isinstance(v, str) and v.strip().lower() in _NULL_TOKENS)
            n = int(mask.sum())
            if n:
                cleaned.loc[mask, c] = np.nan
                converted += n
        if converted:
            log('Standardized missing-value tokens',
                "Values like 'NA', '?', '-', 'none' converted to true nulls", converted)

    # 3) Trim + collapse whitespace ------------------------------------------
    if opts['strip_whitespace']:
        import re as _re
        stripped = 0
        for c in cleaned.select_dtypes(include=['object']).columns:
            before = cleaned[c].copy()
            cleaned[c] = cleaned[c].apply(
                lambda v: _re.sub(r'\s+', ' ', v).strip() if isinstance(v, str) else v)
            stripped += int((before != cleaned[c]).sum())
        if stripped:
            log('Trimmed & normalized whitespace',
                "Leading/trailing trimmed and internal runs collapsed", stripped)

    # 4) Coerce numeric-looking text -----------------------------------------
    if opts['coerce_numeric']:
        coerced = []
        for c in cleaned.select_dtypes(include=['object']).columns:
            if _looks_numeric(cleaned[c]):
                parsed = _coerce_numeric(cleaned[c])
                if parsed.notna().sum() >= int(0.8 * cleaned[c].notna().sum()):
                    cleaned[c] = parsed
                    coerced.append(c)
        if coerced:
            log('Parsed numbers from text',
                "Currency/percent/thousand formats → numeric: "
                + ", ".join(f"`{c}`" for c in coerced), len(coerced))

    # 5) Detect & convert booleans -------------------------------------------
    if opts['detect_booleans']:
        bools = []
        for c in cleaned.select_dtypes(include=['object']).columns:
            vals = set(cleaned[c].dropna().astype(str).str.strip().str.lower().unique())
            if vals and vals <= (_TRUE_TOKENS | _FALSE_TOKENS) and \
               vals & _TRUE_TOKENS and vals & _FALSE_TOKENS:
                cleaned[c] = cleaned[c].astype(str).str.strip().str.lower().map(
                    lambda v: True if v in _TRUE_TOKENS else
                              (False if v in _FALSE_TOKENS else np.nan))
                bools.append(c)
        if bools:
            log('Converted text to boolean',
                ", ".join(f"`{c}`" for c in bools), len(bools))

    # 6) Convert date-like strings -------------------------------------------
    if opts['convert_dates']:
        converted = []
        for col in cleaned.select_dtypes(include=['object']).columns:
            sample = cleaned[col].dropna().head(25)
            if len(sample) < 3:
                continue
            try:
                parsed = pd.to_datetime(sample, errors='coerce', format='mixed')
                if parsed.notna().sum() >= max(3, int(0.8 * len(sample))):
                    cleaned[col] = pd.to_datetime(cleaned[col], errors='coerce', format='mixed')
                    converted.append(col)
            except Exception:
                continue
        if converted:
            log('Converted strings to datetime',
                ", ".join(f"`{c}`" for c in converted), len(converted))

    # 7) Standardize categorical values --------------------------------------
    if opts['standardize_categories']:
        affected = 0
        for c in cleaned.select_dtypes(include=['object']).columns:
            nun = cleaned[c].nunique(dropna=True)
            if nun == 0 or nun > 50:
                continue
            # Map case/spacing variants to their most frequent canonical form
            norm_key = cleaned[c].astype(str).str.strip().str.lower()
            canon = {}
            for key, grp in cleaned[c].groupby(norm_key):
                canon[key] = grp.value_counts().index[0]
            new = norm_key.map(canon)
            changed = int((cleaned[c] != new).sum())
            if changed:
                cleaned[c] = new
                affected += changed
        if affected:
            log('Standardized category labels',
                "Unified casing/spacing variants to a canonical label", affected)

    # 8) Drop fully empty columns --------------------------------------------
    if opts['drop_empty_columns']:
        empty = [c for c in cleaned.columns if cleaned[c].isnull().all()]
        if empty:
            cleaned = cleaned.drop(columns=empty)
            log('Dropped empty columns', ", ".join(f"`{c}`" for c in empty), len(empty))

    # 9) Drop constant (zero-variance) columns -------------------------------
    if opts['drop_constant_columns']:
        const = [c for c in cleaned.columns
                 if cleaned[c].nunique(dropna=True) <= 1 and cleaned[c].notna().any()]
        if const:
            cleaned = cleaned.drop(columns=const)
            log('Dropped constant columns', ", ".join(f"`{c}`" for c in const), len(const))

    # 10) Drop columns above the missing threshold ---------------------------
    if opts['drop_high_missing']:
        thr = float(opts['missing_threshold'])
        n = len(cleaned)
        high = [c for c in cleaned.columns if n and cleaned[c].isnull().mean() > thr]
        if high:
            detail = ", ".join(f"`{c}` ({cleaned[c].isnull().mean()*100:.0f}%)" for c in high)
            cleaned = cleaned.drop(columns=high)
            log(f'Dropped columns >{int(thr*100)}% missing', detail, len(high))

    # 11) Smart missing-value imputation -------------------------------------
    if opts['impute_missing']:
        num_filled, cat_filled, num_cols_done, cat_cols_done = 0, 0, [], []
        for c in cleaned.columns:
            n_missing = int(cleaned[c].isnull().sum())
            if n_missing == 0:
                continue
            s = cleaned[c]
            if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
                # Skew-aware: heavy skew → median, otherwise mean
                non_null = s.dropna()
                strat, val = 'median', non_null.median()
                if len(non_null) > 2:
                    try:
                        if abs(float(non_null.skew())) < 0.5:
                            strat, val = 'mean', round(float(non_null.mean()), 4)
                    except Exception:
                        pass
                cleaned[c] = s.fillna(val)
                num_filled += n_missing
                num_cols_done.append(f"`{c}` ({strat}, {n_missing})")
            elif pd.api.types.is_datetime64_any_dtype(s):
                continue  # leave date gaps; forward-fill is risky without ordering
            else:
                mode = s.mode(dropna=True)
                fill = mode.iloc[0] if not mode.empty else 'Unknown'
                cleaned[c] = s.fillna(fill)
                cat_filled += n_missing
                cat_cols_done.append(f"`{c}` → \"{fill}\" ({n_missing})")
        if num_filled:
            log('Imputed numeric gaps (skew-aware)', ", ".join(num_cols_done), num_filled)
        if cat_filled:
            log('Imputed categorical gaps (mode)', "; ".join(cat_cols_done), cat_filled)

    # 12) Remove exact duplicate rows ----------------------------------------
    if opts['drop_duplicates']:
        dup = int(cleaned.duplicated().sum())
        if dup:
            cleaned = cleaned.drop_duplicates().reset_index(drop=True)
            log('Removed duplicate rows',
                "Fully identical rows reduced to a single occurrence", dup)

    # 13) Outlier handling ----------------------------------------------------
    strat = str(opts.get('outlier_strategy', 'none')).lower()
    if strat in ('cap', 'remove'):
        num_cols = cleaned.select_dtypes(include=[np.number]).columns
        num_cols = [c for c in num_cols if not pd.api.types.is_bool_dtype(cleaned[c])]
        if strat == 'cap':
            capped, cols = 0, []
            for c in num_cols:
                q1, q3 = cleaned[c].quantile([0.25, 0.75])
                iqr = q3 - q1
                if iqr == 0 or pd.isna(iqr):
                    continue
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                n = int(((cleaned[c] < lo) | (cleaned[c] > hi)).sum())
                if n:
                    cleaned[c] = cleaned[c].clip(lo, hi)
                    capped += n
                    cols.append(f"`{c}` ({n})")
            if capped:
                log('Capped outliers to IQR fences (winsorize)', ", ".join(cols), capped)
        else:  # remove rows containing any outlier
            mask = pd.Series(False, index=cleaned.index)
            for c in num_cols:
                q1, q3 = cleaned[c].quantile([0.25, 0.75])
                iqr = q3 - q1
                if iqr == 0 or pd.isna(iqr):
                    continue
                lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                mask |= (cleaned[c] < lo) | (cleaned[c] > hi)
            n = int(mask.sum())
            if n and n < len(cleaned):  # never wipe the whole frame
                cleaned = cleaned.loc[~mask].reset_index(drop=True)
                log('Removed outlier rows (IQR 1.5×)',
                    "Rows with any numeric value beyond the IQR fences", n)

    # 14) Optimize dtypes (memory) -------------------------------------------
    if opts['optimize_dtypes']:
        downcast, categorized = 0, 0
        for c in cleaned.columns:
            s = cleaned[c]
            if pd.api.types.is_integer_dtype(s):
                cleaned[c] = pd.to_numeric(s, downcast='integer'); downcast += 1
            elif pd.api.types.is_float_dtype(s):
                cleaned[c] = pd.to_numeric(s, downcast='float'); downcast += 1
            elif pd.api.types.is_object_dtype(s):
                nun = s.nunique(dropna=True)
                if 0 < nun <= max(1, int(0.5 * len(s))):
                    cleaned[c] = s.astype('category'); categorized += 1
        if downcast or categorized:
            log('Optimized data types',
                f"{downcast} numeric column(s) downcast, "
                f"{categorized} text column(s) categorized", downcast + categorized)

    # Final report
    report = {
        'original_shape': {'rows': original_shape[0], 'columns': original_shape[1]},
        'cleaned_shape': {'rows': cleaned.shape[0], 'columns': cleaned.shape[1]},
        'original_missing': original_missing,
        'cleaned_missing': int(cleaned.isnull().sum().sum()),
        'original_duplicates': original_duplicates,
        'cleaned_duplicates': int(cleaned.duplicated().sum()),
        'rows_removed': original_shape[0] - cleaned.shape[0],
        'columns_removed': original_shape[1] - cleaned.shape[1],
        'actions': actions,
        'options_used': opts,
    }
    return cleaned, report


def build_cleaning_insights(report: dict, before_summary: dict, after_summary: dict) -> list:
    """Build a human-readable narrative summarizing what changed during cleaning."""
    insights = []

    if not report['actions']:
        insights.append("The dataset was already clean — no changes were necessary. ✨")
        return insights

    # Headline summary
    rows_b = report['original_shape']['rows']
    rows_a = report['cleaned_shape']['rows']
    cols_b = report['original_shape']['columns']
    cols_a = report['cleaned_shape']['columns']

    shape_change = []
    if report['rows_removed']:
        pct = (report['rows_removed'] / rows_b) * 100 if rows_b else 0
        shape_change.append(f"**{report['rows_removed']:,} rows removed** ({pct:.1f}%)")
    if report['columns_removed']:
        shape_change.append(f"**{report['columns_removed']} columns removed**")
    if shape_change:
        insights.append(
            f"Shape changed from **{rows_b:,} × {cols_b}** → **{rows_a:,} × {cols_a}** "
            f"({', '.join(shape_change)})."
        )
    else:
        insights.append(
            f"Shape preserved at **{rows_a:,} × {cols_a}** — cleaning happened in place."
        )

    # Missing values
    if report['original_missing'] > 0:
        if report['cleaned_missing'] == 0:
            insights.append(
                f"All **{report['original_missing']:,} missing values** were resolved — "
                f"the dataset is now complete. ✅"
            )
        else:
            recovered = report['original_missing'] - report['cleaned_missing']
            insights.append(
                f"Resolved **{recovered:,}** of {report['original_missing']:,} missing values "
                f"({report['cleaned_missing']:,} still remain)."
            )

    # Duplicates
    if report['original_duplicates'] > 0:
        if report['cleaned_duplicates'] == 0:
            insights.append(
                f"All **{report['original_duplicates']:,} duplicate rows** were removed. 🗑️"
            )

    # Memory savings
    mem_before = before_summary.get('memory_usage_kb', 0)
    mem_after = after_summary.get('memory_usage_kb', 0)
    if mem_before > 0:
        saved = mem_before - mem_after
        if saved > 0:
            pct = (saved / mem_before) * 100
            insights.append(
                f"Memory footprint reduced from **{mem_before:,.1f} KB** to "
                f"**{mem_after:,.1f} KB** (saved {pct:.1f}%)."
            )

    # Per-action recap
    action_count = len(report['actions'])
    insights.append(
        f"A total of **{action_count} cleaning {'operation' if action_count == 1 else 'operations'}** "
        f"were applied — see the detailed log below."
    )

    return insights


def save_chart(fig, name: str, dataset_id: str) -> str:
    """Save a matplotlib figure and return its web-relative path."""
    filename = f"{dataset_id}_{name}_{uuid.uuid4().hex[:6]}.png"
    out = os.path.join(app.config['CHARTS_FOLDER'], filename)
    fig.tight_layout()
    fig.savefig(out, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return f"/static/charts/{filename}"


def generate_charts(df: pd.DataFrame, summary: dict, dataset_id: str) -> list:
    """Generate a curated set of charts based on dataset shape."""
    charts = []
    numeric_cols = summary['numeric_columns']
    categorical_cols = summary['categorical_columns']

    # 1) Missing-values heatmap (only if there are missing values)
    if summary['total_missing'] > 0:
        try:
            fig, ax = plt.subplots(figsize=(9, 4.5))
            sns.heatmap(df.isnull(), cbar=False, yticklabels=False,
                        cmap=sns.color_palette(["#f4f0e8", "#b8533a"], as_cmap=True),
                        ax=ax)
            ax.set_title("Missing Values Map")
            ax.set_xlabel("Columns")
            charts.append({
                'title': 'Missing Values Map',
                'description': 'Red marks indicate missing entries across each column.',
                'url': save_chart(fig, 'missing', dataset_id),
            })
        except Exception:
            pass

    # 2) Correlation heatmap (numeric)
    if len(numeric_cols) >= 2:
        try:
            corr = df[numeric_cols].corr(numeric_only=True)
            size = min(10, max(5, 1 + 0.7 * len(numeric_cols)))
            fig, ax = plt.subplots(figsize=(size, size * 0.85))
            sns.heatmap(corr, annot=True, fmt=".2f", cmap="vlag", center=0,
                        square=True, linewidths=0.5, cbar_kws={"shrink": .75}, ax=ax)
            ax.set_title("Correlation Matrix (Numeric Features)", pad=14)
            charts.append({
                'title': 'Correlation Matrix',
                'description': 'How numeric columns move together. Values near ±1 indicate strong relationships.',
                'url': save_chart(fig, 'corr', dataset_id),
            })
        except Exception:
            pass

    # 3) Distribution plots for up to 4 numeric columns
    for col in numeric_cols[:4]:
        try:
            data = df[col].dropna()
            if len(data) == 0:
                continue
            fig, ax = plt.subplots(figsize=(8, 4.5))
            sns.histplot(data, kde=True, color="#b8533a", ax=ax, edgecolor="white", alpha=0.8)
            ax.axvline(data.mean(), color="#1c1a17", linestyle="--", label=f"Mean: {data.mean():.2f}")
            ax.axvline(data.median(), color="#3d6b63", linestyle="-.", label=f"Median: {data.median():.2f}")
            ax.set_title(f"Distribution of {col}")
            ax.legend()
            charts.append({
                'title': f"Distribution: {col}",
                'description': f"Histogram with kernel-density estimate for `{col}`.",
                'url': save_chart(fig, f"dist_{col}", dataset_id),
            })
        except Exception:
            continue

    # 4) Bar plots for up to 3 low-cardinality categorical columns
    cat_targets = [c for c in categorical_cols if 2 <= df[c].nunique(dropna=True) <= 20][:3]
    for col in cat_targets:
        try:
            counts = df[col].value_counts(dropna=True).head(10)
            fig, ax = plt.subplots(figsize=(8, 4.5))
            sns.barplot(x=counts.values, y=counts.index.astype(str),
                        hue=counts.index.astype(str), palette="rocket",
                        legend=False, ax=ax)
            ax.set_title(f"Top values in {col}")
            ax.set_xlabel("Count")
            ax.set_ylabel(col)
            charts.append({
                'title': f"Top values: {col}",
                'description': f"Most frequent categories in `{col}`.",
                'url': save_chart(fig, f"bar_{col}", dataset_id),
            })
        except Exception:
            continue

    # 5) Box plots — numeric vs first categorical (if it has reasonable cardinality)
    if numeric_cols and cat_targets:
        cat = cat_targets[0]
        num = numeric_cols[0]
        try:
            fig, ax = plt.subplots(figsize=(8, 4.5))
            n_cat = max(1, df[cat].nunique(dropna=True))
            sns.boxplot(data=df, x=cat, y=num, hue=cat,
                        palette=(_PALETTE * ((n_cat // len(_PALETTE)) + 1))[:n_cat],
                        legend=False, ax=ax)
            ax.set_title(f"{num} by {cat}")
            ax.tick_params(axis='x', rotation=30)
            charts.append({
                'title': f"{num} by {cat}",
                'description': f"How `{num}` is distributed across categories of `{cat}`.",
                'url': save_chart(fig, f"box_{num}_{cat}", dataset_id),
            })
        except Exception:
            pass

    # 6) Scatter of the two most-correlated numeric pair
    if len(numeric_cols) >= 2:
        try:
            corr_abs = df[numeric_cols].corr(numeric_only=True).abs().to_numpy().copy()
            np.fill_diagonal(corr_abs, 0)
            i, j = np.unravel_index(np.argmax(corr_abs), corr_abs.shape)
            x_col, y_col = numeric_cols[i], numeric_cols[j]
            if x_col != y_col:
                fig, ax = plt.subplots(figsize=(8, 4.5))
                sns.regplot(data=df, x=x_col, y=y_col, ax=ax,
                            scatter_kws={'alpha': 0.45, 'color': '#3d6b63'},
                            line_kws={'color': '#b8533a'})
                ax.set_title(f"{y_col} vs {x_col} (r = {df[[x_col, y_col]].corr().iloc[0, 1]:.2f})")
                charts.append({
                    'title': f"{y_col} vs {x_col}",
                    'description': "Strongest correlation pair, plotted with a regression line.",
                    'url': save_chart(fig, f"scatter_{x_col}_{y_col}", dataset_id),
                })
        except Exception:
            pass

    return charts


# ----------------------------------------------------------------------------
# PDF report generation
# ----------------------------------------------------------------------------
def _chart_file_path(chart_url: str) -> str:
    """Convert a /static/charts/foo.png URL to an absolute filesystem path."""
    rel = chart_url.lstrip('/')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel) \
        if not os.path.isabs(rel) else rel


def build_pdf_report(dataset_id: str) -> str:
    """Build a polished PDF report for a cleaned dataset.

    Returns the absolute path of the generated PDF file.
    Raises ValueError if the dataset can't be found or isn't a cleaned one.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        Image, PageBreak, KeepTogether,
    )

    info = DATASETS.get(dataset_id)
    if info is None:
        raise ValueError("Dataset not found")

    df = info['df']
    summary = info['summary']
    filename = info.get('original_filename') or info['filename']
    cleaning_report = info.get('cleaning_report')
    cleaning_insights = info.get('cleaning_insights') or []
    before_summary = info.get('before_summary')
    after_narrative = info.get('after_narrative') or []
    charts = info.get('charts') or []
    ai_insights = info.get('ai_insights')
    if ai_insights is None:
        try:
            ai_insights = generate_ai_insights(df, summary)
        except Exception:
            ai_insights = None

    # If this is a not-yet-cleaned dataset, we still produce a useful report
    # — just without the before/after comparison section.
    is_cleaned = cleaning_report is not None

    # ---- Output path ----
    out_name = f"{dataset_id}_report_{uuid.uuid4().hex[:6]}.pdf"
    out_dir = os.path.abspath(app.config['UPLOAD_FOLDER'])
    out_path = os.path.join(out_dir, out_name)

    # ---- Styles ----
    base_styles = getSampleStyleSheet()
    BRAND = colors.HexColor('#b8533a')
    BRAND_2 = colors.HexColor('#3d6b63')
    DARK = colors.HexColor('#1c1a17')
    GREY = colors.HexColor('#847d70')
    LIGHT = colors.HexColor('#e0d8c8')

    title_style = ParagraphStyle(
        'Title', parent=base_styles['Title'],
        fontName='Helvetica-Bold', fontSize=24, leading=28,
        textColor=DARK, alignment=TA_LEFT, spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'Subtitle', parent=base_styles['Normal'],
        fontName='Helvetica', fontSize=11, leading=14,
        textColor=GREY, alignment=TA_LEFT, spaceAfter=18,
    )
    h2_style = ParagraphStyle(
        'H2', parent=base_styles['Heading2'],
        fontName='Helvetica-Bold', fontSize=14, leading=18,
        textColor=BRAND, spaceBefore=14, spaceAfter=8,
    )
    body_style = ParagraphStyle(
        'Body', parent=base_styles['BodyText'],
        fontName='Helvetica', fontSize=10.5, leading=15,
        textColor=DARK, spaceAfter=4,
    )
    bullet_style = ParagraphStyle(
        'Bullet', parent=body_style,
        leftIndent=14, bulletIndent=2, spaceAfter=4,
    )
    caption_style = ParagraphStyle(
        'Caption', parent=base_styles['Italic'],
        fontName='Helvetica-Oblique', fontSize=9, leading=12,
        textColor=GREY, alignment=TA_CENTER, spaceAfter=10,
    )

    # ---- Document ----
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"DataGenius Report — {filename}",
        author="DataGenius",
    )

    story = []

    # ---- Header ----
    story.append(Paragraph("DataGenius Analysis Report", title_style))
    timestamp = datetime.now(timezone.utc).strftime("%B %d, %Y · %H:%M UTC")
    story.append(Paragraph(
        f"<b>Dataset:</b> {filename}<br/>"
        f"<b>Generated:</b> {timestamp}<br/>"
        f"<b>Status:</b> {'Cleaned & analyzed' if is_cleaned else 'Analyzed'}",
        subtitle_style,
    ))

    # Horizontal accent rule
    rule = Table([['']], colWidths=[17 * cm], rowHeights=[2])
    rule.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), BRAND)]))
    story.append(rule)
    story.append(Spacer(1, 14))

    # ---- 1. Executive summary ----
    story.append(Paragraph("1 · Executive Summary", h2_style))
    exec_text = (
        f"This report summarizes the analysis"
        f"{' and cleaning' if is_cleaned else ''} of <b>{filename}</b>. "
        f"The final dataset contains <b>{summary['rows']:,} rows</b> across "
        f"<b>{summary['columns']} columns</b>, comprising "
        f"<b>{len(summary['numeric_columns'])} numeric</b>, "
        f"<b>{len(summary['categorical_columns'])} categorical</b>, and "
        f"<b>{len(summary['datetime_columns']) + len(summary['detected_date_columns'])} "
        f"date/time</b> features."
    )
    if summary['total_missing'] == 0:
        exec_text += " The dataset is complete with <b>no missing values</b>."
    else:
        exec_text += f" It contains <b>{summary['total_missing']:,} missing values</b>."
    story.append(Paragraph(exec_text, body_style))

    # Key metrics table
    metrics = [
        ['Rows', f"{summary['rows']:,}"],
        ['Columns', str(summary['columns'])],
        ['Numeric columns', str(len(summary['numeric_columns']))],
        ['Categorical columns', str(len(summary['categorical_columns']))],
        ['Missing values', f"{summary['total_missing']:,}"],
        ['Duplicate rows', f"{summary['duplicate_rows']:,}"],
        ['Memory usage', f"{summary['memory_usage_kb']:,.1f} KB"],
    ]
    metric_table = Table(metrics, colWidths=[7 * cm, 6 * cm])
    metric_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), GREY),
        ('TEXTCOLOR', (1, 0), (1, -1), DARK),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, LIGHT),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f7f3ec')]),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('BOX', (0, 0), (-1, -1), 0.5, LIGHT),
    ]))
    story.append(Spacer(1, 8))
    story.append(metric_table)

    # ---- 2. Cleaning summary (only if cleaned) ----
    if is_cleaned:
        story.append(Paragraph("2 · Data Cleaning Summary", h2_style))

        if cleaning_report['actions']:
            story.append(Paragraph(
                f"<b>{len(cleaning_report['actions'])} cleaning "
                f"{'operation was' if len(cleaning_report['actions']) == 1 else 'operations were'}</b> "
                f"applied to produce the final dataset:",
                body_style,
            ))

            # Actions table
            action_data = [['#', 'Operation', 'Count']]
            for i, a in enumerate(cleaning_report['actions'], 1):
                action_data.append([str(i), a['action'], f"{a['count']:,}"])
            action_table = Table(action_data, colWidths=[1 * cm, 12 * cm, 4 * cm])
            action_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), BRAND),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9.5),
                ('TEXTCOLOR', (0, 1), (-1, -1), DARK),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                    [colors.white, colors.HexColor('#f7f3ec')]),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.25, LIGHT),
            ]))
            story.append(Spacer(1, 4))
            story.append(action_table)
        else:
            story.append(Paragraph(
                "The dataset was already clean — no operations were necessary.",
                body_style,
            ))

        # Before / after table
        if before_summary:
            story.append(Spacer(1, 12))
            story.append(Paragraph("<b>Before vs. After</b>", body_style))
            ba_rows = [
                ['Metric', 'Before', 'After', 'Change'],
            ]
            comparisons = [
                ('Rows', before_summary['rows'], summary['rows']),
                ('Columns', before_summary['columns'], summary['columns']),
                ('Missing values', before_summary['total_missing'], summary['total_missing']),
                ('Duplicate rows', before_summary['duplicate_rows'], summary['duplicate_rows']),
                ('Memory (KB)', round(before_summary['memory_usage_kb'], 1),
                 round(summary['memory_usage_kb'], 1)),
            ]
            for label, b, a in comparisons:
                delta = a - b
                if isinstance(delta, float):
                    delta_str = f"{delta:+.1f}" if delta else "—"
                else:
                    delta_str = f"{delta:+,}" if delta else "—"
                ba_rows.append([label, f"{b:,}" if isinstance(b, int) else f"{b}",
                                f"{a:,}" if isinstance(a, int) else f"{a}", delta_str])
            ba_table = Table(ba_rows, colWidths=[5 * cm, 4 * cm, 4 * cm, 4 * cm], repeatRows=1)
            ba_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), DARK),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9.5),
                ('TEXTCOLOR', (0, 1), (-1, -1), DARK),
                ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                    [colors.white, colors.HexColor('#f7f3ec')]),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.25, LIGHT),
            ]))
            story.append(Spacer(1, 4))
            story.append(ba_table)

        # Cleaning insights bullets
        if cleaning_insights:
            story.append(Spacer(1, 12))
            story.append(Paragraph("<b>Cleaning insights</b>", body_style))
            for line in cleaning_insights:
                story.append(Paragraph(f"• {_md_to_rl(line)}", bullet_style))

    # Section break for analysis
    story.append(PageBreak())

    # ---- 3. Analysis of (cleaned) dataset ----
    section_num = "3" if is_cleaned else "2"
    story.append(Paragraph(
        f"{section_num} · Analysis of the {'Cleaned ' if is_cleaned else ''}Dataset",
        h2_style,
    ))

    # Narrative bullets
    narrative = after_narrative if after_narrative else build_narrative(filename, summary)
    for line in narrative:
        story.append(Paragraph(f"• {_md_to_rl(line)}", bullet_style))

    # AI Insights subsection
    if ai_insights and ai_insights.get('groups'):
        story.append(Spacer(1, 12))
        story.append(Paragraph("<b>AI Insights</b>", body_style))
        story.append(Paragraph(_md_to_rl(ai_insights.get('headline', '')), body_style))
        story.append(Spacer(1, 4))
        for g in ai_insights['groups']:
            story.append(Paragraph(f"<b>{_md_to_rl(g['title'])}</b>", body_style))
            for it in g['items']:
                tag = ''
                if it['severity'] in ('warning', 'critical'):
                    tag = f" [{it['severity'].upper()}]"
                story.append(Paragraph(f"• {_md_to_rl(it['text'])}{tag}", bullet_style))
            story.append(Spacer(1, 4))

    # Numeric stats table
    if summary['numeric_stats']:
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>Numeric column statistics</b>", body_style))
        header = ['Column', 'Count', 'Mean', 'Std', 'Min', '25%', '50%', '75%', 'Max']
        rows = [header]
        for col in summary['numeric_columns'][:12]:  # cap to 12 columns
            s = summary['numeric_stats'].get(col, {})
            def _fmt(v):
                if v is None:
                    return '—'
                try:
                    return f"{float(v):,.2f}"
                except Exception:
                    return str(v)
            rows.append([
                col, _fmt(s.get('count')), _fmt(s.get('mean')), _fmt(s.get('std')),
                _fmt(s.get('min')), _fmt(s.get('25%')), _fmt(s.get('50%')),
                _fmt(s.get('75%')), _fmt(s.get('max')),
            ])
        stat_table = Table(rows, repeatRows=1)
        stat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_2),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8.5),
            ('TEXTCOLOR', (0, 1), (-1, -1), DARK),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                [colors.white, colors.HexColor('#f7f3ec')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('GRID', (0, 0), (-1, -1), 0.25, LIGHT),
        ]))
        story.append(Spacer(1, 4))
        story.append(stat_table)

    # Categorical columns
    if summary['categorical_stats']:
        story.append(Spacer(1, 14))
        story.append(Paragraph("<b>Categorical columns — top values</b>", body_style))
        cat_rows = [['Column', 'Unique', 'Top values (count)']]
        for col, info_ in summary['categorical_stats'].items():
            top_str = "; ".join(
                f"{tv['value']} ({tv['count']:,})" for tv in info_['top_values'][:3]
            ) or '—'
            # Truncate very long lists
            if len(top_str) > 90:
                top_str = top_str[:87] + '…'
            cat_rows.append([col, f"{info_['unique']:,}", top_str])
        cat_table = Table(cat_rows, colWidths=[4 * cm, 2.5 * cm, 10 * cm])
        cat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_2),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8.5),
            ('TEXTCOLOR', (0, 1), (-1, -1), DARK),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                [colors.white, colors.HexColor('#f7f3ec')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('GRID', (0, 0), (-1, -1), 0.25, LIGHT),
        ]))
        story.append(Spacer(1, 4))
        story.append(cat_table)

    # ---- 4. Visualizations ----
    if charts:
        story.append(PageBreak())
        section_num = "4" if is_cleaned else "3"
        story.append(Paragraph(f"{section_num} · Visualizations", h2_style))
        story.append(Paragraph(
            "The following charts were generated from the "
            f"{'cleaned ' if is_cleaned else ''}dataset.",
            body_style,
        ))
        story.append(Spacer(1, 8))

        for chart in charts:
            chart_path = _chart_file_path(chart['url'])
            if not os.path.exists(chart_path):
                continue
            try:
                # Constrain images to fit on the page while preserving aspect ratio
                img = Image(chart_path, width=15 * cm, height=8.5 * cm, kind='proportional')
                block = [
                    Paragraph(f"<b>{chart['title']}</b>", body_style),
                    img,
                    Paragraph(chart.get('description', ''), caption_style),
                    Spacer(1, 10),
                ]
                story.append(KeepTogether(block))
            except Exception:
                continue

    # ---- Footer with page numbers ----
    def _footer(canvas_, doc_):
        canvas_.saveState()
        canvas_.setFont('Helvetica', 8)
        canvas_.setFillColor(GREY)
        # Footer line
        canvas_.setStrokeColor(LIGHT)
        canvas_.setLineWidth(0.5)
        canvas_.line(2 * cm, 1.6 * cm, A4[0] - 2 * cm, 1.6 * cm)
        canvas_.drawString(2 * cm, 1.2 * cm, "DataGenius · Automated Data Analysis Report")
        canvas_.drawRightString(
            A4[0] - 2 * cm, 1.2 * cm,
            f"Page {doc_.page}",
        )
        canvas_.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


def _md_to_rl(text: str) -> str:
    """Convert our tiny inline markdown (**bold**, `code`) to ReportLab markup.

    Also HTML-escapes everything else so user data can't break the parser,
    and strips emoji glyphs that ReportLab's built-in fonts can't render
    (otherwise they appear as black boxes).
    """
    import html
    import re

    # Strip emoji / pictographs / dingbats — built-in PDF fonts don't have them
    safe = re.sub(
        '['
        '\U0001F300-\U0001F5FF'  # symbols & pictographs
        '\U0001F600-\U0001F64F'  # emoticons
        '\U0001F680-\U0001F6FF'  # transport & map
        '\U0001F700-\U0001F77F'
        '\U0001F780-\U0001F7FF'
        '\U0001F800-\U0001F8FF'
        '\U0001F900-\U0001F9FF'
        '\U0001FA00-\U0001FA6F'
        '\U0001FA70-\U0001FAFF'
        '\u2600-\u26FF'           # misc symbols
        '\u2700-\u27BF'           # dingbats
        '\u2300-\u23FF'           # misc technical
        '\uFE0F'                  # variation selector-16
        ']+',
        '', text,
    ).strip()

    # HTML-escape so user data can't break ReportLab's mini-XML parser
    safe = html.escape(safe)
    # **bold**
    safe = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', safe)
    # `code` — render as monospace
    safe = re.sub(r'`([^`]+)`', r'<font face="Courier" color="#99402b">\1</font>', safe)
    return safe


# ----------------------------------------------------------------------------
# Chatbot
# ----------------------------------------------------------------------------
def _norm_token(s: str) -> str:
    """Lowercase and strip non-alphanumerics for loose comparison."""
    import re
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


# Common abbreviations seen in column names → their expansions (both directions).
_ABBREV = {
    'avg': 'average', 'amt': 'amount', 'qty': 'quantity', 'num': 'number',
    'no': 'number', 'cnt': 'count', 'pct': 'percent', 'perc': 'percent',
    'pcnt': 'percent', 'p': 'per', 'hr': 'hour', 'hrs': 'hours',
    'min': 'minute', 'sec': 'second', 'yr': 'year', 'yrs': 'years',
    'mo': 'month', 'wk': 'week', 'dt': 'date', 'tot': 'total',
    'addr': 'address', 'desc': 'description', 'cat': 'category',
    'id': 'identifier', 'temp': 'temperature', 'dob': 'dateofbirth',
    'std': 'standard', 'dev': 'deviation', 'freq': 'frequency',
    'lat': 'latitude', 'lng': 'longitude', 'lon': 'longitude',
    'wt': 'weight', 'ht': 'height', 'dur': 'duration',
}


def _canonical_tokens(name: str) -> list:
    """Split a column name into normalized tokens, expanding known abbreviations.

    'STUDY_HOURS_P_DAY' -> ['study', 'hours', 'per', 'day']
    """
    import re
    raw = re.split(r'[^a-zA-Z0-9]+', str(name).lower())
    raw = [t for t in raw if t]
    out = []
    for t in raw:
        out.append(_ABBREV.get(t, t))
    return out


def _seq_ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def _score_column(query: str, column: str) -> float:
    """Similarity score in [0,1] between a user-typed phrase and a column name.

    Combines: (1) full-string fuzzy ratio on normalized text, and
    (2) token-level matching with abbreviation expansion, so
    'study hours p day' aligns well with 'STUDY_HOURS_PER_DAY'.
    """
    q_norm, c_norm = _norm_token(query), _norm_token(column)
    if not q_norm or not c_norm:
        return 0.0
    if q_norm == c_norm:
        return 1.0

    # Whole-string fuzzy similarity (catches single-character typos)
    full = _seq_ratio(q_norm, c_norm)

    # Token-level similarity with abbreviation expansion
    q_tokens = [_ABBREV.get(t, t) for t in _canonical_tokens(query)]
    c_tokens = _canonical_tokens(column)
    token_score = 0.0
    if q_tokens and c_tokens:
        matched = 0.0
        for qt in q_tokens:
            best = max((_seq_ratio(qt, ct) for ct in c_tokens), default=0.0)
            if best >= 0.8:
                matched += best
        # Reward covering most of the column's tokens
        coverage = matched / max(len(q_tokens), len(c_tokens))
        token_score = coverage

    # A normalized substring match is a strong signal
    sub = 0.0
    if q_norm in c_norm or c_norm in q_norm:
        sub = 0.9

    return max(full, token_score, sub)


def _find_column(query: str, columns: list, threshold: float = 0.6):
    """Return (best_column, score) for the user's phrase, or (None, 0)."""
    best, best_score = None, 0.0
    for col in columns:
        s = _score_column(query, col)
        if s > best_score:
            best, best_score = col, s
    if best_score >= threshold:
        return best, best_score
    return None, best_score


def _extract_column_phrase(msg: str, columns: list):
    """Find which column the user is referring to in a free-text message.

    Strategy, in priority order:
      1. Anything in quotes ("...", '...', `...`) is treated as the target.
      2. Otherwise, slide over the message's word n-grams and fuzzy-match
         each against the columns, keeping the best.
    Returns (column, score, matched_phrase) or (None, 0, None).
    """
    import re

    # 1) Quoted text wins
    quoted = re.findall(r'["\'`]([^"\'`]+)["\'`]', msg)
    for q in quoted:
        col, score = _find_column(q, columns, threshold=0.5)
        if col:
            return col, score, q

    # 2) Try n-grams of the message words (handles unquoted column refs)
    words = re.findall(r'[a-zA-Z0-9_%]+', msg)
    # Skip common question/stat words so they don't pollute matching
    stop = {'what', 'whats', 'is', 'the', 'of', 'for', 'in', 'a', 'an', 'me',
            'mean', 'average', 'avg', 'median', 'max', 'maximum', 'min',
            'minimum', 'sum', 'total', 'std', 'standard', 'deviation', 'show',
            'tell', 'about', 'value', 'values', 'highest', 'lowest', 'give',
            'and', 'how', 'many', 'much', 'are', 'there', 'do', 'does',
            'count', 'number', 'spread', 'please', 'can', 'you', 'find'}

    best = (None, 0.0, None)
    n = len(words)
    for size in (4, 3, 2, 1):
        for i in range(max(0, n - size + 1)):
            gram_words = words[i:i + size]
            # Don't build a phrase entirely from stop words
            if all(w.lower() in stop for w in gram_words):
                continue
            phrase = ' '.join(gram_words)
            col, score = _find_column(phrase, columns, threshold=0.0)
            if score > best[1]:
                best = (col, score, phrase)
    if best[0] and best[1] >= 0.6:
        return best
    return (None, best[1], best[2])


def _fuzzy_has(msg: str, keywords: list, cutoff: float = 0.82) -> bool:
    """True if the message contains any keyword, allowing for small typos.

    Multi-word keywords are matched as substrings (after a fuzzy pass on the
    whole message); single words are fuzzy-matched against each word token.
    """
    from difflib import get_close_matches
    m = msg.lower()
    words = m.replace('?', ' ').replace('.', ' ').replace(',', ' ').split()
    for kw in keywords:
        if ' ' in kw:
            if kw in m:
                return True
            continue
        if kw in words:
            return True
        if get_close_matches(kw, words, n=1, cutoff=cutoff):
            return True
    return False


def chatbot_response(message: str, dataset_id: str = None) -> str:
    """A rule-based assistant that answers questions about the loaded dataset.

    Understands typos and partial/abbreviated column names via fuzzy matching.
    No external LLM is required — this runs offline and is deterministic.
    """
    msg = message.lower().strip()
    if not msg:
        return "Please type a question and I'll do my best to help."

    info = DATASETS.get(dataset_id) if dataset_id else None
    df: pd.DataFrame = info['df'] if info else None
    summary = info['summary'] if info else None
    filename = info['filename'] if info else None

    # Greetings & meta
    if any(w in msg for w in ['hello', 'hi ', 'hey', 'hi!', 'hi.', 'salam']) or msg in ('hi', 'hello'):
        return ("Hello! 👋 I'm your data assistant. Upload a file and ask me things like "
                "*\"what columns are in this data?\"*, *\"are there missing values?\"*, "
                "*\"what's the average of [column]?\"*, or *\"show me correlations.\"*")

    if _fuzzy_has(msg, ['help', 'what can you do', 'how to use', 'commands']):
        return ("I can answer questions about your uploaded dataset — even with typos or "
                "shortened column names. Try:\n"
                "• *How many rows / columns?*\n"
                "• *What columns are there?*\n"
                "• *Are there missing values?*\n"
                "• *What is the mean / median / max of <column>?*\n"
                "• *Show me correlations.*\n"
                "• *Tell me about <column>.*\n"
                "• *Summarize the dataset.*")

    if 'who are you' in msg or 'who made you' in msg or 'your name' in msg:
        return "I'm **DataGenius Assistant**, a built-in helper for exploring your uploaded data."

    if df is None or summary is None:
        return ("I don't see a dataset yet. Upload a CSV or Excel file using the upload box, "
                "and I'll be ready to answer questions about it!")

    cols = summary['column_names']

    # Detect the statistic the user wants (typo-tolerant)
    def wants(*keys):
        return _fuzzy_has(msg, list(keys))

    # ---- Try to resolve a specific column the user mentioned ----
    target_col, col_score, matched_phrase = _extract_column_phrase(msg, cols)

    # Dataset-level questions (checked before column stats unless a strong
    # column match exists, so "how many rows" isn't hijacked by a column).
    strong_col = target_col is not None and col_score >= 0.8

    if not strong_col:
        if _fuzzy_has(msg, ['how many rows', 'number of rows', 'row count', 'rows', 'records']):
            return f"The dataset has **{summary['rows']:,} rows**."

        if _fuzzy_has(msg, ['how many columns', 'number of columns', 'column count', 'columns']) \
                and not _fuzzy_has(msg, ['list', 'name', 'which', 'what']):
            return f"The dataset has **{summary['columns']} columns**."

        if _fuzzy_has(msg, ['shape', 'size', 'dimensions']):
            return f"The dataset is **{summary['rows']:,} rows × {summary['columns']} columns**."

        if _fuzzy_has(msg, ['column']) and _fuzzy_has(msg, ['what', 'list', 'name', 'which', 'all']):
            col_list = ", ".join(f"`{c}`" for c in cols)
            return f"The columns are: {col_list}."

    if _fuzzy_has(msg, ['missing', 'null', 'nan', 'empty', 'na']):
        if summary['total_missing'] == 0:
            return "Great news — there are **no missing values** in the dataset. ✅"
        items = sorted(summary['missing_values'].items(), key=lambda x: -x[1])[:5]
        lines = "\n".join([f"• `{c}`: {v:,}" for c, v in items])
        return f"Total missing values: **{summary['total_missing']:,}**. Top columns:\n{lines}"

    if _fuzzy_has(msg, ['duplicate', 'duplicates', 'dupes']):
        d = summary['duplicate_rows']
        return (f"Found **{d:,} duplicate rows**." if d
                else "No duplicate rows detected. ✅")

    if _fuzzy_has(msg, ['dtype', 'dtypes', 'data type', 'data types', 'types']) and not strong_col:
        lines = "\n".join([f"• `{c}`: {t}" for c, t in summary['dtypes'].items()])
        return f"Column data types:\n{lines}"

    # ---- Action / meta intents take priority over weak column matches ----
    # (so "how do I clean my data" isn't mistaken for a column query).
    action_intent = None
    if _fuzzy_has(msg, ['insight', 'insights', 'findings', 'anything interesting',
                        'what stands out', 'patterns', 'analyze', 'analysis']):
        action_intent = 'insights'
    elif _fuzzy_has(msg, ['correlation', 'correlate', 'correlated', 'relationship']):
        action_intent = 'correlation'
    elif _fuzzy_has(msg, ['report', 'pdf', 'export']):
        action_intent = 'report'
    elif _fuzzy_has(msg, ['clean', 'cleaning', 'tidy', 'preprocess']):
        action_intent = 'clean'
    elif _fuzzy_has(msg, ['summarize', 'summarise', 'summary', 'overview',
                          'describe the data', 'about the data', 'what is this dataset']):
        action_intent = 'summary'

    # A "column query" needs either a statistic word, an about/tell phrase,
    # or a very strong name match — otherwise stray words won't hijack intent.
    stat_signal = _fuzzy_has(msg, [
        'mean', 'average', 'avg', 'median', 'max', 'maximum', 'highest',
        'largest', 'biggest', 'min', 'minimum', 'lowest', 'smallest', 'std',
        'standard deviation', 'deviation', 'spread', 'sum', 'total',
        'unique', 'distinct', 'count', 'tell', 'about', 'describe', 'info',
        'value', 'values', 'stats', 'statistics',
    ])
    column_query = (target_col is not None and action_intent is None and
                    (stat_signal or col_score >= 0.8))

    # ---- Per-column statistics (fuzzy column resolution) ----
    if column_query:
        col = target_col
        # Politely confirm the match if it wasn't exact, so the user knows.
        note = ""
        if _norm_token(matched_phrase or '') != _norm_token(col):
            note = f" *(interpreting your question as `{col}`)*"

        if col in summary['numeric_columns']:
            stats = summary['numeric_stats'].get(col, {})
            if wants('mean', 'average', 'avg'):
                return f"The mean of `{col}` is **{stats.get('mean')}**.{note}"
            if wants('median', '50%'):
                return f"The median of `{col}` is **{stats.get('50%')}**.{note}"
            if wants('max', 'maximum', 'highest', 'largest', 'biggest'):
                return f"The maximum of `{col}` is **{stats.get('max')}**.{note}"
            if wants('min', 'minimum', 'lowest', 'smallest'):
                return f"The minimum of `{col}` is **{stats.get('min')}**.{note}"
            if wants('std', 'standard deviation', 'spread', 'deviation'):
                return f"The standard deviation of `{col}` is **{stats.get('std')}**.{note}"
            if wants('sum', 'total'):
                try:
                    return f"The sum of `{col}` is **{df[col].sum():,.2f}**.{note}"
                except Exception:
                    return f"I couldn't sum `{col}`.{note}"
            if wants('count', 'how many'):
                return f"`{col}` has **{int(stats.get('count', df[col].count()))}** non-null values.{note}"
            # default: a small numeric summary
            return (f"**`{col}`** (numeric) — mean: {stats.get('mean')}, "
                    f"median: {stats.get('50%')}, min: {stats.get('min')}, "
                    f"max: {stats.get('max')}, std: {stats.get('std')}.{note}")
        elif col in summary['categorical_columns']:
            cinfo = summary['categorical_stats'].get(col, {})
            if not cinfo:
                cinfo = {
                    'unique': int(df[col].nunique()),
                    'top_values': [{'value': str(k), 'count': int(v)}
                                   for k, v in df[col].value_counts().head(5).items()],
                }
            top = cinfo['top_values'][0] if cinfo['top_values'] else None
            top_text = f", most common: \"{top['value']}\" ({top['count']:,} times)" if top else ""
            return f"**`{col}`** (categorical) — {cinfo['unique']:,} unique values{top_text}.{note}"
        else:
            return f"`{col}` exists in the dataset (type: {summary['dtypes'].get(col)}).{note}"

    if action_intent == 'insights':
        ai = info.get('ai_insights') if info else None
        if not ai:
            ai = generate_ai_insights(df, summary)
        if not ai or not ai.get('groups'):
            return "I didn't find any notable statistical patterns in this dataset."
        # Strip markdown bold/code for a clean chat reply, keep top items per group
        import re as _re
        def plain(t):
            t = _re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
            return _re.sub(r'`([^`]+)`', r'\1', t)
        lines = [plain(ai['headline'])]
        for g in ai['groups']:
            top = g['items'][:2]
            for it in top:
                lines.append(f"• {plain(it['text'])}")
        lines.append("\nSee the **AI Insights** section for the full breakdown.")
        return "\n".join(lines)

    if action_intent == 'summary' or _fuzzy_has(msg, ['summary', 'summarize', 'summarise',
                        'overview', 'about the data', 'describe the data',
                        'what is this dataset', 'what is the data', 'describe']):
        return (
            f"**{filename}** — {summary['rows']:,} rows × {summary['columns']} columns. "
            f"{len(summary['numeric_columns'])} numeric, "
            f"{len(summary['categorical_columns'])} categorical, "
            f"{len(summary['datetime_columns']) + len(summary['detected_date_columns'])} date/time. "
            f"Missing values: {summary['total_missing']:,}. "
            f"Duplicates: {summary['duplicate_rows']:,}."
        )

    if action_intent == 'correlation' or _fuzzy_has(msg, ['correlation', 'correlate', 'correlated', 'related', 'relationship']):
        nums = summary['numeric_columns']
        if len(nums) < 2:
            return "I need at least two numeric columns to compute correlations."
        corr_abs = df[nums].corr(numeric_only=True).abs().to_numpy().copy()
        np.fill_diagonal(corr_abs, 0)
        i, j = np.unravel_index(np.argmax(corr_abs), corr_abs.shape)
        a, b = nums[i], nums[j]
        r = df[[a, b]].corr().iloc[0, 1]
        return (f"The strongest correlation is between **`{a}`** and **`{b}`** "
                f"with r = **{r:.3f}**. Check the Correlation Matrix chart for the full picture.")

    if action_intent == 'report' or _fuzzy_has(msg, ['report', 'pdf', 'export']):
        return ("You can generate a polished **PDF report** from the *Clean the dataset* "
                "section. After cleaning, click **Generate Report** — DataGenius will build "
                "a multi-page document with executive summary, cleaning log, statistics, "
                "insights, and all visualizations included.")

    if action_intent == 'clean' or _fuzzy_has(msg, ['clean', 'cleaning', 'tidy', 'preprocess', 'fix data']):
        return ("To clean your dataset, head to the *Clean the dataset* section and click "
                "**Run cleaning pipeline**. DataGenius applies a professional sequence: it "
                "normalizes headers, recovers disguised nulls, parses numbers/booleans/dates "
                "out of text, standardizes category labels, drops empty/constant columns, "
                "imputes missing values (skew-aware), removes duplicates, and optimizes dtypes. "
                "You'll then see before/after stats, a full action log, charts, and a downloadable CSV.")

    if _fuzzy_has(msg, ['thank', 'thanks', 'thx']):
        return "You're welcome! Ask anything else about your data. 🙂"

    # ---- Fallback: did they *almost* name a column? Offer a suggestion. ----
    if matched_phrase and col_score >= 0.4:
        guess, gscore = _find_column(matched_phrase, cols, threshold=0.4)
        if guess:
            return (f"I didn't quite catch that. Did you mean the column **`{guess}`**? "
                    f"Try, for example, *\"mean of {guess}\"* or *\"tell me about {guess}\"*.")

    return ("I'm not sure I understood that. Try asking about rows, columns, missing values, "
            "correlations, or statistics of a specific column (e.g., *\"mean of price\"*). "
            "Type **help** for a full list of examples.")


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part in request.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'}), 400

    if not allowed_file(file.filename):
        return jsonify({
            'success': False,
            'error': f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        }), 400

    filename = secure_filename(file.filename)
    dataset_id = uuid.uuid4().hex[:12]
    saved_name = f"{dataset_id}_{filename}"
    saved_path = os.path.join(app.config['UPLOAD_FOLDER'], saved_name)
    file.save(saved_path)

    try:
        df = read_dataframe(saved_path)
    except Exception as e:
        return jsonify({'success': False, 'error': f"Could not read file: {e}"}), 400

    if df.empty:
        return jsonify({'success': False, 'error': "The uploaded file is empty."}), 400

    summary = df_summary(df)
    narrative = build_narrative(filename, summary)
    ai_insights = generate_ai_insights(df, summary)
    charts = generate_charts(df, summary, dataset_id)

    DATASETS[dataset_id] = {
        'df': df,
        'summary': summary,
        'filename': filename,
        'uploaded_at': datetime.now(timezone.utc).isoformat(),
        'ai_insights': ai_insights,
    }
    session['dataset_id'] = dataset_id

    # Trim summary fields for the JSON response (avoid sending the entire frame back)
    return jsonify({
        'success': True,
        'dataset_id': dataset_id,
        'filename': filename,
        'summary': summary,
        'narrative': narrative,
        'ai_insights': ai_insights,
        'charts': charts,
    })


@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    dataset_id = data.get('dataset_id') or session.get('dataset_id')
    reply = chatbot_response(message, dataset_id)
    return jsonify({'reply': reply})


@app.route('/clean', methods=['POST'])
def clean():
    """Clean the dataset identified by dataset_id and return a fresh
    summary, insights, and charts for the cleaned data."""
    data = request.get_json(silent=True) or {}
    dataset_id = data.get('dataset_id') or session.get('dataset_id')
    options = data.get('options') or {}

    info = DATASETS.get(dataset_id)
    if info is None:
        return jsonify({
            'success': False,
            'error': 'No active dataset. Please upload a file first.'
        }), 400

    original_df = info['df']
    before_summary = info['summary']
    filename = info['filename']

    try:
        cleaned_df, report = clean_dataframe(original_df, options)
    except Exception as e:
        return jsonify({'success': False, 'error': f"Cleaning failed: {e}"}), 500

    after_summary = df_summary(cleaned_df)
    cleaning_insights = build_cleaning_insights(report, before_summary, after_summary)
    after_narrative = build_narrative(f"{filename} (cleaned)", after_summary)
    ai_insights = generate_ai_insights(cleaned_df, after_summary)

    # New dataset id for the cleaned version, so we can keep both side-by-side
    cleaned_id = f"{dataset_id}_clean"
    DATASETS[cleaned_id] = {
        'df': cleaned_df,
        'summary': after_summary,
        'filename': f"{filename} (cleaned)",
        'uploaded_at': datetime.now(timezone.utc).isoformat(),
        'parent_id': dataset_id,
        # Cache the cleaning artifacts so /report can rebuild a polished PDF
        # without rerunning the cleaner.
        'original_filename': filename,
        'cleaning_report': report,
        'cleaning_insights': cleaning_insights,
        'before_summary': before_summary,
        'after_narrative': after_narrative,
        'ai_insights': ai_insights,
    }
    session['dataset_id'] = cleaned_id  # switch active dataset to the cleaned one

    charts = generate_charts(cleaned_df, after_summary, cleaned_id)
    DATASETS[cleaned_id]['charts'] = charts  # cache for the report

    # Save the cleaned dataset as a downloadable CSV
    download_name = f"{cleaned_id}_cleaned.csv"
    download_path = os.path.join(app.config['UPLOAD_FOLDER'], download_name)
    try:
        cleaned_df.to_csv(download_path, index=False)
        download_url = f"/static/uploads/{download_name}"
    except Exception:
        download_url = None

    return jsonify({
        'success': True,
        'dataset_id': cleaned_id,
        'filename': f"{filename} (cleaned)",
        'report': report,
        'cleaning_insights': cleaning_insights,
        'before_summary': {
            'rows': before_summary['rows'],
            'columns': before_summary['columns'],
            'total_missing': before_summary['total_missing'],
            'duplicate_rows': before_summary['duplicate_rows'],
            'memory_usage_kb': before_summary['memory_usage_kb'],
        },
        'after_summary': after_summary,
        'after_narrative': after_narrative,
        'ai_insights': ai_insights,
        'charts': charts,
        'download_url': download_url,
    })


@app.route('/report', methods=['POST'])
def report():
    """Generate a polished PDF report for a dataset (typically a cleaned one)
    and return a URL to download it."""
    data = request.get_json(silent=True) or {}
    dataset_id = data.get('dataset_id') or session.get('dataset_id')

    info = DATASETS.get(dataset_id)
    if info is None:
        return jsonify({
            'success': False,
            'error': 'No active dataset. Please upload (and optionally clean) a file first.'
        }), 400

    try:
        pdf_path = build_pdf_report(dataset_id)
    except Exception as e:
        return jsonify({'success': False, 'error': f"Report generation failed: {e}"}), 500

    # Move to /static/uploads so it has a stable URL
    pdf_name = os.path.basename(pdf_path)
    public_url = f"/static/uploads/{pdf_name}"

    return jsonify({
        'success': True,
        'report_url': public_url,
        'filename': pdf_name,
        'is_cleaned': info.get('cleaning_report') is not None,
    })



@app.errorhandler(413)
def too_large(_):
    return jsonify({'success': False, 'error': 'File too large (max 32 MB).'}), 413


@app.errorhandler(500)
@app.errorhandler(Exception)
def handle_unexpected(err):
    """Always return JSON for our POST API endpoints so the front-end never
    receives an HTML error page (which would cause a JSON-parse error in the
    browser). Other routes fall back to the default handler."""
    from werkzeug.exceptions import HTTPException
    # Let normal HTTP errors (404, 405, 413, …) behave as usual
    if isinstance(err, HTTPException) and err.code not in (500,):
        return err
    if request.path in ('/upload', '/clean', '/report', '/chat'):
        app.logger.exception("Unhandled error in %s", request.path)
        return jsonify({
            'success': False,
            'error': f"{type(err).__name__}: {err}"
        }), 500
    # Non-API routes: re-raise so Flask shows its normal page
    if isinstance(err, HTTPException):
        return err
    raise err


# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------
if __name__ == '__main__':
    print("Starting DataGenius on http://127.0.0.1:5000")
    app.run(debug=True, host='127.0.0.1', port=5000)
