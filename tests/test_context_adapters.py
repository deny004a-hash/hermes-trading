from hermes_trading.adapters.macro import parse_global_market
from hermes_trading.adapters.news import parse_rss
from hermes_trading.adapters.onchain import parse_chain_tvl


def test_solana_tvl_is_versioned_and_uses_latest_observation():
    result = parse_chain_tvl(
        [{"date": 1_700_000_000, "tvl": 1.2e9}, {"date": 1_700_086_400, "tvl": 1.3e9}],
        "Solana",
    )

    assert result["schema_version"] == 1
    assert result["chain"] == "Solana"
    assert result["tvl_usd"] == 1.3e9


def test_news_rss_returns_headlines_without_html():
    xml = """<?xml version='1.0'?>
    <rss><channel><item><title>SOL &amp; markets rally</title>
    <link>https://example.com/a</link><pubDate>Fri, 28 Aug 2026 10:00:00 GMT</pubDate>
    </item></channel></rss>"""

    result = parse_rss(xml, "SOL")

    assert result["schema_version"] == 1
    assert result["headlines"][0]["title"] == "SOL & markets rally"
    assert result["headlines"][0]["url"] == "https://example.com/a"


def test_global_market_payload_is_normalized():
    payload = {
        "data": {
            "total_market_cap": {"usd": 3_000_000_000_000},
            "market_cap_percentage": {"btc": 52.5},
            "market_cap_change_percentage_24h_usd": -1.25,
        }
    }

    result = parse_global_market(payload)

    assert result["schema_version"] == 1
    assert result["total_market_cap_usd"] == 3_000_000_000_000
    assert result["btc_dominance_pct"] == 52.5
    assert result["market_cap_change_24h_pct"] == -1.25
