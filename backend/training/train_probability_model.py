from __future__ import annotations

import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


def main(dataset_path: str) -> None:
    source = Path(dataset_path)
    if not source.exists():
        raise FileNotFoundError(f"Dataset not found: {source}")

    data = pd.read_csv(source)
    if "target" not in data.columns:
        raise ValueError("Dataset must include a 'target' column.")

    features = data.drop(columns=["target"])
    target = data["target"]

    categorical_columns = [
        column for column in features.columns if features[column].dtype == "object"
    ]
    numeric_columns = [
        column for column in features.columns if column not in categorical_columns
    ]

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_columns,
            ),
            (
                "numeric",
                Pipeline(
                    steps=[("imputer", SimpleImputer(strategy="median"))]
                ),
                numeric_columns,
            ),
        ]
    )

    model = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=250,
                    max_depth=8,
                    min_samples_leaf=4,
                    random_state=42,
                ),
            ),
        ]
    )

    X_train, X_test, y_train, y_test = train_test_split(
        features,
        target,
        test_size=0.2,
        random_state=42,
        stratify=target,
    )

    model.fit(X_train, y_train)
    predictions = model.predict(X_test)
    probabilities = model.predict_proba(X_test)[:, 1]

    print(classification_report(y_test, predictions))
    print(f"ROC AUC: {roc_auc_score(y_test, probabilities):.3f}")

    output_path = Path(__file__).with_name("model.joblib")
    joblib.dump(model, output_path)
    print(f"Saved model to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python backend/training/train_probability_model.py "
            "path/to/training_data.csv"
        )

    main(sys.argv[1])

