import sqlite3

source = sqlite3.connect("data/paper_trade.db")
target = sqlite3.connect("/tmp/paper_trade_mac_cutover.db")
source.backup(target)
target.close()
source.close()
