# CAT ME&T gross-margin policy, pilot evidence

Status: approved measurement policy; **not wired into the backtest or live model**.

The Piotroski gross-margin factor compares two trailing-year margins. For CAT,
use Machinery, Energy & Transportation (ME&T) sales and ME&T cost of goods sold
on **both** sides of the ratio:

`ME&T gross margin = (ME&T sales - ME&T cost of goods sold) / ME&T sales`.

Do not substitute CAT's consolidated sales and revenues for ME&T sales. The
consolidated top line includes Financial Products revenue, but cost of goods
sold does not include Financial Products' interest expense and other costs.
Do not combine an isolated CAT `GrossProfit` tag with consolidated revenue or
cost of revenue. This policy is issuer-specific; draft PR #50's broad fallback
is not its implementation.

## Frozen evidence for the September 3, 2024 pilot

Values are USD millions, manually transcribed from the cited SEC filings.
The accession is the **filing that reported the selected value**, not
necessarily the original filing for that comparative period. All three
filings were accepted before the pilot's September 3, 2024 knowledge cutoff.

| Period | ME&T sales | ME&T COGS | Source filing, acceptance (UTC) |
| --- | ---: | ---: | --- |
| FY2022 | 56,574 | 41,356 | [FY2023 10-K](https://www.sec.gov/Archives/edgar/data/18230/000001823024000009/cat-20231231.htm), `0000018230-24-000009`, 2024-02-16 15:05:13 |
| FY2023 | 63,869 | 42,776 | [FY2023 10-K](https://www.sec.gov/Archives/edgar/data/18230/000001823024000009/cat-20231231.htm), `0000018230-24-000009`, 2024-02-16 15:05:13 |
| H1 2022 | 26,425 | 19,538 | [Q2 2023 8-K exhibit](https://www.sec.gov/Archives/edgar/data/18230/000001823023000044/ex991toformcat2q2023earnin.htm), `0000018230-23-000044`, 2023-08-01 10:31:57 |
| H1 2023 | 31,644 | 21,172 | [Q2 2024 8-K exhibit](https://www.sec.gov/Archives/edgar/data/18230/000001823024000042/ex991toformcat2q2024earnin.htm), `0000018230-24-000042`, 2024-08-06 10:32:05 |
| H1 2024 | 30,800 | 19,816 | [Q2 2024 8-K exhibit](https://www.sec.gov/Archives/edgar/data/18230/000001823024000042/ex991toformcat2q2024earnin.htm), `0000018230-24-000042`, 2024-08-06 10:32:05 |

`TTM Jun 2024 = FY2023 + H1 2024 - H1 2023`: sales 63,025; COGS
41,420; margin 34.2800476002%. `TTM Jun 2023 = FY2022 + H1 2023 - H1
2022`: sales 61,793; COGS 42,990; margin 30.4290129950%. The gross-margin
factor alone would be 1 at this date. This says nothing about CAT's other
gates or any investment return.

## Fail-closed integration criteria

The frozen calculation in `src/backtesting/cat_met_margin.py` deliberately
supports only this pair of trailing-year periods. It refuses a missing or
duplicated observation, a filing not public by the knowledge cutoff, an
evidence capture after the data-vintage cutoff, or an unsupported period.

Before it can affect a backtest, a separate reviewed change must:

1. Place segment evidence behind an audited source seam, without treating
   manually transcribed values as ordinary Company Facts. Record the exhibit
   URL, accession, acceptance time, capture time, and policy version in the
   result; verify the five values independently from the cited filings.
2. Pass the ME&T margin pair only to Piotroski's **gross-margin factor**.
   Keep consolidated revenue for asset turnover and all other factors. Never
   mix ME&T numerator with consolidated denominator.
3. Refuse CAT if either margin cannot be reproduced at both cutoffs. Leave
   the live valuation and all other issuers unchanged, and label any new
   pilot run as pipeline validation rather than performance evidence.
