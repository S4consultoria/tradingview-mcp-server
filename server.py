"""
TradingView MCP Server
Custom MCP server for market analysis via TradingView data.
Designed for SSE transport on Render (cloud deployment).
"""

import os
import json
import logging
from typing import Optional
from mcp.server.fastmcp import FastMCP
from tradingview_ta import TA_Handler, Interval, Exchange, TradingView

# --- tvDatafeed for historical OHLCV (uses Pro login) ---
try:
    from tvDatafeed import TvDatafeed, Interval as TvInterval
    TV_USERNAME = os.environ.get("TV_USERNAME", "")
    TV_PASSWORD = os.environ.get("TV_PASSWORD", "")
    if TV_USERNAME and TV_PASSWORD:
        tv_feed = TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
        TV_AUTH = True
    else:
        tv_feed = TvDatafeed()  # anonymous (limited history)
        TV_AUTH = False
    TV_FEED_AVAILABLE = True
except ImportError:
    tv_feed = None
    TV_FEED_AVAILABLE = False
    TV_AUTH = False

# --- Config ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tradingview-mcp")

mcp = FastMCP("TradingView MCP Server")

# --- Interval mapping ---
INTERVAL_MAP = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "30m": Interval.INTERVAL_30_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "2h": Interval.INTERVAL_2_HOURS,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
    "1w": Interval.INTERVAL_1_WEEK,
    "1M": Interval.INTERVAL_1_MONTH,
}

# --- Exchange mapping for common markets ---
EXCHANGE_MAP = {
    "US": "NASDAQ",      # Default US
    "NYSE": "NYSE",
    "NASDAQ": "NASDAQ",
    "AMEX": "AMEX",
    "BVL": "BVL",         # Bolsa de Valores de Lima
    "BMV": "BMV",         # Bolsa Mexicana
    "BCBA": "BCBA",       # Buenos Aires
    "BVSP": "BVSP",       # B3 Brasil
    "LSE": "LSE",
    "TSX": "TSX",
    "ASX": "ASX",
    "NSE": "NSE",         # India
    "HKEX": "HKEX",
    "TSE": "TSE",         # Tokyo
    "BINANCE": "BINANCE",
    "COINBASE": "COINBASE",
    "FX_IDC": "FX_IDC",   # Forex
    "CBOT": "CBOT",       # Commodities
    "NYMEX": "NYMEX",
    "COMEX": "COMEX",
    "TVC": "TVC",         # Indices (DXY, etc.)
}

SCREENER_MAP = {
    "US": "america",
    "NYSE": "america",
    "NASDAQ": "america",
    "AMEX": "america",
    "BVL": "america",
    "BMV": "america",
    "BCBA": "america",
    "BVSP": "america",
    "TSX": "america",
    "LSE": "uk",
    "ASX": "australia",
    "NSE": "india",
    "HKEX": "hongkong",
    "TSE": "japan",
    "BINANCE": "crypto",
    "COINBASE": "crypto",
    "FX_IDC": "forex",
    "CBOT": "cfd",
    "NYMEX": "cfd",
    "COMEX": "cfd",
    "TVC": "cfd",
}


def _resolve_exchange(symbol: str, exchange: Optional[str] = None) -> tuple[str, str, str]:
    """
    Resolve symbol, exchange, and screener.
    Supports formats: 'AAPL', 'NASDAQ:AAPL', or explicit exchange param.
    """
    if ":" in symbol:
        parts = symbol.split(":", 1)
        exch = parts[0].upper()
        sym = parts[1].upper()
    else:
        sym = symbol.upper()
        exch = (exchange or "NASDAQ").upper()

    exch_resolved = EXCHANGE_MAP.get(exch, exch)
    screener = SCREENER_MAP.get(exch, "america")

    return sym, exch_resolved, screener


def _format_analysis(analysis) -> dict:
    """Format TA_Handler analysis into a clean dict."""
    summary = analysis.summary
    oscillators = analysis.oscillators
    moving_averages = analysis.moving_averages
    indicators = analysis.indicators

    return {
        "summary": {
            "recommendation": summary.get("RECOMMENDATION", "N/A"),
            "buy": summary.get("BUY", 0),
            "sell": summary.get("SELL", 0),
            "neutral": summary.get("NEUTRAL", 0),
        },
        "oscillators": {
            "recommendation": oscillators.get("RECOMMENDATION", "N/A"),
            "buy": oscillators.get("BUY", 0),
            "sell": oscillators.get("SELL", 0),
            "neutral": oscillators.get("NEUTRAL", 0),
            "details": oscillators.get("COMPUTE", {}),
        },
        "moving_averages": {
            "recommendation": moving_averages.get("RECOMMENDATION", "N/A"),
            "buy": moving_averages.get("BUY", 0),
            "sell": moving_averages.get("SELL", 0),
            "neutral": moving_averages.get("NEUTRAL", 0),
            "details": moving_averages.get("COMPUTE", {}),
        },
        "key_indicators": {
            "close": indicators.get("close"),
            "open": indicators.get("open"),
            "high": indicators.get("high"),
            "low": indicators.get("low"),
            "volume": indicators.get("volume"),
            "change": indicators.get("change"),
            "change_pct": indicators.get("change") / indicators.get("close") * 100
            if indicators.get("close") and indicators.get("change")
            else None,
            "RSI": indicators.get("RSI"),
            "MACD_macd": indicators.get("MACD.macd"),
            "MACD_signal": indicators.get("MACD.signal"),
            "Stoch_K": indicators.get("Stoch.K"),
            "Stoch_D": indicators.get("Stoch.D"),
            "ADX": indicators.get("ADX"),
            "CCI20": indicators.get("CCI20"),
            "ATR": indicators.get("ATR"),
            "BB_upper": indicators.get("BB.upper"),
            "BB_lower": indicators.get("BB.lower"),
            "VWAP": indicators.get("VWAP"),
            "Pivot_classic_P": indicators.get("Pivot.M.Classic.Middle"),
            "Pivot_classic_R1": indicators.get("Pivot.M.Classic.R1"),
            "Pivot_classic_S1": indicators.get("Pivot.M.Classic.S1"),
            "SMA10": indicators.get("SMA10"),
            "SMA20": indicators.get("SMA20"),
            "SMA50": indicators.get("SMA50"),
            "SMA200": indicators.get("SMA200"),
            "EMA10": indicators.get("EMA10"),
            "EMA20": indicators.get("EMA20"),
            "EMA50": indicators.get("EMA50"),
            "EMA200": indicators.get("EMA200"),
        },
    }


# ===================== MCP TOOLS =====================


@mcp.tool()
def get_technical_analysis(
    symbol: str,
    exchange: str = "NASDAQ",
    interval: str = "1d",
) -> str:
    """
    Get full technical analysis for a symbol: summary (BUY/SELL/NEUTRAL),
    oscillators, moving averages, and key indicators (RSI, MACD, BB, etc.).

    Args:
        symbol: Ticker symbol (e.g. 'AAPL', 'NASDAQ:AAPL', 'BVL:BAP')
        exchange: Exchange code (NASDAQ, NYSE, BVL, BINANCE, FX_IDC, etc.)
        interval: Timeframe: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w, 1M
    """
    try:
        sym, exch, screener = _resolve_exchange(symbol, exchange)
        tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY)

        handler = TA_Handler(
            symbol=sym,
            screener=screener,
            exchange=exch,
            interval=tv_interval,
        )
        analysis = handler.get_analysis()
        result = _format_analysis(analysis)
        result["meta"] = {
            "symbol": sym,
            "exchange": exch,
            "screener": screener,
            "interval": interval,
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error analyzing {symbol}: {e}")
        return json.dumps({"error": str(e), "symbol": symbol})


@mcp.tool()
def get_multi_timeframe_analysis(
    symbol: str,
    exchange: str = "NASDAQ",
    intervals: str = "1h,4h,1d,1w",
) -> str:
    """
    Get technical analysis across multiple timeframes for confluence detection.
    Returns summary recommendation per timeframe.

    Args:
        symbol: Ticker symbol
        exchange: Exchange code
        intervals: Comma-separated timeframes (e.g. '1h,4h,1d,1w')
    """
    try:
        sym, exch, screener = _resolve_exchange(symbol, exchange)
        results = {}

        for tf in intervals.split(","):
            tf = tf.strip()
            tv_interval = INTERVAL_MAP.get(tf)
            if not tv_interval:
                results[tf] = {"error": f"Invalid interval: {tf}"}
                continue

            handler = TA_Handler(
                symbol=sym, screener=screener, exchange=exch, interval=tv_interval
            )
            analysis = handler.get_analysis()
            summary = analysis.summary
            indicators = analysis.indicators

            results[tf] = {
                "recommendation": summary.get("RECOMMENDATION"),
                "buy": summary.get("BUY"),
                "sell": summary.get("SELL"),
                "neutral": summary.get("NEUTRAL"),
                "close": indicators.get("close"),
                "RSI": round(indicators.get("RSI", 0), 2) if indicators.get("RSI") else None,
                "MACD_signal": "bullish"
                if (indicators.get("MACD.macd", 0) or 0) > (indicators.get("MACD.signal", 0) or 0)
                else "bearish",
                "above_SMA200": indicators.get("close", 0) > (indicators.get("SMA200", 0) or 0)
                if indicators.get("close") and indicators.get("SMA200")
                else None,
            }

        return json.dumps(
            {"symbol": sym, "exchange": exch, "timeframes": results},
            indent=2,
            default=str,
        )

    except Exception as e:
        logger.error(f"Error multi-TF {symbol}: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_indicators(
    symbol: str,
    exchange: str = "NASDAQ",
    interval: str = "1d",
    indicators: str = "RSI,MACD.macd,MACD.signal,close,volume,SMA50,SMA200,EMA20,BB.upper,BB.lower,ATR,ADX,Stoch.K,Stoch.D,CCI20,VWAP",
) -> str:
    """
    Get specific indicator values for a symbol. Returns raw numerical values.

    Args:
        symbol: Ticker symbol
        exchange: Exchange code
        interval: Timeframe
        indicators: Comma-separated indicator names (e.g. 'RSI,MACD.macd,close,SMA50')
    """
    try:
        sym, exch, screener = _resolve_exchange(symbol, exchange)
        tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY)

        handler = TA_Handler(
            symbol=sym, screener=screener, exchange=exch, interval=tv_interval
        )
        analysis = handler.get_analysis()
        all_indicators = analysis.indicators

        requested = [i.strip() for i in indicators.split(",")]
        result = {}
        for ind in requested:
            val = all_indicators.get(ind)
            result[ind] = round(val, 6) if isinstance(val, float) else val

        return json.dumps(
            {
                "symbol": sym,
                "exchange": exch,
                "interval": interval,
                "indicators": result,
            },
            indent=2,
            default=str,
        )

    except Exception as e:
        logger.error(f"Error indicators {symbol}: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_symbols(
    symbols: str,
    exchange: str = "NASDAQ",
    interval: str = "1d",
) -> str:
    """
    Scan multiple symbols and return a comparative summary table.
    Useful for screening a watchlist or comparing portfolio positions.

    Args:
        symbols: Comma-separated symbols (e.g. 'AAPL,MSFT,GOOGL,AMZN')
        exchange: Default exchange for all symbols
        interval: Timeframe for analysis
    """
    try:
        results = []
        for raw_sym in symbols.split(","):
            raw_sym = raw_sym.strip()
            if not raw_sym:
                continue

            sym, exch, screener = _resolve_exchange(raw_sym, exchange)
            tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY)

            try:
                handler = TA_Handler(
                    symbol=sym, screener=screener, exchange=exch, interval=tv_interval
                )
                analysis = handler.get_analysis()
                summary = analysis.summary
                ind = analysis.indicators

                results.append(
                    {
                        "symbol": sym,
                        "exchange": exch,
                        "recommendation": summary.get("RECOMMENDATION"),
                        "buy_signals": summary.get("BUY"),
                        "sell_signals": summary.get("SELL"),
                        "close": ind.get("close"),
                        "change_pct": round(
                            ind.get("change", 0) / ind.get("close", 1) * 100, 2
                        )
                        if ind.get("close") and ind.get("change")
                        else None,
                        "RSI": round(ind.get("RSI", 0), 2) if ind.get("RSI") else None,
                        "volume": ind.get("volume"),
                        "above_SMA200": ind.get("close", 0)
                        > (ind.get("SMA200", 0) or 0)
                        if ind.get("close") and ind.get("SMA200")
                        else None,
                    }
                )
            except Exception as sym_err:
                results.append({"symbol": sym, "error": str(sym_err)})

        return json.dumps(
            {"scan_results": results, "interval": interval, "count": len(results)},
            indent=2,
            default=str,
        )

    except Exception as e:
        logger.error(f"Error scanning: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_forex_analysis(
    pair: str = "EURUSD",
    interval: str = "1d",
) -> str:
    """
    Get technical analysis for a forex pair.

    Args:
        pair: Forex pair (e.g. 'EURUSD', 'USDPEN', 'GBPUSD')
        interval: Timeframe
    """
    try:
        pair = pair.upper().replace("/", "")
        tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY)

        handler = TA_Handler(
            symbol=pair,
            screener="forex",
            exchange="FX_IDC",
            interval=tv_interval,
        )
        analysis = handler.get_analysis()
        result = _format_analysis(analysis)
        result["meta"] = {"pair": pair, "type": "forex", "interval": interval}
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error forex {pair}: {e}")
        return json.dumps({"error": str(e), "pair": pair})


@mcp.tool()
def get_crypto_analysis(
    symbol: str = "BTCUSDT",
    exchange: str = "BINANCE",
    interval: str = "1d",
) -> str:
    """
    Get technical analysis for a crypto pair.

    Args:
        symbol: Crypto pair (e.g. 'BTCUSDT', 'ETHUSDT', 'SOLUSDT')
        exchange: BINANCE or COINBASE
        interval: Timeframe
    """
    try:
        sym = symbol.upper()
        exch = exchange.upper()
        tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY)

        handler = TA_Handler(
            symbol=sym,
            screener="crypto",
            exchange=exch,
            interval=tv_interval,
        )
        analysis = handler.get_analysis()
        result = _format_analysis(analysis)
        result["meta"] = {"symbol": sym, "exchange": exch, "type": "crypto", "interval": interval}
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error crypto {symbol}: {e}")
        return json.dumps({"error": str(e), "symbol": symbol})


@mcp.tool()
def get_peru_market(
    symbols: str = "BAP,BVN,SCCO,IFS,CPAC",
    interval: str = "1d",
) -> str:
    """
    Quick scan of key Peruvian / BVL-listed stocks.
    Defaults to major Peruvian equities. Uses NYSE/NASDAQ for ADRs.

    Args:
        symbols: Comma-separated symbols
        interval: Timeframe
    """
    # Map common Peruvian ADRs to their US exchanges
    peru_exchange_map = {
        "BAP": "NYSE",
        "BVN": "NYSE",
        "SCCO": "NYSE",
        "IFS": "NYSE",
        "CPAC": "NYSE",
        "TV": "NYSE",
    }

    results = []
    for raw_sym in symbols.split(","):
        sym = raw_sym.strip().upper()
        if not sym:
            continue
        exch = peru_exchange_map.get(sym, "BVL")
        screener = "america"
        tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY)

        try:
            handler = TA_Handler(
                symbol=sym, screener=screener, exchange=exch, interval=tv_interval
            )
            analysis = handler.get_analysis()
            summary = analysis.summary
            ind = analysis.indicators
            results.append(
                {
                    "symbol": sym,
                    "exchange": exch,
                    "recommendation": summary.get("RECOMMENDATION"),
                    "close": ind.get("close"),
                    "RSI": round(ind.get("RSI", 0), 2) if ind.get("RSI") else None,
                    "SMA50": ind.get("SMA50"),
                    "SMA200": ind.get("SMA200"),
                }
            )
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    return json.dumps(
        {"market": "Peru/BVL", "scan": results, "interval": interval},
        indent=2,
        default=str,
    )


# ===================== HISTORICAL DATA (tvDatafeed / Pro) =====================

# tvDatafeed interval mapping
TV_INTERVAL_MAP = {
    "1m": "in_1_minute",
    "3m": "in_3_minute",
    "5m": "in_5_minute",
    "15m": "in_15_minute",
    "30m": "in_30_minute",
    "45m": "in_45_minute",
    "1h": "in_1_hour",
    "2h": "in_2_hour",
    "3h": "in_3_hour",
    "4h": "in_4_hour",
    "1d": "in_daily",
    "1w": "in_weekly",
    "1M": "in_monthly",
}


@mcp.tool()
def get_historical_data(
    symbol: str,
    exchange: str = "NASDAQ",
    interval: str = "1d",
    n_bars: int = 100,
) -> str:
    """
    Get historical OHLCV candle data for a symbol.
    Requires tvDatafeed. With Pro login: up to 5000+ bars. Without: ~200 bars.

    Args:
        symbol: Ticker symbol (e.g. 'AAPL', 'BAP')
        exchange: Exchange code (NASDAQ, NYSE, BVL, BINANCE, etc.)
        interval: Timeframe: 1m, 3m, 5m, 15m, 30m, 45m, 1h, 2h, 3h, 4h, 1d, 1w, 1M
        n_bars: Number of bars to retrieve (Pro: up to 5000+, Free: ~200)
    """
    if not TV_FEED_AVAILABLE:
        return json.dumps({
            "error": "tvDatafeed not installed. Add 'tvdatafeed' to requirements.txt.",
            "fix": "pip install tvdatafeed"
        })

    try:
        sym = symbol.upper().replace(":", "")
        exch = exchange.upper()
        tv_int_str = TV_INTERVAL_MAP.get(interval, "in_daily")
        tv_int = getattr(TvInterval, tv_int_str, TvInterval.in_daily)

        df = tv_feed.get_hist(
            symbol=sym,
            exchange=exch,
            interval=tv_int,
            n_bars=n_bars,
        )

        if df is None or df.empty:
            return json.dumps({"error": f"No data returned for {sym}:{exch}", "symbol": sym})

        # Convert to JSON-friendly format
        df = df.reset_index()
        df["datetime"] = df["datetime"].astype(str)
        records = df.to_dict(orient="records")

        return json.dumps({
            "symbol": sym,
            "exchange": exch,
            "interval": interval,
            "bars": len(records),
            "authenticated": TV_AUTH,
            "data": records,
        }, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error historical {symbol}: {e}")
        return json.dumps({"error": str(e), "symbol": symbol})


@mcp.tool()
def get_price_change(
    symbol: str,
    exchange: str = "NASDAQ",
    periods: str = "5,20,60,120,252",
) -> str:
    """
    Calculate price returns over multiple lookback periods (in trading days).
    Useful for performance attribution and momentum analysis.

    Args:
        symbol: Ticker symbol
        exchange: Exchange code
        periods: Comma-separated lookback periods in days (e.g. '5,20,60,252')
    """
    if not TV_FEED_AVAILABLE:
        return json.dumps({"error": "tvDatafeed not installed"})

    try:
        sym = symbol.upper()
        exch = exchange.upper()
        max_period = max(int(p) for p in periods.split(",")) + 5

        df = tv_feed.get_hist(
            symbol=sym, exchange=exch,
            interval=TvInterval.in_daily, n_bars=max_period
        )

        if df is None or df.empty:
            return json.dumps({"error": f"No data for {sym}"})

        current_price = float(df["close"].iloc[-1])
        results = {"symbol": sym, "exchange": exch, "current_price": current_price, "returns": {}}

        for p_str in periods.split(","):
            p = int(p_str.strip())
            if p < len(df):
                past_price = float(df["close"].iloc[-(p + 1)])
                ret = (current_price - past_price) / past_price * 100
                results["returns"][f"{p}d"] = round(ret, 2)
            else:
                results["returns"][f"{p}d"] = None

        return json.dumps(results, indent=2, default=str)

    except Exception as e:
        logger.error(f"Error price_change {symbol}: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def server_status() -> str:
    """
    Check server status: which data sources are active and authentication state.
    """
    return json.dumps({
        "server": "TradingView MCP Server",
        "tradingview_ta": True,
        "tvDatafeed": TV_FEED_AVAILABLE,
        "pro_authenticated": TV_AUTH,
        "note": "Pro login gives 5000+ bars history. Without Pro: ~200 bars."
            if TV_FEED_AVAILABLE else "Install tvdatafeed for historical OHLCV data.",
    }, indent=2)


# ===================== ENTRYPOINT =====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting TradingView MCP Server on port {port}")
    mcp.run(transport="sse", host="0.0.0.0", port=port)
