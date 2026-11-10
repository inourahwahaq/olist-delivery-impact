import sqlite3
import sys

import pandas as pd

raw_dir, db_path = sys.argv[1:3]

tables = {
    "orders": "olist_orders_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_names": "product_category_name_translation.csv",
}

# Load each CSV file into its own table
con = sqlite3.connect(db_path)
for table, file_name in tables.items():
    df = pd.read_csv(f"{raw_dir}/{file_name}")
    df.to_sql(table, con, if_exists="replace", index=False)
    print(table, len(df))

con.close()
