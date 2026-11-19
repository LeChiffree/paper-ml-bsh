p = "update_dashboard.py"
s = open(p, encoding="utf-8").read()
s = s.replace('row["retAll"] = round(float(scenario["s_retAll"]), 6)',
              'row["retAll"] = round(float(scenario["s_avg_ret"]), 6)')
s = s.replace('row["ret2026"] = round(float(scenario["s_ret2026"]), 6)',
              'row["ret2026"] = round(float(scenario["s_avg_ret2026"]), 6)')
open(p, "w", encoding="utf-8").write(s)
print("done")