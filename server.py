"""
TradingView MCP Server
Custom MCP server for market analysis via TradingView data.
Includes OAuth 2.1 Dynamic Client Registration for Claude.ai compatibility.
"""

import os
import json
import logging
import secrets
import time
import hashlib
import base64
from typing import Optional
from urllib.parse import urlencode, parse_qs, urlparse

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse
from starlette.middleware import Middleware

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
        tv_feed = TvDatafeed()
        TV_AUTH = False
    TV_FEED_AVAILABLE = True
except Exception as e:
    print(f"tvDatafeed not available: {e}")
    tv_feed = None
    TV_FEED_AVAILABLE = False
    TV_AUTH = False

# --- Config ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tradingview-mcp")

PORT = int(os.environ.get("PORT", 8080))
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

mcp = FastMCP("TradingView MCP Server")

# ===================== OAUTH 2.1 STORAGE =====================

# In-memory stores (reset on restart — fine for this use case)
oauth_clients = {}      # client_id -> client metadata
oauth_codes = {}        # auth_code -> {client_id, code_challenge, redirect_uri, expires}
oauth_tokens = {}       # access_token -> {client_id, expires}


def get_base_url(request: Request) -> str:
    if BASE_URL:
        return BASE_URL
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{scheme}://{host}"


# ===================== OAUTH ENDPOINTS =====================

async def oauth_metadata(request: Request):
    """RFC 8414 — OAuth Authorization Server Metadata"""
    base = get_base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["read"],
    })


async def oauth_register(request: Request):
    """RFC 7591 — Dynamic Client Registration"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    client_id = f"client_{secrets.token_hex(16)}"
    client_secret = secrets.token_hex(32)

    client_meta = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": body.get("client_name", "Claude MCP Client"),
        "redirect_uris": body.get("redirect_uris", []),
        "grant_types": body.get("grant_types", ["authorization_code", "refresh_token"]),
        "response_types": body.get("response_types", ["code"]),
        "token_endpoint_auth_method": body.get("token_endpoint_auth_method", "client_secret_post"),
    }
    oauth_clients[client_id] = client_meta
    logger.info(f"Registered OAuth client: {client_id}")

    return JSONResponse(client_meta, status_code=201)


async def oauth_authorize(request: Request):
    """Authorization endpoint — auto-approves (no user login needed)"""
    params = dict(request.query_params)

    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")
    response_type = params.get("response_type", "code")

    if response_type != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)

    # Generate authorization code
    auth_code = secrets.token_hex(32)
    oauth_codes[auth_code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "expires": time.time() + 300,  # 5 min
    }

    logger.info(f"Issued auth code for client {client_id}")

    # Auto-redirect back with the code
    sep = "&" if "?" in redirect_uri else "?"
    redirect_url = f"{redirect_uri}{sep}code={auth_code}"
    if state:
        redirect_url += f"&state={state}"

    return RedirectResponse(url=redirect_url, status_code=302)


async def oauth_token(request: Request):
    """Token endpoint — exchange code for access token"""
    try:
        body = await request.form()
        body = dict(body)
    except Exception:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    grant_type = body.get("grant_type", "")

    if grant_type == "authorization_code":
        code = body.get("code", "")
        code_verifier = body.get("code_verifier", "")

        code_data = oauth_codes.pop(code, None)
        if not code_data:
            return JSONResponse({"error": "invalid_grant", "error_description": "Invalid or expired code"}, status_code=400)

        if code_data["expires"] < time.time():
            return JSONResponse({"error": "invalid_grant", "error_description": "Code expired"}, status_code=400)

        # Verify PKCE
        if code_data.get("code_challenge") and code_verifier:
            digest = hashlib.sha256(code_verifier.encode()).digest()
            computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if computed != code_data["code_challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

        access_token = secrets.token_hex(32)
        refresh_token = secrets.token_hex(32)

        oauth_tokens[access_token] = {
            "client_id": code_data["client_id"],
            "expires": time.time() + 86400 * 30,  # 30 days
        }
        oauth_tokens[refresh_token] = {
            "client_id": code_data["client_id"],
            "is_refresh": True,
            "expires": time.time() + 86400 * 90,  # 90 days
        }

        logger.info(f"Issued access token for client {code_data['client_id']}")

        return JSONResponse({
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": 86400 * 30,
            "refresh_token": refresh_token,
            "scope": "read",
        })

    elif grant_type == "refresh_token":
        refresh = body.get("refresh_token", "")
        token_data = oauth_tokens.get(refresh)

        if not token_data or not token_data.get("is_refresh"):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        access_token = secrets.token_hex(32)
        oauth_tokens[access_token] = {
            "client_id": token_data["client_id"],
            "expires": time.time() + 86400 * 30,
        }

        return JSONResponse({
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": 86400 * 30,
            "refresh_token": refresh,
            "scope": "read",
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ===================== INTERVAL MAPS =====================

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

EXCHANGE_MAP = {
    "US": "NASDAQ", "NYSE": "NYSE", "NASDAQ": "NASDAQ", "AMEX": "AMEX",
    "BVL": "BVL", "BMV": "BMV", "BCBA": "BCBA", "BVSP": "BVSP",
    "LSE": "LSE", "TSX": "TSX", "ASX": "ASX", "NSE": "NSE",
    "HKEX": "HKEX", "TSE": "TSE",
    "BINANCE": "BINANCE", "COINBASE": "COINBASE",
    "FX_IDC": "FX_IDC", "CBOT": "CBOT", "NYMEX": "NYMEX",
    "COMEX": "COMEX", "TVC": "TVC",
}

SCREENER_MAP = {
    "US": "america", "NYSE": "america", "NASDAQ": "america", "AMEX": "america",
    "BVL": "america", "BMV": "america", "BCBA": "america", "BVSP": "america",
    "TSX": "america", "LSE": "uk", "ASX": "australia", "NSE": "india",
    "HKEX": "hongkong", "TSE": "japan",
    "BINANCE": "crypto", "COINBASE": "crypto",
    "FX_IDC": "forex", "CBOT": "cfd", "NYMEX": "cfd", "COMEX": "cfd", "TVC": "cfd",
}

TV_INTERVAL_MAP = {
    "1m": "in_1_minute", "3m": "in_3_minute", "5m": "in_5_minute",
    "15m": "in_15_minute", "30m": "in_30_minute", "45m": "in_45_minute",
    "1h": "in_1_hour", "2h": "in_2_hour", "3h": "in_3_hour", "4h": "in_4_hour",
    "1d": "in_daily", "1w": "in_weekly", "1M": "in_monthly",
}


def _resolve_exchange(symbol: str, exchange: Optional[str] = None) -> tuple[str, str, str]:
    if ":" in symbol:
        parts = symbol.split(":", 1)
        exch = parts[0].upper()
        sym = parts[1].upper()
    else:
        sym = symbol.upper()
        exch = (exchange or "NASDAQ").upper()
    return sym, EXCHANGE_MAP.get(exch, exch), SCREENER_MAP.get(exch, "america")


def _format_analysis(analysis) -> dict:
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
            if indicators.get("close") and indicators.get("change") else None,
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
            "SMA10": indicators.get("SMA10"), "SMA20": indicators.get("SMA20"),
            "SMA50": indicators.get("SMA50"), "SMA200": indicators.get("SMA200"),
            "EMA10": indicators.get("EMA10"), "EMA20": indicators.get("EMA20"),
            "EMA50": indicators.get("EMA50"), "EMA200": indicators.get("EMA200"),
        },
    }


# ===================== MCP TOOLS =====================


@mcp.tool()
def get_technical_analysis(symbol: str, exchange: str = "NASDAQ", interval: str = "1d") -> str:
    """Get full technical analysis: summary BUY/SELL/NEUTRAL, oscillators, MAs, RSI, MACD, BB, etc.

    Args:
        symbol: Ticker (e.g. 'AAPL', 'NYSE:BAP')
        exchange: Exchange code (NASDAQ, NYSE, BVL, BINANCE, FX_IDC, etc.)
        interval: Timeframe: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w, 1M
    """
    try:
        sym, exch, screener = _resolve_exchange(symbol, exchange)
        handler = TA_Handler(symbol=sym, screener=screener, exchange=exch,
                             interval=INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY))
        result = _format_analysis(handler.get_analysis())
        result["meta"] = {"symbol": sym, "exchange": exch, "screener": screener, "interval": interval}
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


@mcp.tool()
def get_multi_timeframe_analysis(symbol: str, exchange: str = "NASDAQ", intervals: str = "1h,4h,1d,1w") -> str:
    """Multi-timeframe confluence analysis.

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
            handler = TA_Handler(symbol=sym, screener=screener, exchange=exch, interval=tv_interval)
            analysis = handler.get_analysis()
            s = analysis.summary
            ind = analysis.indicators
            results[tf] = {
                "recommendation": s.get("RECOMMENDATION"),
                "buy": s.get("BUY"), "sell": s.get("SELL"), "neutral": s.get("NEUTRAL"),
                "close": ind.get("close"),
                "RSI": round(ind.get("RSI", 0), 2) if ind.get("RSI") else None,
                "MACD_signal": "bullish" if (ind.get("MACD.macd", 0) or 0) > (ind.get("MACD.signal", 0) or 0) else "bearish",
                "above_SMA200": ind.get("close", 0) > (ind.get("SMA200", 0) or 0)
                if ind.get("close") and ind.get("SMA200") else None,
            }
        return json.dumps({"symbol": sym, "exchange": exch, "timeframes": results}, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_indicators(symbol: str, exchange: str = "NASDAQ", interval: str = "1d",
                   indicators: str = "RSI,MACD.macd,MACD.signal,close,volume,SMA50,SMA200,EMA20,BB.upper,BB.lower,ATR,ADX,Stoch.K,Stoch.D,CCI20,VWAP") -> str:
    """Get specific indicator values.

    Args:
        symbol: Ticker symbol
        exchange: Exchange code
        interval: Timeframe
        indicators: Comma-separated indicator names
    """
    try:
        sym, exch, screener = _resolve_exchange(symbol, exchange)
        handler = TA_Handler(symbol=sym, screener=screener, exchange=exch,
                             interval=INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY))
        all_ind = handler.get_analysis().indicators
        result = {}
        for ind in [i.strip() for i in indicators.split(",")]:
            val = all_ind.get(ind)
            result[ind] = round(val, 6) if isinstance(val, float) else val
        return json.dumps({"symbol": sym, "exchange": exch, "interval": interval, "indicators": result}, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def scan_symbols(symbols: str, exchange: str = "NASDAQ", interval: str = "1d") -> str:
    """Scan multiple symbols for comparative summary.

    Args:
        symbols: Comma-separated symbols (e.g. 'AAPL,MSFT,GOOGL')
        exchange: Default exchange
        interval: Timeframe
    """
    results = []
    for raw_sym in symbols.split(","):
        raw_sym = raw_sym.strip()
        if not raw_sym:
            continue
        sym, exch, screener = _resolve_exchange(raw_sym, exchange)
        try:
            handler = TA_Handler(symbol=sym, screener=screener, exchange=exch,
                                 interval=INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY))
            a = handler.get_analysis()
            s, ind = a.summary, a.indicators
            results.append({
                "symbol": sym, "exchange": exch, "recommendation": s.get("RECOMMENDATION"),
                "buy_signals": s.get("BUY"), "sell_signals": s.get("SELL"),
                "close": ind.get("close"),
                "change_pct": round(ind.get("change", 0) / ind.get("close", 1) * 100, 2)
                if ind.get("close") and ind.get("change") else None,
                "RSI": round(ind.get("RSI", 0), 2) if ind.get("RSI") else None,
                "volume": ind.get("volume"),
                "above_SMA200": ind.get("close", 0) > (ind.get("SMA200", 0) or 0)
                if ind.get("close") and ind.get("SMA200") else None,
            })
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return json.dumps({"scan_results": results, "interval": interval, "count": len(results)}, indent=2, default=str)


@mcp.tool()
def get_forex_analysis(pair: str = "EURUSD", interval: str = "1d") -> str:
    """Forex pair technical analysis.

    Args:
        pair: Forex pair (e.g. 'EURUSD', 'USDPEN', 'GBPUSD')
        interval: Timeframe
    """
    try:
        pair = pair.upper().replace("/", "")
        handler = TA_Handler(symbol=pair, screener="forex", exchange="FX_IDC",
                             interval=INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY))
        result = _format_analysis(handler.get_analysis())
        result["meta"] = {"pair": pair, "type": "forex", "interval": interval}
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "pair": pair})


@mcp.tool()
def get_crypto_analysis(symbol: str = "BTCUSDT", exchange: str = "BINANCE", interval: str = "1d") -> str:
    """Crypto pair technical analysis.

    Args:
        symbol: Crypto pair (e.g. 'BTCUSDT', 'ETHUSDT')
        exchange: BINANCE or COINBASE
        interval: Timeframe
    """
    try:
        handler = TA_Handler(symbol=symbol.upper(), screener="crypto", exchange=exchange.upper(),
                             interval=INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY))
        result = _format_analysis(handler.get_analysis())
        result["meta"] = {"symbol": symbol.upper(), "exchange": exchange.upper(), "type": "crypto", "interval": interval}
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


@mcp.tool()
def get_peru_market(symbols: str = "BAP,BVN,SCCO,IFS,CPAC", interval: str = "1d") -> str:
    """Quick scan of Peruvian / BVL-listed ADRs.

    Args:
        symbols: Comma-separated symbols
        interval: Timeframe
    """
    peru_map = {"BAP": "NYSE", "BVN": "NYSE", "SCCO": "NYSE", "IFS": "NYSE", "CPAC": "NYSE", "TV": "NYSE"}
    results = []
    for raw_sym in symbols.split(","):
        sym = raw_sym.strip().upper()
        if not sym:
            continue
        exch = peru_map.get(sym, "BVL")
        try:
            handler = TA_Handler(symbol=sym, screener="america", exchange=exch,
                                 interval=INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY))
            a = handler.get_analysis()
            s, ind = a.summary, a.indicators
            results.append({
                "symbol": sym, "exchange": exch, "recommendation": s.get("RECOMMENDATION"),
                "close": ind.get("close"),
                "RSI": round(ind.get("RSI", 0), 2) if ind.get("RSI") else None,
                "SMA50": ind.get("SMA50"), "SMA200": ind.get("SMA200"),
            })
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return json.dumps({"market": "Peru/BVL", "scan": results, "interval": interval}, indent=2, default=str)


@mcp.tool()
def get_historical_data(symbol: str, exchange: str = "NASDAQ", interval: str = "1d", n_bars: int = 100) -> str:
    """Get historical OHLCV candle data. Pro login gives 5000+ bars, free ~200.

    Args:
        symbol: Ticker symbol
        exchange: Exchange code
        interval: Timeframe
        n_bars: Number of bars
    """
    if not TV_FEED_AVAILABLE:
        return json.dumps({"error": "tvDatafeed not available on this server"})
    try:
        sym, exch = symbol.upper().replace(":", ""), exchange.upper()
        tv_int = getattr(TvInterval, TV_INTERVAL_MAP.get(interval, "in_daily"), TvInterval.in_daily)
        df = tv_feed.get_hist(symbol=sym, exchange=exch, interval=tv_int, n_bars=n_bars)
        if df is None or df.empty:
            return json.dumps({"error": f"No data for {sym}:{exch}"})
        df = df.reset_index()
        df["datetime"] = df["datetime"].astype(str)
        return json.dumps({"symbol": sym, "exchange": exch, "interval": interval,
                           "bars": len(df), "authenticated": TV_AUTH,
                           "data": df.to_dict(orient="records")}, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "symbol": symbol})


@mcp.tool()
def get_price_change(symbol: str, exchange: str = "NASDAQ", periods: str = "5,20,60,120,252") -> str:
    """Price returns over multiple lookback periods (trading days).

    Args:
        symbol: Ticker symbol
        exchange: Exchange code
        periods: Comma-separated day counts (e.g. '5,20,60,252')
    """
    if not TV_FEED_AVAILABLE:
        return json.dumps({"error": "tvDatafeed not available"})
    try:
        sym, exch = symbol.upper(), exchange.upper()
        max_p = max(int(p) for p in periods.split(",")) + 5
        df = tv_feed.get_hist(symbol=sym, exchange=exch, interval=TvInterval.in_daily, n_bars=max_p)
        if df is None or df.empty:
            return json.dumps({"error": f"No data for {sym}"})
        cur = float(df["close"].iloc[-1])
        rets = {}
        for p_str in periods.split(","):
            p = int(p_str.strip())
            if p < len(df):
                past = float(df["close"].iloc[-(p + 1)])
                rets[f"{p}d"] = round((cur - past) / past * 100, 2)
            else:
                rets[f"{p}d"] = None
        return json.dumps({"symbol": sym, "exchange": exch, "current_price": cur, "returns": rets}, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def server_status() -> str:
    """Check server status and authentication state."""
    return json.dumps({
        "server": "TradingView MCP Server",
        "tradingview_ta": True, "tvDatafeed": TV_FEED_AVAILABLE,
        "pro_authenticated": TV_AUTH,
        "note": "Pro login gives 5000+ bars history." if TV_FEED_AVAILABLE else "tvDatafeed not available.",
    }, indent=2)


# ===================== APP ASSEMBLY =====================

async def health(request: Request):
    return JSONResponse({"status": "ok", "server": "TradingView MCP Server"})

# Build MCP SSE app
mcp_sse_app = mcp.sse_app()

# Build Starlette app: OAuth routes (explicit) + MCP SSE (mounted)
# Starlette checks Routes before Mounts, so OAuth won't conflict
app = Starlette(
    routes=[
        Route("/", health),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Route("/register", oauth_register, methods=["POST"]),
        Route("/authorize", oauth_authorize),
        Route("/token", oauth_token, methods=["POST"]),
        # Mount MCP at both root and /mcp for compatibility
        Mount("/mcp", app=mcp_sse_app),
        Mount("/", app=mcp_sse_app),
    ],
)

# ===================== ENTRYPOINT =====================

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting TradingView MCP Server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
