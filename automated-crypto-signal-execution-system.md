# Automated Crypto Signal Execution System

**Role:** Full-stack / Backend Developer  
**Period:** Aug – Sep 2026  
**Type:** Personal / Portfolio project  
**Stack:** Python 3.12, Telethon, CCXT, PyYAML, PostgreSQL (psycopg), Textual, Docker, uv, GitHub Actions, GCP

## Summary

Personal system that ingests crypto trading signals from Telegram, parses them into structured trades, and executes on OKX or Bybit via CCXT — with backtesting, performance metrics, weekly reports, and production deploy on a GCP VM.

## Highlights

- Built an automated pipeline from Telegram signal channels → pluggable parsers → exchange order execution (OKX / Bybit) using CCXT, with demo and live modes
- Designed channel-agnostic config so new signal sources can be added without rewriting the bot (swap `channels.yaml` / parser modules)
- Implemented backtesting plus period metrics (win rate, ROI, avg R) and weekly Telegram performance reports
- Shipped a companion Telegram forwarder (Telethon user client + bot) with rule-based routing and a Textual TUI for setup and management
- Containerized forwarder + trading bot with Docker Compose and CI/CD (GitHub Actions → GCP VM over SSH)

## Key Features

- Multi-format signal parsers (e.g. DEX VIP, OKX confirm)
- Exchange-agnostic execution (OKX / Bybit, sandbox + mainnet)
- Trade store, metrics, backtest CLI, and weekly reporting
- Telegram message forwarder without “Forwarded from” attribution
- Secrets-safe deploy path (sessions/API keys stay on the VM)

## Links

- Demo: _TBD_
- Repository: _TBD_
