import sqlite3
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

db_path, output_dir = sys.argv[1:3]

# Load delivered orders that have a review, with the seller of the main item
con = sqlite3.connect(db_path)
df = pd.read_sql_query(
    """
    SELECT d.*, m.seller_id
    FROM delivered_orders d
    JOIN main_item m ON m.order_id = d.order_id
    WHERE d.review_score IS NOT NULL
    """,
    con,
)
con.close()

# Prepare the fields used in the models
df["low_score"] = (df["review_score"] <= 2).astype(int)
df["log_price"] = np.log(df["price"])
df["log_freight"] = np.log1p(df["freight"])

# Small categories are grouped so each category has enough orders
counts = df["category"].value_counts()
df["category_group"] = df["category"].where(df["category"].map(counts) >= 300, "other")

# Delay level for the second question: does a longer delay do more harm?
df["delay_level"] = pd.cut(
    df["days_after_estimate"],
    bins=[-1000, 0, 3, 7, 1000],
    labels=["on_time", "late_1_3", "late_4_7", "late_8_plus"],
).astype(str)

CONTROLS = "C(customer_state) + C(category_group) + C(month) + log_price + log_freight + items + same_state"


def fit(formula, data):
    # Orders from one seller are related, so errors are clustered by seller
    return smf.ols(formula, data=data).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(data["seller_id"])[0]})


def row(model_name, result, term, data):
    low, high = result.conf_int().loc[term]
    return {"model": model_name, "term": term, "effect": round(result.params[term], 3), "ci_low": round(low, 3), "ci_high": round(high, 3), "orders": len(data)}


rows = []

# 1. Simple comparison, then the same comparison with controls
for outcome in ["review_score", "low_score"]:
    simple = fit(f"{outcome} ~ is_late", df)
    rows.append(row(f"{outcome}: simple", simple, "is_late", df))
    controlled = fit(f"{outcome} ~ is_late + {CONTROLS}", df)
    rows.append(row(f"{outcome}: with controls", controlled, "is_late", df))

# 2. Compare orders from the same seller: subtract each seller's own average
same_seller = df[df.groupby("seller_id")["order_id"].transform("size") >= 2].copy()
design = pd.get_dummies(same_seller[["customer_state", "category_group", "month"]], drop_first=True, dtype=float)
design[["log_price", "log_freight", "items", "same_state", "is_late"]] = same_seller[["log_price", "log_freight", "items", "same_state", "is_late"]]
design["review_score"] = same_seller["review_score"]
demeaned = design - design.groupby(same_seller["seller_id"]).transform("mean")
terms = [c for c in demeaned.columns if c != "review_score" and demeaned[c].std() > 0]
seller_model = sm.OLS(demeaned["review_score"], demeaned[terms]).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(same_seller["seller_id"])[0]})
rows.append(row("review_score: same seller", seller_model, "is_late", same_seller))

# 3. Reviews written after the order arrived
after_delivery = df[df["review_before_delivery"] == 0]
result = fit(f"review_score ~ is_late + {CONTROLS}", after_delivery)
rows.append(row("review_score: reviews after delivery only", result, "is_late", after_delivery))

# 4. Effect by length of delay
levels = fit(f"review_score ~ C(delay_level, Treatment('on_time')) + {CONTROLS}", df)
for level in ["late_1_3", "late_4_7", "late_8_plus"]:
    rows.append(row(f"review_score: {level}", levels, f"C(delay_level, Treatment('on_time'))[T.{level}]", df))

results = pd.DataFrame(rows)
results.to_csv(f"{output_dir}/delivery_effect.csv", index=False)
print(results.drop(columns="term").to_string(index=False))
