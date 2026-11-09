"""Legacy customer-score adapter over Parquet/Excel, split by scoring component.

`PolarsCustomerScoreRepository` is the composition root; each component (MIA, AQL,
DAR, ZC) is a small scorer with its own data sources. The domain formulas live in
`app.domain.score_math`; nothing here redefines them.
"""

from .repository import PolarsCustomerScoreRepository

__all__ = ["PolarsCustomerScoreRepository"]
