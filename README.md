# DataGenius — Intelligent Data Analysis Web App

A polished Flask web application that lets you upload a CSV or Excel file and instantly get:

- 📊 **Automated dataset summary** — rows, columns, data types, missing values, duplicates
- 🤖 **AI Insights Generator** — auto-detects skew, outliers, correlations, imbalance, ID/free-text columns & data-quality issues
- 🧹 **One-click data cleaning** — duplicates, missing values, whitespace, empty/constant columns, outliers
- 📈 **Visualizations** — correlation heatmaps, distributions, bar plots, box plots, scatter regressions (matplotlib + seaborn)
- 💡 **Plain-English insights** before AND after cleaning
- 📄 **Multi-page PDF report** — polished, brand-styled analysis document with executive summary, cleaning log, statistics, AI insights, and embedded charts
- 💬 **Built-in chatbot assistant** (typo & abbreviation tolerant) that answers questions about your dataset
- 🎨 **Modern, professional UI** with a refined editorial aesthetic

> Everything runs locally. No API keys, no external LLMs.

---

## ✨ Features

| Feature | Description |
|---|---|
| File upload | Drag-and-drop or browse. CSV, XLSX, XLS, TSV (max 32 MB). |
| Auto-summary | Shape, dtypes, missing values, duplicates, memory usage, numeric & categorical stats. |
| Smart insights | Human-readable narrative describing the dataset. |
| **AI Insights Generator** | **Statistical engine that surfaces categorized findings — data quality, distributions/outliers, correlations, and structure — with severity flags. Refreshes after cleaning.** |
| Data cleaning | Toggle options to remove duplicates, fill missing values, trim whitespace, drop empty/constant columns, convert date strings, and cap outliers. |
| Before/after comparison | Side-by-side metrics showing exactly what cleaning changed. |
| Download cleaned CSV | Get the cleaned dataset back as a CSV file. |
| **PDF report** | **Generate a multi-page PDF with executive summary, cleaning log, before/after table, statistics, AI insights, and all visualizations embedded.** |
| Charts | 6-10 visualizations chosen based on your data's shape; refresh after cleaning. |
| Chatbot | Ask things like *"key insights"*, *"how many rows?"*, *"mean of price"*, *"show correlations"*, *"clean the data"*, *"generate a report"* — typos and shortened column names are understood. |
| Data preview | First 10 rows in a clean table. |

---

## 🚀 Quick start

### 1. Requirements

- Python 3.9 or newer
- pip

### 2. Setup

```bash
# Extract the zip, then enter the folder
cd data_insights_app

# (Recommended) Create a virtual environment
python -m venv venv

# Activate it
# macOS / Linux:
source venv/bin/activate
# Windows (PowerShell):
venv\Scripts\Activate.ps1
# Windows (cmd):
venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt
```

### 3. Run

```bash
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

---

## 📁 Project structure

```
data_insights_app/
├── app.py                  # Flask backend — upload, analysis, charts, chatbot
├── requirements.txt        # Python dependencies
├── README.md               # This file
├── templates/
│   └── index.html          # Single-page UI
└── static/
    ├── css/
    │   └── style.css       # Styling
    ├── js/
    │   └── main.js         # Client-side logic
    ├── uploads/            # Uploaded files (created at runtime)
    └── charts/             # Generated chart images (created at runtime)
```

---

## 💬 Chatbot — example questions

After uploading a file, try asking the assistant:

- *"How many rows are there?"*
- *"What columns does the dataset have?"*
- *"Are there missing values?"*
- *"What's the mean of [column_name]?"*
- *"Tell me about [column_name]"*
- *"Show correlations"*
- *"Summarize the dataset"*
- *"Help"*

The chatbot is rule-based and runs entirely offline — it answers from the uploaded dataset's metadata and pandas computations.

---

## 🧪 Try it with a sample file

If you don't have a dataset handy, the **Iris**, **Titanic**, or **Wine Quality** datasets from Kaggle / UCI work beautifully.

You can also generate a quick test CSV in Python:

```python
import pandas as pd, numpy as np
np.random.seed(0)
df = pd.DataFrame({
    "age": np.random.randint(18, 80, 200),
    "income": np.random.normal(60000, 15000, 200).round(2),
    "score": np.random.rand(200).round(3),
    "country": np.random.choice(["USA", "UK", "JP", "DE", "BR"], 200),
    "is_member": np.random.choice([True, False], 200),
})
df.to_csv("sample.csv", index=False)
```

---

## 🛠 Tech stack

- **Backend**: Flask, pandas, NumPy
- **Visualization**: matplotlib, seaborn (headless `Agg` backend)
- **Frontend**: Vanilla HTML/CSS/JS — no framework needed
- **Fonts**: Fraunces (display) + Inter (body) + JetBrains Mono (code)

---

## 🔧 Configuration

Edit constants near the top of `app.py`:

| Variable | Default | Description |
|---|---|---|
| `MAX_CONTENT_LENGTH` | 32 MB | Maximum upload size |
| `ALLOWED_EXTENSIONS` | csv, xlsx, xls, tsv | Allowed file types |
| `SECRET_KEY` | placeholder | **Change this for production** |

---

## ⚠️ Notes & limitations

- The Flask development server (`app.run()`) is for local use. For deployment, run behind **gunicorn** or **waitress**.
- Uploaded files and generated charts are stored on disk in `static/uploads/` and `static/charts/`. You can periodically clean these directories.
- Dataset state is kept in memory keyed by a session id — restarting the server clears it.

---

## 📜 License

MIT — use it freely.
