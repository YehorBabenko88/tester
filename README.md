# Bybit Historical Laboratory

Standalone, public-data-only research terminal in **Russian** for Bybit. This project is isolated: it uses no sibling repositories, no real trading endpoints, and no API credentials.

## Windows 11: launch

Requires Python 3.11–3.13 and a running local PostgreSQL server. Create/know a PostgreSQL account with permission to create a database, then:

```powershell
git clone https://github.com/YehorBabenko88/tester.git
cd tester
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Enter PostgreSQL username/password in **local** `.env`. The application will create only the database specified by `PGDATABASE` (default `bybit_tester`) and its own tables. It does **not** install PostgreSQL, invent/reset any database passwords, or open ports. Keep `.env` private. Visit the local URL printed by Streamlit (typically http://localhost:8501).

## Workflow

1. Select spot / linear / inverse; fetch the list of currently trading symbols; sort by 24h high-low range, price change, 24h turnover, current OI notional, or optionally queried 1h and 24h OI changes. For a 1h range, sample the top N by turnover and query closed 1m candles. **Range is a proxy for price movement, not statistical volatility.**
2. Check one or multiple symbols. Default start date is the instrument launch date when the exchange reports it. Choose a date range and mode. Discover tick archives **before** selecting the archive mode. Download all supported API candle kinds, all 13 exchange intervals and derivative OI, long/short and funding. Downloads are chunked, retryable and idempotent using PostgreSQL coverage records.
3. Daily public trade archives are downloaded from Bybit's public directory listing, CRC-verified through the end of the gzip stream, SHA256-hashed and ingested into PostgreSQL. Timestamp, taker direction, size and unique trade ID are checked. If anything fails, the segment is flagged `error` and can be retried. A day found in the index but not successfully processed remains incomplete.
4. Price-level footprint clusters use taker Buy minus taker Sell as delta; all 13 bar intervals can be generated from ticks, with Monday-anchored weeks and calendar months. The chart shows OHLC, volume, delta and separate OI/ratio/funding series. Data is in UTC.

## Actual Bybit coverage / limitations

| Dataset | Source | Availability |
|---|---|---|
| Trade OHLCV | `/v5/market/kline` | spot, linear, inverse; intervals 1,3,5,15,30,60,120,240,360,720,D,W,M |
| Mark and index price candles | Public V5 market API | derivatives only; not every product/era |
| Premium-index candles | Public V5 market API | linear only; not every product/era |
| Open interest | `/v5/market/open-interest` | derivatives; 5min,15min,30min,1h,4h,1d |
| Long/short holder ratio | `/v5/market/account-ratio` | derivatives; 5min,15min,30min,1h,4h,1d |
| Funding rates | `/v5/market/funding/history` | derivative event history when available |
| Historical executions | `public.bybit.com/trading/` or `/spot/` | per-date CSV.gz when published, by instrument and archive era |
| Taker delta and footprint | Computed from historical executions | only as complete as underlying trade files |
| Market cap, historical depth-of-book and historical liquidation stream | Not available from these candle/trade sources | **not fabricated** |
| Options historical volatility and option market metadata | Separate Bybit API | not yet implemented |

**Completeness is not guaranteed by the exchange.** The dashboard percentage means `complete` among *registered download segments*. It is **not** proof that every historical file ever existed, that an archive directory listing is exhaustive, that an exchange API has no hidden retention cutoff, or that full tick data for all markets can be retrieved. A completed API window with no closed records is marked `empty`, never silently counted as complete. An internal gap is flagged `gaps`; incomplete/current windows are `provisional`. Missing daily archive dates are highlighted. Closed candles are stored; an active/incomplete candle is not.

**Caution about performance:** downloading all intervals for multiple years and millions of trades can consume substantial local storage, hours/days of time and PostgreSQL space. Large trade files are streamed and committed in batches to avoid holding them in RAM. Cluster rebuilding is expensive and not incremental yet. Progress shows queued tasks/slices, not byte-perfect exchange-wide coverage. Closing Streamlit stops the job; completed segments are skipped on the next attempt.

**Before relying on data for research:** compare reconstructed OHLCV with exchange candles, audit archive missing dates, handle revisions/delistings, test with your real PostgreSQL and unrestricted Bybit connection. The repository is an initial research application, **not a certified exhaustive historical archive**.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
```

## Documentation

- Bybit V5 public market API: https://bybit-exchange.github.io/docs/v5/market/kline
- OI: https://bybit-exchange.github.io/docs/v5/market/open-interest
- Long/short: https://bybit-exchange.github.io/docs/v5/market/long-short-ratio
- Public recent trades and archive referral: https://bybit-exchange.github.io/docs/v5/market/recent-trade
- Public archive index: https://public.bybit.com/
