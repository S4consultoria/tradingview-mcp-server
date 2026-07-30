# TradingView MCP Server (núcleo)

Servidor MCP construido desde cero que expone datos de TradingView vía
librerías no oficiales (`tradingview-ta`, `tvDatafeed`). Sigue el mismo
patrón de despliegue que el servidor MCP de Alpaca: Streamable HTTP
montado en `/mcp`, listo para Render.

## Herramientas expuestas

- **get_quote_and_rating(symbol, exchange, screener, interval)** — cotización
  actual + rating técnico (Comprar/Vender/Neutral) + indicadores (RSI, MACD,
  medias móviles, etc.)
- **get_historical_data(symbol, exchange, interval, n_bars)** — velas OHLCV
  históricas.
- **screen_market(symbols, exchange, screener, interval)** — compara el
  rating técnico de varios símbolos a la vez.

## Variables de entorno (opcionales)

- `TV_USERNAME` / `TV_PASSWORD`: credenciales de TradingView para
  `tvDatafeed`. Sin ellas funciona en modo anónimo (con más límites).
- `PORT`: puerto HTTP (Render lo inyecta automáticamente).

## Correr localmente

```bash
pip install -r requirements.txt
python server.py
```

El servidor queda escuchando en `http://localhost:8000/mcp`.

## Desplegar en Render

1. Sube esta carpeta a un repo de GitHub.
2. En Render: New → Web Service → conecta el repo.
3. Render detecta `render.yaml` automáticamente (Build: `pip install -r
   requirements.txt`, Start: `python server.py`).
4. (Opcional) configura `TV_USERNAME` / `TV_PASSWORD` en Environment.
5. Una vez desplegado, la URL del conector para Claude.ai es:
   `https://<tu-servicio>.onrender.com/mcp`

## Importante — limitaciones a tener en cuenta

TradingView **no tiene una API pública oficial**. Este servidor usa
librerías no oficiales que dependen de endpoints internos y pueden
romperse sin aviso si TradingView cambia su plataforma. Está pensado
como núcleo funcional (cotizaciones, ratings, histórico, screener
manual); funciones como alertas nativas, Pine Script o dibujos técnicos
de tu cuenta no están cubiertas — requerirían ingeniería inversa
adicional del WebSocket privado de TradingView.
