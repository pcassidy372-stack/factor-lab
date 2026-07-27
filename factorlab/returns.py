"""The one true forward-return function (review #3, 7.1). Used by
factor_eval and factor_fm; delisting fallback bounded to the window."""


def make_fwd(TR, LAST, TRLAST, DL, nxt):
    def fwd(sec, a):
        b = nxt.get(a)
        t0 = TR.get(sec, {}).get(a)
        if not b or not t0:
            return None
        t1 = TR.get(sec, {}).get(b)
        if t1:
            return t1 / t0 - 1.0
        ld = LAST.get(sec)
        if not ld or ld <= a or ld > b:
            return None
        r = TRLAST[sec] / t0 - 1.0
        d = DL.get(sec)
        if d and a < d[0] <= b and d[1] is not None and d[2] == "rung1-deal-manual":
            r = (1 + r) * (1 + float(d[1])) - 1
        return r
    return fwd
