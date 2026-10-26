# lead-scrub report for `leads.csv`

- Rows in: **25**, rows out: **17** (**8** duplicate rows merged into 7 people)
- Field fixes applied: **35**; invalid values set aside: **2**
- Pairs left for a human to decide: **1** (see `review.csv`)
- Rows carrying a flag in `clean.csv`: **3**
- Estimated hand-cleaning time avoided: **~0.08 h** at 3 h per 1,000 rows

## Merged duplicates

| Source rows | Kept as | Why |
| --- | --- | --- |
| 2, 3, 4 | Dana Whitfield | rows 2 + 3: same email dana.whitfield\@northgate-print.test; rows 2 + 4: same phone +12125550111; rows 3 + 4: same phone +12125550111 |
| 5, 6 | Chidi Okafor | rows 5 + 6: same email chidi\@okafor-hvac.test |
| 7, 8 | Robert Alvarez | rows 7 + 8: same phone +13055550147 |
| 13, 14 | Samuel Patel | rows 13 + 14: same email sam.patel\@gmail.com |
| 18, 19 | Mia McDonald | rows 18 + 19: same email mia\@mcd-design.test |
| 20, 21 | Luis García | rows 20 + 21: same email luis\@garcia-auto.test |
| 25, 26 | Ray Dubois | rows 25 + 26: same phone +15045550166 |

## Needs your decision (not merged)

| Rows | Record A | Record B | Why unsure |
| --- | --- | --- | --- |
| 11 / 12 | Tom Reyes reyes.family\@gmail.test | Maria Reyes reyes.family\@gmail.test | same email reyes.family\@gmail.test but names differ (43% alike) |

## Fix list: values that need a person

| Row | Contact | Problem |
| --- | --- | --- |
| 13 | Samuel Patel | email fixed: domain typo 'gmial.com' -&gt; 'gmail.com' |
| 18 | Mia McDonald | invalid phone 'not a phone' |
| 22 | Hannah Brooks | invalid email 'hannah.brooks\@brooks-law' |

## Every change

See `changes.csv` for each before/after value with the reason.
