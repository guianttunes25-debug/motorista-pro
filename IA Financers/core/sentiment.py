"""Market Sentiment Engine — contexto macro do par.

Combina volume 24h, variação 24h e sentimento de notícias em um score
de contexto (-3..+3). É usado pelo SeniorTraderAgent como confirmador:
não dispara trade sozinho, mas reforça ou enfraquece a qualidade do setup.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SentimentScore:
    score: int                 # -3 .. +3
    bullish: bool
    bearish: bool
    reasons: list[str]


def market_sentiment_engine(
    change_pct_24h: float,
    quote_volume_24h: float,
    news_score: int = 0,
    high_volume_threshold: float = 1_000_000_000.0,  # 1B USDT (BTC tem 30B+)
) -> SentimentScore:
    score = 0
    reasons: list[str] = []

    if change_pct_24h > 2.0:
        score += 1
        reasons.append(f"variação 24h forte +{change_pct_24h:.2f}%")
    elif change_pct_24h < -2.0:
        score -= 1
        reasons.append(f"variação 24h fraca {change_pct_24h:.2f}%")

    if quote_volume_24h > high_volume_threshold:
        # volume alto reforça a direção atual
        if change_pct_24h >= 0:
            score += 1
            reasons.append("volume 24h alto + alta")
        else:
            score -= 1
            reasons.append("volume 24h alto + queda")

    if news_score > 0:
        score += 1
        reasons.append(f"notícias positivas (+{news_score})")
    elif news_score < 0:
        score -= 1
        reasons.append(f"notícias negativas ({news_score})")

    score = max(-3, min(3, score))
    return SentimentScore(
        score=score,
        bullish=score >= 2,
        bearish=score <= -2,
        reasons=reasons,
    )
