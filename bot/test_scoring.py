from scoring import summarize

def show(w, s, p):
    r = summarize(w, s, p)
    print(f"W={w} S={s} P={p} -> strikes_erased={r['strikes_erased']} warns_erased={r['warns_erased']} "
          f"current={r['current_strikes']} final={r['final']}")
    return r

assert summarize(0, 1, 5)["strikes_erased"] == 1 and summarize(0, 1, 5)["final"] == 5
assert summarize(0, 1, 4)["strikes_erased"] == 0 and summarize(0, 1, 4)["final"] == 1
assert summarize(0, 3, 0)["current_strikes"] == 3
assert summarize(0, 5, 0)["current_strikes"] == 5
assert summarize(0, 7, 5)["current_strikes"] == 6
r = summarize(2, 1, 8)
assert r["strikes_erased"] == 1 and r["warns_erased"] == 1 and r["final"] == 7
assert summarize(3, 0, 9)["warns_erased"] == 3
assert summarize(1, 2, 10)["strikes_erased"] == 2 and summarize(1, 2, 10)["final"] == 9
assert summarize(0, 4, 20)["strikes_erased"] == 4
show(0, 1, 5)
show(1, 2, 10)
show(0, 3, 15)
show(3, 7, 25)
print("SCORING TESTS OK")
