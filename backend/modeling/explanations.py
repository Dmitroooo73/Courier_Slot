import numpy as np
import pandas as pd
from catboost import Pool
from scipy.sparse import issparse

from .features import CATEGORIES


def ridge_effects(estimator, train: pd.DataFrame, test: pd.DataFrame):
    preprocessing = estimator.named_steps["columntransformer"]
    ridge = estimator.named_steps["ridge"]
    encoder = preprocessing.named_transformers_["categorical"]
    imputer = preprocessing.named_transformers_["numeric"].named_steps["simpleimputer"]
    numeric = list(train.columns[len(CATEGORIES):])
    owners = [
        column
        for column, categories in zip(CATEGORIES, encoder.categories_, strict=True)
        for _ in categories
    ]
    owners.extend(numeric)
    owners.extend(numeric[index] for index in imputer.indicator_.features_)
    encoded_owners = np.array(owners)

    # Center the encoded features so the base equals the mean training prediction.
    center = np.asarray(preprocessing.transform(train).mean(axis=0)).ravel()
    encoded = preprocessing.transform(test)
    if issparse(encoded):
        encoded = encoded.toarray()
    encoded_effects = (encoded - center) * ridge.coef_
    grouped = np.column_stack([
        encoded_effects[:, encoded_owners == column].sum(axis=1)
        for column in train.columns
    ])
    base = ridge.intercept_ + center @ ridge.coef_
    return np.full(len(test), base), grouped


def feature_effects(model: str, estimator, train: pd.DataFrame, test: pd.DataFrame):
    if model == "ridge":
        return ridge_effects(estimator, train, test)
    values = estimator.get_feature_importance(
        data=Pool(test, cat_features=list(CATEGORIES)), type="ShapValues", thread_count=2
    )
    return values[:, -1], values[:, :-1]


def feature_explanations(
    model: str, estimator, train: pd.DataFrame, test: pd.DataFrame, raw_predictions
) -> list[dict]:
    method = "ridge" if model == "ridge" else "catboost_shap"
    bases, effects = feature_effects(model, estimator, train, test)
    explanations = []
    for row, raw, base, impacts in zip(
        test.to_dict("records"), raw_predictions, bases, effects, strict=True
    ):
        contributions = []
        for column, impact in zip(test.columns, impacts, strict=True):
            value = row[column]
            contributions.append({
                "feature": column,
                "value": None if pd.isna(value) else value,
                "impact_orders": float(impact),
            })
        explanations.append({
            "method": method,
            "base_orders": float(base),
            "raw_orders": float(raw),
            "contributions": contributions,
        })
    return explanations
