import re
import sqlite3
import sys

import pandas as pd

db_path, sql_path, output_dir = sys.argv[1:4]

con = sqlite3.connect(db_path)
parts = re.split(r"^-- @export (\w+)\s*$", open(sql_path, encoding="utf-8").read(), flags=re.M)

# The first part builds the tables. Each export after it is one SELECT saved to CSV.
con.executescript(parts[0])
for name, query in zip(parts[1::2], parts[2::2]):
    result = pd.read_sql_query(query, con)
    result.to_csv(f"{output_dir}/{name}.csv", index=False)
    print(name, len(result))

con.close()
