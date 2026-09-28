# Microsoft FY2024 linked forecast foundation

Status: local implementation for review. This does not publish SEC facts, rerun
the CAT pilot, trade, or establish a research sector.

## Opening source and cutoffs

The first issuer is Microsoft, for its June 30, 2024 opening balance sheet.
`src/fundamentals/msft_opening_balance.py` reads the consolidated balance
sheet in the [FY2024 SEC 10-K](https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm),
accession `0000950170-24-087843`. The decoded SEC document used for this
review has SHA-256
`c7666c097e0b34de5406a17070e1f4d33fea896316c1fc12a4bbc6bef5d822f7`.
The parser refuses a different document hash, context, unit, missing line,
duplicate line, or unreconciled section. A reduced official excerpt is a
repository test fixture; production verification must use the full document.
`fetch_microsoft_opening_document` retrieves it from the pinned SEC URL with
an explicit contact User-Agent, decodes the response, and refuses bytes that
do not match the reviewed document SHA-256. The fetch path was checked against
the live SEC document, and its gzip and changed-content cases are tested.
Parsing uses Python's built-in HTML parser; no `lxml` installation is needed.

All amounts below are USD millions, directly tagged in the filed balance
sheet. Five current asset lines sum to 159,734, six noncurrent asset lines
sum to 352,429, seven current liability lines sum to 125,286, six
noncurrent liability lines sum to 118,400, and three equity lines sum to
268,477. Assets of 512,163 equal liabilities of 243,686 plus equity of
268,477. These are sums of independently reported lines; there is no
balancing residual.

The existing SEC store was queried with the historical knowledge cutoff
`2024-09-03T16:00:00-04:00` and ingestion cutoff
`2026-09-27T22:40:05.734129Z`. Its point-in-time opening snapshot built,
and all 13 currently mapped concepts matched the corresponding filed lines.
The remaining filed lines are **not stored under those concept mappings**.
Thus the issuer-specific source adapter has complete filing itemization, but
the generic stored opening still reports gaps in all four asset and liability
sections. No general claim of complete store coverage follows from this
cross-check. `build_verified_microsoft_opening` requires a complete
point-in-time stored snapshot and all 13 comparisons before returning an
opening for the forecast. A filing-only parse cannot start a forecast.

## Forecast contract

`src/fundamentals/linked_forecast.py` takes that opening and explicit Decimal
assumptions for one year, or a prior linked period for the next year. Every
noncash balance line requires a named movement, including an explicit zero.
PP&E moves by capex less depreciation. Revenue and operating margin determine
EBIT; existing debt and opening revolver balances determine interest. Taxes
are the stated rate on positive pretax income, with no loss carryforward.
Equity issuance, repurchases, dividends, and retained earnings are explicit.
Cash comes from the indirect cash-flow statement. Assets must equal
liabilities plus equity, and cash flow must equal the change in balance-sheet
cash. `validate_cash_linkage` checks the actual returned statement fields,
including the balance-sheet cash line, and is also used before chaining a
prior forecast. Corrupted cash-flow or balance-sheet cash fields refuse.
This is a cross-statement consistency check, not independent evidence that
every assumed transaction is cash. AOCI movements refuse until a paired
noncash equity schedule exists.

The revolver uses an explicit limit and minimum cash. It draws only when
needed after operating, investing, and financing cash flows, including fees
and their current-period tax effect. It repays from cash above the minimum.
Interest on a new draw starts in the following period; the commitment fee is
based on unused opening capacity. If the limit cannot cover the shortfall,
the forecast refuses rather than adding a balancing value. The returned
debt schedule shows existing debt movement and total debt alongside revolver
draws, repayments, fees, and interest.
The limit applies to the opening balance as well: shrinking the next year's
limit below outstanding opening borrowing refuses even if that year's cash
flows could subsequently repay the excess.

The model's `unlevered_fcf` uses EBIT after the assumed unlevered tax rate,
plus depreciation, less capex and operating working-capital investment.
The test compares it with the existing unlevered DCF calculation under the
same revenue, margin, tax, depreciation, capex, and working-capital inputs.
Financing does not alter this unlevered measure.

## Scope limits

This is a first issuer-specific annual forecast engine, not a general
multi-issuer line mapper or an investment recommendation. Operating margin
must already include depreciation. Depreciation cannot exceed total operating
expense implied by that margin. Forecast changes in receivables, inventory,
payables, employee accruals, customer contract liabilities, and
existing debt are treated as cash movements. The model refuses nonzero
investment, lease, goodwill, intangible, income-tax-balance, other-asset,
other-liability, and AOCI changes until a reviewed cash/noncash schedule
exists for each. It does not model loss carryforwards, deferred tax cash
timing, acquisitions, other comprehensive income, dilution, or same-year
interest on new revolver draws. Funding shortfalls refuse. Forecast inputs
and returned statements are immutable after validation. No forecast
assumptions here are claimed to be Microsoft's guidance or actual FY2025
results.

Share repurchases in this first model reduce common stock and paid-in capital,
net of cash equity issuance. They cannot drive that line below zero; amounts
requiring treasury-stock accounting or allocation to retained earnings refuse
until a separate equity schedule exists. Retained earnings and total equity
may still be negative. The depreciation-versus-operating-expense diagnostic
is checked before the broader negative-asset rule.
