# Probability Model Training

This folder is the bridge between the MVP's heuristic probability score and a
real ML-driven breakout model.

## Expected dataset

Prepare a CSV with rows representing historical setups. Recommended columns:

- `pattern`
- `rsi14`
- `volume_ratio`
- `price_vs_ema20_pct`
- `atr14`
- `risk_reward_ratio`
- `trend_strength`
- `target`

Where `target` is:

- `1` when the setup reached its profit target before the stop
- `0` when it failed

## Usage

```bash
python backend/training/train_probability_model.py path/to/training_data.csv
```

The script writes `backend/training/model.joblib`, which you can later load in
the runtime scanner to replace the current rule-based probability estimate.

