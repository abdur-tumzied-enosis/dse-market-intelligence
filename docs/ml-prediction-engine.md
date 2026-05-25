# ML Prediction Engine

Nightly pipeline that scores all active DSE tickers across three dimensions: price direction (LSTM), fundamental quality (XGBoost), and intrinsic value (DCF). Results land in `ml_predictions` and `stock_scores` tables.

---

## Overview

Three independent models run every night via APScheduler job `job_nightly_ml` (22:00 BD time):

| Model | Type | Question answered | Output table |
|---|---|---|---|
| LSTM | PyTorch neural net | Will price go up in 5/10/20 days? | `ml_predictions` |
| FundamentalScorer | XGBoost classifier | Will this stock outperform peers in 6 months? | `stock_scores.fundamental_score` |
| DCF | Deterministic math | Is this stock under/overvalued right now? | `stock_scores.valuation_score` |

Models are retrained quarterly by `job_quarterly` (Jan/Apr/Jul/Oct 1st, 03:00 BD).

---

## Model 1: LSTM Price Direction

**File:** `ml/models/lstm_predictor.py`, `ml/inference/predict_prices.py`

### Architecture

```
Input: (batch, 60 days, 10 features)
    → LSTM layer 1  (hidden=64, dropout=0.2)
    → LSTM layer 2  (hidden=64)
    → Linear head   (64 → 3 logits)
    → Sigmoid       → 3 probabilities [5d_up, 10d_up, 20d_up]
```

### Input features (10 technical indicators per day)

| Feature | What it measures |
|---|---|
| `rsi_14` | Overbought / oversold momentum (0–100) |
| `macd_diff` | MACD histogram — trend acceleration |
| `bb_pband` | Where price sits inside Bollinger Bands (0=bottom, 1=top) |
| `atr_norm` | Average True Range ÷ price — volatility |
| `adx_14` | Trend strength (0=no trend, 100=strong trend) |
| `return_1d` | Yesterday's price change % |
| `return_5d` | Last-5-day price change % |
| `return_20d` | Last-20-day price change % |
| `volume_zscore` | Volume spike vs 20-day average |
| `obv` | On-Balance Volume day-over-day change |

### Training (`ml/train/train_lstm.py`)

```
1. Pull all stock_prices rows (quality_flag='ok') from DB
2. For each ticker, slide 60-day window across full history
3. Label each window: did close[+5d] > close[today]? (same for 10d, 20d)
4. Stack into X: (N, 60, 10), y: (N, 3) binary
5. Chronological 80/20 split — NO shuffle (prevents data leakage)
6. Train up to 50 epochs, batch=64, Adam lr=0.001
7. Early stopping: patience=5 (stop if val_loss doesn't improve)
8. Save best model to models/v1/lstm_v0.pt
```

One shared model trained across all tickers — not per-ticker.

### Inference per ticker

```
1. Fetch last 65 days OHLCV from DB (extra days for indicator warmup)
2. Compute 10 indicators → take last 60 rows → tensor (1, 60, 10)
3. model.predict_proba(x) → [p5d, p10d, p20d]
4. p ≥ 0.5 = "up", else "down"
5. confidence = distance from 0.5
6. target_price = close × (1 + (p − 0.5) × 0.1)   # rough estimate
7. Write 3 rows to ml_predictions (one per horizon)
```

---

## Model 2: FundamentalScorer (XGBoost)

**File:** `ml/models/fundamental_scorer.py`, `ml/inference/score_fundamentals.py`

### What it does

XGBoost classifier. Outputs `P(stock outperforms peers over next 6 months)` — a float 0–1.

### Input features (6 fundamental metrics)

| Feature | Source |
|---|---|
| `eps_growth_1yr` | YoY earnings growth |
| `eps_growth_3yr` | 3-year CAGR of earnings |
| `nav_growth` | Book value growth |
| `pe_vs_sector` | Company P/E ÷ sector median P/E |
| `div_yield` | Dividend yield |
| `eps_consistency` | How consistently the company earns |

Data pulled from `fundamentals` and `sector_pe` tables.

### Model config

```python
XGBClassifier(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
)
```

Saved to `models/v1/fundamental_scorer.pkl`.

---

## Model 3: DCF Valuation

**File:** `ml/valuation/dcf.py`, `ml/valuation/monte_carlo.py`

### What it does

Deterministic calculator — no training needed. Estimates intrinsic value per share using EPS as free cash flow proxy.

### Formula

```
For each year 1..5:
    projected_eps = eps_ttm × (1 + growth_rate)^year
    pv_earnings  += projected_eps / (1 + cost_of_equity)^year

Terminal value (Gordon Growth Model):
    TV = eps_year5 × (1 + terminal_growth) / (cost_of_equity − terminal_growth)
    pv_terminal = TV / (1 + cost_of_equity)^5

intrinsic_value = pv_earnings + pv_terminal
margin_of_safety_pct = (intrinsic_value − current_price) / current_price × 100
```

Defaults used in `job_nightly_ml`: `cost_of_equity=0.12`, `terminal_growth=0.03`.

### Monte Carlo variant (`ml/valuation/monte_carlo.py`)

Runs DCF 10,000 times, sampling `growth_rate` from a normal distribution each time. Outputs P10/P50/P90 value range and `downside_prob` (fraction of scenarios where stock is overvalued).

---

## Composite Health Score

**File:** `ml/scoring/health_score.py`

Combines fundamental score and valuation score into a single 0–1 number stored in `stock_scores.health_score`.

```
valuation_score = clamp((margin_of_safety_pct + 50) / 100, 0, 1)
health_score    = compute_health_score(fundamental_score, valuation_score)
```

---

## Scheduler Jobs

### `job_nightly_ml` — daily inference (22:00 BD)

```
Step 1: Load fundamental_scorer.pkl → score all tickers → write stock_scores
Step 2: Load lstm_v0.pt → run LSTM on all tickers → write ml_predictions
Step 3: Run DCF for each ticker → update stock_scores.valuation_score + health_score
```

### `job_quarterly` — retrain (Jan/Apr/Jul/Oct 1st, 03:00 BD)

```
Step 1: populate_outcomes() — evaluate past predictions whose horizon passed
Step 2: check_accuracy_thresholds() — fire alert if accuracy < 48% (warn) or < 45% (critical)
Step 3: Retrain XGBoost on latest fundamentals data (min 30 samples)
Step 4: Retrain LSTM on latest price data (min 200 sequences, max 30 epochs)
Step 5: Save versioned copy → models/YYYYMMDD/ and overwrite models/v1/
Step 6: Fire INFO alert with retrain summary
```

---

## Accuracy Monitoring

**File:** `ml/monitoring/accuracy_report.py`

Every time a prediction's horizon passes (e.g. a 5-day prediction made 6 days ago), `populate_outcomes()` looks up the actual closing price and records whether the direction call was correct.

```sql
-- Written to prediction_outcomes table
actual_direction = "up" if price_at_horizon > price_at_prediction else "down"
correct          = (actual_direction == predicted_direction)
```

Accuracy thresholds (checked quarterly before retrain):

| Level | Threshold |
|---|---|
| WARNING | accuracy < 48% |
| CRITICAL | accuracy < 45% |

50% = random coin flip. Below 45% means the model is actively misleading.

---

## File Map

```
ml/
├── features/
│   ├── price_features.py          # compute_price_features() → 10 technical indicators
│   ├── fundamental_features.py    # compute_fundamental_features()
│   ├── macro_features.py          # compute_macro_features()
│   └── feature_store.py           # build_price_feature_matrix(), build_fundamental_feature_vector()
├── models/
│   ├── lstm_predictor.py          # LSTMPredictor (PyTorch nn.Module)
│   └── fundamental_scorer.py      # FundamentalScorer (XGBoost wrapper)
├── train/
│   ├── train_lstm.py              # offline LSTM training script
│   └── train_fundamental.py       # offline XGBoost training script
├── inference/
│   ├── predict_prices.py          # LSTM inference → ml_predictions
│   └── score_fundamentals.py      # XGBoost inference → stock_scores
├── valuation/
│   ├── dcf.py                     # DCFCalculator
│   └── monte_carlo.py             # MonteCarloSimulator
├── scoring/
│   └── health_score.py            # composite health score
└── monitoring/
    └── accuracy_report.py         # populate_outcomes(), generate_report(), check_accuracy_thresholds()
```

---

## Running Manually

```bash
# Initial training (run once before first nightly job)
python -m ml.train.train_lstm
python -m ml.train.train_fundamental

# Run inference on demand
python -m ml.inference.predict_prices
python -m ml.inference.score_fundamentals

# Check prediction accuracy
python -m ml.monitoring.accuracy_report
```

---

## DB Tables

| Table | Written by | Contents |
|---|---|---|
| `ml_predictions` | `predict_prices.py` | ticker, horizon_days, predicted_direction, confidence, target_price, model_version |
| `stock_scores` | `score_fundamentals.py` + `job_nightly_ml` | fundamental_score, valuation_score, health_score |
| `prediction_outcomes` | `accuracy_report.py` | actual_direction, correct, return_pct per resolved prediction |
