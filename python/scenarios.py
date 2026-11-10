import sqlite3
import sys

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

db_path, output_dir = sys.argv[1:3]

# Load delivered orders
con = sqlite3.connect(db_path)
df = pd.read_sql_query("SELECT * FROM delivered_orders", con, parse_dates=["purchase_time"])
con.close()

# ---- Does a late first order reduce repeat purchases?
# Only first orders with 180 days of later data can be followed up
df = df.sort_values("purchase_time")
df["order_number"] = df.groupby("customer_unique_id").cumcount() + 1
first = df[df["order_number"] == 1].copy()
second = df[df["order_number"] == 2][["customer_unique_id", "purchase_time"]].rename(columns={"purchase_time": "second_time"})
first = first[first["purchase_time"] < "2018-03-01"].merge(second, on="customer_unique_id", how="left")
first["days_to_second"] = (first["second_time"] - first["purchase_time"]).dt.days
first["repeat_180"] = (first["days_to_second"] <= 180).astype(int)
first["log_price"] = np.log(first["price"])

counts = first["category"].value_counts()
first["category_group"] = first["category"].where(first["category"].map(counts) >= 300, "other")

model = smf.ols("repeat_180 ~ is_late + C(customer_state) + C(category_group) + C(month) + log_price", data=first).fit(cov_type="HC1")
repeat_effect = model.params["is_late"]
repeat_low, repeat_high = model.conf_int().loc["is_late"]

repeat_table = first.groupby("is_late")["repeat_180"].agg(["size", "mean"]).reset_index()
repeat_table["repeat_pct"] = (repeat_table["mean"] * 100).round(2)
repeat_table = repeat_table.drop(columns="mean")
repeat_table.to_csv(f"{output_dir}/repeat_purchase.csv", index=False)
print(repeat_table.to_string(index=False))
print("repeat effect pts", round(repeat_effect * 100, 2), round(repeat_low * 100, 2), round(repeat_high * 100, 2))

# ---- Scenarios on the last 12 full months
year = df[(df["month"] >= "2017-09") & (df["month"] <= "2018-08")]
cells = year.groupby(["customer_state", "month"]).agg(orders=("order_id", "size"), late=("is_late", "sum")).reset_index()
effects = pd.read_csv(f"{output_dir}/delivery_effect.csv").set_index("model")
low_score_effect = effects.loc["low_score: with controls", "effect"]
review_effect = effects.loc["review_score: with controls", "effect"]

# Targets come from the data: other states, and the other months
others = year[year["customer_state"] != "RJ"]
target_state = others["is_late"].mean()
rio_rate = year.loc[year["customer_state"] == "RJ", "is_late"].mean()
spike_months = ["2018-02", "2018-03"]
target_month = year.loc[~year["month"].isin(spike_months), "is_late"].mean()
month_rate = year.groupby("month")["is_late"].mean()


def scenario(name, fix_rio, fix_spike):
    new_late = cells["late"].astype(float).copy()
    if fix_rio:
        rio = cells["customer_state"] == "RJ"
        new_late[rio] = new_late[rio] * (target_state / rio_rate)
    if fix_spike:
        for month in spike_months:
            selected = cells["month"] == month
            new_late[selected] = new_late[selected] * (target_month / month_rate[month])
    avoided = cells["late"].sum() - new_late.sum()
    average_price = year["price"].mean()
    return {
        "scenario": name,
        "late_orders_avoided": round(avoided),
        "new_late_pct": round(new_late.sum() / cells["orders"].sum() * 100, 1),
        "low_reviews_avoided": round(avoided * low_score_effect),
        "avg_score_gain": round(-review_effect * avoided / cells["orders"].sum(), 3),
        "revenue_low": 0 if repeat_high >= 0 else round(avoided * -repeat_high * average_price),
        "revenue_mid": round(avoided * max(-repeat_effect, 0) * average_price),
        "revenue_high": round(avoided * max(-repeat_low, 0) * average_price),
    }


results = pd.DataFrame([
    scenario("Bring Rio to the other states' late rate", True, False),
    scenario("Bring Feb and Mar 2018 to the other months' rate", False, True),
    scenario("Both", True, True),
])
results.to_csv(f"{output_dir}/scenarios.csv", index=False)
print(f"12 months: {len(year)} orders, late {year['is_late'].mean() * 100:.1f}%, Rio {rio_rate * 100:.1f}%, others {target_state * 100:.1f}%, other months {target_month * 100:.1f}%, average price {year['price'].mean():.2f}, revenue {year['price'].sum():.0f}")
print(results.to_string(index=False))
