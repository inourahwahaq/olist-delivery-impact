import sqlite3
import sys

import numpy as np
import pandas as pd
from sklearn.compose import make_column_transformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

db_path, output_dir = sys.argv[1:3]

# Load delivered orders with the seller and product size of the main item
con = sqlite3.connect(db_path)
df = pd.read_sql_query(
    """
    SELECT d.*, m.seller_id, p.product_weight_g, p.product_length_cm, p.product_height_cm, p.product_width_cm
    FROM delivered_orders d
    JOIN main_item m ON m.order_id = d.order_id
    JOIN products p ON p.product_id = m.product_id
    """,
    con,
    parse_dates=["purchase_time", "delivered_time", "estimated_date"],
)
con.close()
df = df.sort_values("purchase_time").reset_index(drop=True)

# Facts known when the customer buys
df["promised_days"] = (df["estimated_date"] - df["purchase_time"]).dt.days
df["purchase_month"] = df["purchase_time"].dt.month
df["purchase_weekday"] = df["purchase_time"].dt.dayofweek
df["product_volume"] = df["product_length_cm"] * df["product_height_cm"] * df["product_width_cm"]


def past_late_rate(group_column):
    """Late rate of earlier orders in the same group that had already arrived at purchase time."""
    delivered = df[["delivered_time", group_column, "is_late"]].sort_values("delivered_time")
    delivered["past_late"] = delivered.groupby(group_column)["is_late"].cumsum()
    delivered["past_orders"] = delivered.groupby(group_column).cumcount() + 1
    merged = pd.merge_asof(
        df[["purchase_time", group_column]],
        delivered.drop(columns="is_late"),
        left_on="purchase_time",
        right_on="delivered_time",
        by=group_column,
    )
    return (merged["past_late"] / merged["past_orders"]).values


df["seller_past_late_rate"] = past_late_rate("seller_id")
df["state_past_late_rate"] = past_late_rate("customer_state")

# Platform late rate during the 30 days before purchase, using arrived orders only
daily = df.groupby(df["delivered_time"].dt.floor("D"))["is_late"].agg(["sum", "count"]).asfreq("D", fill_value=0)
recent = daily.rolling(30).sum()
recent_rate = (recent["sum"] / recent["count"]).rename("recent_late_rate").reset_index()
purchases = df[["purchase_time"]].copy()
df["recent_late_rate"] = pd.merge_asof(purchases, recent_rate, left_on="purchase_time", right_on="delivered_time")["recent_late_rate"].values

NUMBERS = ["price", "freight", "items", "same_state", "promised_days", "purchase_month", "purchase_weekday", "product_weight_g", "product_volume", "seller_past_late_rate", "state_past_late_rate", "recent_late_rate"]
CATEGORIES = ["customer_state", "seller_state", "category"]

# Train on 2017 and test on 2018 months the model has not seen
train = df[df["purchase_time"] < "2018-01-01"]
test = df[df["purchase_time"] >= "2018-01-01"]
y_train, y_test = train["is_late"], test["is_late"]


def preprocess():
    return make_column_transformer(
        (make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), NUMBERS),
        (OneHotEncoder(handle_unknown="ignore", min_frequency=100, sparse_output=False), CATEGORIES),
    )


models = {
    "Logistic regression": make_pipeline(preprocess(), LogisticRegression(max_iter=2000)),
    "Gradient boosting": make_pipeline(preprocess(), HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=200, random_state=42)),
}


def scores(y, risk, name):
    # The top 10% is the list an operations team could act on
    top = risk >= np.quantile(risk, 0.9)
    return {
        "model": name,
        "pr_auc": round(average_precision_score(y, risk), 3),
        "roc_auc": round(roc_auc_score(y, risk), 3),
        "precision_top_10pct": round(y[top].mean(), 3),
        "recall_top_10pct": round(y[top].sum() / y.sum(), 3),
    }


# Baselines: no skill, and one simple rule
results = [{"model": "No model (late rate)", "pr_auc": round(y_test.mean(), 3), "roc_auc": 0.5, "precision_top_10pct": round(y_test.mean(), 3), "recall_top_10pct": 0.1}]
results.append(scores(y_test, test["recent_late_rate"].fillna(0).values, "Rule: recent late rate"))

risks = {}
for name, model in models.items():
    model.fit(train[NUMBERS + CATEGORIES], y_train)
    risks[name] = model.predict_proba(test[NUMBERS + CATEGORIES])[:, 1]
    results.append(scores(y_test, risks[name], name))

results = pd.DataFrame(results)
results.to_csv(f"{output_dir}/model_results.csv", index=False)
print(results.to_string(index=False))

# Check the best model month by month
best = results.iloc[2:].sort_values("pr_auc", ascending=False)["model"].iloc[0]
by_month = test.assign(risk=risks[best]).groupby("month").apply(
    lambda g: pd.Series({
        "orders": len(g),
        "late_pct": round(g["is_late"].mean() * 100, 1),
        "avg_predicted_pct": round(g["risk"].mean() * 100, 1),
        "pr_auc": round(average_precision_score(g["is_late"], g["risk"]), 3),
    }),
    include_groups=False,
)
by_month.to_csv(f"{output_dir}/model_by_month.csv")
print("best", best)
print(by_month.to_string())
