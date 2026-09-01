# 핑거(163730) FDD 재무데이터 파이프라인

금융감독원 전자공시(DART)에서 최근 3개년 분기 재무제표를 수집하여
재무실사(FDD)용 분기 시계열과 지표표를 생성한다.

## 왜 필요한가

DART 정기보고서의 손익계산서·현금흐름표는 **누적(YTD)** 기준으로 공시된다.
분기별 추세를 보려면 누적치를 차분해 **분기 단독** 수치를 만들어야 한다.

```
Q1 단독 = 1분기보고서 누적
Q2 단독 = 반기보고서 누적   - 1분기 누적
Q3 단독 = 3분기보고서 누적  - 반기 누적
Q4 단독 = 사업보고서 연간   - 3분기 누적
```

재무상태표는 시점 데이터이므로 차분하지 않는다.
차분 결과는 보고서에 직접 표기된 '3개월' 수치와 대사하며,
불일치는 **소급 재작성 또는 연결범위 변동의 신호**로 별도 보고한다.

## 사용법

```bash
git clone https://github.com/Kals88/kals.git
cd kals
git checkout claude/finger-fdd-report-1a4mv7
pip install -r requirements.txt

export DART_API_KEY=<https://opendart.fss.or.kr 에서 발급>

# 0) 환경 점검 - 키와 DART 연결을 먼저 확인한다
python -m src.main doctor

# 1) 원자료 수집 (연결 + 별도, 3개년 × 4개 보고서)
python -m src.main fetch --stock-code 163730 --years 2023 2024 2025

# 2) 분기 시계열 및 FDD 지표 산출
python -m src.main build --fs-div CFS   # 연결
python -m src.main build --fs-div OFS   # 별도
```

`build` 는 마지막에 지표표를 마크다운으로 터미널에 출력한다.
그 블록을 그대로 복사해 전달하면 해석·분석을 이어서 진행할 수 있다.

## 산출물

| 경로 | 내용 |
|---|---|
| `outputs/핑거_분기재무제표_CFS.xlsx` | 6개 시트: 재무상태표 / 손익계산서 / 현금흐름표 (분기 시계열), FDD표준라인, FDD지표, 차분대사, 계정매핑 |
| `outputs/재무분석표.md` | 보고서 삽입용 마크다운 표 |
| `data/raw/` | DART API 원본 응답 (재수집 없이 재분석 가능) |

## 산출 지표

수익성(매출총이익률·영업이익률·EBITDA마진), 성장성(YoY·QoQ),
현금창출력(OCF, 단순 FCF, 현금전환율), 운전자본(NWC, DSO/DIO/DPO, CCC),
재무안정성(순차입금, 부채비율, 유동비율, 순차입금/EBITDA LTM).

## 구성

```
src/dart_client.py   OpenDART API 클라이언트 (재시도, 상태코드 처리, corp_code 캐시)
src/quarterly.py     누적 -> 분기 단독 변환 및 차분 대사
src/fdd_metrics.py   FDD 표준라인 매핑 및 지표 산출
src/main.py          CLI (fetch / build)
tests/               분기 차분 로직 검증
reports/             FDD 보고서 본문
```

## 테스트

```bash
python tests/test_quarterly.py
```

DART 응답 형태를 모사한 합성 데이터로 flow 항목 차분, stock 항목 비차분,
차분 대사 탐지, 지표 산출을 검증한다.

## 알려진 제약

- `fnlttSinglAcntAll` API 는 2015년 이후 정기보고서만 제공한다.
- 회사가 표준계정코드(`account_id`)를 부여하지 않은 계정은 계정명 키워드로
  매칭한다. 매칭 규칙은 `src/fdd_metrics.py` 의 `NAME_MAP` 에서 조정한다.
  **어떤 원계정이 어떤 라벨로 들어갔는지는 `계정매핑` 시트에서 확인할 것.**
  숫자가 이상하면 여기부터 본다.
- 차입금·사채·매출채권처럼 여러 계정에 흩어지는 항목은 매칭되는 계정을
  전부 합산하고(`ADDITIVE_LABELS`), 매출액·영업이익 등 소계 성격 항목은
  이중계상을 막기 위해 첫 매칭만 채택한다(`FIRST_MATCH_LABELS`).
- 소급 재작성이 있는 경우 연도 간 비교 시 기준이 달라질 수 있다.
  `차분대사` 시트를 먼저 확인할 것.
