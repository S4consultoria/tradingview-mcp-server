"""
Servidor MCP de TradingView — núcleo sólido
Expone: cotizaciones/ratings técnicos, histórico OHLCV y screener de mercado.

Sigue el mismo patrón de despliegue que el servidor MCP de Alpaca:
transporte Streamable HTTP montado en /mcp, listo para Render.
"""

import os
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from tradingview_ta import TA_Handler, Interval
from tvDatafeed import TvDatafeed, Interval as TvInterval

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tradingview-mcp")

mcp = FastMCP("tradingview-mcp", stateless_http=True)

# ---------------------------------------------------------------------------
# tvDatafeed: sesión opcional autenticada (histórico funciona sin login,
# pero con cuenta logueada se evitan límites y datos "delayed" más agresivos)
# ---------------------------------------------------------------------------
_TV_USERNAME = os.environ.get("TV_USERNAME")
_TV_PASSWORD = os.environ.get("TV_PASSWORD")
_tv_client: Optional[TvDatafeed] = None


def get_tv_client() -> TvDatafeed:
    global _tv_client
    if _tv_client is None:
        if _TV_USERNAME and _TV_PASSWORD:
            _tv_client = TvDatafeed(username=_TV_USERNAME, password=_TV_PASSWORD)
        else:
            _tv_client = TvDatafeed()  # sesión anónima
    return _tv_client


_TA_INTERVALS = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
    "1w": Interval.INTERVAL_1_WEEK,
    "1M": Interval.INTERVAL_1_MONTH,
}

_TVD_INTERVALS = {
    "1m": TvInterval.in_1_minute,
    "5m": TvInterval.in_5_minute,
    "15m": TvInterval.in_15_minute,
    "1h": TvInterval.in_1_hour,
    "4h": TvInterval.in_4_hour,
    "1d": TvInterval.in_daily,
    "1w": TvInterval.in_weekly,
    "1M": TvInterval.in_monthly,
}


@mcp.tool()
def get_quote_and_rating(
    symbol: str,
    exchange: str = "NASDAQ",
    screener: str = "america",
    interval: str = "1d",
) -> dict:
    """Obtiene la cotización actual y el rating técnico (Comprar/Vender/Neutral)
    de un símbolo en TradingView, incluyendo indicadores clave (RSI, MACD,
    medias móviles, etc.).

    Args:
        symbol: Ticker, p.ej. "AAPL", "BTCUSDT".
        exchange: Exchange en TradingView, p.ej. "NASDAQ", "BINANCE", "BVL".
        screener: Screener/mercado: "america", "crypto", "forex", "peru", etc.
        interval: "1m","5m","15m","1h","4h","1d","1w","1M".
    """
    handler = TA_Handler(
        symbol=symbol,
        screener=screener,
        exchange=exchange,
        interval=_TA_INTERVALS.get(interval, Interval.INTERVAL_1_DAY),
    )
    analysis = handler.get_analysis()
    return {
        "symbol": symbol,
        "exchange": exchange,
        "summary": analysis.summary,
        "oscillators": analysis.oscillators,
        "moving_averages": analysis.moving_averages,
        "indicators": analysis.indicators,
    }


@mcp.tool()
def get_historical_data(
    symbol: str,
    exchange: str = "NASDAQ",
    interval: str = "1d",
    n_bars: int = 200,
) -> dict:
    """Obtiene velas históricas OHLCV para un símbolo desde TradingView.

    Args:
        symbol: Ticker, p.ej. "AAPL".
        exchange: Exchange en TradingView, p.ej. "NASDAQ", "BINANCE".
        interval: "1m","5m","15m","1h","4h","1d","1w","1M".
        n_bars: Número de velas a traer (máx. recomendado 5000).
    """
    tv = get_tv_client()
    df = tv.get_hist(
        symbol=symbol,
        exchange=exchange,
        interval=_TVD_INTERVALS.get(interval, TvInterval.in_daily),
        n_bars=n_bars,
    )
    if df is None or df.empty:
        return {"symbol": symbol, "exchange": exchange, "bars": []}

    df = df.reset_index()
    df["datetime"] = df["datetime"].astype(str)
    return {
        "symbol": symbol,
        "exchange": exchange,
        "bars": df.to_dict(orient="records"),
    }


@mcp.tool()
def screen_market(
    symbols: list[str],
    exchange: str = "NASDAQ",
    screener: str = "america",
    interval: str = "1d",
) -> dict:
    """Compara el rating técnico de varios símbolos a la vez (screener manual).

    Args:
        symbols: Lista de tickers, p.ej. ["AAPL","MSFT","NVDA"].
        exchange: Exchange en TradingView para todos los símbolos.
        screener: Screener/mercado: "america", "crypto", "forex", "peru", etc.
        interval: "1m","5m","15m","1h","4h","1d","1w","1M".
    """
    results = []
    for sym in symbols:
        try:
            handler = TA_Handler(
                symbol=sym,
                screener=screener,
                exchange=exchange,
                interval=_TA_INTERVALS.get(interval, Interval.INTERVAL_1_DAY),
            )
            analysis = handler.get_analysis()
            results.append({
                "symbol": sym,
                "summary": analysis.summary,
                "close": analysis.indicators.get("close"),
                "RSI": analysis.indicators.get("RSI"),
                "change": analysis.indicators.get("change"),
            })
        except Exception as exc:
            results.append({"symbol": sym, "error": str(exc)})
    return {"exchange": exchange, "results": results}


# ---------------------------------------------------------------------------
# Entrypoint — Streamable HTTP montado en /mcp, tal como el servidor de Alpaca
# ---------------------------------------------------------------------------
app = mcp.streamable_http_app()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
