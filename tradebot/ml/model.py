"""Probability predictor: XGBoost once enough resolved markets exist, else a heuristic."""
from __future__ import annotations

import numpy as np

from tradebot.ml.features import PRICE_IDX, SENTIMENT_IDX

try:
    import xgboost as xgb
except Exception:  # pragma: no cover
    xgb = None


class Predictor:
    def __init__(self, log):
        self.log = log
        self.model = None

    def train(self, X: list[list[float]], y: list[int]) -> bool:
        if xgb is None or len(y) < 20 or len(set(y)) < 2:
            return False
        try:
            dtrain = xgb.DMatrix(np.array(X, dtype=float), label=np.array(y, dtype=float))
            self.model = xgb.train(
                {
                    "objective": "binary:logistic", "max_depth": 3, "eta": 0.2,
                    "eval_metric": "logloss", "verbosity": 0,
                },
                dtrain,
                num_boost_round=40,
            )
            self.log.info("Predictor: trained XGBoost on %d resolved markets", len(y))
            return True
        except Exception as e:  # pragma: no cover
            self.log.warning("Predictor training failed (%s)", e)
            return False

    def predict_yes(self, features: list[float]) -> float:
        if self.model is not None and xgb is not None:
            try:
                p = float(self.model.predict(xgb.DMatrix(np.array([features], dtype=float)))[0])
                return min(1.0, max(0.0, p))
            except Exception:  # pragma: no cover
                pass
        return self._heuristic(features)

    @staticmethod
    def _heuristic(features: list[float]) -> float:
        """Market price nudged by sentiment (treated as an information signal)."""
        price = features[PRICE_IDX] if features else 0.5
        sent = features[SENTIMENT_IDX] if len(features) > SENTIMENT_IDX else 0.0
        return min(1.0, max(0.0, price + 0.25 * sent))
