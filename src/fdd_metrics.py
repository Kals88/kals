"""분기 재무제표에서 FDD 핵심 지표를 산출한다.

여기서 계산하는 지표는 실사 논점을 '지목'하기 위한 것이지, 그 자체가 결론이 아니다.
예컨대 Quality of Earnings 는 조정항목을 사람이 판단해 넣어야 완성된다.
"""

from __future__ import annotations

import pandas as pd

# IFRS 표준 account_id -> FDD 표준 라인 매핑.
# 회사마다 표시계정이 달라 account_id 우선, 실패 시 계정명 키워드로 보완한다.
IFRS_MAP = {
    "ifrs-full_Revenue": "매출액",
    "ifrs-full_RevenueFromContractsWithCustomers": "매출액",
    "ifrs-full_CostOfSales": "매출원가",
    "ifrs-full_GrossProfit": "매출총이익",
    "dart_OperatingIncomeLoss": "영업이익",
    "ifrs-full_ProfitLossFromOperatingActivities": "영업이익",
    "ifrs-full_ProfitLoss": "당기순이익",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "지배주주순이익",
    "ifrs-full_DepreciationAndAmortisationExpense": "감가상각비및무형자산상각비",
    "ifrs-full_Assets": "자산총계",
    "ifrs-full_CurrentAssets": "유동자산",
    "ifrs-full_NoncurrentAssets": "비유동자산",
    "ifrs-full_Liabilities": "부채총계",
    "ifrs-full_CurrentLiabilities": "유동부채",
    "ifrs-full_NoncurrentLiabilities": "비유동부채",
    "ifrs-full_Equity": "자본총계",
    "ifrs-full_EquityAttributableToOwnersOfParent": "지배주주지분",
    "ifrs-full_CashAndCashEquivalents": "현금및현금성자산",
    "ifrs-full_Inventories": "재고자산",
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "영업활동현금흐름",
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": "투자활동현금흐름",
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": "재무활동현금흐름",
}

# account_id 로 못 잡을 때 쓰는 계정명 키워드 (부분일치, 앞에서부터 우선)
NAME_MAP = [
    ("매출액", ["매출액", "수익(매출액)", "영업수익"]),
    ("매출원가", ["매출원가", "영업비용"]),
    ("매출총이익", ["매출총이익"]),
    ("판매비와관리비", ["판매비와관리비", "판매비와일반관리비"]),
    ("영업이익", ["영업이익", "영업손실"]),
    ("당기순이익", ["당기순이익", "당기순손실", "분기순이익"]),
    ("자산총계", ["자산총계"]),
    ("유동자산", ["유동자산"]),
    ("부채총계", ["부채총계"]),
    ("유동부채", ["유동부채"]),
    ("자본총계", ["자본총계"]),
    ("현금및현금성자산", ["현금및현금성자산"]),
    ("매출채권", ["매출채권"]),
    ("재고자산", ["재고자산"]),
    ("매입채무", ["매입채무"]),
    ("단기차입금", ["단기차입금"]),
    ("장기차입금", ["장기차입금"]),
    ("사채", ["사채"]),
    ("유동성장기부채", ["유동성장기", "유동성사채"]),
    ("영업활동현금흐름", ["영업활동현금흐름", "영업활동으로인한현금흐름"]),
    ("투자활동현금흐름", ["투자활동현금흐름", "투자활동으로인한현금흐름"]),
    ("재무활동현금흐름", ["재무활동현금흐름", "재무활동으로인한현금흐름"]),
    ("감가상각비", ["감가상각비"]),
    ("무형자산상각비", ["무형자산상각비", "무형자산의상각"]),
]


def _normalize(name: str) -> str:
    return "".join(str(name).split())


def extract_lines(quarterly: pd.DataFrame) -> pd.DataFrame:
    """분기 재무제표에서 FDD 표준 라인만 뽑아 라벨링한다."""
    quarter_cols = [c for c in quarterly.columns if _is_quarter(c)]
    labeled: dict[str, pd.Series] = {}

    for (sj_div, account_key), row in quarterly.iterrows():
        label = IFRS_MAP.get(account_key)
        if label is None:
            normalized = _normalize(row.get("account_nm", ""))
            for candidate, keywords in NAME_MAP:
                if any(_normalize(k) in normalized for k in keywords):
                    label = candidate
                    break
        if label is None:
            continue
        # 먼저 매칭된 계정을 우선(재무제표 표시순서상 상위 항목).
        if label not in labeled:
            labeled[label] = row[quarter_cols].astype("float64")

    return pd.DataFrame(labeled).T if labeled else pd.DataFrame()


def _is_quarter(col: object) -> bool:
    text = str(col)
    return len(text) == 6 and text[:4].isdigit() and text[4] == "Q" and text[5].isdigit()


def _line(lines: pd.DataFrame, name: str) -> pd.Series:
    if name in lines.index:
        return lines.loc[name].astype("float64")
    return pd.Series(float("nan"), index=lines.columns, dtype="float64")


def _sum_lines(lines: pd.DataFrame, names: list[str]) -> pd.Series:
    total = pd.Series(0.0, index=lines.columns, dtype="float64")
    found = False
    for name in names:
        if name in lines.index:
            total = total.add(lines.loc[name].astype("float64").fillna(0.0), fill_value=0.0)
            found = True
    return total if found else pd.Series(float("nan"), index=lines.columns, dtype="float64")


def build_metrics(lines: pd.DataFrame, unit: float = 1e8) -> pd.DataFrame:
    """FDD 요약 지표표. unit 기본값 1e8 = 억원 단위 표기."""
    if lines.empty:
        return pd.DataFrame()

    revenue = _line(lines, "매출액")
    cogs = _line(lines, "매출원가")
    gross = _line(lines, "매출총이익")
    if gross.isna().all():
        gross = revenue - cogs
    operating = _line(lines, "영업이익")
    net = _line(lines, "당기순이익")
    da = _sum_lines(lines, ["감가상각비", "무형자산상각비", "감가상각비및무형자산상각비"])
    ebitda = operating + da.fillna(0.0)

    ocf = _line(lines, "영업활동현금흐름")
    icf = _line(lines, "투자활동현금흐름")
    fcf = ocf + icf  # 단순 FCF (투자활동 전액을 CAPEX 대용으로 사용)

    ar = _line(lines, "매출채권")
    inventory = _line(lines, "재고자산")
    ap = _line(lines, "매입채무")
    working_capital = ar.fillna(0.0) + inventory.fillna(0.0) - ap.fillna(0.0)

    cash = _line(lines, "현금및현금성자산")
    debt = _sum_lines(lines, ["단기차입금", "유동성장기부채", "장기차입금", "사채"])
    net_debt = debt.fillna(0.0) - cash.fillna(0.0)

    assets = _line(lines, "자산총계")
    liabilities = _line(lines, "부채총계")
    equity = _line(lines, "자본총계")
    current_assets = _line(lines, "유동자산")
    current_liabilities = _line(lines, "유동부채")

    days = 365.0 / 4.0  # 분기 일수 근사

    metrics = {
        "매출액": revenue / unit,
        "매출총이익": gross / unit,
        "영업이익": operating / unit,
        "EBITDA": ebitda / unit,
        "당기순이익": net / unit,
        "매출총이익률(%)": _safe_ratio(gross, revenue) * 100,
        "영업이익률(%)": _safe_ratio(operating, revenue) * 100,
        "EBITDA마진(%)": _safe_ratio(ebitda, revenue) * 100,
        "순이익률(%)": _safe_ratio(net, revenue) * 100,
        "매출 YoY(%)": _yoy(revenue) * 100,
        "매출 QoQ(%)": revenue.pct_change() * 100,
        "영업활동현금흐름": ocf / unit,
        "투자활동현금흐름": icf / unit,
        "잉여현금흐름(단순)": fcf / unit,
        "현금전환율 OCF/영업이익(%)": _safe_ratio(ocf, operating) * 100,
        "매출채권": ar / unit,
        "재고자산": inventory / unit,
        "매입채무": ap / unit,
        "순운전자본": working_capital / unit,
        "순운전자본/매출(연환산, %)": _safe_ratio(working_capital, revenue * 4) * 100,
        "DSO(일)": _safe_ratio(ar, revenue) * days,
        "DIO(일)": _safe_ratio(inventory, cogs) * days,
        "DPO(일)": _safe_ratio(ap, cogs) * days,
        "현금및현금성자산": cash / unit,
        "총차입금": debt / unit,
        "순차입금": net_debt / unit,
        "자산총계": assets / unit,
        "부채총계": liabilities / unit,
        "자본총계": equity / unit,
        "부채비율(%)": _safe_ratio(liabilities, equity) * 100,
        "유동비율(%)": _safe_ratio(current_assets, current_liabilities) * 100,
        "순차입금/EBITDA(LTM, 배)": _safe_ratio(net_debt, ebitda.rolling(4).sum()),
    }

    result = pd.DataFrame(metrics).T
    result["CCC(일)"] = None  # 아래에서 계산
    ccc = metrics["DSO(일)"] + metrics["DIO(일)"].fillna(0.0) - metrics["DPO(일)"].fillna(0.0)
    result = result.drop(columns=["CCC(일)"])
    result.loc["CCC(일)"] = ccc
    return result.round(2)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denom = denominator.replace(0.0, float("nan"))
    return numerator / denom


def _yoy(series: pd.Series) -> pd.Series:
    """전년 동기 대비. 분기 라벨이 연속이라는 가정 하에 4기 전과 비교."""
    return series.pct_change(4)


def ltm(lines: pd.DataFrame, names: list[str], unit: float = 1e8) -> pd.DataFrame:
    """주요 손익 항목의 LTM(최근 12개월) 롤링 합계."""
    rows = {}
    for name in names:
        series = _line(lines, name)
        rows[f"{name}(LTM)"] = (series.rolling(4).sum() / unit).round(2)
    return pd.DataFrame(rows).T
