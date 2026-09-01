"""분기 차분 로직 검증.

DART fnlttSinglAcntAll 응답 형태를 모사한 합성 데이터로
'누적 -> 분기 단독' 변환이 맞는지 확인한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src import fdd_metrics, quarterly


def _row(year, period, sj_div, account_id, account_nm, ytd, reported, ord_=1):
    """OpenDART 응답 1행 모사."""
    return {
        "_year": year,
        "_period": period,
        "_fs_div": "CFS",
        "sj_div": sj_div,
        "sj_nm": {"BS": "재무상태표", "IS": "손익계산서", "CF": "현금흐름표"}[sj_div],
        "account_id": account_id,
        "account_nm": account_nm,
        "account_detail": "-",
        "ord": str(ord_),
        "currency": "KRW",
        # 사업보고서/1분기는 add_amount 가 비어 있고 amount 가 곧 누적이다.
        "thstrm_amount": str(reported),
        "thstrm_add_amount": "" if ytd is None else str(ytd),
        "rcept_no": f"{year}0000000000",
    }


def build_fixture():
    """2024년 매출 누적: 100 / 250 / 420 / 600  -> 단독: 100 / 150 / 170 / 180"""
    cumulative = {"Q1": 100, "H1": 250, "Q3": 420, "FY": 600}
    standalone = {"Q1": 100, "H1": 150, "Q3": 170, "FY": 180}
    assets = {"Q1": 900, "H1": 950, "Q3": 980, "FY": 1000}

    rows = []
    for period in quarterly.PERIOD_ORDER:
        ytd = cumulative[period]
        if period == "Q1":
            rows.append(_row(2024, period, "IS", "ifrs-full_Revenue", "매출액", None, ytd, 1))
        elif period == "FY":
            # 사업보고서: amount 가 연간 누적, add_amount 없음
            rows.append(_row(2024, period, "IS", "ifrs-full_Revenue", "매출액", None, ytd, 1))
        else:
            # 반기/3분기: amount 는 3개월, add_amount 는 누적
            rows.append(
                _row(2024, period, "IS", "ifrs-full_Revenue", "매출액", ytd, standalone[period], 1)
            )
        # 재무상태표는 시점 데이터 - 차분 대상 아님
        rows.append(
            _row(2024, period, "BS", "ifrs-full_Assets", "자산총계", None, assets[period], 1)
        )
    return rows, standalone, assets


def test_flow_is_differenced_and_stock_is_not():
    rows, standalone, assets = build_fixture()
    frame = quarterly.to_dataframe(rows)
    result = quarterly.build_quarterly(frame, fs_div="CFS")

    revenue = result.xs("ifrs-full_Revenue", level="account_key").iloc[0]
    for period, quarter in quarterly.QUARTER_OF.items():
        got = revenue[f"2024{quarter}"]
        want = standalone[period]
        assert got == want, f"매출 {quarter}: {got} != {want}"

    total_assets = result.xs("ifrs-full_Assets", level="account_key").iloc[0]
    for period, quarter in quarterly.QUARTER_OF.items():
        got = total_assets[f"2024{quarter}"]
        want = assets[period]
        assert got == want, f"자산총계 {quarter}: {got} != {want} (BS는 차분 금지)"

    print("PASS: flow 차분 / stock 비차분")


def test_reconciliation_flags_mismatch():
    rows, _, _ = build_fixture()
    # 3분기 보고서의 '3개월' 표기치를 일부러 틀리게 만든다 -> 대사에서 잡혀야 한다.
    for row in rows:
        if row["_period"] == "Q3" and row["sj_div"] == "IS":
            row["thstrm_amount"] = "999"

    frame = quarterly.to_dataframe(rows)
    recon = quarterly.reconciliation(frame, fs_div="CFS")
    assert not recon.empty, "불일치를 잡아내지 못했습니다"
    assert (recon["기간"] == "2024Q3").any()
    print(f"PASS: 차분 대사가 불일치 {len(recon)}건 탐지")


def test_metrics_pipeline():
    rows, _, _ = build_fixture()
    # 지표 계산에 필요한 계정 몇 개를 추가한다.
    for period in quarterly.PERIOD_ORDER:
        rows.append(_row(2024, period, "BS", "ifrs-full_Liabilities", "부채총계", None, 400, 2))
        rows.append(_row(2024, period, "BS", "ifrs-full_Equity", "자본총계", None, 600, 3))
        rows.append(
            _row(2024, period, "BS", "ifrs-full_CashAndCashEquivalents",
                 "현금및현금성자산", None, 120, 4)
        )
        rows.append(_row(2024, period, "BS", "-표준계정없음-", "매출채권", None, 200, 5))

    frame = quarterly.to_dataframe(rows)
    result = quarterly.build_quarterly(frame, fs_div="CFS")
    lines = fdd_metrics.extract_lines(result)

    assert "매출액" in lines.index
    assert "자산총계" in lines.index
    assert "매출채권" in lines.index, "계정명 기반 매칭이 동작해야 합니다"

    metrics = fdd_metrics.build_metrics(lines, unit=1.0)
    assert metrics.loc["매출액", "2024Q2"] == 150
    # 부채비율 = 400/600 = 66.67%
    assert abs(metrics.loc["부채비율(%)", "2024Q1"] - 66.67) < 0.01
    print("PASS: 지표 산출 (매출/부채비율/계정명 매칭)")




def test_debt_lines_are_summed_not_first_match():
    """차입금·사채는 유동/비유동으로 흩어져 나온다. 전부 합산해야 순차입금이 맞다."""
    rows, _, _ = build_fixture()
    for period in quarterly.PERIOD_ORDER:
        rows.append(_row(2024, period, "BS", "-none1-", "단기차입금", None, 100, 10))
        rows.append(_row(2024, period, "BS", "-none2-", "유동성장기부채", None, 50, 11))
        rows.append(_row(2024, period, "BS", "-none3-", "장기차입금", None, 300, 12))
        rows.append(_row(2024, period, "BS", "-none4-", "전환사채", None, 200, 13))
        rows.append(
            _row(2024, period, "BS", "ifrs-full_CashAndCashEquivalents",
                 "현금및현금성자산", None, 150, 14)
        )

    frame = quarterly.to_dataframe(rows)
    result = quarterly.build_quarterly(frame, fs_div="CFS")
    lines, mapping = fdd_metrics.extract_lines(result, trace=True)
    metrics = fdd_metrics.build_metrics(lines, unit=1.0)

    # 총차입금 = 100 + 50 + 300 + 200 = 650
    assert metrics.loc["총차입금", "2024Q1"] == 650, metrics.loc["총차입금"]
    # 순차입금 = 650 - 150 = 500
    assert metrics.loc["순차입금", "2024Q1"] == 500, metrics.loc["순차입금"]
    assert (mapping["처리"] == "합산").any(), "합산 처리 내역이 기록되어야 합니다"
    print("PASS: 차입금 합산 (총차입금 650 / 순차입금 500)")


def test_subtotal_lines_are_not_double_counted():
    """소계 성격 라벨은 중복 매칭돼도 합산하면 안 된다."""
    rows, standalone, _ = build_fixture()
    # 주석 분해 등으로 '매출액'이 한 번 더 등장하는 상황
    for period in quarterly.PERIOD_ORDER:
        rows.append(
            _row(2024, period, "IS", "-dup-", "매출액(제품)", None,
                 {"Q1": 100, "H1": 250, "Q3": 420, "FY": 600}[period], 99)
        )

    frame = quarterly.to_dataframe(rows)
    result = quarterly.build_quarterly(frame, fs_div="CFS")
    lines = fdd_metrics.extract_lines(result)

    assert lines.loc["매출액", "2024Q2"] == standalone["H1"], (
        f"매출액이 이중계상되었습니다: {lines.loc['매출액', '2024Q2']}"
    )
    print("PASS: 소계 라벨 이중계상 방지")


if __name__ == "__main__":
    test_flow_is_differenced_and_stock_is_not()
    test_reconciliation_flags_mismatch()
    test_metrics_pipeline()
    test_debt_lines_are_summed_not_first_match()
    test_subtotal_lines_are_not_double_counted()
    print("\n전체 통과")
