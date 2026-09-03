"""Crypto-news adapter with RSS fallback and optional NewsAPI override."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from . import SCHEMA_VERSION, SchemaError


def parse_rss(xml_text: str, query: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml_text)
        items = root.findall(".//item")
        headlines = []
        for item in items[:20]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title and link:
                headlines.append(
                    {
                        "title": title,
                        "url": link,
                        "published_at": (item.findtext("pubDate") or "").strip(),
                    }
                )
        if not headlines:
            # Return empty headlines instead of raising - allows trading cycle to continue
            return {
                "schema_version": SCHEMA_VERSION,
                "source": "google_news_rss",
                "query": query,
                "headlines": [],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "source": "google_news_rss",
            "query": query,
            "headlines": headlines,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except SchemaError:
        raise
    except ET.ParseError as exc:
        raise SchemaError(f"Malformed RSS payload: {exc}") from exc


def _parse_newsapi(payload: Any, query: str) -> dict[str, Any]:
    try:
        articles = payload["articles"]
        if not isinstance(articles, list) or not articles:
            raise SchemaError("NewsAPI returned no articles")
        headlines = [
            {
                "title": str(article["title"]),
                "url": str(article["url"]),
                "published_at": str(article.get("publishedAt", "")),
            }
            for article in articles[:20]
            if article.get("title") and article.get("url")
        ]
        if not headlines:
            raise SchemaError("NewsAPI articles did not match the expected schema")
        return {
            "schema_version": SCHEMA_VERSION,
            "source": "newsapi",
            "query": query,
            "headlines": headlines,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except SchemaError:
        raise
    except (KeyError, TypeError) as exc:
        raise SchemaError(f"Malformed NewsAPI payload: {exc}") from exc


async def fetch(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch asset news without embedding any credentials."""
    query = os.getenv("HERMES_ASSET", "SOL/USDT").split("/", 1)[0].upper()
    key = os.getenv("NEWS_API_KEY", "").strip()
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    try:
        if key:
            response = await http.get(
                "https://newsapi.org/v2/everything",
                params={"q": f"{query} cryptocurrency", "language": "en", "pageSize": 20},
                headers={"X-Api-Key": key},
            )
            response.raise_for_status()
            return _parse_newsapi(response.json(), query)

        rss_url = (
            "https://news.google.com/rss/search?q="
            f"{quote_plus(query + ' cryptocurrency')}&hl=en-US&gl=US&ceid=US:en"
        )
        response = await http.get(rss_url)
        response.raise_for_status()
        return parse_rss(response.text, query)
    finally:
        if owns_client:
            await http.aclose()
