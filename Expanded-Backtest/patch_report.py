p = "report.py"
s = open(p, encoding="utf-8").read()
old = '            "avg_ret": 0.0, "retAll": 0.0, "ret2026": 0.0,\n            "winRatio": 0.0, "winHoldH": 0.0,'
new = '            "avg_ret": 0.0, "retAll": 0.0, "ret2026": 0.0,\n            "avg_ret2026": 0.0,\n            "winRatio": 0.0, "winHoldH": 0.0,'
assert old in s, "not found"
s = s.replace(old, new, 1)
open(p, "w", encoding="utf-8").write(s)
print("done empty-case")

old2 = '        "avg_ret": float(g["net_return_pct"].mean()),\n        "retAll": float(g["net_pnl_tl"].sum() / capital * 100.0),\n        "ret2026": float(g.loc[year2026, "net_pnl_tl"].sum() / capital * 100.0),'
new2 = ('        "avg_ret": float(g["net_return_pct"].mean()),\n'
        '        "retAll": float(g["net_pnl_tl"].sum() / capital * 100.0),\n'
        '        "ret2026": float(g.loc[year2026, "net_pnl_tl"].sum() / capital * 100.0),\n'
        '        "avg_ret2026": (float(g.loc[year2026, "net_return_pct"].mean())\n'
        '                        if year2026.any() else 0.0),')
assert old2 in s, "not found2"
s = s.replace(old2, new2, 1)
open(p, "w", encoding="utf-8").write(s)
print("done metric")